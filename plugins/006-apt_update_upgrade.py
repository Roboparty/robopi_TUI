#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0
# Copyright (C) 2026 wentywenty

"""Run apt-get update followed by apt-get upgrade."""

import json


PLUGIN = {
    'id': 'RP006',
    'station': ['robopi2', 'rdkx5', 'opi5pro'],
    'clients': ['tui'],
    'author': 'wentywenty',
    'name': 'APT update and upgrade',
    'version': '1.1',
    'lifecycle': 'testing',
    'safety': 'active',
    'category': 'System',
    'confirm': 'Run apt-get update and upgrade on this board?',
    'requires': ['apt'],
    'timeout_s': 3100,
    'documentation': {
        'title': 'APT 更新与升级',
        'criterion': '板端持久任务完成普通升级，并对比 base/bms/analyze/addon/TUI 五个包的源版本与实际版本。',
    },
}


def probe():
    from loader.task_runtime import run_for_tui
    return run_for_tui('apt-upgrade')


if __name__ == '__main__':
    print(json.dumps(probe(), ensure_ascii=False))
