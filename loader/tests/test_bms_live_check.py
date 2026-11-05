# SPDX-License-Identifier: GPL-3.0
# Copyright (C) 2026 wentywenty

import json
import runpy
from pathlib import Path
import subprocess
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[2]
BMS = runpy.run_path(str(ROOT / 'plugins' / '021-bms_service.py'))
FIELDS = {'ActiveState': 'active', 'SubState': 'running', 'Result': 'success',
          'ExecMainStatus': '0', 'InvocationID': 'a' * 32}
DATA = '[BMS Data] Voltage: 53.336V | Current: 0A | SoC: 100% | Power: OFF'


def entry(message=DATA, timestamp=2_000_000, **changes):
    return json.dumps({'MESSAGE': message, '_SYSTEMD_INVOCATION_ID': 'a' * 32,
                       '__MONOTONIC_TIMESTAMP': str(timestamp),
                       '__REALTIME_TIMESTAMP': '1793878811435992', **changes})


def check(lines, states=None):
    state = Mock(side_effect=states or [FIELDS, FIELDS])
    observed = BMS['probe'](state=state, logs=lambda _: '\n'.join(lines),
                            clock=Mock(side_effect=[1_000_000_000, 6_000_000_000]))
    return observed


class BmsLiveCheckTests(unittest.TestCase):
    def test_old_success_followed_by_disconnect_cannot_pass(self):
        # Captured sequence: the old probe ignores newer failure/disconnect lines.
        observed = check([entry(timestamp=900_000),
                          entry('[BMS Daemon] BMS Read Failure (1/5)'),
                          entry('[BMS Daemon] Port seems disconnected. Re-opening...')])
        self.assertEqual(observed['status'], 'fail')
        self.assertEqual(observed['details']['recent_data_lines'], [])
        self.assertEqual(len(observed['details']['failure_lines']), 2)

    def test_only_new_data_within_this_check_can_pass(self):
        observed = check([entry()])
        self.assertEqual(observed['status'], 'pass')
        self.assertEqual(observed['details']['observation_seconds'], 5)
        self.assertIn(DATA, observed['details']['recent_data_lines'][0])
        for lines in ([], [entry(timestamp=900_000)], [entry(timestamp=1_000_000)],
                      [entry('[BMS Daemon] Connected to BMS.')]):
            with self.subTest(lines=lines):
                observed = check(lines)
                self.assertEqual(observed['status'], 'fail')
                self.assertEqual(observed['details']['recent_data_lines'], [])

    def test_failure_wins_even_with_new_data_in_the_same_window(self):
        for failure in ('BMS Read Failure (1/5)', 'Port seems disconnected. Re-opening...',
                        'Waiting for BMS data...', 'Waiting for serial port /dev/ttyS3',
                        'Read failed', 'Bad CRC', 'CRC mismatch', 'Read timed out'):
            for lines in ([entry(), entry('[BMS Daemon] ' + failure)],
                          [entry('[BMS Daemon] ' + failure), entry()]):
                with self.subTest(failure=failure, lines=lines):
                    self.assertEqual(check(lines)['status'], 'fail')

    def test_previous_failure_does_not_prevent_a_fresh_successful_retest(self):
        observed = check([entry('[BMS Daemon] BMS Read Failure (1/5)', timestamp=900_000), entry()])
        self.assertEqual(observed['status'], 'pass')
        self.assertEqual(observed['details']['failure_lines'], [])

    def test_stopped_restarted_or_unhealthy_service_cannot_pass(self):
        for field, value in (('ActiveState', 'inactive'), ('SubState', 'dead'),
                             ('Result', 'exit-code'), ('ExecMainStatus', '1'),
                             ('InvocationID', 'b' * 32)):
            with self.subTest(field=field):
                self.assertEqual(check([entry()], [FIELDS, {**FIELDS, field: value}])['status'], 'fail')

    def test_unhealthy_service_does_not_start_observation(self):
        for field, value in (('ActiveState', 'inactive'), ('InvocationID', '')):
            logs = Mock()
            observed = BMS['probe'](state=lambda: {**FIELDS, field: value}, logs=logs)
            self.assertEqual(observed['status'], 'fail')
            logs.assert_not_called()

    def test_malformed_foreign_or_future_journal_records_cannot_pass(self):
        for line in ('null', '[]', '{}', 'invalid JSON', entry(MESSAGE=['cached']),
                     entry(_SYSTEMD_INVOCATION_ID='b' * 32),
                     entry(__MONOTONIC_TIMESTAMP=None), entry(__REALTIME_TIMESTAMP='bad'),
                     entry(timestamp=7_000_000)):
            with self.subTest(line=line):
                self.assertEqual(check([entry(), line])['status'], 'error')

    def test_follower_starts_at_tail_and_is_bounded_without_writing_or_restarting(self):
        output = entry().encode('utf-8') + b'\n'
        with patch.object(subprocess, 'run', side_effect=subprocess.TimeoutExpired('journalctl', 5, output=output)) as run:
            self.assertEqual(BMS['live_logs']('a' * 32), output.decode('utf-8'))
        command = run.call_args.args[0]
        self.assertEqual(command[0], 'journalctl')
        self.assertIn('--lines=0', command)
        self.assertIn('--follow', command)
        self.assertIn('_SYSTEMD_INVOCATION_ID=' + 'a' * 32, command)
        self.assertNotIn('--since=-30s', command)
        self.assertEqual(run.call_args.kwargs['timeout'], 5)

    def test_journal_early_exit_nonzero_and_silent_timeout_are_not_passes(self):
        for response in (subprocess.CompletedProcess([], 0, entry(), ''),
                         subprocess.CalledProcessError(1, 'journalctl', stderr='permission denied')):
            kwargs = {'side_effect': response} if isinstance(response, Exception) else {'return_value': response}
            with patch.object(subprocess, 'run', **kwargs):
                observed = BMS['probe'](state=lambda: FIELDS)
                self.assertEqual(observed['status'], 'error')
        with patch.object(subprocess, 'run', side_effect=subprocess.TimeoutExpired('journalctl', 5)):
            self.assertEqual(BMS['probe'](state=lambda: FIELDS)['status'], 'fail')

    def test_current_state_is_rechecked_and_inspection_failures_are_errors(self):
        for error in (OSError('unavailable'), subprocess.TimeoutExpired('systemctl', 2)):
            with self.subTest(error=error):
                self.assertEqual(check([entry()], [FIELDS, error])['status'], 'error')


if __name__ == '__main__':
    unittest.main()
