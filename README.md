# RoboPi TUI

RoboPi 板端配置与测试插件系统。`sudo robopi-tui` 打开终端界面；上位机也可通过 ADB 调用板端 JSON 接口。TUI 只显示插件目录、收集参数、确认主动操作和展示报告。检测、配置与诊断都由编号插件执行。

## 贡献者快速开始

在本仓库根目录操作。离线单元测试需要 Python 3 和 `python3-yaml`；构建 Debian 包还需要 `debhelper`。**修改现有插件**时，先从[状态清单](plugins/STATUS.md)找到编号，再打开对应的 `plugins/NNN-name.py`：修改顶层 `PLUGIN` 字典中的显示信息、`documentation` 判据或参数声明，并在同一文件修改 `probe()` 等实际逻辑。只改 `documentation.criterion` 不会改变检测行为；只改检测逻辑也不会自动更新判据。若是板端临时改元数据，可用 `sudo robopi-tui` → **Plugin metadata**；该菜单不能修改插件代码，且软件包升级可能覆盖板端改动。

**新增插件**从未注册的示例复制；下面的 `RP056` 仅是当前编号状态下的示例，动手前先核对[状态清单](plugins/STATUS.md)和文件名，选下一个未使用编号。不要复用或重排旧编号。

```bash
cp loader/examples/056-example.py plugins/056-machine_id.py
# 编辑新文件：文件名 056 对应 PLUGIN['id'] = 'RP056'；实现 probe()，填写真实判据
python3 loader/plugin_validator.py
python3 loader/generate_plugin_status.py --write
python3 loader/generate_plugin_status.py --check
python3 -m unittest discover -s loader/tests -v
python3 loader/plugin_executor.py --list --no-save
```

复制的示例只返回 `skip`，不能直接作为完成的测试提交。新项先设 `lifecycle='testing'`，按真实板卡填 `station`、按实际支持情况填 `clients`，主动操作只能由 TUI 调用；新增或修改判据时补充离线单元测试。`--list` 只读取元数据，不执行插件；确需单项实测时，用 `--run RPnnn --no-save`，先确认该项的 `safety`、依赖和现场条件。不要用 `--run all` 验证新插件。完整源码示例、字段解释、参数传递和验收流程见[插件开发教程](plugins/README.md)。

## 架构

| 路径 | 职责 |
| --- | --- |
| `TUI/robopi-tui` | 终端入口与主菜单。 |
| `TUI/plugin_test_menu.py` | 插件测试目录、参数表单与结果展示。 |
| `loader/plugin_catalog.py` | 静态读取插件元数据、检查能力与依赖、生成目录。 |
| `loader/plugin_executor.py` | 独立进程执行、依赖调度、JSON 接口、报告归档。 |
| `loader/package_version.py` | 从源码或安装包的 Debian changelog 读取统一的 TUI 版本。 |
| `loader/plugin_metadata.py` | 插件元数据查看与编辑菜单、字段校验及源码写回。 |
| `loader/plugin_validator.py` | 静态检查插件格式，不执行板端检测或配置。 |
| `loader/generate_plugin_status.py` | 从各插件的文档元数据生成并检查状态清单。 |
| `loader/plugin_helpers.py` | 插件共享的参数解析、结果构造、文本读取及原子写文件。 |
| `loader/examples/` | 未注册的插件编写示例。 |
| `loader/tests/` | 主机侧 `unittest` 测试，不进入板端软件包。 |
| `plugins/` | `RP001`–`RP055` 的测试和配置动作；详见[状态清单](plugins/STATUS.md)与[字段说明](plugins/README.md)。 |

元数据编辑的调用路径是 `TUI/robopi-tui` 主菜单 → `loader/plugin_metadata.py` 中的 `manage_plugins()` → `plugins/NNN-*.py` 中的 `PLUGIN` 字典。菜单实现仍在 loader 模块内，TUI 入口只负责调用；没有单独的元数据编辑前端文件或插件 `.json` 文件。

原 `compat/robopi-config` 与 TUI 内的配置/串口实现已从源码和安装包移除。Raspberry Pi 专属的 beta 仓库、Pi 桌面会话切换与启动固件设置不属于 RoboPi2 功能。

## 板端功能

| 类别 | 插件覆盖 |
| --- | --- |
| 系统 | 温度、镜像身份、APT、包列表、序列号、SSH、启动目标、主机名、CPU、密码、自动登录、sudo、journald、根目录 Overlay、启动分区写保护、TUI 更新。 |
| 网络 | 接口清单、指定接口 Ping、APT 源、Wi-Fi、代理、网卡命名、省电与链路本地回退。 |
| 接口 | CAN、ADB gadget、USB/USB-CAN、GPIO、设备树 Overlay、VNC。 |
| 串口 | 端口清单、getty、收发/回环/互测、RS485 收发。 |
| 显示与本地化 | DRM 连接/模式、X11/Labwc 熄屏、区域设置、时区。 |
| 外设 | Addon、灯板、BMS、HiPNUC、HPM 固件、单电机参数及 USB Hub 压测。 |

插件头部的字面量 `PLUGIN` 声明编号、板卡集合 `station`、客户端集合 `clients`、编写人、名称、文档标题与判据、版本、生命周期、`kind`、`category`、`batch`、风险、能力要求、依赖、超时、确认文案与输入字段。当前 55 项均声明 `['robopi2', 'rdkx5', 'opi5pro']`，编写人均为 `wentywenty`，生命周期均为 `testing`。这三个板卡名是适用范围声明，尚未代表跨板卡实机验证。`kind=test` 用于检测，`kind=action` 用于配置；TUI 从目录动态生成类别和表单，不识别任何插件的固定编号。

加载器用 AST 读取元数据，列目录时不导入插件。每次执行启动独立子进程；依赖项只有 `pass` 才可放行后续项。`--run all` 只选择 `batch=true` 项，仍跳过其中的主动操作。主动项必须明确选编号；TUI 会询问参数和确认。`pass`、`fail`、`needs_review`、`skip`、`error` 是单项结果，整体结果为 `pass`、`fail` 或 `incomplete`。一次 `pass` 不会将 `testing` 自动晋升为 `official`。

## 调用与日志

### 通用任务命令行（robopi-tui 1.1.0 起）

公共入口为 `robopi-tui rpc --protocol 1`，从 stdin 接收一行 JSON-RPC 2.0 请求。支持任务目录/描述、只读参数、任务提交/查询/历史/恢复；TUI 软件版本与通信协议独立。终端插件使用通用 `loader/task_runtime.py`，Studio 是调用者之一。已有 Studio API v1 保留兼容。

完整命令、输入输出、退出码、任务生命周期、兼容矩阵与新增任务步骤见[命令行接口与兼容规范](docs/命令行接口与兼容规范.md)。以下是保留给旧客户端的 API v1 示例。

Studio 通过 ADB 调用固定入口，只传操作名和 UUID，不发送 Python 代码、APT 命令或固件路径。板端采集、版本比较、整条升级/HPM/USB/CAN 流程由 TUI 负责。RP006 和 RP024 终端入口也调用同一持久任务实现；旧 schema v3 执行器仍保留原客户端限制。

```bash
python3 /opt/roboparty/lib/robopi-tui/loader/studio_api.py capabilities
python3 /opt/roboparty/lib/robopi-tui/loader/studio_api.py snapshot
# 主动操作需要 root；先确认共享网络可达、板卡型号和空间充足
python3 /opt/roboparty/lib/robopi-tui/loader/studio_api.py start pipeline --request-id <唯一UUID>
python3 /opt/roboparty/lib/robopi-tui/loader/studio_api.py status <同一UUID>
python3 /opt/roboparty/lib/robopi-tui/loader/studio_api.py active
```

允许的操作只有 `packages-check`、`apt-upgrade`、`hpm-flash`、`can-check`、`pipeline`。`snapshot` 只读缓存；`packages-check` 只刷新目标源。普通升级执行全系统 `apt-get --with-new-pkgs upgrade`（允许新依赖、禁止移除），结果只比较 base、bms、analyze、addon、TUI 五包。版本来自 `apt.roboparty.com`，由 python3-apt 按 Debian 规则比较；刷新失败不声明最新。

HPM 操作先刷新源并安装 `roboparty-analyze`，从 `dpkg-query -L` 中选唯一的包内 `ethercanfd_*.bin`，调用 `/opt/roboparty/bin/flash_hpm.sh`。脚本成功、正常 USB `1209:2323` 连续出现两次、同一 HPM/gs_usb 的 can0–can3 全部出现才通过；不发 CAN 帧、不做 Flash 字节读回。写操作目前限制已核对的 RoboPi2。

1.1.1 起，四路识别与 CAN 配置分开显示。新增只读配置检查按 RoboPi2 默认值核对 UP、FD、1M/5M、MTU 72、队列 10000 和 ERROR-ACTIVE。DOWN、配置不符或信息不足会在参数中明确标记；`can-check`、`hpm-flash`、`pipeline` 存在配置问题时整体为 `needs_review`，并保留刷写与枚举的独立证据。不会自动修改接口或 base 包规则，通信协议仍为 1。

1.0.3 起，包操作前检查日志空间：`/var/log` 可用不足8MiB时，仅检查 `/var/log/robopi/seven-YYYYMMDD-HHMMSS` 及 `/var/log.hdd/robopi` 的旧会话。必须有已结束记录、至少结束60秒、无进程占用、无符号链接或特殊文件、归档期间内容不变，才先压缩归档到私有 `/var/lib/robopi-tui/legacy-log-archives` 再删除旧目录；磁盘镜像同时处理，避免重启回灌。兼容旧 analyze 追加结束记录导致 manifest JSON 格式错误的已知格式。当前 `/home/robo/robopi-logs`、系统日志、APT日志和配置均不在清理范围。空间充足不清理；无法安全释放到至少2MiB时仍中止升级。实现为 `loader/board_log_cleanup.py`，归档路径和删除清单记入任务结果/事件。

`start` 返回任务身份，`status` 同时返回阶段事件、终态和结果。最终结论检查 `outcome`（pass/fail/needs_review/error），不能仅看进程退出码。任务与日志在私有 `/var/lib/robopi-tui/studio-jobs/<UUID>/`，由 systemd 瞬态服务托管，断开 ADB/关闭 Studio 不会取消板端写入。相同 UUID 去重，冲突任务拒绝；重连只查询，不重放。升级前冻结 loader/plugins 到任务目录，APT 替换 TUI 不会改变本任务实现；下一次 API 调用读取新安装代码。旧任务归档由维护人员保留/清理，当前不自动删历史。

板端重启或进程异常退出会标记 `interrupted` 并锁定新任务。人工检查 `dpkg --audit`、任务日志、USB/CAN 和复位服务后，可用 `studio_api.py acknowledge <UUID>` 确认解除该锁；这个命令不修复系统、不重跑原任务。没有远程强杀升级/烧录接口，Studio 的“停止等待”只解除电脑等待。

实现分工：`loader/task_protocol.py` 管公共协议，`task_catalog.py` 管任务注册，`task_runtime.py` 管持久执行，`studio_api.py` 兼容旧入口；`board_backend.py` 管流程与判定，`board_snapshot.py` 管只读参数，`board_packages.py` 管源和版本。后端与协议测试位于 `loader/tests/test_studio_backend.py`、`test_task_protocol.py`。

```bash
sudo robopi-tui
robopi-tui --version
python3 loader/plugin_executor.py --list --no-save
python3 loader/plugin_executor.py --run RP001 --no-save
python3 loader/plugin_executor.py --env csharp --list --no-save
```

TUI 的标题、背景栏和 `--version` 都从 `debian/changelog` 读取包版本；安装后读取随包安装到 `/opt/roboparty/share/doc/robopi-tui/` 的 `changelog`，不用在 Python 中另写版本号。TUI 默认只显示 `official` 项；主菜单切换 **Developer mode** 可查看全部 55 项，包括测试中、废弃和仅 C# 项。废弃项和仅 C# 项在 TUI 中不能运行。当前全部为 `testing`，所以要从开发者模式进入测试目录。插件各自维护 `PLUGIN['version']`，在菜单和报告中独立显示。主菜单的 **Plugin metadata** 可查看插件元数据；用 `sudo robopi-tui` 可以编辑生命周期和其他字段。编辑菜单与写入逻辑仍在 `loader/plugin_metadata.py`；字段以 JSON 格式展示和输入，实际写回的是 `plugins/` 中对应 `.py` 文件的 `PLUGIN` 字典，没有单独的插件 `.json` 文件。详细规则见[字段说明](plugins/README.md)，各项状态与判据见[自动生成清单](plugins/STATUS.md)。

ADB 上位机可调用 `python3 /opt/roboparty/lib/robopi-tui/loader/plugin_executor.py --env csharp --client-id LINE-PC-01 --list` 或 `--run RP001`。交互入口是 `/opt/roboparty/bin/robopi-tui`，系统兼容命令为 `/usr/bin/robopi-tui`。从旧包升级的外部调用方需将脚本路径 `loader/runner.py` 改为 `/opt/roboparty/lib/robopi-tui/loader/plugin_executor.py`；JSON 参数与返回格式未因文件重命名改变。`schema_version` 为 **3**；`--list` 返回 `tests[]`，`--run` 返回设备身份、时间、整体结论、计数及 `results[]`。C# 目录只包含允许该客户端的 23 项；配置动作与主动测试仅允许 TUI，调度器会拒绝 C# 指定运行。`--env` 是调用方声明，不是身份认证。进程退出码 0 仅表示报告生成，调用方必须检查 `outcome` 和每项 `result`。带 `secret` 字段的插件只能用 `--params-stdin` 从标准输入传 JSON，避免敏感值出现在进程命令行和报告中；目录的 `inputs[].when` 决定条件字段是否显示。

每次执行生成一份 `/var/lib/robopi-tui/reports/<时间>_<run_id>.json`；顶层 `tui_version` 来自 changelog，`results[]` 每项均保存插件完整元数据快照 `plugin`（含独立版本、判据、安全等级、依赖和参数声明）、脱敏的实际输入 `parameters`、`exit_code`、结果、`details` 与带 UTC 时间戳的 `logs`。插件返回 `error` 时，其诊断 `details` 也保留。TUI 结果页展示这些字段和全部捕获日志，C# 客户端可从 JSON 响应读取同一内容。stdout/stderr 时间戳是执行器捕获时刻，不是每行原始产生时间；插件内部子命令的输出以插件写入 `details` 或 stderr 的内容为准。

报告以私有权限原子写入，`/var/lib/robopi-tui/reports/calls/` 保存调用索引、TUI 版本及对应的 `report_path`。USB 压测另存逐轮日志到 `usb-stress/`，单电机参数刷写另存逐步日志到 `motor-params/`。`--no-save` 仍返回完整 JSON，但不归档主报告和调用记录，也不控制插件自身日志。已声明的 `secret` 输入原值和秘密默认值会脱敏；插件仍不应输出派生的敏感数据。

## 操作边界

`RP006` 执行 APT 更新与升级；`RP014` 复位 USB Hub 100 次；`RP024` 擦写 HPM Flash；`RP025` 只对明确选择并物理隔离的一台 LRO 电机写参数、逐项读回，保持 `CAN_TIMEOUT` 不变，不使能或控制运动。即时读回不等于断电保持。操作前必须核对实际 `robot.yaml`、CAN 接口、电机 ID、总线独占、限流、急停和现场监护。

2026-10-22 can0 事故后，CAN 波特率/重启、电机使能与运动、头部电机查询、电机/CAN 压力发帧仍保持锁定，不得通过插件绕开。HiPNUC 版本与波特率查询也尚未完成板端验收。RP026–RP055 是迁移后的新插件，主机侧测试和打包成功不代表实机配置生效；现场应逐项验收。现有 CI 没有 RoboPi 实机、工装或 Windows C# 前端，不能把当前结果作为产线终检放行依据。

## 构建

```bash
python3 loader/plugin_validator.py
python3 loader/plugin_validator.py --directory loader/examples
python3 loader/generate_plugin_status.py --check
python3 -m unittest discover -s loader/tests -v
dpkg-buildpackage -us -uc -b
sudo apt install ../robopi-tui_*_arm64.deb

# 安装后路径
/opt/roboparty/bin/robopi-tui --version
python3 /opt/roboparty/lib/robopi-tui/loader/plugin_validator.py
```

RP046 在 Python 插件内保存 X11 的 DPMS、待机和熄屏配置文本。“关闭熄屏”将其原子写入 `/etc/X11/xorg.conf.d/10-blanking.conf`；“开启熄屏”会删除该文件。Labwc 分支使用自己的 autostart 配置。HiPNUC 帧协议及 USB 枚举逻辑放在各自的测试插件中；`loader/plugin_helpers.py` 只保留跨插件通用的参数、结果、文本读取和文件写入函数。

主机侧测试继续使用 Python 标准库 `unittest`：目前已覆盖加载器、执行器、TUI 接线和插件的模拟环境判据，无需增加 `pytest` 依赖。格式检查器只检查静态契约，单元测试与打包成功仍不能代替板端和工装验收。

推送触发 GitHub 托管的 Ubuntu 24.04 ARM64 runner 构建并上传 Debian 包；`v*` 标签与 Debian changelog 版本匹配时上传到 GitHub Release。构建与发布步骤沿用 `robopi_addon` 的流程，不占用自建 runner。主机单元测试、插件格式检查和状态清单校验需按上方命令在本地运行；当前工作流不执行它们。父仓库 APT 路由仍由其自身 `sync-and-inject` 工作流触发。
