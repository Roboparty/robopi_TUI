#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0
# Copyright (C) 2026 wentywenty

"""Flash the packaged EtherCANFD firmware with roboparty-analyze's tool."""

import json


PLUGIN = {
    'id': 'RP024',
    'station': ['robopi2', 'rdkx5', 'opi5pro'],
    'clients': ['tui'],
    'author': 'wentywenty',
    'name': 'Flash HPM EtherCANFD',
    'version': '1.1',
    'lifecycle': 'testing',
    'safety': 'active',
    'category': 'Interfaces',
    'confirm': 'Erase and write HPM flash using the packaged EtherCANFD image?',
    'requires': [],
    'depends_on': ['RP001'],
    'timeout_s': 3300,
    'documentation': {
        'title': 'HPM EtherCANFD 刷写',
        'criterion': '板端安装 analyze、选择包内固件并刷写，正常 USB 连续两次出现且 HPM 的 can0–can3 全部识别；不验证 Flash 字节或电机通信。',
    },
}


def probe():
    from loader.task_runtime import run_for_tui
    return run_for_tui('hpm-flash')


if __name__ == '__main__':
    print(json.dumps(probe(), ensure_ascii=False))
