from contextlib import contextmanager
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from loader import board_backend as backend
from loader import board_packages as packages
from loader import task_runtime as api


class BackendTests(unittest.TestCase):
    @contextmanager
    def unlocked(self, *_):
        yield

    def usb(self, path='usb-hpm'):
        return dict(vid='1209', pid='2323', path=path)

    def links(self, count=4, path='usb-hpm'):
        return [dict(ifname='can' + str(index), driver='gs_usb', usbParent=self.usb(path)) for index in range(count)]

    def test_four_can_must_belong_to_the_hpm_not_just_have_the_right_names(self):
        self.assertEqual('pass', backend.check_can([self.usb()], self.links())['outcome'])
        self.assertEqual('fail', backend.check_can([self.usb()], self.links(3))['outcome'])
        self.assertEqual('fail', backend.check_can([self.usb()], self.links(path='other-usb'))['outcome'])
        self.assertEqual('fail', backend.check_can([self.usb(), self.usb()], self.links())['outcome'])

    def test_usb_needs_two_consecutive_normal_enumerations_and_no_boot_device(self):
        samples = iter([[], [self.usb()], [], [self.usb()], [self.usb()]])
        observed = backend.wait_usb(lambda: next(samples), lambda _: None, 5)
        self.assertEqual(observed, [self.usb()])
        with self.assertRaises(RuntimeError):
            backend.wait_usb(lambda: [self.usb(), dict(vid='34b7', pid='0008')], lambda _: None, 3)

    def test_selects_only_firmware_owned_by_the_installed_package(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            correct = root / 'ethercanfd_v1.1.0-20260914.bin'
            stale = root / 'ethercanfd_old.bin'
            correct.write_bytes(b'packaged')
            stale.write_bytes(b'stale')
            self.assertEqual(correct, backend.select_firmware([str(correct)], root))
            with self.assertRaises(RuntimeError):
                backend.select_firmware([str(correct), str(stale)], root)
            with self.assertRaises(RuntimeError):
                backend.select_firmware([str(root.parent / 'ethercanfd_outside.bin')], root)

    def test_upgrade_stops_on_source_failure_without_installing(self):
        commands = []
        def run(argv, timeout):
            commands.append(argv)
            return subprocess.CompletedProcess(argv, 100, '', 'DNS failed')
        with self.assertRaisesRegex(RuntimeError, 'DNS failed'):
            backend.upgrade(lambda *_: None, run)
        self.assertEqual(1, len(commands))
        self.assertEqual('update', commands[0][-1])

    def test_package_preflight_checks_space_after_guarded_legacy_cleanup(self):
        from types import SimpleNamespace
        events = []
        def disk(path):
            if path == '/var/log':
                self.assertTrue(events)
            return SimpleNamespace(f_bavail=1024 * 1024, f_frsize=4096)
        def clean(emit):
            events.append('cleanup')
            emit('log-cleanup', 'done')
            return {'removed': ['old-session'], 'archive': 'archive.tar.gz'}
        with patch.object(backend.os, 'geteuid', return_value=0, create=True), \
                patch.object(backend.os, 'statvfs', side_effect=disk, create=True), \
                patch.object(backend.board_snapshot, 'text', return_value='ROBOPI2'), \
                patch.object(backend.board_log_cleanup, 'cleanup', side_effect=clean):
            result = backend.preflight(lambda *_: None)
        self.assertEqual(['old-session'], result['removed'])

    def test_package_check_is_exactly_five_and_obeys_debian_comparison(self):
        class File:
            site = 'apt.roboparty.com'
            archive = 'common'
            component = 'main'
        class Version:
            arch = 'all'
            ver_str = '1:1.0-2'
            file_list = [(File(), 0)]
        class Package:
            name = 'robopi-tui'
            version_list = [Version()]
            current_ver = Version()
            current_state = 6
            selected_state = 1
        class Cache:
            packages = [Package()]
        comparisons = []
        def compare(left, right):
            comparisons.append((left, right))
            return 0
        rows = packages.source_versions(Cache(), compare, 'arm64', True)
        self.assertEqual(list(packages.FOCUS_PACKAGES), [row['name'] for row in rows])
        self.assertEqual('已是源最新版本', rows[-1]['status'])
        self.assertEqual('1:1.0-2', rows[-1]['installed'])
        self.assertEqual('1:1.0-2', rows[-1]['latest'])
        self.assertTrue(comparisons)
        self.assertIsNone(rows[0]['latest'])

    def test_invalid_job_ids_and_actions_do_not_launch_anything(self):
        with self.assertRaises(ValueError):
            api.identifier('../../etc')
        with self.assertRaises(ValueError):
            api.start('arbitrary-shell', 'bad')

    def test_boot_change_marks_existing_task_interrupted_never_replays_it(self):
        import uuid
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            job_id = str(uuid.uuid4())
            directory = root / job_id
            directory.mkdir()
            state = dict(apiVersion=1, jobId=job_id, action='apt-upgrade', state='running',
                         identity=dict(machineId='same-board', bootId='old-boot'))
            api.atomic_json(directory / 'state.json', state)
            with patch.object(backend.board_snapshot, 'identity', return_value=dict(machineId='same-board', bootId='new-boot')), \
                    patch.object(api, 'lock', self.unlocked):
                result = api.read_status(root, job_id)
            self.assertEqual('interrupted', result['state'])
            self.assertEqual('error', result['outcome'])
            self.assertEqual('interrupted', api.read_json(directory / 'state.json')['state'])

    def test_machine_change_rejects_old_result(self):
        import uuid
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            job_id = str(uuid.uuid4())
            directory = root / job_id
            directory.mkdir()
            api.atomic_json(directory / 'state.json', dict(apiVersion=1, jobId=job_id, state='completed',
                identity=dict(machineId='board-one', bootId='boot')))
            with patch.object(backend.board_snapshot, 'identity', return_value=dict(machineId='board-two', bootId='boot')):
                with self.assertRaisesRegex(RuntimeError, 'identity'):
                    api.read_status(root, job_id)

    def test_hpm_restores_monitor_on_flash_failure_and_does_not_verify_usb(self):
        with tempfile.TemporaryDirectory() as temp:
            firmware = Path(temp) / 'ethercanfd_current.bin'
            firmware.write_bytes(b'firmware')
            calls = []
            def run(argv, timeout):
                calls.append(argv)
                output = 'install ok installed' if argv[0] == 'dpkg-query' and '-W' in argv else ''
                if argv[0] == 'systemctl' and 'is-active' in argv:
                    output = 'active'
                code = 1 if argv[0] == str(backend.FLASHER) else 0
                return subprocess.CompletedProcess(argv, code, output, 'flash error' if code else '')
            with patch.object(backend, 'select_firmware', return_value=firmware), \
                    patch.object(Path, 'is_file', return_value=True), \
                    patch.object(backend.os, 'access', return_value=True), \
                    patch.object(backend, 'wait_usb') as wait:
                with self.assertRaisesRegex(RuntimeError, 'flash error'):
                    backend.install_and_flash(lambda *_: None, run, lambda: [self.usb()], sleep=lambda _: None)
            self.assertEqual(['systemctl', 'start', 'hpm-sreset.service'], calls[-1])
            self.assertTrue(any('roboparty-analyze' in c and 'install' in c for c in calls))
            wait.assert_not_called()

    def test_hpm_success_verifies_two_usb_polls_and_can_after_install(self):
        with tempfile.TemporaryDirectory() as temp:
            firmware = Path(temp) / 'ethercanfd_current.bin'
            firmware.write_bytes(b'firmware')
            calls, usb_reads = [], []
            def run(argv, timeout):
                calls.append(argv)
                output = 'install ok installed' if argv[0] == 'dpkg-query' and '-W' in argv else ''
                code = 3 if argv[:2] == ['systemctl', 'is-active'] else 0
                return subprocess.CompletedProcess(argv, code, output, '')
            def usb():
                usb_reads.append(True)
                return [self.usb()]
            with patch.object(backend, 'select_firmware', return_value=firmware), \
                    patch.object(Path, 'is_file', return_value=True), \
                    patch.object(backend.os, 'access', return_value=True):
                result = backend.install_and_flash(lambda *_: None, run, usb, self.links, lambda _: None)
            self.assertTrue(result['usbVerified'])
            self.assertEqual('pass', result['can']['outcome'])
            self.assertEqual(3, len(usb_reads))  # target admission plus two post-flash observations
            self.assertFalse(any(c[:2] == ['systemctl', 'start'] for c in calls))

    def test_pipeline_stops_before_flash_on_upgrade_error(self):
        with patch.object(backend, 'preflight'), \
                patch.object(backend, 'upgrade', side_effect=RuntimeError('source failure')), \
                patch.object(backend, 'install_and_flash') as flash:
            with self.assertRaisesRegex(RuntimeError, 'source failure'):
                backend.execute_action('pipeline', lambda *_: None)
            flash.assert_not_called()

    def test_hpm_restores_monitor_even_when_stopping_it_reports_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            firmware = Path(temp) / 'ethercanfd_current.bin'
            firmware.write_bytes(b'firmware')
            calls = []
            def run(argv, timeout):
                calls.append(argv)
                output = 'install ok installed' if argv[:2] == ['dpkg-query', '-W'] else ''
                if argv[:2] == ['systemctl', 'is-active']:
                    output = 'active'
                code = 1 if argv[:2] == ['systemctl', 'stop'] else 0
                return subprocess.CompletedProcess(argv, code, output, 'stop failure' if code else '')
            with patch.object(backend, 'select_firmware', return_value=firmware), \
                    patch.object(Path, 'is_file', return_value=True), \
                    patch.object(backend.os, 'access', return_value=True):
                with self.assertRaisesRegex(RuntimeError, 'stop failure'):
                    backend.install_and_flash(lambda *_: None, run, lambda: [self.usb()])
            self.assertEqual(['systemctl', 'start', 'hpm-sreset.service'], calls[-1])
            self.assertFalse(any(c[0] == str(backend.FLASHER) for c in calls))

    def test_snapshot_preserves_all_five_package_versions(self):
        raw = dict(identity=dict(hostname='board', uid=0), endIdentity=dict(hostname='board', uid=0), collectedAt='now')
        for key in ('system', 'resources', 'network', 'usb', 'can', 'udev', 'firmware', 'sources', 'services'):
            raw[key] = dict(data=[], error=None)
        raw['packages'] = dict(data=dict(items=[dict(name=name, installed='1.0', latest='1.1',
            status='缓存参考 · 可更新', source=packages.PACKAGE_HOST, architecture='arm64')
            for name in packages.FOCUS_PACKAGES]), error=None)
        with patch.object(backend.board_snapshot, 'collect', return_value=raw):
            data = backend.snapshot('refreshed-now')
        self.assertEqual(5, len(data['softwarePackages']))
        self.assertTrue(all(r['installedVersion'] == '1.0' and r['status'] == '可更新' for r in data['softwarePackages']))

    def test_request_deduplication_freezes_runtime_and_rejects_overlapping_job(self):
        import uuid
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'jobs'
            root.mkdir()
            sources = Path(temp) / 'source'
            for name in ('loader', 'plugins'):
                (sources / name).mkdir(parents=True)
                (sources / name / 'sample.py').write_text('original')
            job_id = str(uuid.uuid4())
            calls = []
            def launch(argv, **_):
                calls.append(argv)
                return subprocess.CompletedProcess(argv, 0, '', '')
            identity = dict(machineId='board', bootId='boot', uid=0)
            with patch.object(api, 'ROOT', sources), patch.object(api, 'lock', self.unlocked), \
                    patch.object(api.os, 'geteuid', return_value=0, create=True), \
                    patch.object(backend.board_snapshot, 'identity', return_value=identity):
                self.assertEqual('queued', api.start('apt-upgrade', job_id, root, launch)['state'])
                (sources / 'loader' / 'sample.py').write_text('new-after-self-update')
                self.assertEqual(job_id, api.start('apt-upgrade', job_id, root, launch)['jobId'])
                with self.assertRaisesRegex(RuntimeError, '正在运行'):
                    api.start('hpm-flash', str(uuid.uuid4()), root, launch)
            self.assertEqual(1, len(calls))
            self.assertEqual('original', (root / job_id / 'runtime/loader/sample.py').read_text())

    def test_stale_reader_cannot_overwrite_completed_worker_state(self):
        import uuid
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            job_id = str(uuid.uuid4())
            path = root / job_id / 'state.json'
            state = dict(apiVersion=1, jobId=job_id, state='running',
                         identity=dict(machineId='board', bootId='old'))
            api.atomic_json(path, state)
            @contextmanager
            def worker_finished(*_):
                api.atomic_json(path, {**state, 'state': 'completed', 'outcome': 'pass'})
                yield
            with patch.object(api, 'lock', worker_finished), \
                    patch.object(backend.board_snapshot, 'identity', return_value=dict(machineId='board', bootId='new')):
                observed = api.read_status(root, job_id)
            self.assertEqual('completed', observed['state'])


if __name__ == '__main__':
    unittest.main()
