#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0
# Copyright (C) 2026 wentywenty

"""Observe new BMS communication during this check, without opening its port."""

from datetime import datetime, timezone
import json
import re
import subprocess
import time

from loader.plugin_helpers import result


PLUGIN = {
    'id': 'RP021',
    'station': ['robopi2', 'rdkx5', 'opi5pro'],
    'clients': ['tui', 'csharp'],
    'author': 'wentywenty',
    'name': 'BMS service and live data',
    'version': '1.1',
    'lifecycle': 'testing',
    'safety': 'passive',
    'category': 'BMS',
    'requires': [],
    'depends_on': ['RP020'],
    'timeout_s': 12,
    'documentation': {
        'title': 'BMS 服务与实时数据',
        'criterion': '服务正常运行且未重启，本次检测的 5 秒观察期内有新 BMS 数据且无读取失败或断开；历史数据不计入。',
    },
}


DATA_LINE = re.compile(r'\[BMS Data\]|\[CAN BMS Data\]|\[NRF PMIC\]')
FAILURE_LINE = re.compile(
    r'\bread fail(?:ure|ed)\b|\bdisconnected\b|\bwaiting for (?:BMS data|serial port)\b'
    r'|\b(?:bad CRC|CRC (?:error|mismatch)|timed? out)\b', re.IGNORECASE)
INVOCATION = re.compile(r'[0-9a-fA-F]{32}\Z')
OBSERVATION_SECONDS = 5


def service_state():
    completed = subprocess.run(
        ['systemctl', 'show', 'bms.service', '--property=ActiveState,SubState,Result,'
         'ExecMainStatus,InvocationID'], capture_output=True, text=True, timeout=2,
        check=True)
    return dict(line.split('=', 1) for line in completed.stdout.splitlines() if '=' in line)


def live_logs(invocation):
    # -n 0 starts at the tail; timeout bounds the follower and kills only journalctl.
    try:
        subprocess.run(
            ['journalctl', f'_SYSTEMD_INVOCATION_ID={invocation}', '--follow', '--lines=0',
             '--no-pager', '-o', 'json',
             '--output-fields=MESSAGE,_SYSTEMD_INVOCATION_ID,__MONOTONIC_TIMESTAMP,__REALTIME_TIMESTAMP'],
            capture_output=True, text=True, timeout=OBSERVATION_SECONDS, check=True)
    except subprocess.TimeoutExpired as exc:
        output = exc.stdout or b''
        return output.decode('utf-8') if isinstance(output, bytes) else output
    raise OSError('BMS journal observation ended before the check window completed')


def healthy(fields):
    return (fields.get('ActiveState') == 'active' and fields.get('SubState') == 'running'
            and fields.get('Result') == 'success' and fields.get('ExecMainStatus') == '0')


def probe(state=service_state, logs=live_logs, clock=time.monotonic_ns):
    details = {}
    try:
        fields = state()
        details = {key: fields.get(key, '') for key in
                   ('ActiveState', 'SubState', 'Result', 'ExecMainStatus', 'InvocationID')}
        if not healthy(details):
            return result('fail', 'bms.service is not running cleanly', details=details)
        invocation = details['InvocationID']
        if not INVOCATION.fullmatch(invocation):
            return result('fail', 'bms.service has no current invocation ID', details=details)
        started_us = clock() // 1000
        output = logs(invocation)
        finished_us = clock() // 1000
        details.update(observation_seconds=OBSERVATION_SECONDS,
                       observation_started_monotonic_us=started_us,
                       observation_finished_monotonic_us=finished_us,
                       recent_data_lines=[], failure_lines=[])
        for line in output.splitlines():
            entry = json.loads(line)
            if (not isinstance(entry, dict) or not isinstance(entry.get('MESSAGE'), str)
                    or entry.get('_SYSTEMD_INVOCATION_ID') != invocation):
                raise ValueError('Invalid BMS journal record or service invocation')
            monotonic = entry.get('__MONOTONIC_TIMESTAMP')
            realtime = entry.get('__REALTIME_TIMESTAMP')
            if (not isinstance(monotonic, str) or not monotonic.isdecimal()
                    or not isinstance(realtime, str) or not realtime.isdecimal()):
                raise ValueError('BMS journal record has no valid timestamps')
            timestamp_us = int(monotonic)
            if timestamp_us <= started_us:
                continue
            if timestamp_us > finished_us:
                raise ValueError('BMS journal timestamp is outside this check')
            message = entry['MESSAGE']
            stamp = datetime.fromtimestamp(int(realtime) / 1_000_000, timezone.utc).isoformat()
            evidence = f'{stamp} {message}'[:300]
            if FAILURE_LINE.search(message):
                details['failure_lines'].append(evidence)
            elif DATA_LINE.search(message):
                details['recent_data_lines'].append(evidence)
        details['recent_data_lines'] = details['recent_data_lines'][-5:]
        details['failure_lines'] = details['failure_lines'][-5:]
        final_state = state()
        if not healthy(final_state) or final_state.get('InvocationID') != invocation:
            details['final_service_state'] = final_state
            return result('fail', 'bms.service stopped, restarted or became unhealthy during this check',
                          details=details)
        if details['failure_lines']:
            return result('fail', 'BMS read failure or disconnection detected during this check',
                          details=details)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        return result('error', f'Could not inspect bms.service: {exc}', details=details)
    if not details['recent_data_lines']:
        return result('fail', 'No fresh BMS data received during this check (5 seconds)',
                      details=details)
    return result('pass', 'bms.service reported fresh BMS data during this check', details=details)


if __name__ == '__main__':
    print(json.dumps(probe(), ensure_ascii=False))
