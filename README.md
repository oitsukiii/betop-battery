# betop-battery

**读取北通（BETOP）手柄电量的开源小工具 —— 在 Windows 通知区域显示电量百分比。**

[English](README.en.md) · 简体中文

> ### 🤖 关于本项目的开发方式
>
> 本项目由 **DeepSeek V4.1 Flash** 模型驱动
> [**DeepSeek Harness (DSH)**](https://github.com/deepseek-ai/deepseek-harness)
> 以 **vibe coding** 方式开发完成 —— 包括协议逆向分析、全部代码、单元测试与文档。
>
> 人类作者负责提出需求、真机验证与拍板；
> 具体的探索、编码、调试与文档撰写由 AI 在 DSH 中完成。
> 我们把这个过程公开，是希望它也能作为「AI 参与硬件工具开发」的一个参考样本。

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
- ✅ **托盘图标**：直接显示数字，鼠标悬停看详情（**3 种样式**可选）
- ✅ **图形设置界面**：看状态、调样式、配叠加层，带实时预览
- ✅ **悬浮叠加层（HUD）**：像帧数那样常驻显示在游戏画面上，可拖动、可鼠标穿透
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
betop-battery tray               # 托盘模式（通知区域显示电量）
betop-battery gui                # 打开图形设置界面
betop-battery overlay            # 启动悬浮叠加层（HUD）
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

## 图形界面

```bash
betop-battery gui
```

三个标签页：

| 标签页 | 能做什么 |
|---|---|
| **常规** | 刷新间隔、低电量阈值、是否弹通知、限定型号 |
| **托盘图标** | 切换 **3 种样式**（数字方块 / 圆环进度 / 电池外形）、配色方案、充电标记，**带真实电量的实时预览** |
| **HUD** | 开关、透明度、字号、显示内容（型号/电量/充电）、emoji 图标、锁定布局、位置复位、背景与文字颜色 |

在托盘图标上**右键 → 设置…** 也能打开。改动实时写入配置文件，叠加层会自动热更新。

## HUD（悬浮叠加层）

```bash
betop-battery overlay
```

在屏幕角落常驻一条半透明电量条，形态类似显卡帧数叠加层：

```
北通鲲鹏20  ·  电量 95%  ·  电池
```

- **鼠标直接拖动**调整位置（位置会自动记住）
- **右键菜单**：立即刷新 / 鼠标穿透 / 显示项开关 / 复位位置 / 关闭
- **锁定布局**（Windows）：开启后鼠标事件穿过 HUD，玩游戏时不挡操作
  - ⚠️ 锁定后该窗口收不到鼠标事件，需先在设置界面取消勾选才能再拖动
- **emoji 图标**（🎮 🔋 ⚡）：可在设置界面关闭；透明度会一并作用于 emoji
- 透明度、字号、配色均可在图形界面里调；**HUD 的刷新间隔与「常规」页共用**

> HUD 与托盘是**两个独立进程**，互不影响；关掉托盘不会带走 HUD。
> 若游戏以**独占全屏**运行，HUD 可能不可见 —— 请把游戏设为「无边框窗口」。

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
├── icon.py        托盘图标绘制（3 种样式，纯函数）
├── tray.py        托盘交互（图标/菜单/通知）
├── overlay.py     悬浮叠加层 HUD（tkinter）
├── gui.py         图形设置界面（tkinter）
├── probe.py       调试/发现工具
├── proc.py        以子进程启动自身（托盘/GUI/叠加层互相独立）
├── log.py         安全日志（无控制台时自动写文件）
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

## ⚠️ 关于 Windows 的"智能应用控制"（重要）

Windows 11 的 **智能应用控制（Smart App Control）** 会**阻止未签名的可执行文件**，
包括你自己刚编译出来的 exe：

```
'betop-battery.exe' 已被组织自 Device Guard 策略阻止
```

**这不是程序有问题**，而是系统策略：SAC 只放行有可信签名的程序。检查是否开启：

```powershell
Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\CI\Policy' |
  Select-Object VerifiedAndReputablePolicyState
# 1 = 开启（强制）   0 = 关闭   2 = 评估模式
```

**建议的处理方式**：

| 情况 | 建议 |
|---|---|
| 自己用 | **用源码运行**（`python run.py tray`）—— Python 解释器有签名，不受影响 |
| 想分发 exe | 需要**代码签名**；开源项目可申请 [SignPath Foundation](https://signpath.org/) 的免费签名 |
| 用户被拦截 | 让其改用 `pip install` 或源码运行；**不要**建议关闭 SAC（关闭不可逆，需重装系统才能恢复） |

> GitHub Release 的附件会被打上"来自互联网"标记，SAC 开启的用户同样会被拦截。

### Release 里的两个 exe 有什么区别

| 文件 | 用途 |
|---|---|
| `betop-battery.exe` | **托盘版**（无控制台窗口）—— 普通用户双击即用 |
| `betop-battery-cli.exe` | **命令行版**（有控制台）—— 脚本与高级用户 |

## 已知限制

- ⚠️ **当前版本只针对"单只手柄"开发**：程序会在所有匹配的接口中选一个来读取。
  同时连接**多只手柄**时行为可能不符合预期（例如只显示其中一只，或读数在两支之间跳变）。
  多手柄支持尚未实现，欢迎[提 Issue](../../issues)说明你的使用场景。
- **未签名 exe 可能被智能应用控制拦截**（见上一节）
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
