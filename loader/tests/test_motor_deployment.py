"""Offline checks only: never invoke a real CAN command."""

from contextlib import contextmanager, nullcontext, redirect_stdout, redirect_stderr
import io
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
PLUGIN = runpy.run_path(str(ROOT / 'plugins' / '025-lro_motor_parameters.py'))
DEPLOY = runpy.run_path(str(PLUGIN['SCRIPT']))
from loader import plugin_executor as executor
from loader.plugin_catalog import load_plugins


def robot():
    return {'motors': {
        'motor_interface': ['can0', 'can1', 'can2', 'can3', 'can4'],
        'motor_interface_type': ['canfd'] * 5,
        'motor_num': [2, 1, 1, 1, 1],
        'motor_type': ['LRO'] * 5,
        'motor_id': [1, 2, 1, 1, 1, 1],
        'motor_model': [0, 1, 2, 3, 1, 1],
    }}


class MotorDeploymentTests(unittest.TestCase):
    def probe(self, config, code=0, check=None, which=None, lock=nullcontext):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / 'robot.yaml'
            path.write_text(yaml.safe_dump(config), encoding='utf-8')
            seen = {}

            def run(command, **kwargs):
                seen['command'] = command
                snapshot = Path(command[command.index('--config') + 1])
                self.assertEqual(yaml.safe_load(snapshot.read_text(encoding='utf-8')), config)
                kwargs['stdout'].write('Interface can0: OK\nInterface can3: ' +
                                       ('FAILED' if code else 'OK') + '\n')
                return subprocess.CompletedProcess(command, code)

            mock_run = Mock(side_effect=run)
            check = check or Mock()
            result = PLUGIN['probe'](path, journal_dir=root / 'logs', run=mock_run,
                                     check=check, which=which or (lambda name: name),
                                     is_root=lambda: True, lock=lock, board='robopi2')
            if mock_run.called and 'log' in result['details']:
                self.assertTrue(Path(result['details']['log']).is_file())
            return result, mock_run, check, seen

    def test_all_configured_motors_on_four_buses_and_no_can4(self):
        result, run, check, seen = self.probe(robot())
        self.assertEqual(result['status'], 'pass')
        self.assertEqual(result['value'], 5)
        self.assertTrue(result['details']['power_cycle_required'])
        self.assertEqual(result['details']['can_timeout_ms'], 0)
        command = seen['command']
        self.assertIn('--parallel', command)
        self.assertEqual(command[command.index('--mode') + 1], 'write')
        self.assertEqual(command[-8:], ['--interface', 'can0', '--interface', 'can1',
                                       '--interface', 'can2', '--interface', 'can3'])
        self.assertEqual(check.call_count, 4)
        run.assert_called_once()

    def test_failed_interface_is_not_reported_as_success(self):
        result, _, _, _ = self.probe(robot(), code=1)
        self.assertEqual(result['status'], 'fail')
        self.assertFalse(result['details']['power_cycle_required'])
        self.assertIn('FAILED', result['details']['output_tail'])
        self.assertEqual(result['details']['exit_code'], 1)

    def test_control_lock_busy_prevents_writes(self):
        @contextmanager
        def busy():
            raise RuntimeError('ROBOT_BUSY')
            yield

        result, run, _, _ = self.probe(robot(), lock=busy)
        self.assertEqual(result['status'], 'error')
        self.assertIn('ROBOT_BUSY', result['message'])
        run.assert_not_called()

    def test_control_lock_wraps_deployment_and_releases_after_failure(self):
        events = []

        @contextmanager
        def locked():
            events.append('acquired')
            try:
                yield
            finally:
                events.append('released')

        result, run, _, _ = self.probe(robot(), code=1, lock=locked)
        self.assertEqual(result['status'], 'fail')
        run.assert_called_once()
        self.assertEqual(events, ['acquired', 'released'])

    def test_generic_host_rejects_rp025_before_execution(self):
        with patch.object(executor, 'generic_host', return_value=True), \
                patch.object(executor, 'run_plugin') as run:
            with self.assertRaisesRegex(ValueError, 'not supported'):
                executor.run_suite(load_plugins(), selected='RP025')
            run.assert_not_called()

    def test_invalid_targets_stop_before_writes(self):
        for field, values in (
            ('motor_id', [1, 1, 1, 1, 1, 1]),
            ('motor_id', [2047, 2, 1, 1, 1, 1]),
            ('motor_model', [99, 1, 2, 3, 1, 1]),
            ('motor_num', [-1, 4, 1, 1, 1]),
            ('motor_interface_type', ['canfd']),
            ('motor_type', ['OTHER'] * 5),
        ):
            with self.subTest(field=field, values=values):
                config = robot()
                config['motors'][field] = values
                result, run, _, _ = self.probe(config)
                self.assertEqual(result['status'], 'error')
                run.assert_not_called()

    def test_missing_tools_or_down_interface_stop_before_writes(self):
        result, run, _, _ = self.probe(robot(), which=lambda _: None)
        self.assertEqual(result['status'], 'error')
        run.assert_not_called()
        result, run, _, _ = self.probe(robot(), check=Mock(side_effect=ValueError('can3 DOWN')))
        self.assertEqual(result['status'], 'error')
        run.assert_not_called()

    def test_motion_team_protocol_vectors(self):
        self.assertEqual(DEPLOY['build_config_frame']('pos', -1250, 1250, True), 'C007FB1E04E2')
        self.assertEqual(DEPLOY['build_config_frame']('kt', 0, 174, False), 'C00400AE')
        self.assertEqual(DEPLOY['build_config_frame']('can_timeout', 0, 0, False), 'C00B0000')
        self.assertEqual(DEPLOY['build_pn_write_frame'](0xE71, 75), 'C0100E710000004B')
        self.assertEqual(DEPLOY['cansend_frame_arg'](1, 'C005000001F4', 'canfd'),
                         '001##1C005000001F4')

    def test_parallel_builds_one_worker_per_interface(self):
        args = DEPLOY['parse_args'](['--mode', 'write', '--config', '/robot.yaml', '--parallel'])
        targets = PLUGIN['select_targets'](robot(), DEPLOY)
        commands = DEPLOY['parallel_commands'](args, targets)
        self.assertEqual(list(commands), ['can0', 'can1', 'can2', 'can3'])
        for interface, command in commands.items():
            self.assertEqual(command[-2:], ['--interface', interface])
            self.assertNotIn('--parallel', command)

    def test_parallel_collects_failure_and_finishes_other_interfaces(self):
        commands = {'can0': [sys.executable, '-c', 'print("done")'],
                    'can1': [sys.executable, '-c', 'import sys; sys.exit(1)'],
                    'can2': [sys.executable, '-c', 'print("done")']}
        output = io.StringIO()
        with redirect_stdout(output):
            code = DEPLOY['run_parallel'](commands)
        self.assertEqual(code, 1)
        self.assertIn('Interface can2: OK', output.getvalue())
        self.assertIn('Interface can1: FAILED', output.getvalue())

    def test_executor_accepts_config_and_rejects_old_single_motor_options(self):
        with patch.object(executor, 'run_suite', return_value={}) as run, \
                redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            code = executor.main(['--run', 'RP025', '--motor-config', '/robot.yaml', '--no-save'])
            self.assertEqual(code, 0)
            self.assertEqual(run.call_args.kwargs['params'],
                             {'robot': '自定义路径', 'config': '/robot.yaml'})
            run.reset_mock()
            with self.assertRaises(SystemExit):
                executor.main(['--run', 'RP025', '--motor-config', '/robot.yaml', '--motor-id', '1'])
            run.assert_not_called()

    def test_plugin_is_not_eligible_for_run_all(self):
        plugins = load_plugins()
        motor = next(p for p in plugins if p['id'] == 'RP025')
        self.assertFalse(motor['batch'])
        self.assertEqual(motor['safety'], 'active')
        self.assertEqual([f['name'] for f in motor['inputs']], ['robot', 'config'])
        with patch.object(executor, 'run_plugin', side_effect=AssertionError('must not execute')):
            report = executor.run_suite([dict(motor, depends_on=[])], selected='all')
        self.assertEqual(report['results'], [])

    def test_presets_resolve_only_the_selected_robot(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for label, folder in PLUGIN['ROBOT_FOLDERS'].items():
                path = root / folder / 'robot.yaml'
                path.parent.mkdir()
                path.write_text('motors: {}', encoding='utf-8')
                self.assertEqual(PLUGIN['resolve_config'](label, roots=[root, root]),
                                 path.resolve())
                self.assertEqual(executor.validate_parameters(PLUGIN['PLUGIN'], {'robot': label}),
                                 {'robot': label})
            with self.assertRaises(ValueError):
                PLUGIN['resolve_config']('24 自由度 (rp1_24dof)', '/override.yaml', [root])

    def test_missing_or_ambiguous_preset_never_launches_script(self):
        with tempfile.TemporaryDirectory() as temp:
            roots = [Path(temp) / 'one', Path(temp) / 'two']
            run = Mock()
            result = PLUGIN['probe'](robot='24 自由度 (rp1_24dof)', roots=roots,
                                     is_root=lambda: True, run=run, board='robopi2')
            self.assertEqual(result['status'], 'error')
            self.assertIn('未找到', result['message'])
            for root in roots:
                path = root / 'rp1_24dof' / 'robot.yaml'
                path.parent.mkdir(parents=True)
                path.write_text('motors: {}', encoding='utf-8')
            result = PLUGIN['probe'](robot='24 自由度 (rp1_24dof)', roots=roots,
                                     is_root=lambda: True, run=run, board='robopi2')
            self.assertEqual(result['status'], 'error')
            self.assertIn('多份配置', result['message'])
            run.assert_not_called()

    def test_tui_presets_skip_typing_and_custom_path_still_prompts(self):
        frontend = runpy.run_path(str(ROOT / 'TUI' / 'plugin_test_menu.py'))
        motor = dict(PLUGIN['PLUGIN'], available=True)
        for selection in [*PLUGIN['ROBOT_FOLDERS'], '自定义路径', None]:
            with self.subTest(selection=selection):
                ui = Mock()
                ui.menu.return_value = selection
                ui.input.return_value = '/custom/robot.yaml'
                ui.yes.return_value = True
                command = Mock(return_value=(0, json.dumps(
                    {'run_id': 'test', 'outcome': 'pass', 'results': []}), ''))
                with patch.dict(frontend['plugin_tests_menu'].__globals__,
                                {'load_catalog': lambda *a, **kw: [motor], 'command': command}), \
                        patch.object(os, 'geteuid', return_value=0, create=True):
                    frontend['plugin_tests_menu'](ui, selected='RP025', develop=True)
                if selection is None:
                    command.assert_not_called()
                    continue
                args = command.call_args.args[0]
                params = json.loads(args[args.index('--params') + 1])
                self.assertEqual(params['robot'], selection)
                if selection == '自定义路径':
                    ui.input.assert_called_once()
                    self.assertEqual(params['config'], '/custom/robot.yaml')
                else:
                    ui.input.assert_not_called()
                    self.assertNotIn('config', params)


if __name__ == '__main__':
    unittest.main()
