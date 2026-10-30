import json
import os
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from loader import board_log_cleanup as logs


class LegacyLogCleanupTests(unittest.TestCase):
    def session(self, root, complete=True, malformed=False):
        folder = root / 'seven-20261026-085717'
        folder.mkdir(parents=True)
        data = dict(started_at_unix=1, capture_service='usbcan-capture.service')
        if complete:
            data['ended_at_unix'] = 2
        text = json.dumps(data, indent=2)
        if malformed:
            del data['ended_at_unix']
            text = json.dumps(data, indent=2) + '\n, "ended_at_unix": 2\n}'
        (folder / 'manifest.json').write_text(text)
        (folder / 'can-details.jsonl').write_bytes(b'historical CAN diagnostics\n')
        return folder

    def test_archives_closed_legacy_and_mirror_without_touching_current_logging(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            roots = [root / 'var-log', root / 'var-log-hdd']
            sessions = [self.session(path, malformed=True) for path in roots]
            current = self.session(root / 'home-logs')
            result = logs.cleanup(lambda *_: None, roots, root / 'archives', lambda: 0, lambda: set())
            self.assertEqual(2, len(result['removed']))
            self.assertTrue(all(not folder.exists() for folder in sessions))
            self.assertTrue(current.exists())
            with tarfile.open(result['archive']) as archive:
                self.assertEqual(b'historical CAN diagnostics\n', archive.extractfile(
                    'root-0/seven-20261026-085717/can-details.jsonl').read())

    def test_open_or_incomplete_session_is_preserved(self):
        for opened in (True, False):
            with self.subTest(opened=opened), tempfile.TemporaryDirectory() as temp:
                root = Path(temp).resolve()
                session = self.session(root / 'logs', complete=opened)
                active = {session / 'can-details.jsonl'} if opened else set()
                result = logs.cleanup(lambda *_: None, [root / 'logs'], root / 'archives', lambda: 0, lambda: active)
                self.assertTrue(session.exists())
                self.assertFalse(result['removed'])
                self.assertIsNone(result['archive'])

    def test_archive_failure_never_deletes_source(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            session = self.session(root / 'logs')
            with patch.object(logs.tarfile, 'open', side_effect=OSError('archive failed')):
                with self.assertRaisesRegex(OSError, 'archive failed'):
                    logs.cleanup(lambda *_: None, [root / 'logs'], root / 'archives', lambda: 0, lambda: set())
            self.assertTrue(session.exists())

    def test_session_changed_during_archive_is_preserved(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            session = self.session(root / 'logs')
            count = 0
            def scan():
                nonlocal count
                count += 1
                if count == 2:
                    (session / 'can-details.jsonl').write_text('changed')
                return set()
            with self.assertRaisesRegex(RuntimeError, '发生变化'):
                logs.cleanup(lambda *_: None, [root / 'logs'], root / 'archives', lambda: 0, scan)
            self.assertTrue(session.exists())

    @unittest.skipUnless(os.name == 'posix', 'Linux symlink behavior')
    def test_symlink_session_or_contents_are_not_followed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            session = self.session(root / 'logs')
            outside = root / 'configuration'
            outside.write_text('keep')
            (session / 'outside').symlink_to(outside)
            result = logs.cleanup(lambda *_: None, [root / 'logs'], root / 'archives', lambda: 0, lambda: set())
            self.assertTrue(session.exists())
            self.assertEqual('keep', outside.read_text())
            self.assertFalse(result['removed'])

    def test_enough_space_does_not_scan_archive_or_delete(self):
        with patch.object(logs, 'open_paths', side_effect=AssertionError('no scan expected')):
            result = logs.cleanup(lambda *_: None, free=lambda: logs.TARGET_FREE)
        self.assertFalse(result['removed'])
        self.assertIsNone(result['archive'])


if __name__ == '__main__':
    unittest.main()
