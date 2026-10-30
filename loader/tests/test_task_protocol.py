import unittest
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import uuid
from unittest.mock import patch

from loader import task_protocol as rpc
from loader import task_catalog as catalog
from loader import studio_api as legacy


class ProtocolTests(unittest.TestCase):
    def call(self, method, params=None, **extra):
        return rpc.dispatch(dict(jsonrpc='2.0', id='request-1', method=method,
                                 params=params or {}, **extra))

    def test_catalog_and_description_do_not_depend_on_package_version(self):
        with patch.object(rpc.runtime, 'capabilities', return_value=dict(
                identity={'machineId': 'board'}, tuiVersion='2099.7', writable=True)):
            response = self.call('tasks.list', ignoredFutureField=True)
        self.assertEqual('2.0', response['jsonrpc'])
        self.assertEqual('2099.7', response['result']['tuiVersion'])
        self.assertEqual(set(catalog.TASKS), {t['id'] for t in response['result']['tasks']})
        self.assertEqual('can-check', self.call('tasks.describe', {'taskId': 'can-check'})['result']['id'])

    def test_new_registered_task_uses_existing_protocol_without_protocol_edits(self):
        task = catalog.Task('future-check', 'New check', lambda emit: {'outcome': 'pass'})
        with patch.dict(catalog.TASKS, {'future-check': task}), \
                patch.object(rpc.runtime, 'start', return_value={'jobId': 'existing'}) as start:
            self.assertEqual('future-check', self.call('tasks.describe', {'taskId': 'future-check'})['result']['id'])
            result = self.call('jobs.start', {'taskId': 'future-check', 'requestId': 'uuid'})
        self.assertEqual('existing', result['result']['jobId'])
        start.assert_called_once_with('future-check', 'uuid')

    def test_unknown_task_is_a_local_error_not_a_protocol_failure(self):
        result = self.call('tasks.describe', {'taskId': 'not-installed'})
        self.assertEqual('UNSUPPORTED_TASK', result['error']['data']['code'])
        self.assertIn('result', self.call('tasks.describe', {'taskId': 'can-check'}))

    def test_invalid_parameters_never_launch_a_job(self):
        with patch.object(rpc.runtime, 'start') as start:
            for params in ({'taskId': 'can-check'}, {'taskId': 'can-check', 'requestId': 'x', 'arguments': {'shell': 'bad'}}):
                self.assertEqual(-32602, self.call('jobs.start', params)['error']['code'])
        start.assert_not_called()

    def test_unknown_method_and_invalid_request_use_standard_rpc_errors(self):
        self.assertEqual(-32601, self.call('unknown')['error']['code'])
        self.assertEqual(-32600, rpc.dispatch([])['error']['code'])
        self.assertEqual(-32600, rpc.dispatch({'jsonrpc': '1.0', 'id': 'x', 'method': 'tasks.list'})['error']['code'])

    def test_existing_job_remains_readable_after_api_or_catalog_changes(self):
        job_id = str(uuid.uuid4())
        identity = dict(machineId='same-board', bootId='boot')
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            state = dict(apiVersion=1, jobId=job_id, action='removed-later', state='completed', identity=identity)
            rpc.runtime.atomic_json(root / job_id / 'state.json', state)
            with patch.object(rpc.runtime, 'API_VERSION', 99), \
                    patch.object(rpc.runtime.backend.board_snapshot, 'identity', return_value=identity):
                self.assertEqual('completed', rpc.runtime.read_status(root, job_id)['state'])
                self.assertEqual(job_id, rpc.runtime.list_jobs(root=root)[0]['jobId'])

    def test_worker_executes_new_registered_task_without_the_builtin_action_switch(self):
        from contextlib import contextmanager
        @contextmanager
        def unlocked(*_):
            yield
        job_id = str(uuid.uuid4())
        identity = dict(machineId='same-board', bootId='boot')
        task = catalog.Task('future-check', 'New check', lambda emit: {'outcome': 'pass', 'summary': 'new task ran'})
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            rpc.runtime.atomic_json(root / job_id / 'state.json', dict(
                apiVersion=1, jobId=job_id, action='future-check', taskId='future-check',
                state='queued', sequence=0, identity=identity))
            with patch.dict(catalog.TASKS, {'future-check': task}), \
                    patch.object(rpc.runtime, 'lock', unlocked), \
                    patch.object(rpc.runtime.backend.board_snapshot, 'identity', return_value=identity):
                self.assertEqual(0, rpc.runtime.worker(job_id, root))
                state = rpc.runtime.read_status(root, job_id)
        self.assertEqual('pass', state['outcome'])
        self.assertEqual('new task ran', state['result']['summary'])

    def test_legacy_snapshot_adapter_keeps_api_v1_for_old_studio(self):
        stream = io.StringIO()
        with redirect_stdout(stream), patch.object(rpc.runtime.backend, 'snapshot', return_value={'schemaVersion': 1}):
            self.assertEqual(0, legacy.main(['snapshot']))
        self.assertEqual({'apiVersion': 1, 'snapshot': {'schemaVersion': 1}}, json.loads(stream.getvalue()))

    def test_public_cli_accepts_stdin_and_returns_a_machine_error_without_ui_dependencies(self):
        executable = Path(__file__).resolve().parents[2] / 'TUI/robopi-tui'
        request = json.dumps(dict(jsonrpc='2.0', id='x', method='tasks.describe', params={'taskId': 'can-check'}))
        result = subprocess.run([sys.executable, str(executable), 'rpc', '--protocol', '1'],
                                input=request + '\n', capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual('can-check', json.loads(result.stdout)['result']['id'])
        result = subprocess.run([sys.executable, str(executable), 'rpc', '--protocol', '1'],
                                input='bad json\n', capture_output=True, text=True)
        self.assertEqual(2, result.returncode)
        self.assertEqual(-32700, json.loads(result.stdout)['error']['code'])


if __name__ == '__main__':
    unittest.main()
