# SPDX-License-Identifier: GPL-3.0
# Copyright (C) 2026 wentywenty

"""Static plugin checks must not execute board probes."""

from pathlib import Path
import tempfile
import unittest

from loader.plugin_validator import check_plugins
from loader.plugin_catalog import load_plugins


ROOT = Path(__file__).resolve().parents[2]


class PluginFormatTests(unittest.TestCase):
    def test_documentation_requires_title_and_criterion(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / '001-fixture.py'
            base = {
                'id': 'RP001', 'station': ['robopi2'], 'clients': ['tui'],
                'author': 'wentywenty', 'name': 'Fixture', 'version': '1',
                'lifecycle': 'testing', 'safety': 'passive',
            }
            for value in (None, {}, {'title': 'Fixture'},
                          {'title': 'Fixture', 'criterion': '   '},
                          {'title': 'Fixture', 'criterion': 'works', 'extra': 'no'}):
                with self.subTest(documentation=value):
                    metadata = {**base, 'documentation': value}
                    path.write_text(f'PLUGIN = {metadata!r}\n', encoding='utf-8')
                    with self.assertRaisesRegex(ValueError, 'documentation'):
                        load_plugins(Path(temp))

    def test_repository_plugins_and_example(self):
        self.assertEqual(len(check_plugins(ROOT / 'plugins')), 58)
        example = check_plugins(ROOT / 'loader' / 'examples')
        self.assertEqual([plugin['id'] for plugin in example], ['RP057'])
        self.assertEqual(example[0]['lifecycle'], 'testing')

    def test_unknown_field_is_rejected_without_running_plugin(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / '001-fixture.py'
            path.write_text(
                "PLUGIN = {'id': 'RP001', 'station': ['robopi2'], "
                "'clients': ['tui'], 'author': 'wentywenty', "
                "'name': 'Fixture', 'documentation': {'title': 'Fixture', "
                "'criterion': 'Fixture criterion'}, 'version': '1', 'lifecycle': 'testing', "
                "'safety': 'passive', 'typo': True}\n"
                "raise RuntimeError('must not execute')\n"
                "if __name__ == '__main__':\n    pass\n")
            with self.assertRaisesRegex(ValueError, 'unknown PLUGIN fields: typo'):
                check_plugins(temp)

    def test_missing_entrypoint_and_empty_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(ValueError, 'No plugins found'):
                check_plugins(temp)
            path = Path(temp) / '001-fixture.py'
            path.write_text(
                "PLUGIN = {'id': 'RP001', 'station': ['robopi2'], "
                "'clients': ['tui'], 'author': 'wentywenty', "
                "'name': 'Fixture', 'documentation': {'title': 'Fixture', "
                "'criterion': 'Fixture criterion'}, 'version': '1', 'lifecycle': 'testing', "
                "'safety': 'passive'}\n")
            with self.assertRaisesRegex(ValueError, 'missing if __name__'):
                check_plugins(temp)


if __name__ == '__main__':
    unittest.main()
