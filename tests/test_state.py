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
