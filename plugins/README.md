# 插件字段与编写规则

本文件说明如何修改、新增和验证插件，以及每个元数据字段的含义。全部项目的生命周期、客户端、依赖和判据见[自动生成的状态清单](STATUS.md)。修改插件的 `PLUGIN` 字典后，运行 `python3 loader/generate_plugin_status.py --write` 更新清单，再用 `--check` 核对清单与源码一致。

以下命令均从项目根目录 `deb/robopi_TUI/` 执行。先看[状态清单](STATUS.md)确认要修改的编号、风险级别和依赖，再编辑 `plugins/NNN-name.py`。`loader/examples/057-example.py` 是未注册模板，不会被执行或打包为正式插件。

## 修改现有插件

1. 在[状态清单](STATUS.md)找到项目编号，再打开同编号的 `plugins/NNN-name.py`。例如 `RP001` 对应 [001-board_temperature.py](001-board_temperature.py)。不要只按菜单显示名猜文件，也不要改旧编号或文件名前缀。
2. 只改显示信息或适用范围时，修改顶层 `PLUGIN` 字典的相应字段；改测试标准时，同时修改 `documentation['criterion']`、实际 `probe()` 判据和对应单元测试。比如调整 RP001 的 90°C 阈值，要同步检查代码中的 `MAX_TEMP_C`、边界测试和判据文字。`documentation` 只用于生成文档，不会参与执行判断。
3. 改输入参数时，同步改 `inputs` 字段、`probe()` 的形参和文件底部 `plugin_params()` 调用；改依赖时检查前置插件的客户端是否覆盖本插件。改副作用时重新审视 `safety`、`batch`、`confirm`、超时和现场验收方式。
4. 运行文末的校验命令。软件包中的元数据也可通过 `sudo robopi-tui` → **Plugin metadata** 临时修改，但该菜单只编辑 `PLUGIN`，不能修改 `probe()`；持久改动应提交源码并重新打包。

## 新增插件：可运行示例

先确认编号未占用。`RP057` 保留给 machine-id 示例，`RP058` 已用于整机软件 SN；新增项从 `RP059` 起。可从 [057-example.py](../loader/examples/057-example.py) 复制并改成下一个空号，同时修改文件名与 `PLUGIN['id']`。新文件会被加载器自动发现，不用修改 TUI 菜单、加载器注册表或 Debian 安装清单。以下完整示例只读 `/etc/machine-id`，验证它是否为 32 位小写十六进制；它**不**证明设备 ID 唯一，判据中应明确这一边界。

```python
#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0
# Copyright (C) 2026 wentywenty

"""Check that the local machine ID has the expected format."""

import json
from pathlib import Path
import re

from loader.plugin_helpers import result


PLUGIN = {
    'id': 'RP057',
    'station': ['robopi2'],
    'clients': ['tui', 'csharp'],
    'author': 'your-name',
    'name': 'Machine ID format',
    'documentation': {
        'title': '机器 ID 格式',
        'criterion': '/etc/machine-id 包含 32 位小写十六进制；不验证设备唯一性。',
    },
    'version': '1.0',
    'lifecycle': 'testing',
    'kind': 'test',
    'generic': False,
    'category': 'System',
    'batch': False,
    'safety': 'passive',
    'requires': [],
    'depends_on': [],
    'timeout_s': 5,
}


def probe(path=Path('/etc/machine-id')):
    try:
        machine_id = path.read_text(encoding='ascii').strip()
    except (OSError, UnicodeError) as exc:
        return result('fail', f'Cannot read machine ID: {exc}')
    if not re.fullmatch(r'[0-9a-f]{32}', machine_id):
        return result('fail', 'Machine ID is not 32 lowercase hex digits')
    return result('pass', 'Machine ID format is valid')


if __name__ == '__main__':
    print(json.dumps(probe(), ensure_ascii=False))
```

把 `author` 换成实际编写人；`station` 只声明已打算支持的板卡，不能因为模板列了三种板卡就照抄。示例设 `batch=False`，方便先单项验证；是否纳入批量应在验收后决定。新插件的测试放在 `loader/tests/`，不要依赖板端文件来证明判据。上面这个示例可配套创建 `loader/tests/test_057_machine_id.py`，通过注入文件对象验证正常、缺失和错误格式三条路径：

```python
# SPDX-License-Identifier: GPL-3.0
# Copyright (C) 2026 wentywenty

from pathlib import Path
import runpy
import unittest
from unittest.mock import Mock


ROOT = Path(__file__).resolve().parents[2]
probe = runpy.run_path(str(ROOT / 'plugins' / '057-machine_id.py'))['probe']


class MachineIdTests(unittest.TestCase):
    def test_valid_id(self):
        path = Mock()
        path.read_text.return_value = 'a' * 32 + '\n'
        self.assertEqual(probe(path)['status'], 'pass')

    def test_missing_file(self):
        path = Mock()
        path.read_text.side_effect = FileNotFoundError('missing')
        self.assertEqual(probe(path)['status'], 'fail')

    def test_bad_format(self):
        path = Mock()
        path.read_text.return_value = 'not-an-id'
        self.assertEqual(probe(path)['status'], 'fail')
```

其他插件的断言与隔离方式可参考 [test_runner.py](../loader/tests/test_runner.py)。

有输入项时，给 `PLUGIN['inputs']` 增加字段，在入口使用 `plugin_params()` 读取执行器传来的 JSON，传给 `probe()`；完整实例见 [026-network_ping.py](026-network_ping.py)。例如 `inputs` 声明 `name='interface'` 后，入口可写 `print(json.dumps(probe(**plugin_params()), ensure_ascii=False))`。执行器会先按 `inputs` 校验类型、选项与正则；插件仍须校验实际资源是否存在。含 `secret` 的参数只允许 `--params-stdin`。不需要输入时直接调用 `probe()`，不要调用要求参数的 `plugin_params()`。

根据行为选择 `kind` 和 `safety`：纯观察用 `passive`，只读协议请求用 `query`，刷写、启停、重启等改变状态的操作用 `active`，并设 `clients=['tui']`、`batch=False`、清晰的 `confirm`。能力检查写在 `requires`，真正的前置验证写在 `depends_on`；例如网络接口存在和特定端口可用是不同判据。无法自动给出结论时返回 `needs_review`，不要把“打印快照”算作 `pass`。

## `PLUGIN` 字段

插件元数据必须是文件顶层的**字面量字典**，加载器用 AST 静态读取，列目录时不会执行插件代码。一个文件只能定义一次 `PLUGIN`，文件名为 `NNN-lowercase_name.py`，其中 `NNN` 与 `id=RPnnn` 对应。字段分行排版，供人阅读和编辑。

| 字段 | 规则 |
| --- | --- |
| `id` | 必填，`RP` 加三位数字；与文件名前缀对应，停用时不重新编号。 |
| `station` | 必填，非空板卡名称数组，例如 `['robopi2', 'rdkx5', 'opi5pro']`；无重复，声明适用范围不代表实机验收。 |
| `clients` | 必填，非空数组，元素为 `tui`、`csharp`，无重复；配置动作和 `active` 项只能使用 `['tui']`。 |
| `author` | 必填，非空编写人。 |
| `name` | 必填，非空界面展示名称，出现在 TUI、目录和报告中。 |
| `documentation` | 必填，包含且仅包含非空字符串 `title`（清单中的中文项目名）与 `criterion`（通过判据及结果边界）；状态清单直接从这里生成。 |
| `version` | 必填，非空插件版本字符串；独立于 `debian/changelog` 的包版本，菜单和报告中记为 `plugin_version`。修改插件行为或判据时由维护者手动递增；加载器校验字段存在，但不会自动递增。 |
| `lifecycle` | 必填，`testing`、`official` 或 `deprecated`；废弃项可列出但不执行。 |
| `kind` | `test` 或 `action`，默认 `test`。 |
| `generic` | 布尔值，默认 `False`；`True` 表示不依赖 RoboPi 专有硬件或路径，可在通用 Linux（x86/amd64）上测试。非 arm64 主机只列出并运行 `generic=True` 的插件。 |
| `category` | 非空菜单分类，默认 `板端测试`。 |
| `batch` | 布尔值，测试默认 `True`、动作默认 `False`；`False` 不进入 `--run all`。 |
| `safety` | 必填，`passive`（只读观察）、`query`（定向只读请求）或 `active`（改变状态）；主动项只能指定编号运行。 |
| `confirm` | 确认提示字符串，默认空；主动项应写明影响和恢复条件。 |
| `requires` | 能力数组，默认 `[]`；支持 `thermal`、`network`、`usb`、`can`、`udc`、`drm`、`gpio`、`serial`、`apt`，缺少能力时跳过。 |
| `depends_on` | `RPnnn` 数组，默认 `[]`；不能成环；前置项只有 `pass` 才放行，且须支持本项的全部客户端。 |
| `timeout_s` | 正整数秒，默认 10；`active` 最多 3600，其他最多 30。 |
| `inputs` | 输入字段数组，默认 `[]`；TUI 根据它生成表单，执行器再次校验参数。 |

`documentation.criterion` 应写出插件实际检查什么、何时视为通过，以及没有覆盖的边界。例如只枚举设备的插件不能宣称验证通信；需目视确认的插件应说明返回 `needs_review`。修改判据时必须同步更新插件实现和这里的文字。

`inputs` 中每项必须有唯一的 `name`（小写字母开头，可含数字和下划线）、非空 `label`、`type`。可选字段如下：

| 字段 | 规则 |
| --- | --- |
| `type` | `text`、`secret`、`choice`、`integer`、`boolean`。 |
| `choices` | `choice` 必填，非空字符串数组。 |
| `required` | 布尔值；`default` 必须匹配 `type`，且落在允许范围内。 |
| `pattern` | `text` 或 `secret` 的正则表达式。 |
| `minimum`、`maximum` | `integer` 的整数上下界。 |
| `when` | 按其他字段取值显示该字段的条件对象。 |

`secret` 参数只能通过 `--params-stdin` 传入，不写入命令行或报告。

## 返回值与验证顺序

插件以独立进程运行。标准输出只写**一个 JSON 对象**，至少包含 `status` 和字符串 `message`；`status` 可为 `pass`、`fail`、`needs_review`、`skip`、`error`。可选 `value` 为有限数值或 `null`，`unit` 为字符串或 `null`，`details` 为对象。建议用 `loader.plugin_helpers.result()` 构造返回值，用 `json.dumps(..., ensure_ascii=False)` 输出。调试信息写标准错误；不要在消息、明细或日志中写密码等敏感值。进程退出码 0 仅表示执行器能生成报告，不等于测试通过，应检查 `results[].result` 和整体 `outcome`。

完成源码和单元测试后，按顺序运行：

```bash
python3 loader/plugin_validator.py
python3 loader/plugin_validator.py --directory loader/examples
python3 -m unittest discover -s loader/tests -v
python3 loader/generate_plugin_status.py --write
python3 loader/generate_plugin_status.py --check
python3 loader/plugin_executor.py --list --no-save
```

格式检查器会验证字面量元数据、编号、依赖和 `__main__` 入口，但**不会运行插件**。生成器只从各插件的 `documentation` 等字段生成 [STATUS.md](STATUS.md)；新增插件后，清单应增加一行，改判据后相应行应更新。`--list --no-save` 仅检查目录能否被执行器加载；TUI 默认只显示 `official` 项，新增 `testing` 项须切换 **Developer mode** 才能在菜单中看到。

确认代码与单元测试通过后，才在符合现场条件的目标板上**指定单项编号**验收，例如只读示例可用 `python3 loader/plugin_executor.py --run RP057 --no-save`。先核对该项的 `safety`、`requires`、`depends_on` 和操作影响；不要为了验证新增项而运行 `--run all`。`--no-save` 只关闭执行器报告归档，不保证插件自身不写日志。`pass` 只证明本插件的判据；需要人工复核的项目返回 `needs_review`。板端和工装验收后再由维护者将 `lifecycle` 从 `testing` 改为 `official`，并重新生成状态清单。

`sudo robopi-tui` 的 **Plugin metadata** 菜单可查看和修改字段；实现位于 `loader/plugin_metadata.py`，修改直接写回插件 `.py` 文件，经全部插件校验后保存。软件包升级可能覆盖板端修改，需将长期变更同步到源码。`official` 只在板端及工装验收后人工设置，单次 `pass` 不会自动晋级。

执行器对每项记录开始、结束时间，并将完整 `PLUGIN` 字段快照（秘密输入默认值脱敏）、独立插件版本、脱敏参数、退出码、`details` 和带时间戳的 stdout/stderr/调度日志一并写入 `results[]`。正常返回 `error` 的插件也应在 `details` 中写清失败阶段和原始错误信息，执行器不会清空这些明细。安装后执行器位于 `/opt/roboparty/lib/robopi-tui/loader/plugin_executor.py`；报告默认存入 `/var/lib/robopi-tui/reports/<时间>_<run_id>.json`，调用索引在 `calls/` 子目录；`--no-save` 只关闭执行器归档。`lifecycle` 是插件验收状态，`status` 是单次运行结果，两者独立。目录与报告的 `schema_version` 为 3；调用方必须检查整体 `outcome` 和每项 `result`，不能只看进程退出码。
