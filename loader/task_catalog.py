# SPDX-License-Identifier: GPL-3.0
"""TUI-owned task registry. Adding a task does not change the public protocol."""
from dataclasses import dataclass
from typing import Callable

from loader import board_backend


class UnsupportedTask(ValueError):
    code = 'UNSUPPORTED_TASK'


@dataclass(frozen=True)
class Task:
    id: str
    title: str
    execute: Callable
    requires_root: bool = True

    def describe(self):
        return dict(id=self.id, title=self.title, requiresRoot=self.requires_root,
                    parametersSchema={'type': 'object', 'properties': {}, 'additionalProperties': False},
                    resultFormat='board-result-v1')


TASKS = {
    identifier: Task(identifier, title,
                     lambda emit, action=identifier: board_backend.execute_action(action, emit))
    for identifier, title in (
        ('packages-check', '联网检查五个软件包版本'),
        ('apt-upgrade', '升级板卡全部软件包'),
        ('hpm-flash', '安装 HPM 固件并验证'),
        ('can-check', '检查 HPM 四路 CAN'),
        ('pipeline', '升级并验证全部'),
    )
}


def get(task_id):
    if not isinstance(task_id, str) or task_id not in TASKS:
        raise UnsupportedTask('TUI 未安装任务：' + str(task_id))
    return TASKS[task_id]
