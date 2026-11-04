#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0
# Copyright (C) 2026 wentywenty

"""Provision the whole-machine QR/SN and optionally derive a device key.

Run this only during manufacturing/provisioning. The SN must be treated as a
high-entropy secret; a human-readable serial is not safe key material.
"""

import argparse
import base64
import json
import os
from pathlib import Path
import secrets
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from loader import software_sn


def write_private(path, data):
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(data)
    os.chmod(path, 0o600)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--generate', action='store_true', help='generate a random 256-bit SN')
    source.add_argument('--sn', help='use an existing QR/SN value')
    parser.add_argument('--path', default=str(software_sn.SN_PATH),
                        help='SN file (default /etc/roboparty/robot-sn)')
    parser.add_argument('--purpose', default='device-key',
                        help='HKDF purpose (default device-key)')
    parser.add_argument('--key-out', default='',
                        help='optional raw 32-byte key output (0600)')
    args = parser.parse_args(argv)

    if args.generate:
        sn = base64.b32encode(secrets.token_bytes(32)).decode('ascii').rstrip('=')
    else:
        try:
            sn = software_sn.validate_sn(args.sn)
        except ValueError as exc:
            parser.error(str(exc))
    sn = software_sn.validate_sn(sn)

    write_private(args.path, (sn + '\n').encode('utf-8'))
    key = software_sn.derive_key(sn, args.purpose)
    if args.key_out:
        write_private(args.key_out, key)

    print(json.dumps({
        'sn': sn,
        'sn_path': str(args.path),
        'qr_payload': sn,
        'key_algorithm': 'HKDF-SHA256',
        'key_purpose': args.purpose,
        'key_id': software_sn.key_id(key),
        'key_out': str(args.key_out) if args.key_out else None,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
