#!/usr/bin/env python3
"""TUI-owned read-only board snapshot. No GPIO, service changes or CAN frames."""
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from loader.board_packages import query_packages

REQUEST = {'board': {'normalUsb': {'vid': '1209', 'pid': '2323'},
                     'bootUsb': {'vid': '34b7', 'pid': '0008'},
                     'resetServices': ['hpm-sreset.service']}}
COMMANDS = []


def text(path, optional=False):
    try:
        with open(path, 'r') as stream:
            return stream.read(131072).strip().strip('\x00')
    except FileNotFoundError:
        if optional:
            return None
        raise


def execute(args, timeout=4):
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                                env=dict(os.environ, LC_ALL='C.UTF-8', LANG='C.UTF-8'))
        record = dict(command=args, exitCode=result.returncode, stdout=result.stdout[:131072], stderr=result.stderr[:8192])
    except subprocess.TimeoutExpired:
        record = dict(command=args, exitCode=-1, stdout='', stderr='Query timed out')
    except FileNotFoundError:
        record = dict(command=args, exitCode=127, stdout='', stderr='Tool not installed')
    COMMANDS.append(record)
    return record


def checked(args, timeout=4):
    result = execute(args, timeout)
    if result['exitCode'] != 0:
        raise RuntimeError('%s exit=%s: %s' % (args[0], result['exitCode'], result['stderr']))
    return result['stdout'].strip()


def section(action):
    try:
        return dict(data=action(), error=None)
    except Exception as error:
        return dict(data=None, error=str(error))


def identity():
    return dict(machineId=text('/etc/machine-id'), bootId=text('/proc/sys/kernel/random/boot_id'),
                hostname=text('/etc/hostname'), uid=os.geteuid())


def system():
    return dict(os=text('/etc/os-release'), kernel=checked(['uname', '-srmo']),
                model=text('/proc/device-tree/model', True) or 'Unsupported',
                uptime=text('/proc/uptime'), architecture=checked(['dpkg', '--print-architecture']))


def resources():
    memory = text('/proc/meminfo')
    disk = os.statvfs('/')
    temperatures = []
    for zone in sorted(Path('/sys/class/thermal').glob('thermal_zone*')):
        temperatures.append(dict(name=text(zone / 'type'), millidegrees=text(zone / 'temp')))
    mounts = {}
    for mount in ('/', '/var/log', '/tmp', '/var/lib'):
        info = os.statvfs(mount)
        mounts[mount] = dict(availableBytes=info.f_bavail * info.f_frsize,
                            totalBytes=info.f_blocks * info.f_frsize)
    return dict(memory=memory, rootAvailableBytes=disk.f_bavail * disk.f_frsize,
                temperatures=temperatures, disks=mounts, load=text('/proc/loadavg'))


def redact(value):
    value = re.sub(r'(https?://)[^/\s@]+@', r'\1***@', value)
    value = re.sub(r'(?i)(password|token|secret)([=: ]+)[^\s&]+', r'\1\2***', value)
    return value


def network():
    return dict(addresses=json.loads(checked(['ip', '-j', 'address'])),
                routes=json.loads(checked(['ip', '-j', 'route'])),
                dns=text('/etc/resolv.conf'),
                dnsLinks=execute(['resolvectl', 'dns'])['stdout'].strip(),
                usbLink=text('/sys/class/net/usb0/operstate', True) or 'USB network absent')


def usb_info(path):
    return dict(path=str(path.resolve()), vid=text(path / 'idVendor'), pid=text(path / 'idProduct'),
                serial=text(path / 'serial', True) or '', product=text(path / 'product', True) or '',
                bcdDevice=text(path / 'bcdDevice'), bcdUSB=text(path / 'version', True) or '')


def usb():
    profile = REQUEST['board']
    ids = [(profile['normalUsb']['vid'].lower(), profile['normalUsb']['pid'].lower()),
           (profile['bootUsb']['vid'].lower(), profile['bootUsb']['pid'].lower())]
    devices = []
    for path in sorted(Path('/sys/bus/usb/devices').iterdir()):
        if not (path / 'idVendor').exists():
            continue
        info = usb_info(path)
        if (info['vid'], info['pid']) in ids:
            devices.append(info)
    return devices


def can():
    links = json.loads(checked(['ip', '-j', '-details', '-statistics', 'link', 'show', 'type', 'can']))
    for link in links:
        device = (Path('/sys/class/net') / link['ifname'] / 'device').resolve()
        driver = device / 'driver'
        link['driver'] = driver.resolve().name if driver.exists() else ''
        link['usbParent'] = None
        for parent in (device, *device.parents):
            if (parent / 'idVendor').exists() and (parent / 'idProduct').exists():
                link['usbParent'] = usb_info(parent)
                break
    return links


def rules():
    found = []
    # /etc rules override vendor rules of the same name.
    paths = {}
    for folder in ('/usr/lib/udev/rules.d', '/run/udev/rules.d', '/etc/udev/rules.d'):
        for path in Path(folder).glob('*.rules'):
            paths[path.name] = path
    for path in paths.values():
        value = text(path)
        if '1209' in value.lower() or 'hpm' in path.name.lower():
            found.append(dict(path=str(path), content=value))
    return found


def firmware():
    files = []
    for path in sorted(Path('/opt/roboparty/lib/firmware').glob('ethercanfd*.bin')):
        if path.is_symlink() or path.stat().st_size > 64 * 1024 * 1024:
            files.append(dict(path=str(path), error='Symlink or firmware exceeds 64 MiB'))
            continue
        files.append(dict(path=str(path), bytes=path.stat().st_size,
                          sha256=hashlib.sha256(path.read_bytes()).hexdigest(), error=None))
    owner = execute(['dpkg-query', '-S', '/opt/roboparty/bin/flash_hpm.sh'])
    return dict(files=files, scriptOwner=owner['stdout'].strip(), ownerError=owner['stderr'].strip(),
                scriptPresent=Path('/opt/roboparty/bin/flash_hpm.sh').is_file())


def packages():
    return query_packages(execute, REQUEST.get('refreshPackages', False))


def sources():
    paths = [Path('/etc/apt/sources.list')]
    for suffix in ('*.list', '*.sources'):
        paths.extend(sorted(Path('/etc/apt/sources.list.d').glob(suffix)))
    return [dict(path=str(path), content=redact(text(path))) for path in paths if path.is_file()]


def services():
    names = list(dict.fromkeys(REQUEST['board']['resetServices'] + ['usbdevice.service']))
    if any(not re.fullmatch(r'[a-zA-Z0-9_.@-]+\.service', name) for name in names):
        raise ValueError('Invalid service name')
    return [dict(name=name, result=execute(['systemctl', 'show', name, '-p', 'LoadState', '-p', 'ActiveState', '-p', 'UnitFileState'])) for name in names]


def collect():
    COMMANDS.clear()
    start = identity()
    result = dict(schemaVersion=1, identity=start,
                  collectedAt=datetime.datetime.now(datetime.timezone.utc).isoformat(), commands=COMMANDS)
    for name, action in [('system', system), ('resources', resources), ('network', network), ('usb', usb),
                         ('can', can), ('udev', rules), ('firmware', firmware), ('packages', packages),
                         ('sources', sources), ('services', services)]:
        result[name] = section(action)
    result['endIdentity'] = identity()
    return result
