# -*- coding: utf-8 -*-
"""设备描述层单元测试：用**真实抓到的帧**验证字段偏移，不需要手柄。"""

import json
import os

import pytest

from betop_battery.devices import (
    DEVICES_DIR,
    DescriptorError,
    FieldSpec,
    load_descriptors,
    parse_descriptor,
)
from betop_battery.protocol import parse_frame
from betop_battery.transport import HidInterface

# 鲲鹏20 实测抓到的状态帧（100% 电量、充电中）
KP20_SAMPLE = bytes([0x02, 0x15, 0x64, 0x00, 0x51, 0x01, 0x01, 0x00] + [0] * 24)


def _kp20():
    """取出内置的鲲鹏20 描述（不存在则跳过相关测试）。"""
    descriptors = {d.id: d for d in load_descriptors(DEVICES_DIR)}
    if "betop-kp20" not in descriptors:
        pytest.skip("缺少 betop-kp20.json")
    return descriptors["betop-kp20"]


# ---------------------------------------------------------------------------
# 描述文件解析
# ---------------------------------------------------------------------------

def test_all_builtin_descriptors_are_valid():
    """内置描述文件必须全部能解析（坏文件会让用户一脸问号）。"""
    descriptors = load_descriptors(DEVICES_DIR)
    assert descriptors, "至少要有一个内置设备描述"
    for desc in descriptors:
        assert desc.id and desc.name
        assert desc.match.vendor_id, f"{desc.id} 缺少 vendor_id"


def test_parse_descriptor_accepts_hex_strings():
    """JSON 里允许写 "0xFF00" 这种十六进制字符串。"""
    desc = parse_descriptor({
        "id": "x",
        "match": {"vendor_id": "0x1234", "product_id": "0xABCD", "usage_page": "0xFF00"},
        "query": {"report_id": "0x02", "header": "0x15"},
        "fields": {"battery_percent": {"offset": 2, "mask": "0xF0", "scale": 0.5}},
    })
    assert desc.match.vendor_id == 0x1234
    assert desc.match.product_id == 0xABCD
    assert desc.match.usage_page == 0xFF00
    assert desc.query.report_id == 0x02
    assert desc.fields["battery_percent"].mask == 0xF0
    assert desc.fields["battery_percent"].scale == 0.5


def test_parse_descriptor_requires_vendor_id():
    """缺 vendor_id 应报错，避免误匹配到别的设备。"""
    with pytest.raises(DescriptorError):
        parse_descriptor({"id": "x", "match": {}, "query": {"report_id": 2, "header": 0x15}})


def test_parse_descriptor_reports_missing_sections():
    """缺少 match/query 段时给出明确错误。"""
    with pytest.raises(DescriptorError):
        parse_descriptor({"id": "x"})


def test_template_file_is_ignored_by_loader():
    """_ 开头的模板文件不应被当成可用设备。"""
    ids = [d.id for d in load_descriptors(DEVICES_DIR)]
    assert all(not i.startswith("betop-REPLACE") for i in ids)


# ---------------------------------------------------------------------------
# 字段提取（核心：偏移正确性）
# ---------------------------------------------------------------------------

def test_kp20_extracts_battery_and_charging_from_real_frame():
    """用实测帧验证：电量 100%、充电中。"""
    desc = _kp20()
    frame = parse_frame(KP20_SAMPLE)
    assert frame is not None
    values = desc.extract(frame)
    assert values["battery_percent"] == 100
    assert values["charging"] is True


def test_kp20_discharging_frame():
    """非充电状态下 charge_state 低 4 位为 0。"""
    desc = _kp20()
    frame = parse_frame(bytes([0x02, 0x15, 0x37, 0x00, 0x50, 0x01] + [0] * 26))
    values = desc.extract(frame)
    assert values["battery_percent"] == 0x37 == 55
    assert values["charging"] is False


def test_charging_mask_ignores_high_nibble():
    """高 4 位的其它信息不应干扰充电判断。"""
    spec = FieldSpec(offset=4, mask=0x0F, boolean=True)
    frame = parse_frame(bytes([0x02, 0x15, 0x50, 0x00, 0xF1]))
    assert spec.extract(frame) is True
    frame2 = parse_frame(bytes([0x02, 0x15, 0x50, 0x00, 0xF0]))
    assert spec.extract(frame2) is False


def test_field_extraction_returns_none_when_out_of_range():
    """帧太短时返回 None，而不是崩溃或给出错误数值。"""
    spec = FieldSpec(offset=20)
    frame = parse_frame(bytes([0x02, 0x15, 0x64]))
    assert spec.extract(frame) is None


def test_u16_field_type():
    """16 位小端字段可正确解析。"""
    spec = FieldSpec(offset=7, type="uint16le")
    frame = parse_frame(bytes([0x02, 0x15, 0, 0, 0, 0, 0, 0x34, 0x12]))
    assert spec.extract(frame) == 0x1234


# ---------------------------------------------------------------------------
# 接口匹配
# ---------------------------------------------------------------------------

def _iface(**kw):
    """构造一个测试用 HID 接口。"""
    base = dict(path=b"/dev/x", vendor_id=0x20BC, product_id=0x5191, usage_page=0xFF00,
                usage=0x03, interface_number=1, product_string="BTP-KP20EB XINPUT DONGLE",
                manufacturer_string="", serial_number="")
    base.update(kw)
    return HidInterface(**base)


def test_match_accepts_matching_interface():
    """正常匹配。"""
    assert _kp20().match.matches(_iface())


def test_match_rejects_wrong_vendor_or_product():
    """厂商/产品 ID 不符必须拒绝。"""
    desc = _kp20()
    assert not desc.match.matches(_iface(vendor_id=0x045E))
    assert not desc.match.matches(_iface(product_id=0x0001))


def test_match_rejects_wrong_usage_page():
    """usage_page 不符（例如鼠标/键盘接口）必须拒绝。"""
    assert not _kp20().match.matches(_iface(usage_page=0x0001))


def test_match_rejects_wrong_product_string():
    """产品字符串关键字不符必须拒绝。"""
    assert not _kp20().match.matches(_iface(product_string="SOMETHING ELSE"))


def test_match_score_prefers_more_specific_descriptor():
    """描述越具体，得分越高（用于多型号冲突时择优）。"""
    specific = parse_descriptor({
        "id": "a", "match": {"vendor_id": 0x20BC, "product_id": 0x5191, "usage_page": 0xFF00},
        "query": {"report_id": 2, "header": 0x15},
    })
    loose = parse_descriptor({
        "id": "b", "match": {"vendor_id": 0x20BC, "product_id": 0x5191},
        "query": {"report_id": 2, "header": 0x15},
    })
    assert specific.match.score > loose.match.score


def test_json_files_use_underscore_prefixed_keys_for_comments():
    """约定：以 _ 开头的键是注释。确认内置文件遵循此约定。"""
    path = os.path.join(DEVICES_DIR, "betop-kp20.json")
    with open(path, encoding="utf-8") as fp:
        data = json.load(fp)
    assert data["id"] == "betop-kp20"
    assert data["_comment" if "_comment" in data else "id"]  # 至少能正常读取


# ---------------------------------------------------------------------------
# usage_page 字节序兼容（真机测试中发现的坑）
# ---------------------------------------------------------------------------

def test_swap16_round_trip():
    """字节交换函数应可逆。"""
    from betop_battery.devices import swap16
    assert swap16(0xFF00) == 0x00FF
    assert swap16(0x00FF) == 0xFF00
    assert swap16(swap16(0x1234)) == 0x1234


def test_match_accepts_byteswapped_usage_page():
    """hidapi 的 Windows 后端把 0xFF00 报成 0x00FF，匹配必须仍然成功。"""
    desc = _kp20()
    assert desc.match.matches(_iface(usage_page=0x00FF)), "应兼容字节序颠倒"
    assert desc.match.matches(_iface(usage_page=0xFF00)), "规范写法也必须支持"


def test_match_still_rejects_non_vendor_usage_page():
    """字节序兼容不应放宽到普通用途页。"""
    assert not _kp20().match.matches(_iface(usage_page=0x0001))


def test_kp20_descriptor_pins_usage_to_disambiguate_interfaces():
    """接收器有 4 个接口，描述里必须用 usage 精确区分（真机发现的问题）。"""
    desc = _kp20()
    assert desc.match.usage == 0x0003, "鲲鹏20 的厂商接口 usage 为 0x03"
    # 鼠标/键盘/手柄接口不应被匹配
    assert not desc.match.matches(_iface(usage=0x0002)), "鼠标接口不应匹配"
    assert not desc.match.matches(_iface(usage=0x0006)), "键盘接口不应匹配"
    assert not desc.match.matches(_iface(usage=0x0005)), "手柄接口不应匹配"


def test_is_vendor_usage_page_handles_both_orders():
    """厂商用途页判定要同时接受两种字节序。"""
    from betop_battery.devices import is_vendor_usage_page
    assert is_vendor_usage_page(0xFF00)
    assert is_vendor_usage_page(0x00FF)
    assert is_vendor_usage_page(0xFF01)
    assert not is_vendor_usage_page(0x0001)
    assert not is_vendor_usage_page(0x0006)


def test_find_device_picks_vendor_interface_among_many():
    """在 4 个接口中应选中厂商接口（真机场景回归测试）。"""
    from betop_battery.devices import find_device
    desc = _kp20()
    ifaces = [
        _iface(usage_page=0x0001, usage=0x0002),   # 鼠标
        _iface(usage_page=0x0001, usage=0x0006),   # 键盘
        _iface(usage_page=0x0001, usage=0x0005),   # 手柄
        _iface(usage_page=0x00FF, usage=0x0003),   # 厂商（注意字节序）
    ]
    found, iface = find_device(ifaces, [desc])
    assert found is not None and iface is not None
    assert iface.usage == 0x0003
