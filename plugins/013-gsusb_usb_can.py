#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0
# Copyright (C) 2026 wentywenty

"""Check that the RoboPi USB-CAN device is bound to gs_usb."""

import json
from pathlib import Path

from loader.plugin_helpers import read_text, result


PLUGIN = {
    'id': 'RP013',
    'station': ['robopi2', 'rdkx5', 'opi5pro'],
    'clients': ['tui', 'csharp'],
    'author': 'wentywenty',
    'name': 'GSUSB USB-CAN interface',
    'version': '1.0',
    'lifecycle': 'testing',
    'safety': 'passive',
    'category': 'USB',
    'requires': ['usb'],
    'depends_on': [],
    'timeout_s': 5,
    'documentation': {
        'title': 'GSUSB USB-CAN 接口',
        'criterion': '`1209:2323` 绑定 `gs_usb` 且出现 CAN 网络接口；不验证 CAN 通信。',
    },
}


USB_ROOT = Path('/sys/bus/usb/devices')


def probe(root=USB_ROOT):
    matches = []
    for vendor_file in sorted(root.glob('*/idVendor')):
        directory = vendor_file.parent
        if (read_text(vendor_file).lower() != '1209'
                or read_text(directory / 'idProduct').lower() != '2323'):
            continue
        matches.append({'device': directory.name, 'vendor_id': read_text(vendor_file),
                        'product_id': read_text(directory / 'idProduct'),
                        'name': read_text(directory / 'product'),
                        'speed_mbps': read_text(directory / 'speed'),
                        'usb_version': read_text(directory / 'version'),
                        'device_version': read_text(directory / 'bcdDevice')})
    if not matches:
        return result('fail', 'GSUSB device 1209:2323 is not enumerated')
    bound = []
    for device in matches:
        interfaces = []
        for interface in sorted(root.glob(device['device'] + ':*')):
            driver = interface / 'driver'
            if driver.is_symlink() and driver.resolve().name == 'gs_usb':
                interfaces.append({'name': interface.name,
                                   'can_interfaces': [path.name for path in
                                                      sorted((interface / 'net').glob('can[0-9]*'))]})
        bound.append({**device, 'gs_usb_interfaces': interfaces})
    ready = [device for device in bound if any(interface['can_interfaces']
             for interface in device['gs_usb_interfaces'])]
    return result('pass' if ready else 'fail',
                  f'{len(ready)} GSUSB device(s) bound with CAN interfaces',
                  value=len(ready), unit='devices', details={'devices': bound})


if __name__ == '__main__':
    print(json.dumps(probe(), ensure_ascii=False))
