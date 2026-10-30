# 插件状态清单

此表由 `python3 loader/generate_plugin_status.py --write` 从各插件的 `PLUGIN` 元数据生成。字段及编写规则见 [README.md](README.md)。

当前 **55 项**；生命周期：`testing` 55 项。
全部插件声明适用板卡：`robopi2`, `rdkx5`, `opi5pro`；编写人：`wentywenty`。板卡声明不等于实机验收。

`批量=是` 只表示 `batch=True`；`active` 项在 `--run all` 中仍会跳过，必须指定编号。`TUI+C#` 表示两个客户端均可调用。

| 编号 | 版本 | 分类 | 项目 | 生命周期 | 类型 / 安全 | 客户端 | 批量 | 前置 | 判据 / 结果边界 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| RP001 | 1.0 | System | 板卡温度 | testing | test / passive | TUI+C# | 是 | 无 | 最高可读温区低于 90°C；无可读温区则跳过。 |
| RP002 | 1.0 | System | 镜像出厂身份 | testing | test / passive | TUI+C# | 是 | 无 | `/etc/armbian-image-release` 的 8 个字段匹配出厂快照；不是 OTA 当前版本。 |
| RP003 | 1.0 | Network | 网络接口清单 | testing | test / passive | TUI+C# | 是 | 无 | 记录非 loopback 接口，结果待人工复核。 |
| RP004 | 1.0 | Network | RoboParty APT 源 | testing | test / passive | TUI+C# | 是 | 无 | 已配置 `apt.roboparty.com`，`common/InRelease` 可访问并有 PGP 签名消息头；不单独验证签名。 |
| RP005 | 1.0 | System | 已安装 RoboParty 包 | testing | test / passive | TUI+C# | 是 | 无 | 列出 `roboparty-*`、`robopi-*` 包，且包含 `roboparty-base`。 |
| RP006 | 1.1 | System | APT 更新与升级 | testing | test / active | TUI | 是 | 无 | 板端持久任务完成普通升级，并对比 base/bms/analyze/addon/TUI 五个包的源版本与实际版本。 |
| RP007 | 1.0 | Interfaces | CAN 接口清单 | testing | test / passive | TUI+C# | 是 | 无 | 记录 `canN` 状态，待人工复核；不发送 CAN 帧。 |
| RP008 | 1.0 | Interfaces | HiPNUC udev 规则 | testing | test / passive | TUI+C# | 是 | 无 | `roboparty-base` 拥有已安装规则，且规则命名 `can_hipnuc`。 |
| RP009 | 1.0 | Interfaces | HiPNUC CAN 波特率 | testing | test / query | TUI+C# | 是 | RP008 | 主机 `can_hipnuc` 为 1 Mb/s，寄存器 `0x009A` 表示设备也为 1 Mb/s。 |
| RP010 | 1.0 | Interfaces | HiPNUC 固件版本 | testing | test / query | TUI+C# | 是 | RP009 | 寄存器 `0x0078` 返回非零版本；不比较最低版本。 |
| RP011 | 1.0 | USB | USB 设备清单 | testing | test / passive | TUI+C# | 是 | 无 | 记录 USB 设备和枚举速率，结果待人工复核。 |
| RP012 | 1.0 | USB | USB 3.0 扩展坞 | testing | test / passive | TUI+C# | 是 | 无 | 外部 Hub 的设备类或接口类为 `09`，速率至少 5000 Mb/s。 |
| RP013 | 1.0 | USB | GSUSB USB-CAN 接口 | testing | test / passive | TUI+C# | 是 | 无 | `1209:2323` 绑定 `gs_usb` 且出现 CAN 网络接口；不验证 CAN 通信。 |
| RP014 | 1.0 | USB | USB Hub 复位压力测试 | testing | test / active | TUI | 是 | RP012 | 100 次复位后身份恢复、无新增内核错误且每轮成功；会短暂断开 USB。 |
| RP015 | 1.0 | Interfaces | GPIO 引脚快照 | testing | test / passive | TUI+C# | 是 | 无 | 记录 GPIO 状态，结果待人工复核。 |
| RP016 | 1.0 | Addon | 灯板就绪状态 | testing | test / passive | TUI+C# | 是 | 无 | WS2812 字符设备与工具齐备仍待人工复核，需要目视确认。 |
| RP017 | 1.0 | Addon | RoboPi Addon 就绪状态 | testing | test / passive | TUI+C# | 是 | 无 | `robopi-addon`、WS2812 设备/工具、`gpiochip0` 和 `gpiochip1` 均存在。 |
| RP018 | 1.0 | Addon | Addon LED 目视测试 | testing | test / active | TUI | 否 | RP017 | 点灯工具成功后仍需目视确认。 |
| RP019 | 1.0 | Addon | Addon SIG 双输出测试 | testing | test / active | TUI | 是 | 无 | 15 秒内检测到 SIG 上升沿后仍需确认物理输出。 |
| RP020 | 1.0 | BMS | BMS 端口就绪 | testing | test / passive | TUI+C# | 是 | 无 | BMS 类型与端口已配置，相应 CAN 接口或串口存在；不证明通信。 |
| RP021 | 1.0 | BMS | BMS 服务与实时数据 | testing | test / passive | TUI+C# | 是 | RP020 | 服务 active/running，最近 30 秒有本次启动的 BMS 数据日志。 |
| RP022 | 1.0 | BMS | BMS 固件版本 | testing | test / passive | TUI+C# | 是 | RP021 | 按 `BMS_TYPE` 读到非零固件/硬件版本；不比较目标版本。 |
| RP023 | 1.0 | BMS | BMS 重启与通信 | testing | test / active | TUI | 是 | RP020 | 重启服务成功，10 秒后满足 RP021 的实时数据判据。 |
| RP024 | 1.1 | Interfaces | HPM EtherCANFD 刷写 | testing | test / active | TUI | 是 | RP001 | 板端安装 analyze、选择包内固件并刷写，正常 USB 连续两次出现且 HPM 的 can0–can3 全部识别；不验证 Flash 字节或电机通信。 |
| RP025 | 1.0 | Motor | 单台 LRO 电机参数刷写 | testing | test / active | TUI | 否 | RP001 | 选择并隔离一台电机，逐项读回且 `CAN_TIMEOUT` 不变；不验证断电保持。 |
| RP026 | 1.0 | Network | 指定接口 Ping | testing | test / query | TUI+C# | 否 | 无 | 选定接口和目标，5 次 Ping；不修改网络。 |
| RP027 | 1.0 | Interfaces | ADB gadget 状态 | testing | test / passive | TUI+C# | 是 | 无 | 观察 adbd 和 UDC。 |
| RP028 | 1.0 | Interfaces | ADB gadget 控制 | testing | action / active | TUI | 否 | 无 | 明确选择启停；可能断开 ADB。 |
| RP029 | 1.0 | Serial | 串口清单 | testing | test / passive | TUI+C# | 是 | 无 | 仅列设备节点，不打开串口。 |
| RP030 | 1.0 | Serial | 串口 getty | testing | action / active | TUI | 否 | 无 | 明确选择端口和启停。 |
| RP031 | 1.0 | Serial | 串口收发/回环/互测 | testing | test / active | TUI | 否 | 无 | 有界读写并恢复 termios，禁止内核控制台端口。 |
| RP032 | 1.0 | Serial | RS485 收发 | testing | test / active | TUI | 否 | 无 | 有界收发，可指定收发方向 GPIO。 |
| RP033 | 1.0 | Display | DRM 连接与模式 | testing | test / passive | TUI+C# | 是 | 无 | 只读连接状态与支持的分辨率。 |
| RP034 | 1.0 | System | 板卡序列号 | testing | test / passive | TUI+C# | 是 | 无 | 只读设备树或 CPU 信息。 |
| RP035 | 1.0 | System | SSH 服务 | testing | action / active | TUI | 否 | 无 | 启停服务。 |
| RP036 | 1.0 | Interfaces | VNC 服务 | testing | action / active | TUI | 否 | 无 | 在已安装的 VNC 服务中选择并启停。 |
| RP037 | 1.0 | System | 启动目标 | testing | action / active | TUI | 否 | 无 | 选择终端或桌面，重启后生效。 |
| RP038 | 1.0 | System | 主机名 | testing | action / active | TUI | 否 | 无 | 使用 hostnamectl。 |
| RP039 | 1.0 | Localisation | 时区 | testing | action / active | TUI | 否 | 无 | 验证 zoneinfo 路径后设置。 |
| RP040 | 1.0 | System | CPU 调频策略 | testing | action / active | TUI | 否 | 无 | 查询或设置 governor/频率范围。 |
| RP041 | 1.0 | Interfaces | 设备树 Overlay | testing | action / active | TUI | 否 | 无 | 只允许已安装的 DTBO 名称。 |
| RP042 | 1.0 | Network | Wi-Fi 连接 | testing | action / active | TUI | 否 | 无 | NetworkManager 密钥文件以 0600 写入；连接可能中断。 |
| RP043 | 1.0 | System | 本地用户密码 | testing | action / active | TUI | 否 | 无 | 密码经 stdin 传给 chpasswd，不写报告。 |
| RP044 | 1.0 | System | 自动登录 | testing | action / active | TUI | 否 | 无 | 控制终端或 LightDM 自动登录。 |
| RP045 | 1.0 | Localisation | 区域设置 | testing | action / active | TUI | 否 | 无 | 仅接受 SUPPORTED 中的 locale。 |
| RP046 | 1.0 | Display | X11/Labwc 熄屏 | testing | action / active | TUI | 否 | 无 | X11 配置文本内嵌；Labwc 管理用户与 greeter 的 autostart。 |
| RP047 | 1.0 | Network | Wi-Fi 省电 | testing | action / active | TUI | 否 | 无 | 修改 NetworkManager 配置并重新加载。 |
| RP048 | 1.0 | Network | 链路本地回退 | testing | action / active | TUI | 否 | 无 | 修改 NetworkManager 配置并重新加载。 |
| RP049 | 1.0 | System | journald 存储 | testing | action / active | TUI | 否 | 无 | 写入独立 drop-in 并重启服务。 |
| RP050 | 1.0 | Network | 代理 | testing | action / active | TUI | 否 | 无 | 配置 shell 和 APT 代理；不记录地址值。 |
| RP051 | 1.0 | Network | 可预测网卡名 | testing | action / active | TUI | 否 | 无 | 设置 systemd link 掩码，重启后生效。 |
| RP052 | 1.0 | System | sudo 密码策略 | testing | action / active | TUI | 否 | 无 | 仅针对明确选择的本地用户，先经 visudo 校验。 |
| RP053 | 1.0 | System | 更新 RoboPi TUI | testing | action / active | TUI | 否 | 无 | APT 刷新并安装 robopi-tui。 |
| RP054 | 1.0 | System | 根目录 Overlay | testing | action / active | TUI | 否 | 无 | 使用 overlayroot.local.conf，重建 initramfs 后需重启。 |
| RP055 | 1.0 | System | 启动分区写保护 | testing | action / active | TUI | 否 | 无 | 修改现有 fstab 挂载选项，重启后生效。 |

生命周期描述的是插件验收状态，不是单次运行结果。`testing` 不会因一次 `pass` 自动变成 `official`；`deprecated` 可列出但不能执行。需要人工判断的项目返回 `needs_review`，不等于通过。现有项目尚未完成所有声明板卡的实机验收。
