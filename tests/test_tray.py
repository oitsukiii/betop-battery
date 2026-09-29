# -*- coding: utf-8 -*-
"""托盘层测试：只测纯逻辑（配色），不依赖真实托盘环境。"""

import pytest

from betop_battery.reader import BatteryStatus
from betop_battery.tray import (
    COLOR_CHARGING,
    COLOR_LOW,
    COLOR_OK,
    COLOR_UNKNOWN,
    COLOR_WARN,
    pick_color,
)


def test_color_green_when_healthy():
    """电量充足用绿色。"""
    s = BatteryStatus(battery_percent=80, charging=False)
    assert pick_color(s, 20) == COLOR_OK


def test_color_yellow_when_medium():
    """中等电量用黄色。"""
    s = BatteryStatus(battery_percent=40, charging=False)
    assert pick_color(s, 20) == COLOR_WARN


def test_color_red_at_or_below_threshold():
    """低于阈值用红色。"""
    assert pick_color(BatteryStatus(battery_percent=20, charging=False), 20) == COLOR_LOW
    assert pick_color(BatteryStatus(battery_percent=5, charging=False), 20) == COLOR_LOW


def test_color_blue_when_charging_even_if_low():
    """充电中优先显示蓝色（避免边充边报警）。"""
    s = BatteryStatus(battery_percent=8, charging=True)
    assert pick_color(s, 20) == COLOR_CHARGING


def test_color_grey_when_error_or_unknown():
    """读不到时用灰色。"""
    assert pick_color(BatteryStatus(error="休眠"), 20) == COLOR_UNKNOWN
    assert pick_color(BatteryStatus(battery_percent=None), 20) == COLOR_UNKNOWN


def test_status_summary_is_human_readable():
    """摘要文本供托盘提示使用。"""
    s = BatteryStatus(device_name="北通鲲鹏20", battery_percent=55, charging=True)
    text = s.summary()
    assert "北通鲲鹏20" in text and "55%" in text and "充电中" in text


def test_status_ok_property():
    """ok 仅在无错误且有电量时为真。"""
    assert BatteryStatus(battery_percent=50).ok
    assert not BatteryStatus(battery_percent=None).ok
    assert not BatteryStatus(battery_percent=50, error="x").ok


def test_render_icon_requires_pillow_but_does_not_crash_import():
    """Pillow 缺失时应给出明确错误提示（而不是 ImportError 堆栈）。"""
    try:
        import PIL  # noqa: F401
    except ImportError:
        pytest.skip("未安装 Pillow")
    from betop_battery.tray import render_icon

    img = render_icon(BatteryStatus(battery_percent=88, charging=False), 20)
    assert img.size == (64, 64)
