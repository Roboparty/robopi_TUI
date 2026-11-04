#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0
# Copyright (C) 2026 wentywenty

"""Schedule package-owned plugins and emit versioned JSON reports."""

import argparse
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import platform
import re
import signal
import subprocess
import sys
import tempfile
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from loader import acceptance
from loader.board_snapshot import identity as board_identity
from loader.plugin_catalog import (CLIENTS, PLUGIN_DIR, SCHEMA_VERSION, available,
                                   catalog, execution_order, has_capability, load_plugins)
from loader.package_version import package_version
from loader.task_runtime import JobError, control_lock

logger = logging.getLogger(__name__)

REPORT_DIR = Path('/var/lib/robopi-tui/reports')
PACKAGE_VERSION = package_version()
STATUSES = ('pass', 'fail', 'needs_review', 'skip', 'error')
MOTOR_PLUGIN_ID = 'RP025'
ADDON_LED_PLUGIN_ID = 'RP018'
CLIENT_ID_PATTERN = re.compile(r'[A-Za-z0-9_.:-]{1,64}\Z')


def now():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds')


def generic_host():
    """True on non-board hosts (x86/amd64), where only generic plugins may run.

    ``RP_TUI_GENERIC=0/1`` overrides detection (used by host-side tests).
    """
    override = os.environ.get('RP_TUI_GENERIC', '').lower()
    if override in ('0', 'false', 'no'):
        return False
    if override in ('1', 'true', 'yes'):
        return True
    return platform.machine().lower() not in ('aarch64', 'arm64')


def generic_plugins(plugins):
    """Filter to generic plugins and reject dependencies outside that set."""
    allowed = {plugin['id'] for plugin in plugins if plugin.get('generic', False)}
    for plugin in plugins:
        if plugin['id'] not in allowed:
            continue
        missing = [dep for dep in plugin['depends_on'] if dep not in allowed]
        if missing:
            raise ValueError('Generic plugin ' + plugin['id']
                             + ' depends on non-generic ' + ', '.join(missing))
    return [plugin for plugin in plugins if plugin['id'] in allowed]


def execute_plugin(command, timeout, input_data=None):
    process = subprocess.Popen(command, stdin=subprocess.PIPE if input_data is not None else None,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, start_new_session=True,
                               env={**os.environ, 'PYTHONPATH': os.pathsep.join(filter(
                                   None, (str(PLUGIN_DIR.parent), os.environ.get('PYTHONPATH'))))})

    def stop():
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            return process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            return process.communicate()

    try:
        stdout, stderr = process.communicate(input=input_data, timeout=timeout)
    except subprocess.TimeoutExpired:
        stdout, stderr = stop()
        raise subprocess.TimeoutExpired(command, timeout, output=stdout, stderr=stderr)
    except BaseException:
        stop()
        raise
    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)


def read_device_file(path):
    try:
        return Path(path).read_text(errors='replace').strip('\x00\n ') or None
    except OSError:
        return None


def device_identity():
    return {
        'serial': (read_device_file('/proc/device-tree/serial-number')
                   or read_device_file('/sys/firmware/devicetree/base/serial-number')),
        'model': (read_device_file('/proc/device-tree/model')
                  or read_device_file('/sys/firmware/devicetree/base/model')),
    }


def validate_parameters(plugin, values):
    if not isinstance(values, dict):
        raise ValueError('Plugin parameters must be a JSON object')
    fields = {field['name']: field for field in plugin.get('inputs', [])}
    unknown = set(values) - set(fields)
    if unknown:
        raise ValueError('Unknown plugin parameters: ' + ', '.join(sorted(unknown)))
    validated = {}
    for name, field in fields.items():
        if any(values.get(key, fields.get(key, {}).get('default')) != expected
               for key, expected in field.get('when', {}).items()):
            if name in values:
                raise ValueError(f'Parameter {name} is not applicable')
            continue
        value = values.get(name, field.get('default'))
        if value is None or value == '':
            if field.get('required', False):
                raise ValueError(f'Missing plugin parameter: {name}')
            continue
        kind = field['type']
        if kind == 'integer':
            if type(value) is not int:
                raise ValueError(f'Parameter {name} must be an integer')
            if value < field.get('minimum', value) or value > field.get('maximum', value):
                raise ValueError(f'Parameter {name} is out of range')
        elif kind == 'boolean':
            if type(value) is not bool:
                raise ValueError(f'Parameter {name} must be a boolean')
        elif not isinstance(value, str):
            raise ValueError(f'Parameter {name} must be text')
        if kind == 'choice' and value not in field['choices']:
            raise ValueError(f'Invalid choice for {name}')
        if kind in ('text', 'secret') and 'pattern' in field and not re.fullmatch(field['pattern'], value):
            raise ValueError(f'Invalid value for {name}')
        validated[name] = value
    return validated


def diagnostic_metadata(plugin):
    metadata = {field: deepcopy(value) for field, value in plugin.items()
                if field != '_script'}
    for field in metadata.get('inputs', []):
        if field['type'] == 'secret' and 'default' in field:
            field['default'] = '[redacted]'
    return metadata


def redact(value, secrets):
    if isinstance(value, str):
        for secret in secrets:
            for form in {secret, json.dumps(secret, ensure_ascii=True)[1:-1]}:
                value = value.replace(form, '[redacted]')
        return value
    if isinstance(value, list):
        return [redact(item, secrets) for item in value]
    if isinstance(value, dict):
        return {redact(key, secrets): redact(item, secrets)
                for key, item in value.items()}
    return value


@contextmanager
def _write_lock(timeout=2.0):
    """Take the shared control lock for active (write) plugins."""
    with control_lock(timeout=timeout):
        yield



def run_plugin(plugin, directory=PLUGIN_DIR, checker=has_capability, allow_active=False,
               blocked=None, addon_mode=None, params=None):
    started = now()
    tick = time.monotonic()
    secrets = [str(params[field['name']]) for field in plugin.get('inputs', [])
               if field['type'] == 'secret' and params and params.get(field['name'])]
    parameters = dict(params or {})
    if addon_mode:
        parameters['mode'] = addon_mode
    for field in plugin.get('inputs', []):
        if field['type'] == 'secret' and field['name'] in parameters:
            parameters[field['name']] = '[redacted]'
    runnable, missing = available(plugin, checker)
    status = 'skip'
    exit_code = None
    value = None
    unit = None
    message = 'Missing capability: ' + ', '.join(missing)
    details = {}
    logs = [{'timestamp': started, 'source': 'runner', 'message': 'Test scheduled'}]
    if plugin['lifecycle'] == 'deprecated':
        message = 'Deprecated test is not executable'
    elif blocked:
        message = 'Blocked by dependencies: ' + ', '.join(blocked)
    elif plugin['safety'] == 'active' and not allow_active:
        message = 'Active test requires an explicit test ID; skipped by --run all'
    elif runnable:
        try:
            command = [sys.executable, str(directory / plugin['_script'])]
            if plugin['id'] == ADDON_LED_PLUGIN_ID and addon_mode:
                command.extend(['--mode', addon_mode])
            input_data = None
            if params is not None:
                encoded = json.dumps(params, ensure_ascii=False)
                if any(field['type'] == 'secret' for field in plugin.get('inputs', [])):
                    command.append('--params-stdin')
                    input_data = encoded
                else:
                    command.extend(['--params', encoded])
            if plugin['safety'] == 'active':
                with _write_lock():
                    process = execute_plugin(command, plugin['timeout_s'], input_data)
            else:
                process = execute_plugin(command, plugin['timeout_s'], input_data)
            exit_code = process.returncode
            captured_at = now()
            if process.stdout:
                logs.append({'timestamp': captured_at, 'source': 'stdout',
                             'message': process.stdout})
            if process.stderr:
                logs.append({'timestamp': captured_at, 'source': 'stderr',
                             'message': process.stderr})
            if process.returncode:
                raise ValueError(f'Plugin exited {process.returncode}: {process.stderr.strip()[:300]}')
            result = json.loads(process.stdout)
            if not isinstance(result, dict) or result.get('status') not in STATUSES:
                raise ValueError('Invalid plugin status')
            status = result['status']
            value = result.get('value')
            unit = result.get('unit')
            message = result.get('message')
            details = result.get('details', {})
            if value is not None and (type(value) not in (int, float) or not math.isfinite(value)):
                raise ValueError('Plugin value must be a finite number or null')
            if unit is not None and not isinstance(unit, str):
                raise ValueError('Plugin unit must be a string or null')
            if not isinstance(message, str) or not isinstance(details, dict):
                raise ValueError('Invalid plugin message or details')
        except subprocess.TimeoutExpired as exc:
            status, message = 'error', f'Plugin timed out after {plugin["timeout_s"]} seconds'
            value, unit, details = None, None, {}
            captured_at = now()
            for source, output in (('stdout', exc.stdout), ('stderr', exc.stderr)):
                if output:
                    logs.append({'timestamp': captured_at, 'source': source,
                                 'message': output.decode(errors='replace')
                                 if isinstance(output, bytes) else output})
        except (OSError, ValueError, json.JSONDecodeError, JobError) as exc:
            status, message = 'error', str(exc)
            value, unit, details = None, None, {}
        if status == 'error':
            value, unit = None, None
    finished = now()
    logs.append({'timestamp': finished, 'source': 'runner',
                 'message': f'{status}: {message}'})
    return {
        'id': plugin['id'], 'station': plugin['station'], 'clients': plugin['clients'],
        'name': plugin['name'], 'generic': plugin.get('generic', False),
        'author': plugin['author'], 'lifecycle': plugin['lifecycle'],
        'plugin_version': plugin['version'], 'plugin': diagnostic_metadata(plugin),
        'parameters': parameters, 'exit_code': exit_code,
        'result': status, 'status': status, 'value': value, 'unit': unit,
        'message': redact(message, secrets),
        'details': redact(details, secrets), 'logs': redact(logs, secrets),
        'started_at': started, 'finished_at': finished,
        'duration_ms': round((time.monotonic() - tick) * 1000),
    }


def run_suite(plugins, selected='all', client='tui', directory=PLUGIN_DIR,
              checker=has_capability, client_id=None,
              addon_mode=None, params=None):
    if client not in CLIENTS:
        raise ValueError(f'Unknown client: {client}')
    if generic_host():
        if selected == 'all':
            plugins = generic_plugins(plugins)
        else:
            target = next((plugin for plugin in plugins if plugin['id'] == selected), None)
            if target is not None and not target.get('generic', False):
                raise ValueError(f'{selected} is not supported on {platform.machine()}')
    chosen = execution_order(plugins, selected)
    if selected != 'all' and client not in chosen[-1]['clients']:
        raise ValueError(f'{selected} is not available to {client}')
    if selected == 'all':
        chosen = [plugin for plugin in chosen
                  if plugin.get('batch', True) and client in plugin['clients']]
    started = now()
    results = []
    by_id = {}
    for plugin in chosen:
        blocked = [f'{name} ({by_id[name]["result"]})' for name in plugin['depends_on']
                   if by_id[name]['result'] != 'pass']
        item = run_plugin(plugin, directory, checker,
                          allow_active=selected == plugin['id'], blocked=blocked,
                          addon_mode=addon_mode,
                          params=params if selected == plugin['id'] else None)
        results.append(item)
        by_id[plugin['id']] = item
    counts = {status: sum(result['status'] == status for result in results)
              for status in STATUSES}
    outcome = ('incomplete' if not results else
               'fail' if counts['fail'] else
               'incomplete' if counts['error'] or counts['skip'] or counts['needs_review'] else
               'pass')
    return {
        'schema_version': SCHEMA_VERSION, 'run_id': str(uuid.uuid4()),
        'tui_version': PACKAGE_VERSION,
        'client': client, 'client_id': client_id, 'selected': selected,
        'device': device_identity(), 'started_at': started,
        'finished_at': now(), 'outcome': outcome, 'summary': counts, 'results': results,
    }



def write_acceptance(report, report_path):
    """Best-effort per-SN acceptance index next to the saved report."""
    identity = dict(board_identity())
    device = report.get('device') or {}
    identity['model'] = device.get('model', '')
    if not identity.get('serial'):
        identity['serial'] = device.get('serial') or ''
        identity['serialSource'] = 'report-device' if identity['serial'] else ''
    results = []
    for item in report.get('results') or []:
        plugin = item.get('plugin') or {}
        criteria = {key: plugin.get(key) for key in
                    ('id', 'version', 'documentation', 'requires')}
        results.append({
            'id': item.get('id'), 'result': item.get('result'),
            'message': item.get('message'),
            'plugin_version': item.get('plugin_version'),
            'duration_ms': item.get('duration_ms'),
            'criteriaHash': hashlib.sha256(
                json.dumps(criteria, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
        })
    payload = {
        'tuiVersion': report.get('tui_version'),
        'outcome': report.get('outcome'),
        'summary': report.get('summary'),
        'selected': report.get('selected'),
        'client': report.get('client'),
        'client_id': report.get('client_id'),
        'results': results,
        'reportPath': str(report_path),
        'reportSha256': hashlib.sha256(Path(report_path).read_bytes()).hexdigest(),
    }
    root = (REPORT_DIR.parent if REPORT_DIR.name == 'reports' else REPORT_DIR) / 'acceptance'
    return acceptance.write(root, 'plugin',
                            report.get('run_id', ''), identity, payload,
                            report.get('finished_at'))


def save_report(report, directory=REPORT_DIR):
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if directory.is_symlink() or directory.stat().st_mode & 0o077:
        raise OSError(f'Report directory is not private: {directory}')
    name = report['started_at'].replace(':', '').replace('-', '')[:15] + '_' + report['run_id'] + '.json'
    target = directory / name
    temp_name = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=directory,
                                         prefix='.report-', delete=False) as stream:
            temp_name = stream.name
            os.fchmod(stream.fileno(), 0o600)
            json.dump(report, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, target)
        return target
    finally:
        if temp_name and os.path.exists(temp_name):
            os.unlink(temp_name)


def call_record(action, client, client_id, *, selected=None, outcome=None, report_path=None):
    return {
        'schema_version': SCHEMA_VERSION, 'run_id': str(uuid.uuid4()),
        'tui_version': PACKAGE_VERSION,
        'started_at': now(), 'action': action, 'client': client,
        'client_id': client_id, 'selected': selected, 'outcome': outcome,
        'report_path': str(report_path) if report_path else None,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument('--list', action='store_true', help='list installed tests as JSON')
    actions.add_argument('--run', metavar='ID', help='run one test or all tests')
    parser.add_argument('--env', choices=('tui', 'csharp'), default='tui',
                        help='record the calling client; does not bypass capability checks')
    parser.add_argument('--include-all', action='store_true',
                        help='list all plugin metadata for the TUI developer view')
    parser.add_argument('--client-id', help='caller identifier, such as a production PC name')
    parser.add_argument('--no-save', action='store_true', help='do not archive the report')
    parser.add_argument('--motor-config', help='absolute robot.yaml path for RP025')
    parser.add_argument('--addon-mode', choices=('demo', 'red', 'green', 'blue', 'off'),
                        help='LED pattern for RP018')
    parser.add_argument('--params', help='JSON object of declared plugin input values')
    parser.add_argument('--params-stdin', action='store_true',
                        help='read declared plugin input values as JSON from stdin')
    args = parser.parse_args(argv)
    try:
        if args.include_all and (not args.list or args.env != 'tui'):
            raise ValueError('--include-all is only valid with --env tui --list')
        if args.client_id and not CLIENT_ID_PATTERN.fullmatch(args.client_id):
            raise ValueError('Client ID must be 1..64 letters, digits, dots, underscores, colons or hyphens')
        has_params = args.params is not None or args.params_stdin
        if args.motor_config:
            if args.run != MOTOR_PLUGIN_ID or has_params:
                raise ValueError('--motor-config requires --run RP025 without --params')
            args.params = json.dumps({'config': args.motor_config})
            has_params = True
        if args.run == MOTOR_PLUGIN_ID and not has_params:
            raise ValueError('RP025 requires --motor-config ABSOLUTE_PATH or --params with config')
        if args.run == ADDON_LED_PLUGIN_ID and not args.addon_mode and not has_params:
            raise ValueError('RP018 requires --addon-mode demo|red|green|blue|off')
        if args.run != ADDON_LED_PLUGIN_ID and args.addon_mode:
            raise ValueError('--addon-mode is only valid with --run RP018')
        plugins = load_plugins()
        if args.params is not None and args.params_stdin:
            raise ValueError('Use only one parameter transport')
        if args.params is not None or args.params_stdin:
            if args.run in (None, 'all'):
                raise ValueError('--params requires a single test ID')
            selected_plugin = next((plugin for plugin in plugins if plugin['id'] == args.run), None)
            if selected_plugin is None:
                raise ValueError(f'Unknown test: {args.run}')
            if args.params is not None and any(field['type'] == 'secret'
                                               for field in selected_plugin['inputs']):
                raise ValueError('Secret parameters require --params-stdin')
            params = validate_parameters(
                selected_plugin, json.loads(sys.stdin.read() if args.params_stdin else args.params))
        else:
            params = None
        if args.list:
            if not args.no_save:
                save_report(call_record('list', args.env, args.client_id), REPORT_DIR / 'calls')
            listed = plugins
            if generic_host() and not args.include_all:
                listed = generic_plugins(plugins)
            print(json.dumps(catalog(listed, client=args.env,
                                     include_all=args.include_all), ensure_ascii=False))
            return 0
        report = run_suite(plugins, args.run, args.env, client_id=args.client_id,
                           addon_mode=args.addon_mode,
                           params=params)
        if not args.no_save:
            path = save_report(report, REPORT_DIR)
            save_report(call_record('run', args.env, args.client_id, selected=args.run,
                                    outcome=report['outcome'], report_path=path),
                        REPORT_DIR / 'calls')
            try:
                write_acceptance(report, path)
            except Exception as exc:
                logger.warning('acceptance record failed: %s', exc)
            print(f'Saved report: {path}', file=sys.stderr)
        print(json.dumps(report, ensure_ascii=False))
        return 0
    except (OSError, ValueError) as exc:
        print(f'runner: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
