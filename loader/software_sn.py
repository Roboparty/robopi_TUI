# SPDX-License-Identifier: GPL-3.0
# Copyright (C) 2026 wentywenty

"""Whole-machine software SN and deterministic key derivation.

The SN is the value carried by the machine's QR label. It must be provisioned
as a high-entropy secret; a human-readable serial is not sufficient as key
material. Keys are derived with HKDF-SHA256 and are never printed by the TUI
plugin.
"""

from __future__ import annotations

import hashlib
import hmac
from pathlib import Path

SN_PATH = Path('/etc/roboparty/robot-sn')
MIN_SN_LENGTH = 16
SALT = b'roboparty-software-sn-v1'


def read_sn(path=SN_PATH) -> str:
    try:
        value = Path(path).read_text(encoding='utf-8', errors='replace')
    except OSError:
        return ''
    return value.replace('\x00', '').strip()


def validate_sn(sn: str) -> str:
    value = str(sn or '').replace('\x00', '').strip()
    if len(value) < MIN_SN_LENGTH:
        raise ValueError(f'SN must contain at least {MIN_SN_LENGTH} characters')
    if any(ord(ch) < 0x20 or ord(ch) == 0x7f for ch in value):
        raise ValueError('SN must not contain control characters')
    return value


def hkdf_sha256(ikm: bytes, salt: bytes, info: bytes, length: int = 32) -> bytes:
    if length <= 0 or length > 255 * 32:
        raise ValueError('invalid HKDF length')
    prk = hmac.new(salt, ikm, hashlib.sha256).digest()
    output = bytearray()
    block = b''
    counter = 1
    while len(output) < length:
        block = hmac.new(prk, block + info + bytes([counter]), hashlib.sha256).digest()
        output.extend(block)
        counter += 1
    return bytes(output[:length])


def derive_key(sn: str, purpose: str = 'device-key', length: int = 32) -> bytes:
    value = validate_sn(sn)
    info = purpose.encode('utf-8')
    return hkdf_sha256(value.encode('utf-8'), SALT, info, length)


def key_id(key: bytes) -> str:
    return hashlib.sha256(key).hexdigest().upper()[:16]


def derive_key_id(sn: str, purpose: str = 'device-key') -> str:
    return key_id(derive_key(sn, purpose))
