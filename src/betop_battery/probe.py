# -*- coding: utf-8 -*-
"""调试 / 发现工具：帮助贡献者适配新型号。

这是本项目的”贡献者入口”。它把我们在鲲鹏20 上走过的探索过程做成命令，
让拿到**别的北通型号**的人（或 AI agent）能自助完成适配：

    probe interfaces        列出所有 HID 接口，找出厂商接口
    probe dump              发送状态查询并转储全部响应帧（带 cmd/subcmd 解码）
    probe watch             周期性转储，观察哪个字节随电量变化
    probe scan              自动尝试一组状态查询命令，看哪个有响应
    probe suggest           根据观察到的帧，生成描述文件草稿

安全说明：除 `scan` 外都只读取；`scan` 只发送 cmd=0x5（报告类）的查询命令，
不涉及写配置，但仍然建议在了解风险后使用。
"""

from __future__ import annotations

import json
import time
from collections import Counter
from typing import Any, Iterable, Optional

from .devices import (
    DEVICES_DIR,
    DeviceDescriptor,
    find_device,
    is_vendor_usage_page,
    load_descriptors,
)
from .protocol import (
    CMD_STATUS,
    SUB_FIRMWARE,
    SUB_KEY_EVENT,
    SUB_STATUS,
    build_query,
    encode_header,
)
from .transport import HidError, HidInterface, HidSession, enumerate_interfaces

#: header 语义表（用于把人看不懂的字节翻译成人话）
HEADER_NAMES: dict[int, str] = {
    encode_header(CMD_STATUS, SUB_STATUS): "状态报告（含电量）",
    encode_header(CMD_STATUS, SUB_KEY_EVENT): "按键/摇杆报告",
    encode_header(CMD_STATUS, SUB_FIRMWARE): "固件信息",
}
for _sub in range(0x0, 0x10):  # cmd=0x2 是配置读写
    HEADER_NAMES.setdefault(encode_header(0x2, _sub), f"配置命令 subcmd=0x{_sub:X}")


def _hex(data: bytes, limit: int = 24) -> str:
    """把字节串格式化成 hex 字符串。"""
    shown = data[:limit]
    text = " ".join(f"{b:02X}" for b in shown)
    return text + (" …" if len(data) > limit else "")


def _describe_header(header: int) -> str:
    """把 header 翻译成人类可读含义。"""
    cmd, sub = header & 0x0F, (header >> 4) & 0x0F
    name = HEADER_NAMES.get(header, "")
    return f"cmd=0x{cmd:X} subcmd=0x{sub:X} {name}"


# ---------------------------------------------------------------------------
# 子命令实现
# ---------------------------------------------------------------------------

def cmd_interfaces(vendor_id: Optional[int] = None) -> int:
    """列出 HID 接口。

    Args:
        vendor_id: 只看该厂商（默认 0x20BC 北通；传 0 表示全部）

    Returns:
        进程退出码。
    """
    ifaces = enumerate_interfaces()
    if vendor_id:
        ifaces = [i for i in ifaces if i.vendor_id == vendor_id]
    if not ifaces:
        print("没有找到匹配的 HID 接口。")
        print("提示：手柄休眠时接口仍在，但需要按一下按键才能读到数据。")
        return 1

    print(f"共 {len(ifaces)} 个接口：\n")
    for idx, iface in enumerate(ifaces):
        tag = "  ← 厂商接口（电量通常在这里）" if is_vendor_usage_page(iface.usage_page) else ""
        print(f"[{idx}] {iface.label}{tag}")
        print(f"      path: {iface.path}")
    print("\n下一步：用  probe dump  发送查询并看响应。")
    return 0


def cmd_dump(iface: HidInterface, descriptor: Optional[DeviceDescriptor],
             seconds: float = 2.0) -> int:
    """发送查询并转储所有响应帧。

    Args:
        iface:      要操作的接口
        descriptor: 若已知型号则用它的查询命令；否则用默认 cmd=0x5/subcmd=0x1
        seconds:    收集时长

    Returns:
        退出码。
    """
    report_id = descriptor.query.report_id if descriptor else 0x02
    header = descriptor.query.header if descriptor else encode_header(CMD_STATUS, SUB_STATUS)

    print(f"接口: {iface.label}")
    print(f"发送查询: report_id=0x{report_id:02X} header=0x{header:02X} "
          f"({_describe_header(header)})")

    try:
        with HidSession(iface.path) as session:
            dropped = session.drain()
            print(f"（先丢弃了 {dropped} 帧旧数据）")
            used = session.write_query(build_query(report_id, header, 64),
                                      lengths=descriptor.query.write_lengths if descriptor
                                      else (64, 32, 16))
            print(f"写入成功（长度 {used} 字节），正在收集 {seconds}s 的响应…\n")

            groups: dict[int, list[bytes]] = {}
            for frame in session.read_frames(duration=seconds):
                groups.setdefault(frame.header, []).append(frame.raw)
    except HidError as exc:
        print(f"✗ {exc}")
        return 2

    if not groups:
        print("✗ 没有收到任何响应。")
        print("  · 请按一下手柄按键唤醒后重试")
        print("  · 或换一个接口再试（见 probe interfaces）")
        return 3

    print("--- 收到的帧（按 header 分组）---")
    for header_val, raws in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        print(f"\n0x{header_val:02X}  {_describe_header(header_val)}   ×{len(raws)} 帧")
        for raw in raws[:4]:
            print(f"     {_hex(raw)}")
        if len(raws) > 4:
            print(f"     …（还有 {len(raws) - 4} 帧）")

    print("\n--- 字节变化分析（帮你找电量字段）---")
    _report_byte_stats(groups)
    return 0


def _report_byte_stats(groups: dict[int, list[bytes]], limit: int = 12) -> None:
    """统计每个字节位置的变化情况，提示可能的电量字段。

    Args:
        groups: header → 原始帧列表
        limit:  最多展示多少个字节位置
    """
    for header_val, raws in sorted(groups.items()):
        if len(raws) < 3:
            continue
        width = min(len(r) for r in raws)
        print(f"\n  header 0x{header_val:02X}: 共 {len(raws)} 帧，按 {width} 字节统计")
        printed = 0
        for pos in range(width):
            values = [r[pos] for r in raws]
            uniq = sorted(set(values))
            if len(uniq) == 1:
                continue  # 恒定不变，不可能是电量
            marker = ""
            # 单字节 1~100 且取值较集中 → 很可能是电量
            if pos >= 2 and all(1 <= v <= 100 for v in uniq) and len(uniq) <= 8:
                marker = "  ← 疑似电量（1~100）"
            print(f"    byte[{pos:>2}] 取值 {uniq[:8]}{' …' if len(uniq) > 8 else ''}{marker}")
            printed += 1
            if printed >= limit:
                print("    …")
                break
        if printed == 0:
            print("    所有字节都恒定不变（可能这次没抓到状态帧）")


def cmd_watch(iface: HidInterface, descriptor: Optional[DeviceDescriptor],
              rounds: int = 10, interval: float = 2.0) -> int:
    """周期性打印状态帧，用于观察某个字节是否与电量相关。

    Args:
        iface:      接口
        descriptor: 已知型号（可选）
        rounds:     采集轮数
        interval:   每轮间隔秒数

    Returns:
        退出码。
    """
    report_id = descriptor.query.report_id if descriptor else 0x02
    header = descriptor.query.header if descriptor else encode_header(CMD_STATUS, SUB_STATUS)
    print(f"每 {interval}s 抓一次，共 {rounds} 次。")
    print("技巧：一边充电一边观察，或等电量下降后对比，找出变化的字节。\n")

    history: list[bytes] = []
    for i in range(rounds):
        try:
            with HidSession(iface.path) as session:
                session.drain(rounds=10)
                session.write_query(build_query(report_id, header, 64),
                                    lengths=descriptor.query.write_lengths if descriptor
                                    else (64, 32, 16))
                frame = session.read_until(header, timeout=1.5)
        except HidError as exc:
            print(f"[{i + 1:>2}] ✗ {exc}")
            time.sleep(interval)
            continue
        if frame is None:
            print(f"[{i + 1:>2}] （无响应，可能休眠了）")
        else:
            history.append(frame.raw)
            print(f"[{i + 1:>2}] {_hex(frame.raw)}")
        time.sleep(interval)

    if len(history) >= 2:
        print("\n--- 与首帧对比，发生变化的字节 ---")
        base = history[0]
        width = min(len(r) for r in history)
        changed = False
        for pos in range(width):
            vals = sorted({r[pos] for r in history})
            if len(vals) > 1:
                print(f"  byte[{pos:>2}] {vals}")
                changed = True
        if not changed:
            print("  （没有变化 —— 期间电量/充电状态没变，属正常）")
    return 0


def cmd_scan(iface: HidInterface) -> int:
    """自动尝试一组状态查询命令，看哪些会得到响应。

    只发送 cmd=0x5（报告类）的查询，不触碰配置写入命令。

    Args:
        iface: 厂商接口

    Returns:
        退出码。
    """
    print("⚠️  将尝试发送 cmd=0x5 的各类查询命令（只读类）。")
    print("    如果担心，请先关闭手柄的宏/配置软件。开始…\n")

    found: list[tuple[int, bytes]] = []
    for sub in range(0x0, 0x10):
        header = encode_header(CMD_STATUS, sub)
        try:
            with HidSession(iface.path) as session:
                session.drain(rounds=8)
                session.write_query(build_query(0x02, header, 64), lengths=(64, 32, 16))
                frame = session.read_until(header, timeout=1.0)
        except HidError as exc:
            print(f"  header=0x{header:02X}  写入失败：{exc}")
            continue
        if frame is not None:
            found.append((header, frame.raw))
            print(f"  ✅ header=0x{header:02X}  {_describe_header(header)}")
            print(f"      响应: {_hex(frame.raw)}")
        else:
            print(f"  ·  header=0x{header:02X}  无响应")

    print(f"\n共 {len(found)} 个命令有响应。")
    if found:
        print("其中带电量的是「状态报告」——看哪个响应的第 2 字节在 0~100 之间。")
    return 0


def cmd_suggest(iface: HidInterface, descriptor: Optional[DeviceDescriptor],
                seconds: float = 3.0) -> int:
    """捕获状态帧并生成设备描述草稿（打印到屏幕）。

    Args:
        iface:      厂商接口
        descriptor: 已知型号（提供查询命令）
        seconds:    捕获时长

    Returns:
        退出码。
    """
    report_id = descriptor.query.report_id if descriptor else 0x02
    header = descriptor.query.header if descriptor else encode_header(CMD_STATUS, SUB_STATUS)
    try:
        with HidSession(iface.path) as session:
            session.drain()
            session.write_query(build_query(report_id, header, 64),
                                lengths=descriptor.query.write_lengths if descriptor
                                else (64, 32, 16))
            raws = [f.raw for f in session.read_frames(duration=seconds)
                    if f.header == header]
    except HidError as exc:
        print(f"✗ {exc}")
        return 2

    if not raws:
        print("✗ 没抓到状态帧，无法生成草稿。先按一下手柄按键再试。")
        return 3

    width = min(len(r) for r in raws)
    # 找出所有帧里恒定的位置和变化的位置
    varying = [pos for pos in range(width)
               if len({r[pos] for r in raws}) > 1]

    sample = " ".join(f"{b:02X}" for b in raws[0][:16])
    draft: dict[str, Any] = {
        "id": "betop-REPLACE-ME",
        "name": "北通<型号名>",
        "match": {
            "vendor_id": f"0x{iface.vendor_id:04X}",
            "product_id": f"0x{iface.product_id:04X}",
            "usage_page": f"0x{iface.usage_page:04X}",
            "product_string_contains": (iface.product_string or "").split()[0]
            if iface.product_string else "",
        },
        "query": {
            "report_id": f"0x{report_id:02X}",
            "header": f"0x{header:02X}",
            "write_lengths": [64, 32, 16],
        },
        "reply_header": f"0x{header:02X}",
        "fields": {
            "battery_percent": {"offset": 2, "type": "uint8"},
            "charging": {"offset": 4, "type": "uint8", "mask": "0x0F", "boolean": True},
        },
        "tested": {
            "os": "",
            "connection": "",
            "date": time.strftime("%Y-%m-%d"),
            "sample_frame": sample,
            "sample_value": "",
        },
        "_hint": (
            f"变化中的字节位置: {varying}。"
            "电量字段应在其中：一边充电一边用 probe watch 对比，"
            "数值在 1~100 区间的那个就是电量。"
        ),
    }
    print("--- 描述文件草稿（复制到 src/betop_battery/devices/ 后修改）---\n")
    print(json.dumps(draft, ensure_ascii=False, indent=2))
    return 0
