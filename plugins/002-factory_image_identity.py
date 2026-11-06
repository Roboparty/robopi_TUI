#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0
# Copyright (C) 2026 wentywenty

"""Validate the immutable image factory snapshot for the running board."""

import json
import os
from pathlib import Path
import re
import shlex
import uuid

from loader.board_context import board_id, normalize_board
from loader.plugin_helpers import result


PLUGIN = {
    'id': 'RP002',
    'station': ['robopi2', 'rdkx5', 'opi5pro'],
    'clients': ['tui', 'csharp'],
    'author': 'wentywenty',
    'name': 'Factory image identity',
    'version': '1.1',
    'lifecycle': 'testing',
    'safety': 'passive',
    'category': 'System',
    'requires': [],
    'timeout_s': 5,
    'documentation': {
        'title': '镜像出厂身份',
        'criterion': '动态核对 `/etc/armbian-image-release`：BOARD 与实板一致、VENDOR 为 roboparty-os、VERSION=REVISION、KERNEL_TARGET 含当前内核变体、commit/UUID 合法；若存在 `/etc/roboparty/factory-image.json` 则逐字段对照。',
    },
}


RELEASE = Path('/etc/armbian-image-release')
BOARD_RELEASE = Path('/etc/armbian-release')
MODEL = Path('/proc/device-tree/model')
APPROVED = Path('/etc/roboparty/factory-image.json')
REQUIRED = ('BOARD', 'VENDOR', 'VERSION', 'REVISION', 'KERNEL_TARGET',
            'BUILD_REPOSITORY_COMMIT', 'IMAGE_UUID', 'EXTENSIONS')


def read_fields(path):
    fields = {}
    for line in Path(path).read_text(encoding='utf-8').splitlines():
        key, separator, raw = line.partition('=')
        key = key.strip()
        if not separator or not key or key.startswith('#'):
            continue
        try:
            values = shlex.split(raw, comments=True)
        except ValueError as exc:
            raise ValueError(f'Invalid release field {key}: {exc}') from exc
        if not values:
            values = ['']
        if len(values) != 1 or key in fields:
            raise ValueError(f'Invalid or duplicate release field {key}')
        fields[key] = values[0]
    return fields


def kernel_variant(release):
    release = str(release).lower()
    for variant in ('vendor-rt', 'vendor', 'current', 'edge', 'legacy'):
        if variant in release:
            return variant
    return ''


def load_manifest(path, board):
    if path is None:
        return None
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return None
    if not isinstance(data, dict):
        raise ValueError('Approved image manifest must be a JSON object')
    for key in ('by_board', 'boards'):
        if isinstance(data.get(key), dict):
            expected = data[key].get(board)
            return expected if isinstance(expected, dict) else None
    return data


def probe(release=RELEASE, board_release=BOARD_RELEASE, model=MODEL,
          manifest=APPROVED, uname=os.uname):
    try:
        snapshot = read_fields(release)
    except OSError as exc:
        return result('fail', f'Cannot read factory image identity: {exc}')
    except ValueError as exc:
        return result('error', f'Invalid factory image identity: {exc}')
    try:
        board, source = board_id(board_release, model)
        approved = load_manifest(manifest, board)
    except (OSError, ValueError) as exc:
        return result('error', f'Could not load the approved image identity: {exc}')

    checks = []
    mismatches = {}

    def check(name, expected, actual, ok=None):
        passed = actual == expected if ok is None else ok
        row = {'check': name, 'expected': expected, 'actual': actual, 'ok': passed}
        checks.append(row)
        if not passed:
            mismatches[name] = {'expected': expected, 'actual': actual}

    if approved is not None:
        mode = 'approved-manifest'
        for key in REQUIRED:
            if key in approved:
                check(key, approved[key], snapshot.get(key))
        check('BOARD', board, normalize_board(snapshot.get('BOARD', '')),
              normalize_board(snapshot.get('BOARD', '')) == board)
    else:
        mode = 'consistency'
        for key in REQUIRED:
            check(key, 'present', snapshot.get(key), key in snapshot)
        if not mismatches:
            check('BOARD', board, normalize_board(snapshot['BOARD']),
                  normalize_board(snapshot['BOARD']) == board)
            check('VENDOR', 'roboparty-os', snapshot['VENDOR'])
            check('REVISION', snapshot['VERSION'], snapshot['REVISION'])
            variant = kernel_variant(uname().release)
            targets = [item.strip() for item in snapshot['KERNEL_TARGET'].split(',')]
            check('KERNEL_TARGET', f'contains {variant}' if variant else 'any',
                  snapshot['KERNEL_TARGET'], not variant or variant in targets)
            commit = snapshot['BUILD_REPOSITORY_COMMIT']
            check('BUILD_REPOSITORY_COMMIT', 'git hash or d_e_a_d_b_e_e_f', commit,
                  bool(re.fullmatch(r'[0-9a-fA-F]{7,40}(?:-dirty)?', commit)
                       or commit == 'd_e_a_d_b_e_e_f'))
            try:
                uuid.UUID(snapshot['IMAGE_UUID'])
                valid_uuid = True
            except ValueError:
                valid_uuid = False
            check('IMAGE_UUID', 'UUID', snapshot['IMAGE_UUID'], valid_uuid)

    details = {'snapshot': snapshot, 'checks': checks, 'mode': mode,
               'board': board, 'board_source': source,
               'kernel': uname().release, 'mismatches': mismatches}
    return result('fail' if mismatches else 'pass',
                  'Factory image identity mismatch' if mismatches
                  else f'Factory image identity is valid for {board}',
                  details=details)


if __name__ == '__main__':
    print(json.dumps(probe(), ensure_ascii=False))
