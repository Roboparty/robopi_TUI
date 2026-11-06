#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0
# Copyright (C) 2026 wentywenty

"""Read adbd process and USB gadget controller state."""

import json
from pathlib import Path
import shutil
import subprocess

from loader.plugin_helpers import read_text, result


PLUGIN = {
    'id': 'RP027',
    'station': ['robopi2', 'rdkx5', 'opi5pro'],
    'clients': ['tui', 'csharp'],
    'author': 'wentywenty',
    'name': 'ADB gadget status',
    'version': '1.1',
    'lifecycle': 'testing',
    'kind': 'test',
    'category': 'Interfaces',
    'safety': 'passive',
    'requires': ['udc'],
    'timeout_s': 5,
    'documentation': {
        'title': 'ADB gadget 状态',
        'criterion': 'adbd 已安装并运行、UDC 已 configured；adbd 未安装时跳过。',
    },
}


def probe(root=Path('/sys/class/udc'), run=subprocess.run):
    states = {path.parent.name: read_text(path) for path in sorted(root.glob('*/state'))}
    if not states:
        return result('skip', 'No USB device controller found')
    try:
        process = run(['pgrep', '-a', 'adbd'], capture_output=True, text=True,
                      timeout=2, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return result('error', str(exc))
    running = process.returncode == 0 and bool(process.stdout.strip())
    connected = 'configured' in states.values()
    adbd = shutil.which('adbd')
    details = {'udc_states': states, 'adbd': process.stdout.strip(),
               'adbd_path': adbd, 'set_device': shutil.which('set_device.sh')}
    if not running and not adbd:
        return result('skip', 'adbd is not installed on this board', details=details)
    return result('pass' if running and connected else 'fail',
                  f'adbd {"running" if running else "not running"}; gadget '
                  f'{"configured" if connected else "not configured"}',
                  details=details)


if __name__ == '__main__':
    print(json.dumps(probe(), ensure_ascii=False))
