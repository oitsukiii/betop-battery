# betop-battery

**读取北通（BETOP）手柄电量的开源小工具 —— 在 Windows 通知区域显示电量百分比。**

[English](README.en.md) · 简体中文

---

## 为什么做这个

北通官方客户端（北通智控）**不显示精确电量**，只给"LED 灯颜色对应的范围"：

| 灯光颜色 | 电量范围 |
|---|---|
| 白色 | 76–100% |
| 绿色 | 51–75% |
| 黄色 | 26–50% |
| 红色 | 0–25% |

而且手柄走 **2.4G 接收器**时，Windows 的标准接口（XInput / GameInput）**拿不到电量**——
系统会认为它是"有线设备"，所有依赖标准 API 的工具（XInputBatteryMeter 等）都读不到。

**本工具直接与接收器的厂商接口通信，读出精确百分比。** 无需安装官方客户端。

```
🔋 电量     : 100%
⚡ 充电状态 : 充电中
```

## 特性

- ✅ **精确电量百分比**（官方客户端只给范围）
- ✅ **充电状态**识别
- ✅ **托盘图标**：直接显示数字，鼠标悬停看详情
- ✅ **低电量通知**：低于阈值弹系统通知（阈值/间隔可调）
- ✅ **命令行模式**：`once` / `--json`，方便脚本与自动化调用
- ✅ **数据驱动**：适配新型号只需新增一个 JSON，不用改代码
- ✅ **调试工具**：内置 `probe` 帮助你自己适配手里的其它型号

## 快速开始

### 方式一：直接下载可执行文件（推荐普通用户）

到 [Releases](../../releases) 下载 `betop-battery.exe`，双击即可（托盘图标会出现）。

### 方式二：从源码运行

```bash
git clone https://github.com/REPLACE-ME/betop-battery.git
cd betop-battery
pip install -r requirements.txt

# 方式 A：免安装直接跑（推荐先试这个）
python run.py once        # 读一次
python run.py tray        # 启动托盘
# Windows 上也可以直接双击 betop-battery.bat

# 方式 B：正式安装（之后可用 betop-battery 命令）
pip install -e .
betop-battery once
betop-battery tray
```

> Windows 上 `pip install hidapi` 会装预编译 wheel，**不需要 C 编译器**。

### 命令行用法

```bash
betop-battery                    # 读一次（默认）
betop-battery once --json        # JSON 输出（便于脚本调用）
betop-battery tray               # 托盘模式
betop-battery devices            # 查看已支持的型号
betop-battery list               # 列出系统上的北通 HID 接口
betop-battery probe interfaces   # 调试：列出全部接口
betop-battery probe dump         # 调试：转储原始帧
betop-battery probe suggest      # 调试：生成新型号描述草稿
```

**使用提示**：手柄休眠时读不到数据，**按一下手柄任意按键**再试即可。官方客户端可以同时开着，不冲突。

## 支持的手柄

| 型号 | 状态 | 连接方式 | 贡献者 |
|---|---|---|---|
| **北通鲲鹏20**（BTP-KP20EB）| ✅ 已验证 | 2.4G 接收器 | 初始版本 |
| 其它北通型号 | 🙋 **等待你贡献** | — | [看这里](docs/adapt-new-device.md) |

> 北通各型号的协议属于**同一家族**（同样的 `report_id + (subcmd<<4|cmd)` 帧结构），
> 所以适配新型号通常只需要改几个数字。**欢迎提 PR！**

## 工作原理（三步）

```
① 找到厂商接口          ② 发一条状态查询           ③ 从响应里取字段
   usage_page=0xFF00      02 15 (cmd=5, subcmd=1)      byte[2] = 电量 0~100
                                                       byte[4] & 0x0F = 充电中
```

实测抓到的完整帧：

```
02 15 64 00 51 01 01 00 ...
│  │  │  │  │
│  │  │  │  └─ byte[4] 低 4 位 = 1 → 充电中
│  │  │  └──── byte[3]
│  │  └─────── byte[2] = 0x64 = 100 → 电量 100%
│  └────────── header = (subcmd 0x1 << 4) | cmd 0x5 → "状态报告"
└───────────── report_id = 0x02
```

详细的帧格式、命令表、字段语义见 **[docs/protocol.md](docs/protocol.md)**。

## 想让它支持你的手柄？→ 通常只要改一个 JSON

```bash
# 1. 让工具带你找答案
betop-battery probe interfaces    # 找到厂商接口（usage_page 0xFF00）
betop-battery probe dump          # 看原始帧，工具会标注疑似电量字节
betop-battery probe watch         # 一边充电一边看，找变化的字节
betop-battery probe suggest       # 自动生成描述文件草稿

# 2. 把草稿存成 src/betop_battery/devices/betop-<型号>.json 并按注释修改
# 3. 验证
betop-battery once
# 4. 提 PR（记得填好 tested 里的实测信息）
```

**完整的分步教程（含常见坑）**：[docs/adapt-new-device.md](docs/adapt-new-device.md)

**用 AI Agent 帮你适配**：仓库根目录带 [AGENTS.md](AGENTS.md)，
把你的手柄插上，让 agent 读它并跑 `probe`，它能自己生成描述文件并提 PR。

## 项目结构

```
src/betop_battery/
├── protocol.py    帧编解码（纯函数，无 I/O）
├── transport.py   HID 收发（只认识字节，不认识型号）
├── devices.py     设备描述（JSON 驱动）
├── reader.py      编排：三者串成一次读取
├── probe.py       调试/发现工具
├── tray.py        托盘界面（只负责展示）
├── config.py      用户设置持久化
└── cli.py         命令分发
```

分层原则：**高内聚、低耦合**。`transport` 不知道北通，`tray` 不认识 HID，
适配新设备只改 `devices/*.json`。详见各模块的 docstring。

## 测试

```bash
pip install -e ".[dev]"
pytest -q
```

单元测试**不需要手柄**：用实测抓到的帧验证字段偏移，用真实 JSON 验证描述文件。

## 协议是怎么来的？

通过**互操作性分析**：观察官方 Electron 客户端与本工具接收器之间的通信行为
（官方客户端的界面逻辑以 JavaScript 形式分发，其中定义了查询命令与响应字段的位置），
据此写出**独立的实现**。

- 本项目**不包含任何厂商的代码、二进制或资源**
- 本项目**与北通公司无任何关联**，未获其授权或背书
- 仅用于读取**用户自己设备**的状态；请遵守当地法律与设备使用条款

## 已知限制

- **手柄休眠时读不到**（按一下按键唤醒即可）
- 协议可能随**固件更新**变化；若失效请[提 Issue](../../issues)并附上 `probe dump` 输出
- 目前验证环境：Windows 11 + 2.4G 接收器（蓝牙模式未测）
- 写入长度对某些固件敏感，描述文件里可配 `write_lengths`

## 贡献

非常欢迎！尤其是**适配你手上的其它北通型号**——你不需要会写代码，改 JSON 就行。

- [适配新型号教程](docs/adapt-new-device.md)
- [协议规格](docs/protocol.md)
- Issue / PR 中英文都欢迎

## 许可

[MIT](LICENSE)
