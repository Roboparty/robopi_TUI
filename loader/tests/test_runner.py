# SPDX-License-Identifier: GPL-3.0
# Copyright (C) 2026 wentywenty

from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import runpy
import socket
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
RUNNER = runpy.run_path(str(ROOT / 'loader' / 'plugin_executor.py'))
BOARD_TEMP = runpy.run_path(str(ROOT / 'plugins' / '001-board_temperature.py'))
NETWORK = runpy.run_path(str(ROOT / 'plugins' / '003-network_interfaces.py'))
USB = runpy.run_path(str(ROOT / 'plugins' / '011-usb_devices.py'))
USB_DEVICE = runpy.run_path(str(ROOT / 'plugins' / '013-gsusb_usb_can.py'))
CAN = runpy.run_path(str(ROOT / 'plugins' / '007-can_interfaces.py'))
GPIO = runpy.run_path(str(ROOT / 'plugins' / '015-gpio_lines.py'))
HIPNUC = runpy.run_path(str(ROOT / 'plugins' / '008-hipnuc_udev_rule.py'))
BMS_PORT = runpy.run_path(str(ROOT / 'plugins' / '020-bms_port.py'))
LIGHT_BOARD = runpy.run_path(str(ROOT / 'plugins' / '016-light_board_readiness.py'))
HPM = runpy.run_path(str(ROOT / 'plugins' / '024-hpm_ethercanfd_flash.py'))
APT = runpy.run_path(str(ROOT / 'plugins' / '006-apt_update_upgrade.py'))
HUB = runpy.run_path(str(ROOT / 'plugins' / '012-usb3_hub.py'))
IMAGE = runpy.run_path(str(ROOT / 'plugins' / '002-factory_image_identity.py'))
BMS_VERSION = runpy.run_path(str(ROOT / 'plugins' / '022-bms_firmware_version.py'))
IMU_BITRATE = runpy.run_path(str(ROOT / 'plugins' / '009-hipnuc_can_bitrate.py'))
IMU_VERSION = runpy.run_path(str(ROOT / 'plugins' / '010-hipnuc_firmware_version.py'))
APT_SOURCE = runpy.run_path(str(ROOT / 'plugins' / '004-roboparty_apt_repository.py'))
PACKAGES = runpy.run_path(str(ROOT / 'plugins' / '005-installed_roboparty_packages.py'))
MOTOR_PARAMS = runpy.run_path(str(ROOT / 'plugins' / '025-lro_motor_parameters.py'))
BMS_SERVICE = runpy.run_path(str(ROOT / 'plugins' / '021-bms_service.py'))
ADDON_STATUS = runpy.run_path(str(ROOT / 'plugins' / '017-addon_readiness.py'))
ADDON_LED = runpy.run_path(str(ROOT / 'plugins' / '018-addon_led_visual.py'))
ADDON_SIG = runpy.run_path(str(ROOT / 'plugins' / '019-addon_sig_outputs.py'))
USB_STRESS = runpy.run_path(str(ROOT / 'plugins' / '014-usb_hub_reset_stress.py'))
BMS_RESTART = runpy.run_path(str(ROOT / 'plugins' / '023-bms_restart.py'))
APP = runpy.run_path(str(ROOT / 'TUI' / 'robopi-tui'))


def fixture_plugin(directory, body, requires=None, timeout=2, safety='passive',
                   lifecycle='testing'):
    metadata = {'id': 'RP001', 'station': ['robopi2', 'rdkx5', 'opi5pro'],
                'clients': ['tui'] if safety == 'active' else ['tui', 'csharp'],
                'author': 'wentywenty',
                'name': 'Fixture test',
                'documentation': {'title': 'Fixture test', 'criterion': 'Fixture criterion'},
                'version': '1.0', 'lifecycle': lifecycle,
                'safety': safety, 'requires': requires or [], 'timeout_s': timeout}
    (directory / '001-fixture.py').write_text(f'PLUGIN = {metadata!r}\n{body}')
    return RUNNER['load_plugins'](directory)


class RunnerTests(unittest.TestCase):
    def test_temperature_uses_hottest_zone_and_90c_limit(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            gpu = root / 'thermal_zone0'
            gpu.mkdir()
            (gpu / 'type').write_text('gpu-thermal')
            (gpu / 'temp').write_text('62000')
            cpu = root / 'thermal_zone1'
            cpu.mkdir()
            (cpu / 'type').write_text('soc-thermal')
            (cpu / 'temp').write_text('45250')
            result = BOARD_TEMP['measure'](root)
            self.assertEqual(result['status'], 'pass')
            self.assertEqual(result['value'], 62)
            self.assertEqual(result['details']['zone'], 'thermal_zone0')
            (gpu / 'temp').write_text('90000')
            self.assertEqual(BOARD_TEMP['measure'](root)['status'], 'fail')

    def test_missing_sensor_is_skip(self):
        with tempfile.TemporaryDirectory() as temp:
            self.assertEqual(BOARD_TEMP['measure'](Path(temp))['status'], 'skip')

    def test_installed_plugin_catalog(self):
        plugins = RUNNER['load_plugins'](ROOT / 'plugins')
        self.assertEqual(len(plugins), 55)
        self.assertEqual({plugin['safety'] for plugin in plugins},
                         {'passive', 'query', 'active'})
        self.assertEqual({tuple(plugin['station']) for plugin in plugins},
                         {('robopi2', 'rdkx5', 'opi5pro')})
        self.assertEqual({tuple(plugin['clients']) for plugin in plugins},
                         {('tui',), ('tui', 'csharp')})
        self.assertEqual({plugin['author'] for plugin in plugins}, {'wentywenty'})
        self.assertEqual({plugin['lifecycle'] for plugin in plugins}, {'testing'})
        self.assertEqual([plugin['id'] for plugin in plugins],
                         [f'RP{number:03}' for number in range(1, 56)])
        self.assertEqual(RUNNER['catalog'](plugins)['tests'][-1]['batch'], False)

    def test_declared_inputs_and_nonbatch_plugins(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            metadata = {'id': 'RP001', 'station': ['robopi2', 'rdkx5', 'opi5pro'],
                        'clients': ['tui', 'csharp'], 'author': 'wentywenty',
                        'name': 'Parameterized fixture',
                        'documentation': {'title': 'Parameterized fixture',
                                          'criterion': 'Fixture criterion'},
                        'version': '1.0',
                        'lifecycle': 'testing', 'safety': 'query', 'kind': 'test',
                        'category': 'Serial', 'batch': False,
                        'inputs': [{'name': 'port', 'label': 'Port', 'type': 'text',
                                    'pattern': 'ttyS[0-9]+', 'required': True}],
                        'timeout_s': 2}
            (directory / '001-fixture.py').write_text(
                f'PLUGIN = {metadata!r}\n'
                'import argparse, json\n'
                'parser = argparse.ArgumentParser()\n'
                'parser.add_argument("--params")\n'
                'values = json.loads(parser.parse_args().params)\n'
                'print(json.dumps({"status": "pass", "message": values["port"]}))\n')
            plugins = RUNNER['load_plugins'](directory)
            self.assertEqual(RUNNER['run_suite'](plugins, 'all', directory=directory)['results'], [])
            validated = RUNNER['validate_parameters'](plugins[0], {'port': 'ttyS2'})
            self.assertEqual(validated, {'port': 'ttyS2'})
            self.assertEqual(RUNNER['run_suite'](plugins, 'RP001', directory=directory,
                                                 params=validated)['results'][0]['message'],
                             'ttyS2')
            with self.assertRaisesRegex(ValueError, 'Invalid value'):
                RUNNER['validate_parameters'](plugins[0], {'port': '../ttyS2'})

    def test_secret_parameters_only_use_stdin_and_never_enter_report(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            metadata = {'id': 'RP001', 'station': ['robopi2', 'rdkx5', 'opi5pro'],
                        'clients': ['tui'], 'author': 'wentywenty',
                        'name': 'Secret fixture',
                        'documentation': {'title': 'Secret fixture',
                                          'criterion': 'Fixture criterion'},
                        'version': '1.0', 'lifecycle': 'testing',
                        'safety': 'active', 'batch': False,
                        'inputs': [{'name': 'password', 'label': 'Password',
                                    'type': 'secret', 'required': True,
                                    'default': 'factory-secret'}], 'timeout_s': 2}
            (directory / '001-fixture.py').write_text(f'PLUGIN = {metadata!r}\n')
            plugin = RUNNER['load_plugins'](directory)[0]
            seen = {}

            def execute(command, timeout, input_data):
                seen['command'] = command
                seen['input'] = input_data
                return subprocess.CompletedProcess(command, 0,
                                                   '{"status":"pass","message":"private-value",'
                                                   '"details":{"echo":"private-value"}}',
                                                   'private-value')

            with patch.dict(RUNNER['run_plugin'].__globals__, {'execute_plugin': execute}):
                report = RUNNER['run_plugin'](plugin, directory=directory,
                                               allow_active=True,
                                               params={'password': 'private-value'})
            self.assertIn('--params-stdin', seen['command'])
            self.assertNotIn('private-value', repr(seen['command']))
            self.assertIn('private-value', seen['input'])
            self.assertNotIn('private-value', json.dumps(report))
            self.assertNotIn('factory-secret', json.dumps(report))
            self.assertEqual(report['parameters'], {'password': '[redacted]'})
            self.assertEqual(report['plugin']['inputs'][0]['default'], '[redacted]')
            self.assertEqual(plugin['inputs'][0]['default'], 'factory-secret')

    def test_conditional_fields_are_validated_by_operation(self):
        plugin = next(item for item in RUNNER['load_plugins'](ROOT / 'plugins')
                      if item['id'] == 'RP040')
        values = RUNNER['validate_parameters'](plugin, {
            'operation': 'limits', 'minimum_mhz': 408, 'maximum_mhz': 1800})
        self.assertEqual(values['maximum_mhz'], 1800)
        with self.assertRaisesRegex(ValueError, 'not applicable'):
            RUNNER['validate_parameters'](plugin, {'operation': 'inspect', 'governor': 'ondemand'})
        with self.assertRaisesRegex(ValueError, 'Missing plugin parameter'):
            RUNNER['validate_parameters'](plugin, {'operation': 'limits'})
            with self.assertRaisesRegex(ValueError, 'Missing'):
                RUNNER['validate_parameters'](plugins[0], {})

    def test_bms_service_checks_systemd_status_and_current_live_data(self):
        self.assertEqual(BMS_VERSION['PLUGIN']['depends_on'], ['RP021'])
        fields = {'ActiveState': 'active', 'SubState': 'running', 'Result': 'success',
                  'ExecMainStatus': '0', 'InvocationID': 'a' * 32}
        invocations = []
        def logs(invocation):
            invocations.append(invocation)
            return '[BMS Data] Voltage: 48V\n'
        self.assertEqual(BMS_SERVICE['probe'](lambda: fields, logs)['status'], 'pass')
        self.assertEqual(invocations, ['a' * 32])
        self.assertEqual(BMS_SERVICE['probe'](lambda: fields, lambda _: 'Connected to BMS')
                         ['status'], 'fail')
        self.assertEqual(BMS_SERVICE['probe'](lambda: {**fields, 'ExecMainStatus': '1'}, logs)
                         ['status'], 'fail')
        self.assertEqual(len(invocations), 1)

    def test_bms_restart_requires_health_check_after_successful_restart(self):
        restart = lambda *args, **kwargs: subprocess.CompletedProcess(args, 0, '', '')
        healthy = lambda *args, **kwargs: subprocess.CompletedProcess(
            args, 0, json.dumps({'status': 'pass', 'message': 'live data',
                                 'details': {'ExecMainStatus': '0'}}), '')
        observed = BMS_RESTART['probe'](restart, healthy, lambda seconds: None,
                                        is_root=lambda: True)
        self.assertEqual(observed['status'], 'pass')
        self.assertEqual(observed['details']['health']['ExecMainStatus'], '0')
        failed = lambda *args, **kwargs: subprocess.CompletedProcess(args, 1, '', 'failed')
        self.assertEqual(BMS_RESTART['probe'](failed, healthy, lambda _: None,
                                              is_root=lambda: True)['status'], 'fail')

    def test_addon_status_and_led_require_hardware_and_visual_review(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'gpiochip0').symlink_to('/dev/null')
            (root / 'gpiochip1').symlink_to('/dev/null')
            observed = ADDON_STATUS['probe'](lambda: True, Path('/dev/null'), root,
                                              '/usr/bin/robopi-ws2812')
            self.assertEqual(observed['status'], 'pass')
            self.assertEqual(ADDON_STATUS['probe'](lambda: False, Path('/dev/null'), root,
                                                   '/usr/bin/robopi-ws2812')['status'], 'fail')
        calls = []
        def run(command, **kwargs):
            calls.append(command)
            return subprocess.CompletedProcess(command, 0, '', '')
        self.assertEqual(ADDON_LED['probe']('red', run, lambda: True)['status'], 'needs_review')
        self.assertEqual(calls, [['robopi-ws2812', 'solid', '32', '0', '0']])
        self.assertEqual(ADDON_LED['probe']('invalid', run, lambda: True)['status'], 'fail')

    def test_sig_edge_restores_outputs_and_requires_a_new_rising_edge(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for number in (61, 40, 18):
                path = root / f'gpio{number}'
                path.mkdir()
                (path / 'direction').write_text('low')
                (path / 'value').write_text('0')
            elapsed = [0.0]
            def pause(seconds):
                elapsed[0] += seconds
                if elapsed[0] >= 0.01:
                    (root / 'gpio61' / 'value').write_text('1')
            observed = ADDON_SIG['probe'](root, lambda: True, lambda: elapsed[0],
                                          pause, window_s=0.1)
            self.assertEqual(observed['status'], 'needs_review')
            for number in (40, 18):
                self.assertEqual((root / f'gpio{number}' / 'direction').read_text(), 'low')
            self.assertTrue(observed['details']['outputs_restored_low'])
            elapsed[0] = 0.0
            self.assertEqual(ADDON_SIG['probe'](root, lambda: True,
                                               lambda: elapsed[0], pause,
                                               window_s=0.1)['status'], 'fail')

    def test_usb_stress_compares_original_device_ids_and_restores_hub(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            baseline = ('Bus 001 Device 001: ID 1234:5678 Hub\n', 'normal boot\n')
            changed_address = ('Bus 001 Device 099: ID 1234:5678 Hub\n', 'normal boot\n')
            missing = ('', 'normal boot\n')
            sequence = iter((baseline, changed_address, changed_address))
            resets = []
            def snapshot(path, usb, dmesg):
                path.mkdir()
                (path / 'lsusb.txt').write_text(usb)
                (path / 'dmesg.txt').write_text(dmesg)
            observed = USB_STRESS['probe'](report_root=root, sample=lambda: next(sequence),
                                            pause=lambda _: None, is_root=lambda: True,
                                            cycles=2, write_reset=resets.append,
                                            snapshot=snapshot)
            self.assertEqual(observed['status'], 'pass')
            self.assertEqual(resets, [1, 0, 1, 0, 0])
            log = Path(observed['details']['log_directory']) / 'result.log'
            self.assertIn('PASS: 2 FAIL: 0', log.read_text())
            sequence = iter((baseline, missing))
            resets.clear()
            failed = USB_STRESS['probe'](report_root=root, sample=lambda: next(sequence),
                                          pause=lambda _: None, is_root=lambda: True,
                                          cycles=1, write_reset=resets.append,
                                          snapshot=snapshot)
            self.assertEqual(failed['status'], 'fail')
            self.assertEqual(resets[-1], 0)
            def stuck_reset(value):
                if value == 0:
                    raise OSError('reset line stuck high')
            sequence = iter((baseline, changed_address))
            unsafe = USB_STRESS['probe'](report_root=root, sample=lambda: next(sequence),
                                          pause=lambda _: None, is_root=lambda: True,
                                          cycles=1, write_reset=stuck_reset,
                                          snapshot=snapshot)
            self.assertEqual(unsafe['status'], 'error')
            self.assertIn('Could not restore', unsafe['message'])

    def test_hipnuc_rule_is_owned_by_base_package(self):
        with tempfile.TemporaryDirectory() as temp:
            rule = Path(temp) / '99-can_hipnuc.rules'
            rule.write_text('SUBSYSTEM=="net", NAME="can_hipnuc"\n')
            found = subprocess.CompletedProcess([], 0, str(rule) + '\n', '')
            missing = subprocess.CompletedProcess([], 1, '', 'not installed')
            self.assertEqual(HIPNUC['probe'](lambda: found, rule)['status'], 'pass')
            self.assertEqual(HIPNUC['probe'](lambda: missing, rule)['status'], 'fail')
            rule.write_text('NAME="wrong"\n')
            self.assertEqual(HIPNUC['probe'](lambda: found, rule)['status'], 'fail')

    def test_bms_port_checks_configured_device_without_opening_it(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = root / 'bms_daemon'
            net = root / 'net'
            net.mkdir()
            config.write_text('BMS_TYPE=GFCAN\nBMS_PORT=canbms\n')
            self.assertEqual(BMS_PORT['probe'](config, net)['status'], 'fail')
            (net / 'canbms').mkdir()
            self.assertEqual(BMS_PORT['probe'](config, net)['status'], 'pass')
            config.write_text('BMS_TYPE=SCUD485\nBMS_PORT=/dev/null\n')
            self.assertEqual(BMS_PORT['probe'](config, net)['status'], 'pass')
            config.write_text('BMS_TYPE=GFCAN\nBMS_PORT=$(touch /tmp/unsafe)\n')
            self.assertEqual(BMS_PORT['probe'](config, net)['status'], 'fail')

    def test_light_board_readiness_does_not_drive_leds(self):
        with tempfile.TemporaryDirectory() as temp:
            absent = Path(temp) / 'light-board'
            self.assertEqual(LIGHT_BOARD['probe'](absent, '')['status'], 'skip')
            self.assertEqual(LIGHT_BOARD['probe'](absent, '/usr/bin/robopi-ws2812')['status'],
                             'fail')
            self.assertEqual(LIGHT_BOARD['probe'](Path('/dev/null'),
                                                  '/usr/bin/robopi-ws2812')['status'],
                             'needs_review')

    def test_apt_terminal_plugin_uses_shared_durable_backend(self):
        self.assertEqual(APT['PLUGIN'].get('depends_on', []), [])
        from unittest.mock import patch
        with patch('loader.task_runtime.run_for_tui', return_value={'status': 'needs_review'}) as shared:
            self.assertEqual('needs_review', APT['probe']()['status'])
            shared.assert_called_once_with('apt-upgrade')

    def test_hpm_terminal_plugin_uses_shared_durable_backend(self):
        from unittest.mock import patch
        with patch('loader.task_runtime.run_for_tui', return_value={'status': 'fail'}) as shared:
            self.assertEqual('fail', HPM['probe']()['status'])
            shared.assert_called_once_with('hpm-flash')

    def test_one_lro_motor_is_selected_written_and_read_back(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = root / 'robot.yaml'
            config.write_text('motors:\n'
                              '  motor_interface: [can0, can1]\n'
                              '  motor_interface_type: [canfd, canfd]\n'
                              '  motor_num: [1, 1]\n'
                              '  motor_type: [LRO, LRO]\n'
                              '  motor_id: [2, 2]\n'
                              '  motor_model: [1, 2]\n')
            self.assertEqual(MOTOR_PARAMS['select_target'](config, 'can0', 2), 1)
            self.assertEqual(MOTOR_PARAMS['select_target'](config, 'can1', 2), 2)
            class Session:
                def __init__(self):
                    self.writes = []
                    self.values = {'can_timeout': (0.0, 100.0)}
                def read_param(self, key):
                    return self.values.get(key, (0.0, 0.0))
                def write_param(self, key, low, high):
                    self.writes.append(key)
                    self.values[key] = (low, high)
            session = Session()
            observed = MOTOR_PARAMS['probe'](
                config, 'can0', 2, True, lambda *args: session,
                lambda interface: {'state': 'ERROR-ACTIVE'}, lambda: True,
                root / 'logs', lambda delay: None)
            self.assertEqual(observed['status'], 'pass')
            self.assertEqual(session.writes, ['kt', 'kp', 'kd', 'pos', 'spd', 'tor'])
            self.assertEqual(observed['details']['can_timeout_after_ms'], 100)
            events = [json.loads(line)['event'] for line in
                      Path(observed['details']['journal']).read_text().splitlines()]
            self.assertEqual(events.count('readback'), 6)
            self.assertEqual(events[-1], 'complete')
            self.assertEqual(MOTOR_PARAMS['probe'](
                config, 'can0', 2, False, lambda *args: session,
                lambda interface: {}, lambda: True, root / 'unused')['status'], 'fail')

    def test_lro_motor_parameter_mismatch_stops_remaining_writes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = root / 'robot.yaml'
            config.write_text('motors:\n'
                              '  motor_interface: [can0]\n'
                              '  motor_interface_type: [canfd]\n'
                              '  motor_num: [1]\n'
                              '  motor_type: [LRO]\n'
                              '  motor_id: [3]\n'
                              '  motor_model: [1]\n')
            class MismatchSession:
                writes = []
                def read_param(self, key):
                    return (0.0, 20.0) if key == 'can_timeout' else (0.0, 0.0)
                def write_param(self, key, low, high):
                    self.writes.append(key)
            session = MismatchSession()
            observed = MOTOR_PARAMS['probe'](
                config, 'can0', 3, True, lambda *args: session,
                lambda interface: {}, lambda: True, root / 'logs', lambda delay: None)
            self.assertEqual(observed['status'], 'fail')
            self.assertEqual(session.writes, ['kt'])
            self.assertEqual(observed['details']['completed_writes'][0]['key'], 'kt')

    def test_network_snapshot(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            network = root / 'net'
            (network / 'lo').mkdir(parents=True)
            iface = network / 'eth0'
            iface.mkdir()
            (iface / 'operstate').write_text('up\n')
            (iface / 'carrier').write_text('1\n')
            (iface / 'address').write_text('00:11:22:33:44:55\n')
            observed = NETWORK['probe'](network)
            self.assertEqual(observed['status'], 'needs_review')
            self.assertEqual(observed['details']['interfaces'][0]['name'], 'eth0')

    def test_usb_topology_gsusb_binding_and_superspeed_hub(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            device = root / '1-2'
            device.mkdir()
            for name, content in {'idVendor': '1209', 'idProduct': '2323',
                                  'product': 'RoboPi USB', 'speed': '480',
                                  'version': '2.00', 'bcdDevice': '0100'}.items():
                (device / name).write_text(content)
            observed = USB['probe'](root)
            self.assertEqual(observed['details']['devices'][0]['usb_version'], '2.00')
            targeted = USB_DEVICE['probe'](root)
            self.assertEqual(targeted['status'], 'fail')
            driver = root / 'drivers' / 'gs_usb'
            driver.mkdir(parents=True)
            interface = root / '1-2:1.0'
            (interface / 'net' / 'can0').mkdir(parents=True)
            (interface / 'driver').symlink_to(driver)
            targeted = USB_DEVICE['probe'](root)
            self.assertEqual(targeted['status'], 'pass')
            self.assertEqual(targeted['value'], 1)
            hub = root / '2-1'
            hub.mkdir()
            for name, content in {'idVendor': '2109', 'idProduct': '0817',
                                  'speed': '5000', 'bDeviceClass': '09'}.items():
                (hub / name).write_text(content)
            hub_result = HUB['probe'](root)
            self.assertEqual(hub_result['status'], 'pass')
            self.assertEqual(hub_result['details']['hubs'][0]['vendor_id'], '2109')
            self.assertEqual(hub_result['details']['hubs'][0]['speed_mbps'], '5000')
            (hub / 'bDeviceClass').write_text('invalid')
            hub_interface = root / '2-1:1.0'
            hub_interface.mkdir()
            (hub_interface / 'bInterfaceClass').write_text('09')
            self.assertEqual(HUB['probe'](root)['status'], 'pass')
            (hub / 'speed').write_text('480')
            self.assertEqual(HUB['probe'](root)['status'], 'fail')

    def test_can_snapshot_is_read_only(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            can0 = root / 'net' / 'can0'
            can0.mkdir(parents=True)
            (can0 / 'operstate').write_text('up')
            (can0 / 'mtu').write_text('72')
            seen = []
            observed = CAN['probe'](can0.parent,
                                    lambda name: seen.append(name) or {'can_state': 'ERROR-ACTIVE'})
            self.assertEqual(seen, ['can0'])
            self.assertEqual(observed['details']['interfaces'][0]['can_state'], 'ERROR-ACTIVE')

    def test_gpio_snapshot(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            gpio = root / 'gpio'
            gpio.write_text('gpiochip0: GPIOs 0-31\n')
            observed = GPIO['probe'](gpio, gpioinfo=None)
            self.assertEqual(observed['status'], 'needs_review')
            self.assertIn('gpiochip0', observed['details']['snapshot'])

    def test_factory_image_identity_checks_all_fields(self):
        with tempfile.TemporaryDirectory() as temp:
            release = Path(temp) / 'armbian-image-release'
            release.write_text('\n'.join(f'{key}={value!r}'
                                         for key, value in IMAGE['EXPECTED'].items()))
            self.assertEqual(IMAGE['probe'](release)['status'], 'pass')
            release.write_text(release.read_text().replace("'robopi2'", "'wrong'"))
            observed = IMAGE['probe'](release)
            self.assertEqual(observed['status'], 'fail')
            self.assertIn('BOARD', observed['details']['mismatches'])

    def test_bms_version_from_current_serial_service_and_can_frame(self):
        with tempfile.TemporaryDirectory() as temp:
            config = Path(temp) / 'bms_daemon'
            config.write_text('BMS_TYPE=TWS\nBMS_PORT=/dev/ttyS3\n')
            log = lambda: ' > FW Version: 0x0106 | HW Version: 0x0002\n'
            observed = BMS_VERSION['probe'](config, query_log=log)
            self.assertEqual(observed['details']['software_version'], '0x0106')
            config.write_text('BMS_TYPE=GFCAN\nBMS_PORT=canbms\n')
            observed = BMS_VERSION['probe'](
                config, listen=lambda port: bytes((6, 1, 0, 2, 0, 0, 0, 0)))
            self.assertEqual(observed['status'], 'pass')
            self.assertEqual(observed['details']['software_version'], '6.1.0')
            self.assertEqual(observed['details']['source'], 'canbms CAN frame 0x18F00903')

    def test_imu_query_frame_and_version_bitrate_decoding(self):
        node = 8
        data_frame = struct.pack('=IB3x8s', 0x8CFF3408, 8, b'12345678')
        remote_frame = struct.pack('=IB3x8s', 0xCCFF3408, 8, b'12345678')
        for plugin, address, raw in ((IMU_VERSION, 0x0078, 172),
                                     (IMU_BITRATE, 0x009A, 0)):
            with self.subTest(address=address):
                self.assertEqual(plugin['hipnuc_data_node'](data_frame), node)
                self.assertIsNone(plugin['hipnuc_data_node'](remote_frame))
                request = plugin['hipnuc_read_frame'](address, node)
                self.assertEqual(struct.unpack('=IB3x8s', request),
                                 (0x8CEF0855, 8, struct.pack('<HBBI', address, 3, 0, 0)))
                response = struct.pack('=IB3x8s', 0x8CEF5508, 8,
                                       struct.pack('<HBBHH', address, 3, 0, raw, 0))
                self.assertEqual(plugin['hipnuc_reply_value'](response, address, node), raw)
                self.assertIsNone(plugin['hipnuc_reply_value'](response,
                                                              address ^ 1, node))
                with self.assertRaisesRegex(ValueError, 'Only'):
                    plugin['hipnuc_query_register'](address ^ 1)
        self.assertEqual(IMU_VERSION['probe'](lambda address: (172, node))['details']['version'],
                         '1.7.2')
        good = IMU_BITRATE['probe'](lambda address: (0, node), lambda: 1000000)
        bad = IMU_BITRATE['probe'](lambda address: (2, node), lambda: 1000000)
        self.assertEqual(good['status'], 'pass')
        self.assertEqual(bad['status'], 'fail')

    def test_imu_query_waits_for_data_before_sending(self):
        node = 8
        data_frame = struct.pack('=IB3x8s', 0x8CFF3408, 8, b'12345678')

        class FakeBus:
            def __init__(self, frames):
                self.frames = list(frames)
                self.sent = []

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def bind(self, address):
                self.address = address

            def settimeout(self, timeout):
                pass

            def recv(self, length):
                if not self.frames:
                    raise socket.timeout()
                return self.frames.pop(0)

            def send(self, frame):
                self.sent.append(frame)

        for plugin, address, raw in ((IMU_VERSION, 0x0078, 172),
                                     (IMU_BITRATE, 0x009A, 0)):
            with self.subTest(address=address):
                silent = FakeBus([])
                with patch.object(plugin['socket'], 'socket', return_value=silent):
                    with self.assertRaisesRegex(TimeoutError, 'no query was sent'):
                        plugin['hipnuc_query_register'](address)
                self.assertEqual(silent.sent, [])
                reply = struct.pack('=IB3x8s', 0x8CEF5508, 8,
                                    struct.pack('<HBBHH', address, 3, 0, raw, 0))
                responsive = FakeBus([data_frame, reply])
                with patch.object(plugin['socket'], 'socket', return_value=responsive):
                    self.assertEqual(plugin['hipnuc_query_register'](address), (raw, node))
                self.assertEqual(responsive.address, ('can_hipnuc',))
                self.assertEqual(responsive.sent, [plugin['hipnuc_read_frame'](address, node)])

    def test_public_source_and_installed_package_list(self):
        signed = lambda url: b'-----BEGIN PGP SIGNED MESSAGE-----\n'
        configured = lambda: ['https://apt.roboparty.com/dists/common/main/binary-arm64/Packages']
        self.assertEqual(APT_SOURCE['probe'](configured, signed)['status'], 'pass')
        self.assertEqual(APT_SOURCE['probe'](lambda: [], signed)['status'], 'fail')
        output = ('roboparty-base\t1.0\tinstall ok installed\n'
                  'robopi-tui\t0.1\tinstall ok installed\n'
                  'roboparty-imu\t2.0\tdeinstall ok config-files\n')
        query = lambda: subprocess.CompletedProcess([], 0, output, '')
        observed = PACKAGES['probe'](query)
        self.assertEqual(observed['status'], 'pass')
        self.assertEqual(observed['value'], 2)

    def test_contract_and_atomic_report(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            plugins = fixture_plugin(directory, 'import json\nprint(json.dumps('
                                     '{"status":"pass","value":42,"unit":"C",'
                                     '"message":"ok","details":{}}))\n')
            plugins[0]['diagnostic_note'] = 'retained'
            report = RUNNER['run_suite'](plugins, 'RP001', 'csharp', directory)
            self.assertEqual(report['schema_version'], 3)
            self.assertEqual(report['tui_version'], RUNNER['PACKAGE_VERSION'])
            self.assertEqual(report['outcome'], 'pass')
            self.assertEqual(report['results'][0]['value'], 42)
            self.assertEqual({key: report['results'][0][key]
                              for key in ('id', 'station', 'name', 'result')},
                             {'id': 'RP001', 'station': ['robopi2', 'rdkx5', 'opi5pro'],
                              'name': 'Fixture test', 'result': 'pass'})
            self.assertEqual(report['results'][0]['status'], report['results'][0]['result'])
            self.assertEqual(report['results'][0]['author'], 'wentywenty')
            self.assertEqual(report['results'][0]['lifecycle'], 'testing')
            self.assertEqual(report['results'][0]['plugin_version'], '1.0')
            self.assertEqual(report['results'][0]['plugin']['version'], '1.0')
            self.assertEqual(report['results'][0]['plugin']['documentation']['criterion'],
                             'Fixture criterion')
            self.assertEqual(report['results'][0]['plugin']['requires'], [])
            self.assertEqual(report['results'][0]['plugin']['diagnostic_note'], 'retained')
            self.assertNotIn('_script', report['results'][0]['plugin'])
            self.assertEqual(report['results'][0]['parameters'], {})
            self.assertEqual(report['results'][0]['exit_code'], 0)
            self.assertEqual(RUNNER['catalog'](plugins)['tests'][0]['author'], 'wentywenty')
            self.assertEqual(RUNNER['catalog'](plugins)['tests'][0]['lifecycle'], 'testing')
            logs = report['results'][0]['logs']
            self.assertEqual([entry['source'] for entry in logs],
                             ['runner', 'stdout', 'runner'])
            self.assertEqual(logs[0]['timestamp'], report['results'][0]['started_at'])
            self.assertEqual(logs[-1]['timestamp'], report['results'][0]['finished_at'])
            self.assertIn('"status": "pass"', logs[1]['message'])
            self.assertEqual(RUNNER['catalog'](plugins)['tests'][0]['station'],
                             ['robopi2', 'rdkx5', 'opi5pro'])
            saved = RUNNER['save_report'](report, directory / 'reports')
            self.assertEqual(json.loads(saved.read_text()), report)
            self.assertEqual(saved.stat().st_mode & 0o777, 0o600)
            self.assertEqual(list((directory / 'reports').glob('.report-*')), [])

    def test_plugin_error_details_are_kept_in_diagnostic_report(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            plugins = fixture_plugin(directory, 'import json\nprint(json.dumps('
                                     '{"status":"error","message":"sensor query failed",'
                                     '"details":{"stage":"read","errno":5}}))\n')
            item = RUNNER['run_suite'](plugins, 'RP001', directory=directory)['results'][0]
            self.assertEqual(item['result'], 'error')
            self.assertEqual(item['details'], {'stage': 'read', 'errno': 5})
            self.assertEqual([log['source'] for log in item['logs']],
                             ['runner', 'stdout', 'runner'])

    def test_plugin_subprocess_imports_loader_helpers(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            plugins = fixture_plugin(
                directory,
                'import json\nfrom loader.plugin_helpers import result\n'
                'print(json.dumps(result("pass", "loader helper available")))\n')
            report = RUNNER['run_suite'](plugins, 'RP001', directory=directory)
            self.assertEqual(report['outcome'], 'pass')
            self.assertEqual(report['results'][0]['message'], 'loader helper available')

    def test_client_catalog_call_is_archived_without_changing_stdout(self):
        with tempfile.TemporaryDirectory() as temp:
            globals_ = RUNNER['main'].__globals__
            previous = globals_['REPORT_DIR']
            stdout, stderr = io.StringIO(), io.StringIO()
            globals_['REPORT_DIR'] = Path(temp)
            try:
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    exit_code = RUNNER['main'](['--list', '--env', 'csharp',
                                                '--client-id', 'LINE-PC-01'])
            finally:
                globals_['REPORT_DIR'] = previous
            self.assertEqual(exit_code, 0, stderr.getvalue())
            self.assertEqual(json.loads(stdout.getvalue())['schema_version'], 3)
            calls = list((Path(temp) / 'calls').glob('*.json'))
            self.assertEqual(len(calls), 1)
            entry = json.loads(calls[0].read_text())
            self.assertEqual((entry['action'], entry['client'], entry['client_id']),
                             ('list', 'csharp', 'LINE-PC-01'))
            self.assertEqual(entry['tui_version'], RUNNER['PACKAGE_VERSION'])

    def test_client_catalog_filters_and_runner_rejects_disallowed_plugin(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            marker = directory / 'executed'
            plugins = fixture_plugin(
                directory, f'from pathlib import Path\nPath({str(marker)!r}).touch()\n',
                safety='active')
            self.assertEqual(RUNNER['catalog'](plugins, client='csharp')['tests'], [])
            visible = RUNNER['catalog'](plugins, client='tui')['tests'][0]
            self.assertEqual(visible['clients'], ['tui'])
            self.assertTrue(visible['available'])
            with self.assertRaisesRegex(ValueError, 'not available to csharp'):
                RUNNER['run_suite'](plugins, 'RP001', 'csharp', directory)
            self.assertEqual(RUNNER['run_suite'](plugins, 'all', 'csharp', directory)['results'], [])
            self.assertFalse(marker.exists())

    def test_csharp_only_plugin_appears_in_developer_catalog_but_not_tui_catalog(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            plugins = fixture_plugin(directory, 'pass\n')
            plugins[0]['clients'] = ['csharp']
            self.assertEqual(RUNNER['catalog'](plugins, client='tui')['tests'], [])
            visible = RUNNER['catalog'](plugins, client='tui', include_all=True)['tests'][0]
            self.assertEqual(visible['clients'], ['csharp'])
            self.assertFalse(visible['available'])
            self.assertTrue(RUNNER['catalog'](plugins, client='csharp')['tests'][0]['available'])

    def test_client_run_links_call_log_to_report(self):
        with tempfile.TemporaryDirectory() as temp:
            globals_ = RUNNER['main'].__globals__
            previous = globals_['REPORT_DIR']
            stdout, stderr = io.StringIO(), io.StringIO()
            globals_['REPORT_DIR'] = Path(temp)
            try:
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    exit_code = RUNNER['main'](['--run', 'RP001', '--env', 'csharp',
                                                '--client-id', 'LINE-PC-01'])
            finally:
                globals_['REPORT_DIR'] = previous
            self.assertEqual(exit_code, 0, stderr.getvalue())
            report = json.loads(stdout.getvalue())
            self.assertEqual((report['selected'], report['client_id']),
                             ('RP001', 'LINE-PC-01'))
            call = json.loads(next((Path(temp) / 'calls').glob('*.json')).read_text())
            self.assertEqual(call['action'], 'run')
            self.assertEqual(call['selected'], 'RP001')
            self.assertEqual(call['tui_version'], report['tui_version'])
            self.assertEqual(json.loads(Path(call['report_path']).read_text()), report)

    def test_unavailable_plugin_is_not_executed(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            marker = directory / 'executed'
            plugins = fixture_plugin(directory, f'open({str(marker)!r}, "w").close()\n',
                                     requires=['thermal'])
            report = RUNNER['run_suite'](plugins, directory=directory,
                                         checker=lambda name: False)
            self.assertEqual(report['results'][0]['status'], 'skip')
            self.assertFalse(marker.exists())
            self.assertFalse(RUNNER['catalog'](plugins, lambda name: False)['tests'][0]['available'])

    def test_active_plugin_requires_explicit_selection(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            marker = directory / 'executed'
            plugins = fixture_plugin(directory,
                                     f'from pathlib import Path\nPath({str(marker)!r}).touch()\n',
                                     safety='active')
            report = RUNNER['run_suite'](plugins, 'all', directory=directory)
            self.assertEqual(report['results'][0]['status'], 'skip')
            self.assertFalse(marker.exists())
            self.assertEqual(RUNNER['run_plugin'](plugins[0], directory)['status'], 'skip')
            self.assertFalse(marker.exists())
            plugins = fixture_plugin(
                directory, f'from pathlib import Path\nimport json\n'
                f'Path({str(marker)!r}).touch()\n'
                'print(json.dumps({"status":"pass","message":"done"}))\n',
                safety='active')
            self.assertEqual(RUNNER['run_suite'](plugins, 'RP001',
                                                directory=directory)['outcome'], 'pass')
            self.assertTrue(marker.exists())

    def test_deprecated_plugin_is_listed_but_never_executed(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            marker = directory / 'executed'
            plugins = fixture_plugin(directory, f'open({str(marker)!r}, "w").close()\n',
                                     lifecycle='deprecated')
            listed = RUNNER['catalog'](plugins)['tests'][0]
            self.assertEqual(listed['lifecycle'], 'deprecated')
            self.assertFalse(listed['available'])
            report = RUNNER['run_suite'](plugins, 'RP001', directory=directory)
            self.assertEqual(report['results'][0]['status'], 'skip')
            self.assertEqual(report['results'][0]['lifecycle'], 'deprecated')
            self.assertFalse(marker.exists())

    def test_dependencies_run_first_and_block_failed_dependents(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            first = {'id': 'RP001', 'station': ['robopi2', 'rdkx5', 'opi5pro'],
                     'clients': ['tui', 'csharp'], 'author': 'wentywenty',
                     'name': 'First',
                     'documentation': {'title': 'First', 'criterion': 'Fixture criterion'},
                     'version': '1.0', 'lifecycle': 'testing',
                     'safety': 'passive', 'requires': [],
                     'timeout_s': 2}
            second = {**first, 'id': 'RP002', 'name': 'Second',
                      'depends_on': ['RP001']}
            (directory / '001-first.py').write_text(
                f'PLUGIN = {first!r}\nimport json\n'
                'print(json.dumps({"status":"fail","message":"failed"}))\n')
            marker = directory / 'executed'
            (directory / '002-second.py').write_text(
                f'PLUGIN = {second!r}\nfrom pathlib import Path\n'
                f'Path({str(marker)!r}).touch()\n')
            plugins = RUNNER['load_plugins'](directory)
            report = RUNNER['run_suite'](plugins, 'RP002', directory=directory)
            self.assertEqual([item['id'] for item in report['results']], ['RP001', 'RP002'])
            self.assertEqual(report['results'][1]['status'], 'skip')
            self.assertFalse(marker.exists())
            first['depends_on'] = ['RP002']
            (directory / '001-first.py').write_text(f'PLUGIN = {first!r}\n')
            with self.assertRaisesRegex(ValueError, 'cycle'):
                RUNNER['load_plugins'](directory)

    def test_invalid_result_and_timeout_are_errors(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            plugins = fixture_plugin(directory, 'print("not json")\n')
            result = RUNNER['run_plugin'](plugins[0], directory)
            self.assertEqual(result['status'], 'error')
            self.assertIsNone(result['value'])
            self.assertIn('not json', result['logs'][1]['message'])
            plugins = fixture_plugin(directory, 'import sys\nprint("diagnostic", file=sys.stderr)\n'
                                     'raise SystemExit(1)\n')
            result = RUNNER['run_plugin'](plugins[0], directory)
            self.assertEqual(result['status'], 'error')
            self.assertEqual(result['logs'][1]['source'], 'stderr')
            self.assertIn('diagnostic', result['logs'][1]['message'])
            plugins = fixture_plugin(directory, 'import time\ntime.sleep(2)\n', timeout=1)
            result = RUNNER['run_plugin'](plugins[0], directory)
            self.assertEqual(result['status'], 'error')
            self.assertIn('timed out', result['message'])
            marker = directory / 'terminated'
            body = ('import signal, time\nfrom pathlib import Path\n'
                    f'signal.signal(signal.SIGTERM, lambda *_: '
                    f'(Path({str(marker)!r}).write_text("restored"), exit(0)))\n'
                    'time.sleep(2)\n')
            plugins = fixture_plugin(directory, body, timeout=1)
            result = RUNNER['run_plugin'](plugins[0], directory)
            self.assertEqual(result['status'], 'error')
            self.assertTrue(marker.exists())

    def test_metadata_rejects_path_traversal_and_unbounded_timeout(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            fixture_plugin(directory, 'print("ok")\n')
            script = directory / '001-fixture.py'
            metadata = {'id': 'RP001', 'station': ['robopi2', 'rdkx5', 'opi5pro'],
                        'clients': ['tui', 'csharp'], 'author': 'wentywenty',
                        'name': 'Fixture test',
                        'documentation': {'title': 'Fixture test',
                                          'criterion': 'Fixture criterion'},
                        'version': '1.0', 'lifecycle': 'testing', 'safety': 'passive',
                        'requires': [], 'timeout_s': 2}
            metadata['id'] = '../unsafe'
            script.write_text(f'PLUGIN = {metadata!r}\n')
            with self.assertRaises(ValueError):
                RUNNER['load_plugins'](directory)
            metadata['id'] = 'RP001'
            metadata['timeout_s'] = 999
            script.write_text(f'PLUGIN = {metadata!r}\n')
            with self.assertRaises(ValueError):
                RUNNER['load_plugins'](directory)
            metadata['timeout_s'] = 2
            metadata['safety'] = 'unreviewed'
            script.write_text(f'PLUGIN = {metadata!r}\n')
            with self.assertRaises(ValueError):
                RUNNER['load_plugins'](directory)
            metadata['safety'] = 'passive'
            metadata['lifecycle'] = 'unreviewed'
            script.write_text(f'PLUGIN = {metadata!r}\n')
            with self.assertRaisesRegex(ValueError, 'lifecycle'):
                RUNNER['load_plugins'](directory)
            metadata['lifecycle'] = 'testing'
            metadata.pop('station')
            script.write_text(f'PLUGIN = {metadata!r}\n')
            with self.assertRaises(ValueError):
                RUNNER['load_plugins'](directory)
            metadata['station'] = ['robopi2', 'rdkx5', 'opi5pro']
            for station in ('board', [], ['robopi2', 'robopi2']):
                metadata['station'] = station
                script.write_text(f'PLUGIN = {metadata!r}\n')
                with self.assertRaisesRegex(ValueError, 'station'):
                    RUNNER['load_plugins'](directory)
            metadata['station'] = ['robopi2', 'rdkx5', 'opi5pro']
            for clients in ([], ['csharp', 'csharp'], ['browser']):
                metadata['clients'] = clients
                script.write_text(f'PLUGIN = {metadata!r}\n')
                with self.assertRaisesRegex(ValueError, 'clients'):
                    RUNNER['load_plugins'](directory)
            metadata['clients'] = ['tui', 'csharp']
            metadata['kind'] = 'action'
            script.write_text(f'PLUGIN = {metadata!r}\n')
            with self.assertRaisesRegex(ValueError, 'TUI-only'):
                RUNNER['load_plugins'](directory)
            metadata['kind'] = 'test'
            metadata['safety'] = 'active'
            script.write_text(f'PLUGIN = {metadata!r}\n')
            with self.assertRaisesRegex(ValueError, 'TUI-only'):
                RUNNER['load_plugins'](directory)
            metadata['safety'] = 'passive'
            metadata.pop('author')
            script.write_text(f'PLUGIN = {metadata!r}\n')
            with self.assertRaisesRegex(ValueError, 'author'):
                RUNNER['load_plugins'](directory)
            metadata['author'] = 'wentywenty'
            metadata['id'] = 'RP002'
            script.write_text(f'PLUGIN = {metadata!r}\n')
            with self.assertRaises(ValueError):
                RUNNER['load_plugins'](directory)

    def test_catalog_does_not_execute_plugin_or_dynamic_metadata(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            marker = directory / 'executed'
            fixture_plugin(directory, f'open({str(marker)!r}, "w").close()\n')
            RUNNER['catalog'](RUNNER['load_plugins'](directory))
            self.assertFalse(marker.exists())
            (directory / '001-fixture.py').write_text('PLUGIN = dict(id="RP001")\n')
            with self.assertRaises(ValueError):
                RUNNER['load_plugins'](directory)

    def test_tui_builds_menu_from_catalog(self):
        with tempfile.TemporaryDirectory() as temp:
            stub = Path(temp) / 'stub.py'
            catalog = {'tests': [{'id': 'RP001', 'station': ['robopi2', 'rdkx5', 'opi5pro'],
                                  'clients': ['tui', 'csharp'],
                                  'name': 'Fixture test', 'version': '1.2',
                                  'available': True,
                                  'lifecycle': 'testing'}]}
            report = {'run_id': 'test-run', 'outcome': 'incomplete', 'results': [
                {'id': 'RP001', 'station': ['robopi2', 'rdkx5', 'opi5pro'], 'name': 'Fixture test',
                 'result': 'needs_review', 'status': 'needs_review', 'value': 42,
                 'unit': 'C', 'message': 'Review measurement',
                 'lifecycle': 'testing', 'plugin_version': '1.2',
                 'plugin': {'id': 'RP001', 'version': '1.2'},
                 'parameters': {'port': 'ttyS2'}, 'exit_code': 0,
                 'logs': [{'timestamp': '2026-10-25T00:00:00Z', 'source': 'stderr',
                           'message': 'diagnostic output'}]}]}
            stub.write_text('import sys\nprint(' + repr(json.dumps(catalog))
                            + ' if "--list" in sys.argv else ' + repr(json.dumps(report)) + ')\n')

            class FakeUI:
                def __init__(self):
                    self.choices = ['RP001', None]
                    self.items = None
                    self.result = None

                def menu(self, title, items):
                    self.items = items
                    return self.choices.pop(0)

                def output(self, title, body):
                    self.result = body

                def message(self, title, body):
                    self.fail_message = body

            ui = FakeUI()
            APP['plugin_tests_menu'](ui, stub, develop=True)
            self.assertIn(('RP001', '[Board] Fixture test v1.2 [testing]'), ui.items)
            self.assertIn('Review measurement', ui.result)
            self.assertIn('Plugin version: 1.2', ui.result)
            self.assertIn('"version": "1.2"', ui.result)
            self.assertIn('"port": "ttyS2"', ui.result)
            self.assertIn('diagnostic output', ui.result)
            self.assertIn('[RP001] Fixture test (robopi2, rdkx5, opi5pro)', ui.result)

    def test_tui_developer_view_lists_csharp_only_plugin_without_running_it(self):
        with tempfile.TemporaryDirectory() as temp:
            stub = Path(temp) / 'stub.py'
            catalog = {'tests': [{'id': 'RP001', 'station': ['robopi2'],
                                  'clients': ['csharp'], 'name': 'CSharp check',
                                  'version': '1.0',
                                  'lifecycle': 'testing', 'available': False}]}
            stub.write_text('import json, sys\n'
                            f'print(json.dumps({catalog!r})) if "--list" in sys.argv '
                            'else sys.exit(9)\n')

            class FakeUI:
                def message(self, title, body):
                    self.message_text = body

            ui = FakeUI()
            APP['plugin_tests_menu'](ui, stub, selected='RP001', develop=True)
            self.assertIn('only available to another client', ui.message_text)

    def test_tui_normal_view_hides_testing_plugins(self):
        with tempfile.TemporaryDirectory() as temp:
            stub = Path(temp) / 'stub.py'
            catalog = {'tests': [{'id': 'RP001', 'station': ['robopi2'],
                                  'clients': ['tui'], 'name': 'Testing check',
                                  'lifecycle': 'testing', 'available': True}]}
            stub.write_text('import json\nprint(json.dumps(' + repr(catalog) + '))\n')

            class FakeUI:
                def message(self, title, body):
                    self.message_text = body

            ui = FakeUI()
            APP['plugin_tests_menu'](ui, stub)
            self.assertEqual(ui.message_text, 'No tests installed.')

    def test_tui_forwards_declared_led_mode_through_runner(self):
        with tempfile.TemporaryDirectory() as temp:
            stub = Path(temp) / 'runner.py'
            catalog = {'tests': [{'id': 'RP018', 'station': ['robopi2', 'rdkx5', 'opi5pro'],
                                  'clients': ['tui'],
                                  'name': 'Addon LED visual test', 'version': '1.0',
                                  'available': True,
                                  'safety': 'active', 'lifecycle': 'testing',
                                  'inputs': [{'name': 'mode', 'label': 'Mode', 'type': 'choice',
                                              'choices': ['red', 'blue'], 'required': True}]}]}
            report = {'run_id': 'led-run', 'outcome': 'incomplete', 'results': [
                {'id': 'RP018', 'station': ['robopi2', 'rdkx5', 'opi5pro'], 'name': 'Addon LED visual test',
                 'result': 'needs_review', 'value': None, 'unit': None,
                 'message': 'Verify LED output', 'lifecycle': 'testing',
                 'plugin_version': '1.0'}]}
            stub.write_text('import json, sys\n'
                            f'catalog = {catalog!r}\nreport = {report!r}\n'
                            'if "--list" in sys.argv: print(json.dumps(catalog))\n'
                            'elif "--params" in sys.argv and '
                            'json.loads(sys.argv[sys.argv.index("--params") + 1]) == {"mode": "red"}: '
                            'print(json.dumps(report))\n'
                            'else: raise SystemExit(2)\n')

            class FakeUI:
                def menu(self, title, items):
                    return 'red'
                def yes(self, title, prompt):
                    return True
                def output(self, title, body):
                    self.body = body
                def message(self, title, body):
                    self.fail(body)

            ui = FakeUI()
            with patch('plugin_test_menu.os.geteuid', return_value=0):
                APP['plugin_tests_menu'](ui, stub, selected='RP018', develop=True)
            self.assertIn('Verify LED output', ui.body)


if __name__ == '__main__':
    unittest.main()
