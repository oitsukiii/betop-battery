# 适配新型号：手把手教程

> 目标：让你手上的**其它北通手柄**也能显示电量。
> **你不需要会写代码** —— 只要填一个 JSON 文件。整个过程大约 15 分钟。

## 准备

```bash
pip install -r requirements.txt
```

把手柄连上（2.4G 接收器或 USB 线），**按一下手柄按键唤醒它**。

---

## 第 1 步：确认能认出你的设备

```bash
betop-battery probe interfaces
```

你会看到类似输出：

```
[0] VID=0x20bc PID=0x5191 usage_page=0xff00 usage=0x0003 iface=1 (BTP-KP20EB XINPUT DONGLE)  ← 厂商接口（电量通常在这里）
[1] VID=0x20bc PID=0x5191 usage_page=0x0001 usage=0x0002 iface=1 (BTP-KP20EB XINPUT DONGLE)
[2] VID=0x20bc PID=0x5191 usage_page=0x0001 usage=0x0006 iface=1 (BTP-KP20EB XINPUT DONGLE)
[3] VID=0x20bc PID=0x5191 usage_page=0x0001 usage=0x0005 iface=0 (Controller (BTP-KP20EB ...))
```

**记下三个值**，后面要填进 JSON：

| 要记的 | 从哪看 | 例子 |
|---|---|---|
| `product_id` | `PID=` 后面 | `0x5191` |
| `usage_page` | 厂商接口那行的 `usage_page=` | `0xFF00` |
| 产品字符串关键字 | 括号里的内容 | `BTP-KP20` |

> 💡 `vendor_id` 是北通的 USB 厂商 ID，固定 `0x20BC`，一般不用改。
> 如果这里**看不到 `0xFF00` 厂商接口**，说明这个型号可能不通过 HID 上报电量（先去提 Issue 讨论）。

---

## 第 2 步：发一条查询，看响应

```bash
betop-battery probe dump
```

输出会分两部分：

**① 收到的帧**（按 header 分组，并翻译成人话）：

```
0x15  cmd=0x5 subcmd=0x1 状态报告（含电量）   ×7 帧
    02 15 64 00 51 01 01 00 00 00 ...
0x25  cmd=0x5 subcmd=0x2 按键/摇杆报告       ×50 帧
    02 25 80 80 80 80 00 00 00 00 ...
```

**② 字节变化分析**（工具会帮你找电量）：

```
  header 0x15: 共 7 帧，按 32 字节统计
    byte[ 2] 取值 [98, 99, 100]  ← 疑似电量（1~100）
    byte[ 4] 取值 [80, 81]
```

**看到 `← 疑似电量` 就是它了！** 记下这个偏移。

> 如果**没有任何响应**：
> - 确认第 1 步选的是**厂商接口**（`usage_page=0xFF00`）
> - 试试 `betop-battery probe scan` —— 它会自动尝试一组状态查询命令
> - 试试手动指定接口：`betop-battery probe dump --iface 0`

---

## 第 3 步：找出"充电状态"字节

电量好找，充电状态需要**对比**：

```bash
betop-battery probe watch --rounds 6 --interval 3
```

**技巧**：一边拔插充电线，一边观察输出。会变的字节里：
- 电量字节应该只在 1~100 之间小幅变化
- **充电状态字节**通常只有两个取值（0 / 非 0），且**拔插瞬间跳变**

结束时工具会打印"与首帧对比，发生变化的字节"，正是你要的：

```
--- 与首帧对比，发生变化的字节 ---
  byte[ 2] [100, 100, 99]
  byte[ 4] [81, 80, 81, 80]     ← 拔插充电线时在变 → 充电状态
```

> 有时充电状态藏在某个字节的**低位**（高 4 位是别的东西），
> 这时用 JSON 里的 `mask` 取需要的位，例如 `"mask": "0x0F"`。

---

## 第 4 步：生成描述文件

```bash
betop-battery probe suggest
```

它会打印一份**填好一半的草稿**（接口信息、查询命令、示例帧都帮你写好了）：

```json
{
  "id": "betop-REPLACE-ME",
  "name": "北通<型号名>",
  "match": {
    "vendor_id": "0x20BC",
    "product_id": "0x5191",
    "usage_page": "0xFF00",
    "product_string_contains": "BTP-KP20"
  },
  "query": { "report_id": "0x02", "header": "0x15", "write_lengths": [64, 32, 16] },
  "reply_header": "0x15",
  "fields": {
    "battery_percent": { "offset": 2, "type": "uint8" },
    "charging": { "offset": 4, "type": "uint8", "mask": "0x0F", "boolean": true }
  },
  "tested": { "os": "", "connection": "", "date": "...", "sample_frame": "...", "sample_value": "" }
}
```

**把草稿复制到** `src/betop_battery/devices/` 下，命名成 `betop-<型号>.json`，然后：

1. 改 `id` 和 `name`（例如 `betop-kp40` / `北通鲲鹏40`）
2. 把第 2、3 步找到的 **`offset` / `mask`** 填进 `fields`
3. 删掉 `_hint` 字段
4. **填全 `tested`**（别人会参考你的实测信息）

### 各字段含义速查

| 字段 | 含义 | 例子 |
|---|---|---|
| `offset` | **整帧偏移**（0 = report_id）| 电量在第 2 字节 → `2` |
| `type` | `uint8` 或 `uint16le` | 单字节用 `uint8` |
| `mask` | 取位掩码 | 取低 4 位 → `"0x0F"` |
| `boolean` | 是否转成真/假 | 充电状态 → `true` |
| `scale` | 线性缩放 | 若是 0~255，填 `0.3922` 变 0~100 |

---

## 第 5 步：验证

```bash
betop-battery once
```

期望输出：

```
设备：北通鲲鹏40
电量：87%
状态：使用电池
原始帧：02 15 57 00 40 01 01 00 ...
```

**对照官方客户端**：打开北通智控，看它显示的充电状态是否与你读到的一致。
（官方只有灯色范围，所以电量你可以对照灯色：比如读到 87% 应对应"白色 76-100%"。）

**自己写测试（可选但强烈建议）**：把抓到的帧贴进 `tests/test_devices.py`，
这样即使没有手柄，别人也能验证偏移没写错。

---

## 第 6 步：提 PR 🎉

```bash
git checkout -b add-<型号>
git add src/betop_battery/devices/betop-<型号>.json
git commit -m "Add device descriptor for BETOP <型号>"
git push origin add-<型号>
```

PR 描述里请附上：
- `probe dump` 的输出（**脱敏**：帧里没有个人信息，可以放心贴）
- 你的手柄型号、连接方式、系统版本
- 与官方客户端对照的结果

我们会把型号加进 README 的支持列表。**谢谢你帮下一个人省事！** 🙏

---

## 常见问题

<details>
<summary><b>读到的电量一直是 100%，但手柄没插电</b></summary>

偏移错了 —— 你可能读到了某个恒定字节。回到第 2 步，确认取的是**变化过**的那个字节。
</details>

<details>
<summary><b>写不进去 / 报 "写入查询失败"</b></summary>

在 JSON 里加长 `write_lengths`，例如 `[64, 32, 16, 33, 9]`。
不同固件对中断 OUT 报告长度要求不同。
</details>

<details>
<summary><b>用蓝牙连接时读不到</b></summary>

蓝牙模式下走的是标准 BLE 电量服务，通常**系统自己就能显示**（设置 → 蓝牙和其他设备）。
本项目主要解决 2.4G 接收器（标准接口拿不到）的情况。
</details>

<details>
<summary><b>手柄休眠后读不到</b></summary>

正常现象 —— 按一下手柄按键唤醒。托盘模式会在下一轮自动恢复。
</details>

<details>
<summary><b>电量字节看起来是 0~255 而不是 0~100</b></summary>

加缩放：`"scale": 0.3922`（= 100/255）。
</details>

---

## 给 AI Agent 的提示

如果你是 AI agent 在帮用户适配：请阅读仓库根目录的 [AGENTS.md](../AGENTS.md)，
那里有面向自动化的完整流程与注意事项。
