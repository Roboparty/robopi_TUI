#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0
# Copyright (C) 2026 wentywenty

"""Read HiPNUC software version over the dedicated J1939 interface."""

import json
import socket
import struct
import time

from loader.board_context import has_can_interface
from loader.plugin_helpers import result


PLUGIN = {
    'id': 'RP010',
    'station': ['robopi2', 'rdkx5', 'opi5pro'],
    'clients': ['tui', 'csharp'],
    'author': 'wentywenty',
    'name': 'HiPNUC firmware version',
    'version': '1.0',
    'lifecycle': 'testing',
    'safety': 'query',
    'category': 'Interfaces',
    'requires': [],
    'depends_on': ['RP009'],
    'timeout_s': 15,
    'documentation': {
        'title': 'HiPNUC 固件版本',
        'criterion': '寄存器 `0x0078` 返回非零版本；不比较最低版本。',
    },
}


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
    if address != 0x0078:
        raise ValueError('Only the HiPNUC firmware version register is supported')
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


def probe(query=hipnuc_query_register, present=None):
    present = has_can_interface(HIPNUC_INTERFACE) if present is None else present
    present = present() if callable(present) else present
    if not present:
        return result('skip', f'{HIPNUC_INTERFACE} is not present; HiPNUC is not connected')
    try:
        raw, node = query(0x0078)
    except (OSError, ValueError, TimeoutError) as exc:
        return result('fail', f'Could not read HiPNUC firmware version: {exc}')
    if raw == 0:
        return result('fail', 'HiPNUC returned an empty firmware version')
    version = f'{raw // 100}.{(raw % 100) // 10}.{raw % 10}'
    return result('pass', f'HiPNUC firmware version {version}', value=raw,
                  details={'version': version, 'raw_version': raw, 'node_id': node,
                           'interface': 'can_hipnuc'})


if __name__ == '__main__':
    print(json.dumps(probe(), ensure_ascii=False))
