# SPDX-License-Identifier: GPL-3.0
"""Shared TUI board operations. All package, firmware and acceptance logic lives here."""
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time

from loader.board_packages import FOCUS_PACKAGES, refresh_target_indexes
from loader import board_snapshot
from loader import board_log_cleanup

FIRMWARE_DIR = Path('/opt/roboparty/lib/firmware')
FLASHER = Path('/opt/roboparty/bin/flash_hpm.sh')
NORMAL = ('1209', '2323')
BOOT = ('34b7', '0008')
EXPECTED_CAN = {'can0', 'can1', 'can2', 'can3'}
APT_OPTIONS = ['-y', '--no-remove', '-o', 'DPkg::Lock::Timeout=120',
               '-o', 'Dpkg::Options::=--force-confold']


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def command(argv, timeout=30):
    # APT owns its dpkg transaction. Client cancellation/timeouts must not kill it mid-write.
    return subprocess.run(argv, capture_output=True, text=True,
                          timeout=None if argv[0] == 'apt-get' else timeout, check=False,
                          env={**os.environ, 'LC_ALL': 'C.UTF-8', 'LANG': 'C.UTF-8',
                               'TMPDIR': '/tmp', 'DEBIAN_FRONTEND': 'noninteractive',
                               'NEEDRESTART_MODE': 'l'})


def checked(argv, timeout=30, run=command):
    completed = run(argv, timeout)
    if completed.returncode:
        raise RuntimeError(f'{argv[0]} exit={completed.returncode}: '
                           + (completed.stderr or completed.stdout)[-4000:])
    return completed.stdout


def check_can(devices, links):
    targets = [usb for usb in devices if (usb['vid'].lower(), usb['pid'].lower()) == NORMAL]
    if len(targets) != 1:
        return {'outcome': 'fail', 'message': f'正常 HPM 数量应为1，实际{len(targets)}', 'interfaces': []}
    usb = targets[0]
    belonging = [link for link in links if link.get('driver') == 'gs_usb'
                 and (link.get('usbParent') or {}).get('path') == usb['path']]
    names = {link['ifname'] for link in belonging}
    missing = EXPECTED_CAN - names
    return {'outcome': 'fail' if missing else 'pass',
            'message': '缺少 HPM 接口：' + ', '.join(sorted(missing)) if missing
            else 'HPM 的 can0–can3 已全部识别（未发送 CAN 帧）',
            'interfaces': sorted(names), 'usb': usb}


def check_can_configuration(recognition, links):
    """Inspect the RoboPi2 HPM defaults without changing links or sending frames."""
    if recognition['outcome'] != 'pass':
        return dict(outcome='needs_review', message='HPM 四路未完整识别，无法确认 CAN 配置', interfaces=[])
    rows = []
    for link in links:
        if (link['ifname'] not in EXPECTED_CAN or link.get('driver') != 'gs_usb'
                or (link.get('usbParent') or {}).get('path') != recognition['usb']['path']):
            continue
        info = (link.get('linkinfo') or {}).get('info_data') or {}
        issues, unknown = [], []

        def expect(actual, expected, label):
            if actual is None:
                unknown.append(label + '未采集到')
            elif actual != expected:
                issues.append(f'{label}={actual}，期望 {expected}')

        if 'flags' not in link:
            unknown.append('接口启用状态未采集到')
        elif 'UP' not in link['flags']:
            issues.append('接口 DOWN，尚未启用')
        expect(link.get('mtu'), 72, 'MTU')
        expect(link.get('txqlen'), 10000, '发送队列长度')
        if 'ctrlmode' not in info:
            unknown.append('FD 模式未采集到')
        elif 'FD' not in info['ctrlmode']:
            issues.append('FD 未开启')
        expect((info.get('bittiming') or {}).get('bitrate'), 1000000, '仲裁速率')
        expect((info.get('data_bittiming') or {}).get('bitrate'), 5000000, '数据速率')
        expect(info.get('state'), 'ERROR-ACTIVE', 'CAN 控制器状态')
        outcome = 'fail' if issues else 'needs_review' if unknown else 'pass'
        rows.append(dict(ifname=link['ifname'], outcome=outcome,
                         message='；'.join(issues + unknown) if issues or unknown
                         else 'UP；FD；1M/5M；MTU 72；队列 10000；ERROR-ACTIVE'))
    outcome = 'fail' if any(row['outcome'] == 'fail' for row in rows) else (
        'needs_review' if len(rows) != 4 or any(row['outcome'] != 'pass' for row in rows) else 'pass')
    message = ('HPM 四路配置符合 RoboPi2 默认值（未发送 CAN 帧，未验证电机通信）'
               if outcome == 'pass' else 'HPM CAN 配置异常或未确认：' + '；'.join(
                   row['ifname'] + '：' + row['message'] for row in rows if row['outcome'] != 'pass'))
    return dict(outcome=outcome, message=message, interfaces=rows)


def usb_ready(devices):
    normal = [usb for usb in devices if (usb['vid'].lower(), usb['pid'].lower()) == NORMAL]
    boot = [usb for usb in devices if (usb['vid'].lower(), usb['pid'].lower()) == BOOT]
    return len(normal) == 1 and not boot


def wait_usb(read_usb=board_snapshot.usb, sleep=time.sleep, attempts=30):
    stable = 0
    for _ in range(attempts):
        observed = read_usb()
        stable = stable + 1 if usb_ready(observed) else 0
        if stable >= 2:
            return observed
        sleep(1)
    raise RuntimeError('刷写命令完成，但正常 HPM USB 1209:2323 未连续两次稳定出现')


def select_firmware(paths, directory=FIRMWARE_DIR):
    directory = Path(directory).resolve()
    candidates = []
    for value in paths:
        path = Path(value)
        if path.parent != directory or not re.fullmatch(r'ethercanfd_[A-Za-z0-9_.-]+\.bin', path.name):
            continue
        if path.is_symlink() or path.resolve().parent != directory:
            raise RuntimeError('固件路径是符号链接或越出允许目录')
        if not path.is_file() or not 0 < path.stat().st_size <= 64 * 1024 * 1024:
            raise RuntimeError('包内固件不存在、为空或超过64MiB')
        candidates.append(path)
    candidates = sorted(set(candidates))
    if len(candidates) != 1:
        raise RuntimeError(f'roboparty-analyze 包内应有唯一 EtherCANFD 固件，实际{len(candidates)}：'
                           + ', '.join(str(path) for path in candidates))
    return candidates[0]


def preflight(emit=lambda *_: None):
    if os.geteuid() != 0:
        raise RuntimeError('板端操作需要 root；未自动提权')
    model = board_snapshot.text('/proc/device-tree/model', True) or ''
    if 'ROBOPI2' not in model.upper():
        raise RuntimeError('当前写操作仅支持已验证的 RoboPi2 板型')
    disk = os.statvfs('/')
    if disk.f_bavail * disk.f_frsize < 256 * 1024 * 1024:
        raise RuntimeError('根分区可用空间不足；未开始升级/烧录')
    cleanup = board_log_cleanup.cleanup(emit)
    disk = os.statvfs('/var/log')
    if disk.f_bavail * disk.f_frsize < 2 * 1024 * 1024:
        raise RuntimeError('/var/log 可用空间不足；没有可安全清理的已结束旧会话，未开始升级/烧录')
    return cleanup


def refresh_indexes(emit, run=command):
    emit('apt-update', '刷新板卡软件源')
    output = checked(['apt-get', '-o', 'APT::Update::Error-Mode=any',
                      '-o', 'Acquire::http::Timeout=15', '-o', 'Acquire::https::Timeout=15',
                      '-o', 'Acquire::Retries=0', 'update'], 300, run)
    if re.search(r'(?im)^Err:|^E:|Failed to fetch|Some index files failed', output):
        raise RuntimeError('APT 索引未完整刷新：' + output[-3000:])
    emit('apt-update', output[-6000:] or '软件源已刷新')
    return now()


def upgrade(emit, run=command):
    refreshed = refresh_indexes(emit, run)
    emit('apt-upgrade', '升级板卡全部可升级软件包；TUI 更新结束后会重新读取新版接口')
    output = checked(['apt-get', *APT_OPTIONS, '--with-new-pkgs', 'upgrade'], 3600, run)
    emit('apt-upgrade', output[-10000:] or '软件升级命令完成')
    return refreshed


def install_and_flash(emit, run=command, read_usb=board_snapshot.usb,
                      read_can=board_snapshot.can, sleep=time.sleep):
    refreshed = refresh_indexes(emit, run)
    emit('analyze-install', '安装/升级 roboparty-analyze')
    output = checked(['apt-get', *APT_OPTIONS, 'install', 'roboparty-analyze'], 1200, run)
    emit('analyze-install', output[-6000:] or 'analyze 安装完成')
    installed = checked(['dpkg-query', '-W', '-f=${Status}', 'roboparty-analyze'], 10, run).strip()
    if installed != 'install ok installed':
        raise RuntimeError('roboparty-analyze 未完整安装：' + installed)
    firmware = select_firmware(checked(['dpkg-query', '-L', 'roboparty-analyze'], 10, run).splitlines())
    devices = read_usb()
    if len([usb for usb in devices if (usb['vid'].lower(), usb['pid'].lower()) in (NORMAL, BOOT)]) != 1:
        raise RuntimeError('HPM 目标缺失或不唯一，未执行刷写')
    if not FLASHER.is_file() or not os.access(FLASHER, os.X_OK):
        raise RuntimeError('analyze 包的 flash_hpm.sh 缺失或不可执行')
    digest = hashlib.sha256(firmware.read_bytes()).hexdigest()
    emit('hpm-flash', f'调用包内脚本：{FLASHER} {firmware}；SHA256={digest}')
    # New analyze packages handle the service themselves. This also protects old scripts:
    # once stopped here their own guard sees inactive; this finally restores the original state.
    service = 'hpm-sreset.service'
    state = run(['systemctl', 'is-active', service], 10)
    was_active = state.returncode == 0 and state.stdout.strip() == 'active'
    try:
        if was_active:
            checked(['systemctl', 'stop', service], 30, run)
        env = {**{key: value for key, value in os.environ.items() if not key.startswith('HPM')},
               'HPM_CHIP': 'hpm5e00', 'HPM_ADDR': '0x400',
               'HPM_RESET_LED': '/sys/class/leds/hpm_reset/brightness',
               'HPM_BOOT_LED': '/sys/class/leds/hpm_boot_enable/brightness',
               'HPM_RESET_SERVICE': service}
        # Board policy and script defaults are independent of caller HPM overrides.
        if run is command:
            process = subprocess.run([str(FLASHER), str(firmware)], capture_output=True,
                                     text=True, env=env, check=False)
            if process.returncode:
                raise RuntimeError('HPM 刷写失败：' + (process.stdout + process.stderr)[-6000:])
            output = process.stdout + process.stderr
        else:
            output = checked([str(FLASHER), str(firmware)], 1200, run)
        emit('hpm-flash', output[-10000:] or '刷写脚本执行成功')
    finally:
        if was_active:
            checked(['systemctl', 'start', service], 30, run)
    emit('usb-verify', '等待正常 HPM USB 连续两次枚举')
    devices = wait_usb(read_usb, sleep)
    emit('usb-verify', '刷写完成，HPM USB 1209:2323 已识别')
    # Driver/netdev creation can follow USB enumeration. Allow a bounded settle interval.
    for _ in range(15):
        can = check_can(devices, read_can())
        if can['outcome'] == 'pass':
            break
        sleep(1)
    emit('can-verify', can['message'])
    if can['outcome'] != 'pass':
        raise RuntimeError(can['message'])
    return {'refreshedAt': refreshed, 'firmware': str(firmware), 'sha256': digest,
            'usbVerified': True, 'can': can, 'byteVerification': 'not_performed'}


def snapshot(refreshed_at=None):
    raw = board_snapshot.collect()
    if raw['identity'] != raw['endIdentity']:
        raise RuntimeError('采集期间板端身份发生变化')
    stamp = raw['collectedAt']
    parameters = []

    def add(group, name, value, outcome='pass'):
        parameters.append(dict(group=group, name=name,
                               value=value if isinstance(value, str) else json.dumps(value, ensure_ascii=False),
                               outcome=outcome, collectedAt=stamp))

    def section(name, group):
        part = raw[name]
        if part['error']:
            add(group, '采集错误', part['error'], 'error')
            return None
        return part['data']

    add('设备', '板端后端', 'robopi-tui JSON API v1；全部业务由板端执行')
    add('设备', '主机名', raw['identity']['hostname'])
    add('设备', '运行权限', f"uid={raw['identity']['uid']}")
    for key, group in [('system', '系统'), ('resources', '资源'), ('network', '网络')]:
        data = section(key, group)
        if data:
            labels = {'os': '当前系统', 'kernel': '内核', 'model': '板型', 'uptime': '运行时间',
                      'architecture': '架构', 'memory': '内存', 'rootAvailableBytes': '根分区可用字节',
                      'temperatures': '温度', 'disks': '各分区空间', 'load': 'CPU负载',
                      'addresses': '接口地址', 'routes': '路由', 'dns': 'DNS配置',
                      'dnsLinks': '各链路DNS', 'usbLink': 'USB网络链路'}
            for name, value in data.items():
                add(group, labels.get(name, name), value)
    usb = section('usb', 'HPM USB')
    if usb is not None:
        add('HPM USB', '枚举状态', '正常模式已识别' if usb_ready(usb) else '正常模式未就绪',
            'pass' if usb_ready(usb) else 'fail')
        for item in usb:
            add('HPM USB', '设备', item)
    links = section('can', 'CAN')
    can_check = None
    can_configuration = None
    if usb is not None and links is not None:
        can_check = check_can(usb, links)
        add('CAN', 'HPM四路识别', can_check['message'], can_check['outcome'])
        can_configuration = check_can_configuration(can_check, links)
        add('CAN', 'HPM配置检查', can_configuration['message'], can_configuration['outcome'])
        for row in can_configuration['interfaces']:
            add('CAN', 'HPM配置 ' + row['ifname'], row['message'], row['outcome'])
        for link in links:
            add('CAN', link['ifname'], link)
    firmware = section('firmware', '固件文件')
    if isinstance(firmware, dict):
        files = firmware.get('files', [])
        if not files:
            add('固件文件', 'HPM固件文件', '未找到 ethercanfd 固件文件', 'needs_review')
        for item in files:
            suffix = f"（{Path(item['path']).name}）" if len(files) > 1 else ''
            add('固件文件', 'HPM固件文件' + suffix, item['path'])
            if item.get('error'):
                add('固件文件', '文件检查' + suffix, item['error'], 'needs_review')
                continue
            size = item.get('bytes', 0)
            add('固件文件', '固件大小' + suffix, f'{size:,} 字节（{size / 1024:.2f} KiB）')
            add('固件文件', '文件SHA256校验码' + suffix, item.get('sha256', '未查询到'))
        present = firmware.get('scriptPresent', False)
        add('固件文件', 'HPM刷写脚本', str(FLASHER) if present else '未找到 ' + str(FLASHER),
            'pass' if present else 'needs_review')
        add('固件文件', '刷写脚本来源', firmware.get('scriptOwner') or firmware.get('ownerError') or '未查询到所属软件包',
            'pass' if firmware.get('scriptOwner') else 'needs_review')
    for key, group in [('udev', 'udev'), ('sources', '软件源'), ('services', '服务')]:
        data = section(key, group)
        if data is not None:
            for index, item in enumerate(data if isinstance(data, list) else [data]):
                add(group, item.get('name', item.get('path', f'明细{index + 1}')), item)
    package_data = section('packages', '软件包')
    packages = []
    if package_data:
        for item in package_data['items']:
            status = item['status'].removeprefix('缓存参考 · ') if refreshed_at else item['status']
            packages.append(dict(name=item['name'], installedVersion=item['installed'] or '未安装',
                                 latestVersion=item['latest'] or '未确认', status=status,
                                 source=item['source'], architecture=item['architecture']))
        add('软件包', '软件源索引检查', f'本次已联网刷新：{refreshed_at}' if refreshed_at
            else '缓存参考；点击联网检查软件包更新可刷新源版本', 'pass' if refreshed_at else 'needs_review')
    else:
        packages = [dict(name=name, installedVersion='未确认', latestVersion='未确认',
                         status='查询失败', source='apt.roboparty.com', architecture='') for name in FOCUS_PACKAGES]
    return dict(schemaVersion=1, format='board-snapshot-v1', identity=raw['identity'], collectedAt=stamp,
                parameters=parameters, softwarePackages=packages, can=can_check,
                canConfiguration=can_configuration,
                packageError=raw['packages']['error'], raw=raw)


def execute_action(action, emit):
    if action not in ('packages-check', 'apt-upgrade', 'hpm-flash', 'can-check', 'pipeline'):
        raise ValueError('Unknown board action')
    refreshed = None
    detail = {}
    if action != 'can-check':
        detail['logCleanup'] = preflight(emit)
    if action == 'packages-check':
        emit('apt-update', '刷新 apt.roboparty.com 索引，不安装包')
        refreshed = refresh_target_indexes(board_snapshot.execute, os.geteuid())
    if action in ('apt-upgrade', 'pipeline'):
        refreshed = upgrade(emit)
    if action in ('hpm-flash', 'pipeline'):
        detail.update(install_and_flash(emit))
        refreshed = detail['refreshedAt']
    emit('snapshot', '读取板卡参数和五个重点包版本')
    data = snapshot(refreshed)
    if data['packageError'] and action != 'can-check':
        raise RuntimeError('软件包版本查询失败：' + data['packageError'])
    outcome = 'pass'
    if action == 'can-check':
        outcome = (data['can'] or {}).get('outcome', 'error')
    if action in ('apt-upgrade', 'pipeline'):
        if any(row['status'] != '已是源最新版本' for row in data['softwarePackages']):
            outcome = 'needs_review'
    can_warning = action in ('can-check', 'hpm-flash', 'pipeline') and (
        data.get('canConfiguration') or {}).get('outcome') != 'pass'
    if can_warning and outcome == 'pass':
        outcome = 'needs_review'
    return dict(outcome=outcome, summary='板端任务完成' if outcome == 'pass'
                else '任务已执行，HPM CAN 配置异常或未确认，请查看 CAN 明细' if can_warning
                else '任务已执行，部分版本或设备未达到期望，请查看明细', snapshot=data, details=detail)
