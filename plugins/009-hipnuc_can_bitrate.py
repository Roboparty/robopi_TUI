#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0
# Copyright (C) 2026 wentywenty

"""Read HiPNUC's CAN rate index and compare with the dedicated host link."""

import json
import socket
import struct
import subprocess
import time

from loader.board_context import has_can_interface
from loader.plugin_helpers import result


PLUGIN = {
    'id': 'RP009',
    'station': ['robopi2', 'rdkx5', 'opi5pro'],
    'clients': ['tui', 'csharp'],
    'author': 'wentywenty',
    'name': 'HiPNUC CAN bitrate',
    'version': '1.0',
    'lifecycle': 'testing',
    'safety': 'query',
    'category': 'Interfaces',
    'requires': [],
    'depends_on': ['RP008'],
    'timeout_s': 15,
    'documentation': {
        'title': 'HiPNUC CAN 波特率',
        'criterion': '主机 `can_hipnuc` 为 1 Mb/s，寄存器 `0x009A` 表示设备也为 1 Mb/s。',
    },
}


BITRATES = {0: 1000000, 2: 500000, 3: 250000, 4: 125000}
EXPECTED_BITRATE = 1000000
HIPNUC_INTERFACE = 'can_hipnuc'
CAN_EFF_FLAG = 0x80000000
CAN_RTR_FLAG = 0x40000000
CAN_ERR_FLAG = 0x20000000
CAN_EFF_MASK = 0x1FFFFFFF
HIPNUC_HOST_ADDRESS = 0x55


def hipnuc_data_node(frame):
    if len(frame) != 16:
        return None
    can_id, size, _ = struct.unpack('=IB3x8s', frame)
    identifier = can_id & CAN_EFF_MASK
    node = identifier & 0xFF
    if (can_id & CAN_EFF_FLAG and not can_id & (CAN_RTR_FLAG | CAN_ERR_FLAG)
            and size == 8 and 1 <= node <= 127
            and (identifier & 0x1FFF0000) == 0x0CFF0000
            and ((identifier >> 8) & 0xFF) in (0x34, 0x37, 0x3D, 0x41)):
        return node
    return None


def hipnuc_read_frame(address, node):
    identifier = CAN_EFF_FLAG | 0x0CEF0000 | node << 8 | HIPNUC_HOST_ADDRESS
    return struct.pack('=IB3x8s', identifier, 8, struct.pack('<HBBI', address, 3, 0, 0))


def hipnuc_reply_value(frame, address, node):
    if len(frame) != 16:
        return None
    can_id, size, data = struct.unpack('=IB3x8s', frame)
    expected_id = 0x0CEF0000 | HIPNUC_HOST_ADDRESS << 8 | node
    if (not can_id & CAN_EFF_FLAG or can_id & (CAN_RTR_FLAG | CAN_ERR_FLAG)
            or (can_id & CAN_EFF_MASK) != expected_id
            or size != 8 or data[:4] != struct.pack('<HBB', address, 3, 0)):
        return None
    return int.from_bytes(data[4:6], 'little')


def hipnuc_query_register(address):
    if address != 0x009A:
        raise ValueError('Only the HiPNUC nominal bitrate register is supported')
    with socket.socket(socket.PF_CAN, socket.SOCK_RAW, socket.CAN_RAW) as bus:
        bus.bind((HIPNUC_INTERFACE,))
        deadline = time.monotonic() + 2
        node = None
        while time.monotonic() < deadline:
            bus.settimeout(max(0.001, deadline - time.monotonic()))
            try:
                node = hipnuc_data_node(bus.recv(16))
            except socket.timeout:
                break
            if node is not None:
                break
        if node is None:
            raise TimeoutError('No HiPNUC J1939 data observed; no query was sent')
        bus.send(hipnuc_read_frame(address, node))
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            bus.settimeout(max(0.001, deadline - time.monotonic()))
            try:
                value = hipnuc_reply_value(bus.recv(16), address, node)
            except socket.timeout:
                break
            if value is not None:
                return value, node
    raise TimeoutError(f'HiPNUC node {node} did not answer read 0x{address:04X}')


def host_bitrate():
    completed = subprocess.run(['ip', '-j', '-details', 'link', 'show',
                                'dev', 'can_hipnuc'], capture_output=True,
                               text=True, timeout=2, check=True)
    links = json.loads(completed.stdout)
    if len(links) != 1 or 'UP' not in links[0].get('flags', []):
        raise ValueError('can_hipnuc is missing or down')
    return (links[0].get('linkinfo') or {}).get('info_data', {}).get('bittiming', {}).get('bitrate')


def probe(query=hipnuc_query_register, host=host_bitrate, present=None):
    present = has_can_interface(HIPNUC_INTERFACE) if present is None else present
    present = present() if callable(present) else present
    if not present:
        return result('skip', f'{HIPNUC_INTERFACE} is not present; HiPNUC is not connected')
    try:
        host_rate = host()
        if host_rate != EXPECTED_BITRATE:
            return result('fail', f'Host can_hipnuc bitrate is {host_rate}; expected 1000000',
                          details={'host_bitrate': host_rate})
        index, node = query(0x009A)
    except (OSError, ValueError, TimeoutError, subprocess.SubprocessError) as exc:
        return result('fail', f'Could not verify HiPNUC CAN bitrate: {exc}')
    device_rate = BITRATES.get(index)
    details = {'interface': 'can_hipnuc', 'node_id': node, 'device_rate_index': index,
               'device_bitrate': device_rate, 'host_bitrate': host_rate,
               'expected_bitrate': EXPECTED_BITRATE}
    return result('pass' if device_rate == host_rate else 'fail',
                  'HiPNUC and host both use 1000000 bit/s' if device_rate == host_rate
                  else 'HiPNUC bitrate does not match the required 1000000 bit/s',
                  value=device_rate, unit='bit/s' if device_rate is not None else None,
                  details=details)


if __name__ == '__main__':
    print(json.dumps(probe(), ensure_ascii=False))
