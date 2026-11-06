#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0
# Copyright (C) 2026 wentywenty

"""Provision the per-unit product identity and user product selection."""

import datetime
import json
import os
from pathlib import Path
import platform
import uuid

from loader.board_context import board_id, normalize_board
from loader.plugin_helpers import atomic_write, plugin_params, result

PLUGIN = {
    'id': 'RP059',
    'station': ['robopi2', 'rdkx5', 'opi5pro'],
    'clients': ['tui'],
    'author': 'wentywenty',
    'name': 'Product identity initialization',
    'version': '1.0',
    'lifecycle': 'testing',
    'kind': 'action',
    'generic': True,
    'category': 'System',
    'batch': False,
    'safety': 'active',
    'confirm': 'Write the product identity and factory record? This should be done once on the production line.',
    'inputs': [
        {
            'name': 'product',
            'label': '产品配置',
            'type': 'choice',
            'choices': ['rpo', 'rp1', 'rp1_24dof', 'rpo-sim', 'rp1-sim'],
            'required': True,
        },
        {
            'name': 'sku',
            'label': 'SKU / 产品编号',
            'type': 'text',
            'required': False,
        },
        {
            'name': 'force',
            'label': '覆盖已有出厂身份',
            'type': 'choice',
            'choices': ['no', 'yes'],
            'default': 'no',
            'required': True,
        },
    ],
    'timeout_s': 10,
    'documentation': {
        'title': '产品身份初始化',
        'criterion': '读取板卡，校验产品与板卡兼容，生成一次性 factory_id，写 /etc/roboparty/product.json 和 /boot/config/product.json；已有身份默认拒绝覆盖。',
    },
}

PRODUCT_DOF = {'rpo': 23, 'rp1': 28, 'rp1_24dof': 24, 'rpo-sim': 23, 'rp1-sim': 28}
BOARD_PRODUCTS = {
    'rdkx5': {'rpo'},
    'robopi2': {'rp1', 'rp1_24dof'},
    'x86_64': set(PRODUCT_DOF),
}
PRODUCT_FILE = Path('/etc/roboparty/product.json')
def _factory_dir():
    boot = Path('/boot/config')
    if os.path.ismount(boot) or os.path.ismount('/boot') or boot.is_dir():
        return boot
    return None


FACTORY_DIR = _factory_dir()
TEMPLATE = Path('/usr/share/roboparty/product-template.json')
ROBOTS_DIR = Path('/opt/roboparty/share/roboparty_inference/robots')


def _board():
    value = board_id()[0]
    return value or normalize_board(platform.machine())


def _load(path):
    try:
        data = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _atomic_boot_write(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name('.' + path.name + '.tmp')
    with temporary.open('w', encoding='utf-8') as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def _existing_generation(directory):
    if directory is None:
        return 0
    data = _load(Path(directory) / 'product.json')
    if data and data.get('provisioned') and isinstance(data.get('generation'), int):
        return data['generation']
    return 0


def probe(product, sku='', force='no', board=None, product_file=PRODUCT_FILE,
          factory_dir=FACTORY_DIR, template=TEMPLATE, robots_dir=ROBOTS_DIR,
          require_root=True, now=None):
    if require_root and os.geteuid() != 0:
        return result('fail', 'Root privileges required for product initialization')
    product = str(product or '').strip()
    if product not in PRODUCT_DOF:
        return result('error', f'Unsupported product: {product}')
    board = normalize_board(board or _board())
    if board in ('x8664', 'amd64'):
        board = 'x86_64'
    allowed = BOARD_PRODUCTS.get(board)
    if allowed is None:
        return result('error', f'Unsupported board: {board}')
    if product not in allowed:
        return result('needs_review', f'Product {product} is not supported on board {board}',
                      details={'board': board, 'product': product})
    if board != 'x86_64' and not (Path(robots_dir) / product / 'robot.yaml').is_file():
        return result('needs_review', f'Robot profile missing: {Path(robots_dir) / product / "robot.yaml"}')

    generation = _existing_generation(factory_dir)
    if generation and str(force).lower() not in ('yes', 'true', '1'):
        return result('needs_review', 'Factory identity already provisioned; set force=yes to replace',
                      details={'generation': generation})

    clock = now or datetime.datetime.now(datetime.timezone.utc)
    template_data = _load(template) or {}
    identity = {
        **template_data,
        'schema': 1,
        'generation': generation + 1,
        'provisioned': True,
        'board': board,
        'product': product,
        'profile': product,
        'sku': str(sku or '').strip(),
        'dof': PRODUCT_DOF[product],
        'factory_id': uuid.uuid4().hex,
        'provisioned_at': clock.isoformat(),
    }
    selected = {
        'schema': 1,
        'board': board,
        'product': product,
        'profile': product,
        'dof': PRODUCT_DOF[product],
        'sku': identity['sku'],
    }
    try:
        atomic_write(Path(product_file), json.dumps(selected, ensure_ascii=False, sort_keys=True) + '\n')
        if factory_dir is not None:
            _atomic_boot_write(Path(factory_dir) / 'product.json',
                               json.dumps(identity, ensure_ascii=False, sort_keys=True) + '\n')
    except OSError as exc:
        return result('error', f'Could not write product identity: {exc}')
    return result('pass', f'Product initialized: {product} on {board}',
                  details={'generation': identity['generation'], 'factory_id': identity['factory_id'],
                           'product_file': str(product_file),
                           'factory_file': str(Path(factory_dir) / 'product.json') if factory_dir else None})


if __name__ == '__main__':
    print(json.dumps(probe(**plugin_params()), ensure_ascii=False))
