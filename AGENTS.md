# AGENTS.md — 面向 AI Agent 的贡献指南

本文件写给**帮助用户适配手柄的 AI agent**。
如果你是人类，请直接看 [docs/adapt-new-device.md](docs/adapt-new-device.md)。

## 你的任务

用户插上了一个**尚未被本项目支持**的北通手柄，希望它能在通知区域显示电量。
你的目标是产出一个**设备描述文件**（`src/betop_battery/devices/betop-<型号>.json`）并提 PR。

## 环境准备

```bash
pip install -r requirements.txt      # 需要 hidapi；托盘另需 pystray Pillow
python run.py devices      # 确认项目可用
```

**前提**：目标手柄已连接，且已按过按键唤醒。若目标系统是 Windows，
使用 SSH 远程执行时**必须通过 SSH 所在会话操作**，因为 HID 设备属于本地会话。

## 执行流程（严格按顺序）

### 1. 枚举接口，定位厂商接口

```bash
python run.py probe interfaces
```

**判定规则**：`usage_page == 0xFF00`（或 `>= 0xFF00`）的那一行就是厂商接口。
记下该行的 `PID`、`usage_page`、以及产品字符串。

**如果不存在 0xFF00 接口** → 停止，向用户报告：
"该型号在当前连接方式下不通过厂商 HID 接口暴露状态，建议改用蓝牙模式或提交 Issue 讨论。"

### 2. 抓帧

```bash
python run.py probe dump
```

**成功判据**：存在 header `0x15`（`cmd=0x5 subcmd=0x1`，"状态报告"）的帧。

**若没有响应**，依次尝试：
1. 让用户按手柄按键唤醒后重试
2. `python run.py probe scan`（自动尝试 cmd=0x5 的各个 subcmd，只读类命令）
3. `python run.py probe dump --iface <序号>` 换接口

### 3. 定位字段（关键步骤）

```bash
python run.py probe watch --rounds 8 --interval 3
```

**电量字节**的特征：
- 位置 `>= 2`（跳过 report_id 与 header）
- 多次采样取值**全部落在 1~100**
- 取值在多次采样中**有变化**（或至少不是恒定）

**充电状态字节**的特征：
- 取值只有两种（例如 `0` 与 `1`，或某一位在 0/1 间跳变）
- 让用户**拔插充电线**时会跳变

> 若无法让用户配合拔插充电线，可先用"电量字节"完成基本适配，
> 并在 PR 描述中说明"充电状态字段未验证"。

### 4. 生成描述文件

```bash
python run.py probe suggest
```

它会输出一份草稿。在此基础上：
- 修改 `id`（`betop-<型号简写>`，全小写连字符）与 `name`
- 按第 3 步结果修改 `fields.*.offset`（必要时加 `mask` / `scale` / `type`）
- **删除 `_hint` 等所有以下划线开头的注释键**
- 填全 `tested`（os / connection / date / sample_frame / sample_value）

### 5. 验证（必须）

```bash
python run.py once
```

**必须同时满足**：
1. 输出电量值合理（0~100）
2. 与官方客户端对照：**充电状态显示一致**
3. 电量与官方 LED 灯色所属区间**不矛盾**
   （白 76-100 / 绿 51-75 / 黄 26-50 / 红 0-25）

### 6. 加测试（必须）

在 `tests/test_devices.py` 中新增一个用例，用**实测抓到的帧**验证提取结果：

```python
def test_<型号>_extracts_battery():
    desc = _load("betop-<型号>")
    frame = parse_frame(bytes([0x02, 0x15, 0x64, 0x00, 0x50, 0x01] + [0] * 26))
    assert desc.extract(frame)["battery_percent"] == 100
```

运行 `pytest -q` 必须全绿。

### 7. 提 PR

```bash
git checkout -b add-<型号>
git add src/betop_battery/devices/betop-<型号>.json tests/test_devices.py
git commit -m "Add device descriptor for BETOP <型号>"
```

PR 描述中包含：`probe dump` 输出、手柄型号、连接方式、系统版本、验证结论。

## 硬性约束（不要违反）

| 约束 | 原因 |
|---|---|
| **只发送 `cmd=0x5`（读类）命令** | 绝不写配置、不改灯效、不刷固件 |
| **不要 `git add` 任何厂商文件** | 项目不包含厂商代码/二进制 |
| **不要从官方客户端复制代码** | 只参考协议行为，写独立实现 |
| **不要把 `_` 开头的注释键留在正式描述文件里** | 描述文件要干净 |
| **不要修改 `_template.json`** | 它是给人类的模板 |
| **单位是"整帧偏移"** | 0 = report_id，不是 payload 偏移 |
| 提交信息用英文 | 项目惯例 |

## 代码阅读顺序（如需改代码）

1. `protocol.py` — 帧编解码（纯函数，最容易理解）
2. `devices.py` — 描述文件如何被解析与匹配
3. `transport.py` — HID 收发
4. `reader.py` — 三者如何串联

## 失败时的报告格式

若无法完成适配，向用户报告：

```
结论：<型号> 在当前连接方式下无法适配
依据：<probe 输出摘要 / 缺少 0xFF00 接口 / 无 0x15 响应>
建议：<改用蓝牙模式 / 关闭官方客户端后重试 / 提交 Issue 并附 probe dump>
```

## 参考

- 协议规格：[docs/protocol.md](docs/protocol.md)
- 人类教程：[docs/adapt-new-device.md](docs/adapt-new-device.md)
- 已验证示例：`src/betop_battery/devices/betop-kp20.json`
