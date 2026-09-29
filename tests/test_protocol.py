# -*- coding: utf-8 -*-
"""协议层单元测试（纯逻辑，不需要任何硬件）。"""

import pytest

from betop_battery.protocol import (
    CMD_STATUS,
    SUB_STATUS,
    Frame,
    build_query,
    decode_header,
    encode_header,
    matches_header,
    parse_frame,
)


def test_encode_header_packs_subcmd_high_cmd_low():
    """header = (subcmd << 4) | cmd。"""
    assert encode_header(0x5, 0x1) == 0x15
    assert encode_header(0x5, 0x2) == 0x25
    assert encode_header(0x2, 0x1) == 0x12


def test_encode_header_masks_to_nibbles():
    """超出 4 位的输入应被截断，避免悄悄污染相邻字段。"""
    assert encode_header(0x15, 0x21) == 0x15


def test_decode_header_is_inverse_of_encode():
    """编解码应互为逆运算。"""
    for cmd in range(0x10):
        for sub in range(0x10):
            assert decode_header(encode_header(cmd, sub)) == (cmd, sub)


def test_build_query_pads_to_requested_length():
    """查询帧应补零到指定长度，且前两字节是 report_id + header。"""
    buf = build_query(0x02, 0x15, 64)
    assert len(buf) == 64
    assert buf[0] == 0x02
    assert buf[1] == 0x15
    assert set(buf[2:]) == {0}


def test_build_query_rejects_too_short_length():
    """长度不足 2 应立即报错，而不是产出畸形帧。"""
    with pytest.raises(ValueError):
        build_query(0x02, 0x15, 1)


def test_parse_frame_splits_header_and_payload():
    """解析后 cmd/subcmd/payload 应正确。"""
    frame = parse_frame(bytes([0x02, 0x15, 0x64, 0x00, 0x51, 0x01]))
    assert frame is not None
    assert frame.report_id == 0x02
    assert (frame.cmd, frame.subcmd) == (CMD_STATUS, SUB_STATUS)
    assert frame.payload[:2] == bytes([0x64, 0x00])


def test_parse_frame_returns_none_on_short_input():
    """过短的输入返回 None，而不是抛异常（读 HID 时会出现空数据）。"""
    assert parse_frame(b"") is None
    assert parse_frame(b"\x02") is None


def test_frame_offset_accessors_respect_whole_frame_offsets():
    """byte_at/u16_at 使用整帧偏移，便于与厂商脚本的 substr 对照。"""
    frame = parse_frame(bytes([0x02, 0x15, 0x64, 0x00, 0x51, 0x01, 0x02]))
    assert frame.byte_at(2) == 0x64
    assert frame.byte_at(0) == 0x02           # 也能取 report_id 本身
    assert frame.u16_at(2) == 0x0064
    assert frame.byte_at(99) is None           # 越界返回 None
    assert frame.u16_at(99) is None


def test_matches_header_compares_full_byte():
    """matches_header 只比对 header 字节。"""
    frame = parse_frame(bytes([0x02, 0x15, 0x64]))
    assert matches_header(frame, 0x15)
    assert not matches_header(frame, 0x25)


def test_frame_is_immutable():
    """Frame 是 frozen dataclass，防止被下游意外修改。"""
    frame = Frame(raw=b"\x02\x15", report_id=2, cmd=5, subcmd=1, payload=b"")
    with pytest.raises(Exception):
        frame.cmd = 9  # type: ignore[misc]


# ---------------------------------------------------------------------------
# 打包相关回归测试（真机打包时踩过的坑）
# ---------------------------------------------------------------------------

def test_packaging_entries_exist_and_use_absolute_import():
    """打包入口必须放包外并用绝对导入。

    原因：PyInstaller 把入口脚本当作顶层 __main__ 执行，此时它不属于任何包，
    ``from .cli import main`` 会抛 "attempted relative import with no known parent package"。
    """
    import os
    import re

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    entries = [os.path.join(root, "tools", "entry_cli.py"),
               os.path.join(root, "tools", "entry_tray.py")]
    for entry in entries:
        assert os.path.isfile(entry), f"缺少打包入口 {entry}"
        source = open(entry, encoding="utf-8").read()
        # 不能出现 "from .xxx" / "from ..xxx" 这样的相对导入
        assert not re.search(r"^\s*from\s+\.+", source, re.M), \
            f"{os.path.basename(entry)} 使用了相对导入，打包后会崩溃"
        assert "from betop_battery" in source, \
            f"{os.path.basename(entry)} 应使用绝对导入"
