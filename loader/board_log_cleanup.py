"""Archive completed legacy analyze sessions when the small /var/log is low.

Only the old recorder roots and their ramlog mirror are candidates. Current
/home/robo/robopi-logs, system journals, package logs and configurations are
outside this policy. Active, incomplete or changing sessions are retained.
"""
import json
import os
from pathlib import Path
import re
import shutil
import stat
import tarfile
import tempfile
import time
import uuid

LEGACY_ROOTS = (Path('/var/log/robopi'), Path('/var/log.hdd/robopi'))
ARCHIVE_ROOT = Path('/var/lib/robopi-tui/legacy-log-archives')
TARGET_FREE = 8 * 1024 * 1024


def available():
    return shutil.disk_usage('/var/log').free


def open_paths():
    """Inspect open descriptors/cwd without stopping or restarting recorders."""
    observed = set()
    for process in Path('/proc').iterdir():
        if not process.name.isdecimal():
            continue
        try:
            paths = list((process / 'fd').iterdir()) + [process / 'cwd']
            for path in paths:
                try:
                    target = os.readlink(path).removesuffix(' (deleted)')
                    if target.startswith('/'):
                        observed.add(Path(target))
                except FileNotFoundError:
                    pass  # process/descriptor closed during the scan
        except (FileNotFoundError, ProcessLookupError):
            pass
    return observed


def signature(directory):
    items = {}
    for path in [directory, *directory.rglob('*')]:
        info = path.lstat()
        if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
            raise ValueError('Session contains symlink or special file')
        items[str(path.relative_to(directory))] = (info.st_ino, info.st_size, info.st_mtime_ns, info.st_mode)
    return items


def ended(directory, stamp):
    manifest = directory / 'manifest.json'
    if not manifest.is_file() or manifest.is_symlink() or manifest.stat().st_size > 65536:
        return False
    value = manifest.read_text(encoding='utf-8', errors='replace')
    # Old 1.0.3 appended a closing record after an already closed JSON object.
    # Recognize only its explicit recorder identity and start/end markers.
    try:
        data = json.loads(value)
        start, finish = float(data['started_at_unix']), float(data['ended_at_unix'])
        service = data['capture_service']
    except (ValueError, KeyError, TypeError):
        start_match = re.search(r'^\s*"started_at_unix"\s*:\s*([0-9]+(?:\.[0-9]+)?)', value, re.M)
        end_match = re.search(r'^\s*,?\s*"ended_at_unix"\s*:\s*([0-9]+(?:\.[0-9]+)?)', value, re.M)
        if not start_match or not end_match or not re.search(r'"capture_service"\s*:\s*"usbcan-capture\.service"', value):
            return False
        start, finish = float(start_match[1]), float(end_match[1])
        service = 'usbcan-capture.service'
    return service == 'usbcan-capture.service' and 0 < start <= finish < stamp - 60


def cleanup(emit, roots=LEGACY_ROOTS, archive_root=ARCHIVE_ROOT, free=available, scan=open_paths):
    before = free()
    result = dict(beforeBytes=before, afterBytes=before, removed=[], skipped=[], archive=None)
    if before >= TARGET_FREE:
        return result
    emit('log-cleanup', '日志空间不足，检查已结束的旧 analyze 采集记录及磁盘镜像')
    opened = scan()
    selected = []
    for index, root in enumerate(roots):
        root = Path(root)
        if not root.exists():
            continue
        if root.is_symlink() or root.resolve() != root:
            raise RuntimeError('旧日志根目录不是固定实际路径，未清理：' + str(root))
        for directory in sorted(root.iterdir()):
            if not re.fullmatch(r'seven-\d{8}-\d{6}', directory.name):
                continue
            reason = None
            try:
                if directory.is_symlink() or not directory.is_dir() or directory.resolve().parent != root:
                    raise ValueError('不是实际会话目录')
                if any(path == directory or directory in path.parents for path in opened):
                    raise ValueError('仍被进程使用')
                if not ended(directory, time.time()):
                    raise ValueError('没有可确认的结束记录')
                state = signature(directory)
            except (ValueError, OSError) as exc:
                reason = str(exc)
            if reason:
                result['skipped'].append(dict(path=str(directory), reason=reason))
            else:
                selected.append((index, root, directory, state))
    if not selected:
        emit('log-cleanup', '没有可安全清理的旧会话；保留全部文件')
        return result
    archive_root = Path(archive_root)
    if archive_root.is_symlink() or archive_root.resolve() != archive_root:
        raise RuntimeError('归档目录路径异常，未清理')
    if any(archive_root == root or root in archive_root.parents for root in roots):
        raise RuntimeError('归档不能位于待清理目录内')
    archive_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    total = sum(value[1] for _, _, _, state in selected for value in state.values())
    if shutil.disk_usage(archive_root).free < total + 256 * 1024 * 1024:
        raise RuntimeError('持久分区空间不足以先归档；未删除旧日志')
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=archive_root, prefix='.archive-', delete=False) as stream:
            temporary = Path(stream.name)
        with tarfile.open(temporary, 'w:gz') as archive:
            for index, _, directory, _ in selected:
                archive.add(directory, arcname=f'root-{index}/{directory.name}', recursive=True)
        # Read all archived data before removing any source file (also verifies gzip CRC).
        with tarfile.open(temporary, 'r:gz') as archive:
            for member in archive:
                if member.isfile():
                    with archive.extractfile(member) as stream:
                        while stream.read(1024 * 1024):
                            pass
            while archive.fileobj.read(1024 * 1024):
                pass  # drain through the gzip trailer and validate its checksum
        opened = scan()
        for _, root, directory, state in selected:
            if directory.resolve().parent != root or signature(directory) != state or any(
                    path == directory or directory in path.parents for path in opened):
                raise RuntimeError('归档期间会话发生变化或被打开，未删除旧日志')
        final = archive_root / ('legacy-seven-' + time.strftime('%Y%m%dT%H%M%S', time.gmtime()) + '-' + uuid.uuid4().hex[:8] + '.tar.gz')
        with temporary.open('r+b') as stream:
            os.fsync(stream.fileno())
        os.replace(temporary, final)
        result['archive'] = str(final)
        for _, root, directory, state in selected:
            # Recheck the resolved absolute target immediately before bounded removal.
            if directory.is_symlink() or directory.resolve().parent != root or signature(directory) != state:
                raise RuntimeError('会话路径或内容已改变；其余文件保留，归档已保存')
            shutil.rmtree(directory)
            result['removed'].append(str(directory))
        result['afterBytes'] = free()
        emit('log-cleanup', f"旧会话已归档：{final}；清理{len(result['removed'])}个目录；日志可用{result['afterBytes']}字节")
        return result
    finally:
        if temporary and temporary.exists():
            temporary.unlink()
