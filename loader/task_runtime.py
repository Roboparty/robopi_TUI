#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0
"""Durable TUI task execution shared by terminal, CLI and compatible clients."""
import argparse
from contextlib import contextmanager
import datetime
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from loader import board_backend as backend
from loader import task_catalog as catalog

API_VERSION = 1
JOB_ROOT = Path('/var/lib/robopi-tui/studio-jobs')
STATE_VERSION = 1
TERMINAL = ('completed', 'failed', 'interrupted')


class JobError(RuntimeError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def identifier(value):
    parsed = uuid.UUID(value)
    if str(parsed) != value:
        raise ValueError('Expected a canonical UUID')
    return value


def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix='.state-', delete=False) as stream:
            name = stream.name
            json.dump(data, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(name, 0o600)
        os.replace(name, path)
    finally:
        if name and os.path.exists(name):
            os.unlink(name)


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def ensure_private(root):
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if root.is_symlink() or root.stat().st_mode & 0o077:
        raise RuntimeError('TUI job directory must be private and not a symlink')


@contextmanager
def lock(root, name):
    import fcntl
    ensure_private(root)
    path = root / name
    if path.is_symlink():
        raise RuntimeError('Invalid lock path')
    with path.open('a') as stream:
        os.chmod(path, 0o600)
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def job_path(root, job_id):
    identifier(job_id)
    directory = root / job_id
    if directory.is_symlink():
        raise RuntimeError('Invalid job path')
    return directory


def interruption_reason(state, current):
    if state['identity']['bootId'] != current['bootId']:
        return '板端已重启；任务中断，禁止自动重跑'
    if state.get('pid'):
        try:
            os.kill(state['pid'], 0)
        except ProcessLookupError:
            return '板端任务进程已退出但未写入结果；请检查日志，禁止自动重跑'
    elif state.get('unit'):
        # A transient unit can disappear with --collect. Allow systemd time to launch it.
        age = time.time() - datetime.datetime.fromisoformat(state['startedAt']).timestamp()
        if age > 20:
            observed = backend.command(['systemctl', 'show', state['unit'],
                                        '-p', 'LoadState', '-p', 'ActiveState'], 8)
            values = dict(line.split('=', 1) for line in observed.stdout.splitlines() if '=' in line)
            if values.get('LoadState') == 'not-found' or values.get('ActiveState') in ('failed', 'inactive'):
                return '板端任务未正常启动或已退出；请检查任务日志'
    return None


def read_status(root, job_id):
    directory = job_path(root, job_id)
    state = read_json(directory / 'state.json')
    # Stored state has its own format; never compare it with a package/API version.
    # Missing stateVersion is the original API-v1 disk format, supported indefinitely.
    if state['jobId'] != job_id or state.get('stateVersion', 1) != 1:
        raise JobError('UNSUPPORTED_JOB_FORMAT', 'Job identity/state format mismatch')
    current = backend.board_snapshot.identity()
    if state['identity']['machineId'] != current['machineId']:
        raise RuntimeError('Board identity mismatch')
    if state['state'] not in TERMINAL:
        reason = interruption_reason(state, current)
        if reason:
            try:
                # Never replace a live worker's newer state based on a stale reader snapshot.
                with lock(root, '.worker.lock'):
                    state = read_json(directory / 'state.json')
                    reason = interruption_reason(state, current) if state['state'] not in TERMINAL else None
                    if reason:
                        state.update(state='interrupted', outcome='error', summary=reason, finishedAt=backend.now())
                        atomic_json(directory / 'state.json', state)
            except BlockingIOError:
                state = read_json(directory / 'state.json')
    events = []
    event_file = directory / 'events.jsonl'
    if event_file.exists():
        # Events are bounded by stage count; full command output remains in per-stage events.
        for line in event_file.read_text(encoding='utf-8', errors='replace').splitlines()[-60:]:
            try:
                events.append(json.loads(line))
            except ValueError:
                pass  # an incomplete final line may be in the middle of being appended
    state['events'] = events
    if state['state'] in TERMINAL and (directory / 'result.json').is_file():
        state['result'] = read_json(directory / 'result.json')
    return state


def active_job(root=JOB_ROOT):
    path = root / 'active.json'
    if not path.is_file():
        return None
    return read_status(root, read_json(path)['jobId'])


def capabilities():
    identity = backend.board_snapshot.identity()
    version = backend.command(['dpkg-query', '-W', '-f=${Version}', 'robopi-tui'], 8)
    return dict(apiVersion=API_VERSION, backend='robopi-tui', tuiVersion=version.stdout.strip(),
                identity=identity, actions=list(catalog.TASKS), pluginMap={'apt-upgrade': 'RP006',
                'hpm-flash': 'RP024', 'can-check': ['RP007', 'RP013']},
                focusPackages=list(backend.FOCUS_PACKAGES),
                writable=identity['uid'] == 0 and shutil.which('systemd-run') is not None)


def start(action, request_id, root=JOB_ROOT, launch=subprocess.run):
    catalog.get(action)
    identifier(request_id)
    if os.geteuid() != 0:
        raise PermissionError('Board job requires root')
    with lock(root, '.admission.lock'):
        directory = job_path(root, request_id)
        if (directory / 'state.json').is_file():
            state = read_status(root, request_id)
            if state['action'] != action:
                raise JobError('REQUEST_CONFLICT', 'requestId was already used for another action')
            return state
        active = active_job(root)
        if active and active['state'] not in TERMINAL:
            raise JobError('BUSY', '板端任务正在运行：' + active['jobId'])
        if active and active['state'] == 'interrupted':
            raise JobError('INTERRUPTED_JOB', '既有任务中断，须人工核对板端状态；不会自动启动新升级/烧录')
        directory.mkdir(mode=0o700)
        runtime = directory / 'runtime'
        runtime.mkdir(mode=0o700)
        # Freeze the TUI implementation before APT can replace this package.
        for name in ('loader', 'plugins'):
            shutil.copytree(ROOT / name, runtime / name,
                            ignore=shutil.ignore_patterns('__pycache__', '*.pyc', 'tests'))
        changelog = ROOT / 'debian' / 'changelog'
        if not changelog.is_file():
            changelog = Path('/opt/roboparty/share/doc/robopi-tui/changelog')
        if changelog.is_file():
            (runtime / 'debian').mkdir()
            shutil.copy2(changelog, runtime / 'debian' / 'changelog')
        unit = 'robopi-tui-job-' + request_id
        state = dict(apiVersion=API_VERSION, jobId=request_id, requestId=request_id,
                     stateVersion=STATE_VERSION, taskId=action,
                     action=action, state='queued', stage='queued', sequence=0,
                     summary='板端任务已创建', startedAt=backend.now(),
                     identity=backend.board_snapshot.identity(), unit=unit, pid=None)
        atomic_json(directory / 'state.json', state)
        atomic_json(root / 'active.json', {'jobId': request_id})
        try:
            result = launch(['systemd-run', '--quiet', '--collect', '--unit=' + unit,
                             '--property=Type=exec', '--property=UMask=0077',
                             sys.executable, str(runtime / 'loader' / 'task_runtime.py'),
                             'worker', request_id, '--job-root', str(root)],
                            capture_output=True, text=True, timeout=15, check=False)
            if result.returncode:
                raise RuntimeError('systemd-run 未启动任务：' + result.stderr[-2000:])
        except subprocess.TimeoutExpired:
            # The command may have reached systemd. Keep its identity; never launch again.
            return state
        except Exception as exc:
            state.update(state='failed', outcome='error', summary=str(exc), finishedAt=backend.now())
            atomic_json(directory / 'state.json', state)
            raise
        # Return the queued identity; worker may already be updating state concurrently.
        return state


def worker(job_id, root=JOB_ROOT):
    directory = job_path(root, job_id)
    with lock(root, '.worker.lock'):
        state = read_json(directory / 'state.json')
        if state['state'] != 'queued' or state['identity'] != backend.board_snapshot.identity():
            raise RuntimeError('任务已结束/中断或板端身份改变；拒绝再次执行')
        state.update(state='running', pid=os.getpid())
        atomic_json(directory / 'state.json', state)

        def emit(stage, message):
            state['sequence'] += 1
            state.update(stage=stage, summary=message[-12000:])
            event = dict(sequence=state['sequence'], time=backend.now(), stage=stage, message=message[-12000:])
            with (directory / 'events.jsonl').open('a', encoding='utf-8') as stream:
                stream.write(json.dumps(event, ensure_ascii=False) + '\n')
                stream.flush()
                os.fsync(stream.fileno())
            atomic_json(directory / 'state.json', state)

        try:
            result = catalog.get(state.get('taskId', state['action'])).execute(emit)
            atomic_json(directory / 'result.json', result)
            state.update(state='completed', outcome=result['outcome'], summary=result['summary'])
        except BaseException as exc:
            state.update(state='failed', outcome='error', summary=str(exc),
                         errorType=type(exc).__name__, errorCode='TASK_EXECUTION_FAILED')
            try:
                atomic_json(directory / 'result.json', dict(outcome='error', summary=str(exc),
                            snapshot=backend.snapshot(), details={}))
            except Exception:
                pass
        state['finishedAt'] = backend.now()
        atomic_json(directory / 'state.json', state)
    return 0


def acknowledge(job_id, root=JOB_ROOT):
    """Explicit operator acknowledgement after inspecting dpkg/USB/service state; no retry."""
    if os.geteuid() != 0:
        raise PermissionError('Acknowledgement requires root')
    with lock(root, '.admission.lock'), lock(root, '.worker.lock'):
        directory = job_path(root, job_id)
        state = read_json(directory / 'state.json')
        if state['state'] != 'interrupted':
            raise ValueError('Only an interrupted task can be acknowledged')
        if state['identity']['machineId'] != backend.board_snapshot.identity()['machineId']:
            raise RuntimeError('Board identity mismatch')
        state.update(acknowledgedAt=backend.now(), state='failed', outcome='error',
                     summary=state['summary'] + '；操作员已人工核对并解除新任务锁定，原任务未重跑')
        atomic_json(directory / 'state.json', state)
        return state


def run_for_tui(action):
    """Terminal plugins use the same durable jobs as Studio; never run another APT in-process."""
    state = start(action, str(uuid.uuid4()))
    while state['state'] not in TERMINAL:
        time.sleep(1)
        state = read_status(JOB_ROOT, state['jobId'])
    result = state.get('result', {})
    return {'status': result.get('outcome', state.get('outcome', 'error')),
            'value': None, 'unit': None, 'message': state['summary'],
            'details': {'job_id': state['jobId'], **result}}


def list_jobs(limit=20, root=JOB_ROOT):
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
        raise ValueError('limit must be between 1 and 100')
    if not root.is_dir():
        return []
    paths = []
    for path in root.iterdir():
        if not path.is_dir() or path.is_symlink() or not (path / 'state.json').is_file():
            continue
        try:
            identifier(path.name)
        except ValueError:
            continue
        paths.append(path)
    paths.sort(key=lambda p: (p / 'state.json').stat().st_mtime, reverse=True)
    return [read_status(root, path.name) for path in paths[:limit]]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('worker',))
    parser.add_argument('job_id')
    parser.add_argument('--job-root', type=Path, default=JOB_ROOT)
    args = parser.parse_args()
    return worker(args.job_id, args.job_root)


if __name__ == '__main__':
    raise SystemExit(main())
