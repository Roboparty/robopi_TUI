# SPDX-License-Identifier: GPL-3.0
"""Dynamic board and peripheral detection shared by TUI plugins."""

from pathlib import Path
import re

BOARD_RELEASE = Path('/etc/armbian-release')
MODEL = Path('/proc/device-tree/model')
NET = Path('/sys/class/net')


def _text(path):
    try:
        return Path(path).read_text(encoding='utf-8', errors='replace').strip('\x00\n ')
    except OSError:
        return ''


def normalize_board(value):
    """Normalize board names such as ``rdk-x5`` and ``RDK X5`` to one id."""
    compact = re.sub(r'[^a-z0-9]+', '', str(value).lower())
    aliases = {'rdkx5': 'rdkx5', 'robopi2': 'robopi2', 'opi5pro': 'opi5pro'}
    return aliases.get(compact, compact)


def board_id(board_release=BOARD_RELEASE, model=MODEL):
    """Return ``(normalized_id, source)`` for the running board."""
    for line in _text(board_release).splitlines():
        key, separator, raw = line.partition('=')
        if separator and key.strip() == 'BOARD':
            value = raw.strip().strip('"\'')
            if value:
                return normalize_board(value), 'armbian-release'
    text = _text(model).upper()
    if 'RDK X5' in text or 'RDK-X5' in text:
        return 'rdkx5', 'device-tree'
    if 'ROBOPI2' in text or 'ROBO PI2' in text:
        return 'robopi2', 'device-tree'
    if 'OPI5' in text or 'ORANGE PI 5' in text:
        return 'opi5pro', 'device-tree'
    return normalize_board(text), 'device-tree'


def can_interfaces(root=NET):
    """Return all CAN network interface names currently visible."""
    try:
        return sorted(path.name for path in Path(root).glob('can*') if path.is_dir())
    except OSError:
        return []


def has_can_interface(name, root=NET):
    return bool(name) and (Path(root) / name).is_dir()
