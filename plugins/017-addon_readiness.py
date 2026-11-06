#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0
# Copyright (C) 2026 wentywenty

"""Check that the addon package, LED device and GPIO controllers are present."""

import json
from pathlib import Path
import shutil
import subprocess

from loader.plugin_helpers import result


PLUGIN = {
    'id': 'RP017',
    'station': ['robopi2', 'rdkx5', 'opi5pro'],
    'clients': ['tui', 'csharp'],
    'author': 'wentywenty',
    'name': 'RoboPi addon readiness',
    'version': '1.1',
    'lifecycle': 'testing',
    'safety': 'passive',
    'category': 'Addon',
    'requires': [],
    'timeout_s': 8,
    'documentation': {
        'title': 'RoboPi Addon 就绪状态',
        'criterion': '`robopi-addon`、WS2812 设备/工具、`gpiochip0` 和 `gpiochip1` 均存在。',
    },
}


def installed():
    completed = subprocess.run(
        ['dpkg-query', '-W', '-f=${db:Status-Abbrev}', 'robopi-addon'],
        capture_output=True, text=True, timeout=3, check=False)
    return completed.returncode == 0 and completed.stdout.startswith('ii ')


def probe(package=installed, device=Path('/dev/robopi-ws2812'),
          gpio_root=Path('/dev'), command=None):
    try:
        present = package()
    except (OSError, subprocess.SubprocessError) as exc:
        return result('error', f'Could not inspect robopi-addon: {exc}')
    command = shutil.which('robopi-ws2812') if command is None else command
    device_present = device.is_char_device()
    details = {'package_installed': present, 'led_device': device_present,
               'led_command': command,
               'gpiochips': [str(gpio_root / name) for name in ('gpiochip0', 'gpiochip1')
                             if (gpio_root / name).is_char_device()]}
    if not present and not device_present and not command:
        return result('skip', 'Addon package and WS2812 hardware are not present on this board',
                      details=details)
    ready = (present and device_present and bool(command)
             and len(details['gpiochips']) == 2)
    return result('pass' if ready else 'fail',
                  'Addon prerequisites are present' if ready else 'Addon prerequisites are missing',
                  details=details)


if __name__ == '__main__':
    print(json.dumps(probe(), ensure_ascii=False))
