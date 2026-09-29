# -*- coding: utf-8 -*-
"""共享读数缓存的测试。

这个模块存在的意义是：托盘、设置窗口、HUD 是三个独立进程，
如果各自去读 HID，会因为手柄电量本身在浮动而出现"设置窗口 94%、托盘 93%"，
看起来像 bug。缓存让它们在一段时间窗口内显示同一个值。
"""

import json
import os
import time

import pytest

from betop_battery import state


@pytest.fixture(autouse=True)
def isolated_config_dir(tmp_path, monkeypatch):
    """把配置目录指到临时目录，避免污染真实用户配置。"""
    monkeypatch.setattr(state, "config_dir", lambda: str(tmp_path))
    yield tmp_path


def test_save_then_load_round_trip():
    """写入后能读回同样的值。"""
    original = state.SharedStatus(battery_percent=88, charging=True,
                                 device_id="betop-kp20", device_name="北通鲲鹏20",
                                 timestamp=time.time(), source="tray")
    state.save_status(original)
    loaded = state.load_status()
    assert loaded is not None
    assert loaded.battery_percent == 88
    assert loaded.charging is True
    assert loaded.device_id == "betop-kp20"
    assert loaded.source == "tray"


def test_load_returns_none_when_missing():
    """文件不存在时返回 None（不应抛异常）。"""
    assert state.load_status() is None


def test_load_returns_none_on_corrupt_file(tmp_path):
    """文件损坏时返回 None，而不是让调用方崩溃。"""
    with open(state.state_path(), "w", encoding="utf-8") as fp:
        fp.write("{ 这不是合法 JSON")
    assert state.load_status() is None


def test_load_ignores_unknown_fields():
    """多出来的字段应被忽略（向前兼容）。"""
    with open(state.state_path(), "w", encoding="utf-8") as fp:
        json.dump({"battery_percent": 50, "未来新增字段": 1}, fp)
    loaded = state.load_status()
    assert loaded is not None and loaded.battery_percent == 50


def test_age_reports_elapsed_time():
    """age() 反映经过的秒数。"""
    fresh = state.SharedStatus(timestamp=time.time())
    assert fresh.age() < 2
    old = state.SharedStatus(timestamp=time.time() - 100)
    assert old.age() >= 99


def test_age_handles_clock_going_backwards():
    """系统时钟被回拨时，age() 返回无穷大（视为过期，而不是负数）。"""
    future = state.SharedStatus(timestamp=time.time() + 10_000)
    assert future.age() == float("inf")


def test_save_is_atomic_leaves_no_temp_files(tmp_path):
    """原子写入后不应残留 .tmp 文件。"""
    state.save_status(state.SharedStatus(battery_percent=77, timestamp=time.time()))
    leftovers = [n for n in os.listdir(tmp_path) if n.endswith(".tmp")]
    assert leftovers == [], f"残留临时文件：{leftovers}"


def test_save_does_not_raise_when_directory_unusable(monkeypatch):
    """写入失败时静默（缓存不可用不应影响主功能）。"""
    monkeypatch.setattr(state, "config_dir", lambda: "/不存在/的/目录")
    state.save_status(state.SharedStatus(battery_percent=1))  # 不应抛异常


def test_placeholder_status_must_have_zero_timestamp():
    """占位状态的 timestamp 必须是 0。

    三个界面启动时都会先用一个"读取中…"的占位状态。如果它的 timestamp 是
    "现在"，那么任何比它早的真实读数（尤其是共享缓存里别人刚写的）都会被
    ``latest.timestamp >= self._status.timestamp`` 这类判断拒绝，
    界面就会一直停在"读取中…"—— 实测踩过这个坑。
    """
    import inspect

    from betop_battery import gui, overlay, tray

    for module in (gui, overlay, tray):
        source = inspect.getsource(module)
        assert 'timestamp=0.0' in source, f"{module.__name__} 的占位状态缺少 timestamp=0.0"


def test_hud_position_only_changes_on_explicit_reset():
    """HUD 位置只应在「首次启动」或「显式复位」时改变。

    真机反馈：锁定布局时 HUD 会跳回初始位置 —— 根因是每次应用设置都把
    ``overlay_x == -1`` 当作复位信号，而用户可能从未拖动过（x/y 本来就是 -1）。
    现在改用 overlay_reset_token 作为一次性显式信号。
    """
    import inspect

    from betop_battery import gui, overlay
    from betop_battery.config import Settings

    # 配置里必须有复位令牌
    assert "overlay_reset_token" in Settings.__dataclass_fields__

    # HUD 端：仅在 initial 或 reset_position 时移动
    src = inspect.getsource(overlay.OverlayWindow._apply_settings)
    assert "reset_position" in src and "if initial:" in src

    # 界面端：复位按钮必须递增令牌
    gui_src = inspect.getsource(gui.SettingsWindow._reset_hud_position)
    assert "overlay_reset_token += 1" in gui_src, "复位按钮必须递增令牌"


def test_apply_does_not_clobber_hud_position():
    """界面「应用」不能覆盖 HUD 自己保存的位置（否则拖动后会跳回）。"""
    import inspect

    from betop_battery import gui

    src = inspect.getsource(gui.SettingsWindow._apply_now)
    assert "Settings.load()" in src, "应用前应先读取磁盘上的最新配置"
    collect = inspect.getsource(gui.SettingsWindow._collect_settings_into)
    # 用正则找真正的赋值语句（注释里提到字段名不算）
    import re

    assert not re.search(r"\bs\.overlay_(x|y|reset_token)\s*=", collect), \
        "收集界面设置时不应给 overlay_x / overlay_y / overlay_reset_token 赋值"
