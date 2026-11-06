# SPDX-License-Identifier: GPL-3.0
# Copyright (C) 2026 wentywenty

"""Whole-machine software SN and key derivation tests."""

from pathlib import Path
import runpy
import tempfile
import unittest

from loader import software_sn

ROOT = Path(__file__).resolve().parents[2]
PLUGIN = runpy.run_path(str(ROOT / 'plugins' / '058-whole_machine_sn.py'))


class SoftwareSnTests(unittest.TestCase):
    def test_rfc5869_hkdf_vector(self):
        ikm = bytes([0x0b]) * 22
        salt = bytes(range(13))
        info = bytes(range(0xf0, 0xfa))
        self.assertEqual(
            '3cb25f25faacd57a90434f64d0362f2a2d2d0a90cf1a5a4c5db02d56ecc4c5bf'
            '34007208d5b887185865',
            software_sn.hkdf_sha256(ikm, salt, info, 42).hex())

    def test_key_derivation_is_deterministic_and_purpose_separated(self):
        sn = 'RPSN-ABCDEF0123456789'
        first = software_sn.derive_key(sn, 'device-key')
        second = software_sn.derive_key(sn, 'device-key')
        other = software_sn.derive_key(sn, 'pairing')
        self.assertEqual(first, second)
        self.assertNotEqual(first, other)
        self.assertEqual(32, len(first))
        self.assertEqual(16, len(software_sn.key_id(first)))

    def test_sn_validation(self):
        with self.assertRaises(ValueError):
            software_sn.validate_sn('short')
        self.assertEqual('RPSN-ABCDEF0123456789',
                         software_sn.validate_sn(' RPSN-ABCDEF0123456789\x00 '))


class PluginTests(unittest.TestCase):
    def test_valid_sn_passes_and_returns_key_id(self):
        result = PLUGIN['probe'](sn='RPSN-ABCDEF0123456789')
        self.assertEqual('pass', result['status'])
        self.assertEqual('RPSN-ABCDEF0123456789', result['value'])
        self.assertEqual('HKDF-SHA256', result['details']['key_algorithm'])
        self.assertNotIn('key', result['details'])
        self.assertEqual(16, len(result['details']['key_id']))

    def test_sn_is_read_from_file(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'robot-sn'
            path.write_text('RPSN-FILE-SN-0123456789\n')
            result = PLUGIN['probe'](path=path)
        self.assertEqual('pass', result['status'])
        self.assertEqual('RPSN-FILE-SN-0123456789', result['value'])

    def test_missing_or_short_sn_needs_review(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'robot-sn'
            self.assertEqual('needs_review', PLUGIN['probe'](path=path)['status'])
            path.write_text('short')
            self.assertEqual('needs_review', PLUGIN['probe'](path=path)['status'])


if __name__ == '__main__':
    unittest.main()
