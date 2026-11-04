#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0
# Copyright (C) 2026 wentywenty

"""Read the QR/software SN and derive its key identifier."""

import json

from loader.plugin_helpers import result
from loader.software_sn import SN_PATH, derive_key_id, read_sn, validate_sn

PLUGIN = {
    'id': 'RP058',
    'station': ['robopi2', 'rdkx5', 'opi5pro'],
    'clients': ['tui', 'csharp'],
    'author': 'wentywenty',
    'name': 'Whole-machine software SN',
    'documentation': {
        'title': '整机软件 SN',
        'criterion': '读取二维码/标签写入的 /etc/roboparty/robot-sn（至少 16 字符），派生 key_id 但不输出密钥；SN 缺失或过短时 needs_review。',
    },
    'version': '1.0',
    'lifecycle': 'testing',
    'kind': 'test',
    'generic': True,
    'category': 'System',
    'batch': False,
    'safety': 'passive',
    'requires': [],
    'depends_on': [],
    'timeout_s': 5,
}


def probe(sn=None, path=SN_PATH):
    raw = read_sn(path) if sn is None else sn
    if not raw:
        return result('needs_review',
                      'Software SN is not provisioned; write the QR value to ' + str(path))
    try:
        value = validate_sn(raw)
    except ValueError as exc:
        return result('needs_review', 'Invalid software SN: ' + str(exc),
                      value=raw, details={'source': str(path)})

    key_id = derive_key_id(value, 'device-key')
    return result('pass', 'Software SN: ' + value + ' (key_id ' + key_id + ')',
                  value=value, details={
                      'sn': value,
                      'source': str(path),
                      'key_algorithm': 'HKDF-SHA256',
                      'key_purpose': 'device-key',
                      'key_id': key_id,
                  })


if __name__ == '__main__':
    print(json.dumps(probe(), ensure_ascii=False))
