# -*- coding: utf-8 -*-
"""图标绘制测试：验证三种样式都能出图，且配色逻辑正确。

这些是纯函数，不需要托盘环境，也不需要手柄。
"""

import pytest

from betop_battery.icon import (
    COLOR_CHARGING,
    COLOR_LOW,
    COLOR_MONO,
    COLOR_OK,
    COLOR_UNKNOWN,
    COLOR_WARN,
    ICON_SIZE,
    IconStyle,
    pick_color,
    render_icon,
)
from betop_battery.reader import BatteryStatus

PIL = pytest.importorskip("PIL", reason="需要 Pillow 才能测试图标绘制")


# ---------------------------------------------------------------------------
# 配色
# ---------------------------------------------------------------------------

def test_auto_scheme_colors_by_level():
    """自动配色：充足绿、中等黄、低红、充电蓝。"""
    assert pick_color(BatteryStatus(battery_percent=90), 20) == COLOR_OK
    assert pick_color(BatteryStatus(battery_percent=40), 20) == COLOR_WARN
    assert pick_color(BatteryStatus(battery_percent=10), 20) == COLOR_LOW
    assert pick_color(BatteryStatus(battery_percent=10, charging=True), 20) == COLOR_CHARGING


def test_mono_scheme_ignores_level():
    """单色方案下所有状态同色。"""
    for status in (BatteryStatus(battery_percent=90),
                   BatteryStatus(battery_percent=5),
                   BatteryStatus(error="x")):
        assert pick_color(status, 20, "mono") == COLOR_MONO


def test_unknown_status_uses_grey():
    """读不到时用灰色。"""
    assert pick_color(BatteryStatus(error="休眠"), 20) == COLOR_UNKNOWN
    assert pick_color(BatteryStatus(battery_percent=None), 20) == COLOR_UNKNOWN


# ---------------------------------------------------------------------------
# 样式
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("style", ["number", "ring", "battery"])
def test_all_styles_render(style):
    """每种内置样式都能渲染出正确尺寸的图片。"""
    img = render_icon(BatteryStatus(battery_percent=75, charging=False),
                      IconStyle(style=style), ICON_SIZE)
    assert img.size == (ICON_SIZE, ICON_SIZE)
    assert img.mode == "RGBA"


@pytest.mark.parametrize("style", ["number", "ring", "battery"])
def test_all_styles_render_with_unknown_battery(style):
    """读不到电量时也要能出图（显示 --），不能崩。"""
    img = render_icon(BatteryStatus(battery_percent=None, error="休眠"),
                      IconStyle(style=style), ICON_SIZE)
    assert img.size == (ICON_SIZE, ICON_SIZE)


def test_unknown_style_falls_back_to_default():
    """样式名写错时回退到数字样式，而不是抛异常。"""
    img = render_icon(BatteryStatus(battery_percent=50), IconStyle(style="不存在的样式"))
    assert img.size == (ICON_SIZE, ICON_SIZE)


def test_custom_size_is_respected():
    """自定义尺寸生效（GUI 预览用了放大尺寸）。"""
    img = render_icon(BatteryStatus(battery_percent=50), IconStyle(), 128)
    assert img.size == (128, 128)


def test_charging_marker_can_be_disabled():
    """关闭充电标记后不影响出图。"""
    for marker in (True, False):
        img = render_icon(BatteryStatus(battery_percent=50, charging=True),
                          IconStyle(show_charging_marker=marker))
        assert img.size == (ICON_SIZE, ICON_SIZE)


def test_icon_differs_between_styles():
    """不同样式应当画出不同的像素（避免"改了没反应"）。"""
    status = BatteryStatus(battery_percent=60)
    a = render_icon(status, IconStyle(style="number")).tobytes()
    b = render_icon(status, IconStyle(style="ring")).tobytes()
    assert a != b


# ---------------------------------------------------------------------------
# IconStyle 序列化
# ---------------------------------------------------------------------------

def test_icon_style_round_trip():
    """IconStyle 可以安全地序列化/反序列化。"""
    original = IconStyle(style="battery", scheme="mono",
                         show_charging_marker=False, low_threshold=35)
    restored = IconStyle.from_dict(original.to_dict())
    assert restored == original


def test_icon_style_ignores_unknown_keys():
    """配置里有多余字段时不应报错（向前兼容）。"""
    style = IconStyle.from_dict({"style": "ring", "不认识的字段": 123})
    assert style.style == "ring"


def test_icon_style_from_empty_dict_uses_defaults():
    """空配置用默认值。"""
    assert IconStyle.from_dict(None) == IconStyle()
    assert IconStyle.from_dict({}) == IconStyle()


# ---------------------------------------------------------------------------
# HUD 图标语义
# ---------------------------------------------------------------------------

def test_emoji_for_charging_switches_icon():
    """充电状态用图标表达：充电 ⚡、用电池 🔋。"""
    from betop_battery.overlay import emoji_for

    assert emoji_for(True) == "⚡"
    assert emoji_for(False) == "🔋"
    assert emoji_for(None) == "🔋", "状态未知时按用电池显示"


def test_emoji_and_text_use_separate_fonts():
    """中文与 emoji 必须用不同字体族，否则微软雅黑缺 emoji 字形会显示方框。"""
    from betop_battery import overlay

    assert overlay.EMOJI_FONT_FAMILY != overlay.TEXT_FONT_FAMILY
    assert "YaHei" in overlay.TEXT_FONT_FAMILY, "中文默认用微软雅黑"
    assert "Emoji" in overlay.EMOJI_FONT_FAMILY
