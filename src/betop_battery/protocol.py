# -*- coding: utf-8 -*-
"""北通手柄 HID 帧协议（纯逻辑层，不做任何 I/O）。

设计原则
--------
本模块只负责**帧的编解码**，不关心设备、不关心操作系统、不碰 HID。
所有函数都是纯函数 → 可脱离硬件单元测试。

帧格式（由对官方 Electron 客户端的互操作性分析得出）
----------------------------------------------------
    字节 0 : report_id        HID 报告 ID（鲲鹏20 为 0x02）
    字节 1 : header           = (subcmd << 4) | cmd
    字节 2+: payload          负载

    header 的高低 4 位各放了 cmd / subcmd 两个小字段，
    这是北通协议家族统一的”一字节两个字段”打包方式。

已知命令（cmd / subcmd）
------------------------
    cmd=0x5, subcmd=0x1   状态报告（含电量、充电状态、面板信息）
    cmd=0x5, subcmd=0x2   按键/摇杆报告
    cmd=0x5, subcmd=0x5   固件版本信息
    cmd=0x2, subcmd=0x1   读配置
    cmd=0x2, subcmd=0x2   写配置

本文件只是**规格实现**，不含任何厂商代码。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

CMD_STATUS = 0x5
"""状态报告命令号。"""

SUB_STATUS = 0x1
"""状态报告子命令（含电量）。"""

SUB_KEY_EVENT = 0x2
"""按键报告子命令。"""

SUB_FIRMWARE = 0x5
"""固件信息子命令。"""

#: 状态报告里各字段相对于 payload 起始（即整帧字节 2）的偏移 —— 仅作文档记录，
#: 真正使用哪个偏移由设备描述文件（devices/*.json）决定，便于适配其它型号。
KP20_FIELD_OFFSETS = {
    "battery_percent": 0,   # 整帧 byte[2]
    "charge_state": 2,      # 整帧 byte[4]，取低 4 位
    "panel": 5,             # 整帧 byte[7..8]（16 位）
    "panel_mask": 7,        # 整帧 byte[9..10]（16 位）
}


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Frame:
    """一帧解析后的结果。

    Attributes:
        raw:        原始字节（含 report_id）
        report_id:  字节 0
        cmd:        header 低 4 位
        subcmd:     header 高 4 位
        payload:    字节 2 之后的内容
    """

    raw: bytes
    report_id: int
    cmd: int
    subcmd: int
    payload: bytes

    @property
    def header(self) -> int:
        """本帧的 header 字节。"""
        return self.raw[1] if len(self.raw) > 1 else 0

    def byte_at(self, offset: int) -> Optional[int]:
        """按**整帧偏移**取字节（越过 report_id/header 也能取，方便对照厂商脚本）。

        Args:
            offset: 整帧偏移，0 表示 report_id。

        Returns:
            字节值；越界返回 None。
        """
        return self.raw[offset] if 0 <= offset < len(self.raw) else None

    def u16_at(self, offset: int) -> Optional[int]:
        """按整帧偏移取小端 16 位值；越界返回 None。"""
        if offset < 0 or offset + 1 >= len(self.raw):
            return None
        return self.raw[offset] | (self.raw[offset + 1] << 8)


# ---------------------------------------------------------------------------
# 编解码（纯函数）
# ---------------------------------------------------------------------------

def encode_header(cmd: int, subcmd: int) -> int:
    """把 cmd / subcmd 打包成一个 header 字节。

    Args:
        cmd:    命令号（低 4 位有效）
        subcmd: 子命令号（低 4 位有效）

    Returns:
        ``(subcmd << 4) | cmd``

    Examples:
        >>> hex(encode_header(0x5, 0x1))
        '0x15'
    """
    return ((subcmd & 0x0F) << 4) | (cmd & 0x0F)


def decode_header(header: int) -> tuple[int, int]:
    """把 header 字节拆成 (cmd, subcmd)。

    Args:
        header: header 字节

    Returns:
        ``(cmd, subcmd)``

    Examples:
        >>> decode_header(0x15)
        (5, 1)
    """
    return (header & 0x0F, (header >> 4) & 0x0F)


def build_query(report_id: int, header: int, length: int) -> bytes:
    """构造一条查询帧（不足部分补 0）。

    北通接收器对**写入长度**敏感：有些固件要求写满报告长度（如 64 字节），
    因此这里允许调用方指定长度，并在 transport 层逐个尝试。

    Args:
        report_id: HID 报告 ID
        header:    header 字节（用 :func:`encode_header` 生成）
        length:    要写入的总字节数（含 report_id），至少 2

    Returns:
        长度为 ``length`` 的字节串。

    Raises:
        ValueError: ``length`` 小于 2。
    """
    if length < 2:
        raise ValueError("length 至少为 2（report_id + header）")
    return bytes([report_id & 0xFF, header & 0xFF]) + b"\x00" * (length - 2)


def parse_frame(data: bytes) -> Optional[Frame]:
    """把一段原始字节解析成 :class:`Frame`。

    Args:
        data: HID 读到的原始字节（hidapi 已含 report_id）

    Returns:
        解析成功的 Frame；长度不足 2 时返回 None。
    """
    if not data or len(data) < 2:
        return None
    cmd, subcmd = decode_header(data[1])
    return Frame(raw=bytes(data), report_id=data[0], cmd=cmd, subcmd=subcmd, payload=bytes(data[2:]))


def matches_header(frame: Frame, expected_header: int) -> bool:
    """判断一帧是否为我们等待的响应（比对 header 字节）。

    Args:
        frame:           待判断的帧
        expected_header: 期望的 header（含 cmd/subcmd）

    Returns:
        是否匹配。
    """
    return frame.header == (expected_header & 0xFF)
