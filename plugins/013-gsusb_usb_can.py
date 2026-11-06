#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0
# Copyright (C) 2026 wentywenty

"""Check for USB-CAN devices bound to gs_usb, without assuming a VID/PID."""

import json
from pathlib import Path

from loader.plugin_helpers import read_text, result


PLUGIN = {
    'id': 'RP013',
    'station': ['robopi2', 'rdkx5', 'opi5pro'],
    'clients': ['tui', 'csharp'],
    'author': 'wentywenty',
    'name': 'GSUSB USB-CAN interface',
    'version': '1.2',
    'lifecycle': 'testing',
    'safety': 'passive',
    'category': 'USB',
    'requires': ['usb'],
    'depends_on': [],
    'timeout_s': 5,
    'documentation': {
        'title': 'GSUSB USB-CAN 接口',
        'criterion': '任一 USB 设备绑定 `gs_usb` 且出现 CAN 网络接口；不绑定具体 VID/PID，不验证 CAN 通信。',
    },
}


USB_ROOT = Path('/sys/bus/usb/devices')


def probe(root=USB_ROOT):
    devices = []
    for vendor_file in sorted(root.glob('*/idVendor')):
        directory = vendor_file.parent
        vendor_id = read_text(vendor_file).lower()
        product_id = read_text(directory / 'idProduct').lower()
        if not vendor_id or not product_id:
            continue
        interfaces = []
        for interface in sorted(root.glob(directory.name + ':*')):
            driver = interface / 'driver'
            if driver.is_symlink() and driver.resolve().name == 'gs_usb':
                interfaces.append({
                    'name': interface.name,
                    'can_interfaces': [path.name for path in
                                       sorted((interface / 'net').glob('can*'))],
                })
        if interfaces:
            devices.append({
                'device': directory.name,
                'vendor_id': vendor_id,
                'product_id': product_id,
                'name': read_text(directory / 'product'),
                'serial': read_text(directory / 'serial'),
                'speed_mbps': read_text(directory / 'speed'),
                'usb_version': read_text(directory / 'version'),
                'device_version': read_text(directory / 'bcdDevice'),
                'gs_usb_interfaces': interfaces,
            })
    if not devices:
        return result('skip', 'No USB device bound to gs_usb is enumerated')
    ready = [device for device in devices if any(interface['can_interfaces']
             for interface in device['gs_usb_interfaces'])]
    identifiers = sorted({f"{device['vendor_id']}:{device['product_id']}"
                          for device in devices})
    return result('pass' if ready else 'fail',
                  f'{len(ready)} USB-CAN device(s) bound to gs_usb'
                  if ready else 'gs_usb is bound but no CAN interface was created',
                  value=len(ready), unit='devices',
                  details={'devices': devices, 'device_ids': identifiers})


if __name__ == '__main__':
    print(json.dumps(probe(), ensure_ascii=False))
