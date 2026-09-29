# -*- coding: utf-8 -*-
"""进程相关工具测试：存活判断（含 Windows 安全性）与单实例锁。"""

import os
import subprocess
import sys
import time

import pytest

from betop_battery import config as config_mod
from betop_battery import proc


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    """把配置目录指到临时目录，避免污染真实配置。"""
    monkeypatch.setattr(config_mod, "config_dir", lambda: str(tmp_path))
    monkeypatch.setattr(proc, "_lock_path",
                        lambda name: os.path.join(str(tmp_path), f"{name}.pid"))
    yield tmp_path


# --------------------------------------------------------------------------- 存活判断


def test_pid_alive_false_for_invalid():
    """非法 PID 直接判否。"""
    assert proc.pid_alive(0) is False
    assert proc.pid_alive(-1) is False


def test_pid_alive_true_for_self_and_process_survives():
    """自己一定活着 —— 而且**探测之后必须还活着**。

    这条测试在 Windows 上尤其重要：如果实现里误用了 ``os.kill(pid, 0)``，
    Windows 会把它当成 TerminateProcess 把当前进程杀掉，测试直接崩
    （真机上踩过这个坑，所以特意留一条）。
    """
    assert proc.pid_alive(os.getpid()) is True
    time.sleep(0.1)
    assert proc.pid_alive(os.getpid()) is True, "探测动作不应影响目标进程"


def test_pid_alive_false_for_dead_process():
    """已退出的子进程应判否。"""
    child = subprocess.Popen([sys.executable, "-c", "print('hi')"])
    child.wait()
    time.sleep(0.2)
    assert proc.pid_alive(child.pid) is False


# --------------------------------------------------------------------------- 单实例锁


def test_acquire_succeeds_when_nothing_running(isolated_config):
    """没有别的实例时可以拿到锁。"""
    assert proc.acquire_single_instance("tray") is True
    assert (isolated_config / "tray.pid").read_text(encoding="utf-8") == str(os.getpid())


def test_acquire_is_idempotent_for_same_process(isolated_config):
    """同一个进程重复抢锁应成功（重入）。"""
    assert proc.acquire_single_instance("tray") is True
    assert proc.acquire_single_instance("tray") is True


def test_acquire_blocked_by_live_process(isolated_config):
    """已有活着的实例时，第二个实例必须被拦住。"""
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        (isolated_config / "tray.pid").write_text(str(child.pid), encoding="utf-8")
        assert proc.acquire_single_instance("tray") is False
    finally:
        child.kill()
        child.wait()


def test_acquire_recovers_from_stale_lock(isolated_config):
    """锁文件里的进程已死（上次崩溃残留）→ 应能正常接管。"""
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    time.sleep(0.2)
    (isolated_config / "tray.pid").write_text(str(child.pid), encoding="utf-8")
    assert proc.acquire_single_instance("tray") is True


def test_release_only_removes_own_lock(isolated_config):
    """释放锁时只删自己的，不误删别人的。"""
    lock = isolated_config / "tray.pid"
    lock.write_text("999999", encoding="utf-8")
    proc.release_single_instance("tray")
    assert lock.is_file(), "不应该删掉别人持有的锁"

    lock.write_text(str(os.getpid()), encoding="utf-8")
    proc.release_single_instance("tray")
    assert not lock.is_file(), "自己的锁应被删除"


def test_garbage_lock_file_does_not_block_startup(isolated_config):
    """锁文件内容损坏时也要能启动（不能把用户锁死在外面）。"""
    (isolated_config / "tray.pid").write_text("这不是数字", encoding="utf-8")
    assert proc.acquire_single_instance("tray") is True


# --------------------------------------------------------------------------- 按设置拉起组件


class _FakeSettings:
    """只带两个开关的假设置对象。"""

    def __init__(self, tray=True, hud=True):
        self.tray_enabled = tray
        self.overlay_enabled = hud


def test_ensure_processes_starts_both_on_first_run():
    """首次安装（两个开关都开）→ 托盘与 HUD 都要起来。"""
    started_cmds = []
    started = proc.ensure_processes(
        _FakeSettings(), runner=started_cmds.append, checker=lambda _n: False)
    assert started == ["tray", "hud"]
    assert started_cmds == ["tray", "overlay"], "命令行子命令要对应上"


def test_ensure_processes_respects_disabled_flags():
    """上次关掉了 HUD → 这次只起托盘（恢复上次状态）。"""
    cmds = []
    started = proc.ensure_processes(_FakeSettings(hud=False), runner=cmds.append,
                                    checker=lambda _n: False)
    assert started == ["tray"]
    assert cmds == ["tray"]

    cmds.clear()
    started = proc.ensure_processes(_FakeSettings(tray=False, hud=True), runner=cmds.append,
                                    checker=lambda _n: False)
    assert started == ["hud"]
    assert cmds == ["overlay"]


def test_ensure_processes_starts_nothing_when_all_disabled():
    """两个都关掉 → 什么都不启动（也不应该报错）。"""
    cmds = []
    started = proc.ensure_processes(_FakeSettings(False, False), runner=cmds.append,
                                    checker=lambda _n: False)
    assert started == [] and cmds == []


def test_ensure_processes_skips_already_running():
    """已经在跑的不要重复启动（单实例锁之外再加一道判断）。"""
    cmds = []
    started = proc.ensure_processes(_FakeSettings(), runner=cmds.append,
                                    checker=lambda _n: True)
    assert started == [] and cmds == []


def test_ensure_processes_partial_running():
    """托盘已在跑、HUD 没跑 → 只补起 HUD。"""
    cmds = []
    started = proc.ensure_processes(_FakeSettings(), runner=cmds.append,
                                    checker=lambda name: name == "tray")
    assert started == ["hud"] and cmds == ["overlay"]


def test_is_running_false_without_lock_file(isolated_config):
    """没有锁文件 → 没在跑。"""
    assert proc.is_running("tray") is False


def test_is_running_true_for_live_process(isolated_config):
    """锁文件指向活着的进程 → 认为在跑。"""
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        (isolated_config / "tray.pid").write_text(str(child.pid), encoding="utf-8")
        assert proc.is_running("tray") is True
    finally:
        child.kill()
        child.wait()


def test_is_running_ignores_own_pid(isolated_config):
    """锁是自己写的 → 不算"别的实例在跑"。"""
    (isolated_config / "tray.pid").write_text(str(os.getpid()), encoding="utf-8")
    assert proc.is_running("tray") is False


def test_overlay_defaults_to_enabled_for_first_run():
    """首次安装默认开启 HUD —— 这是"第一次打开两个都出现"的关键。"""
    from betop_battery.config import Settings

    fresh = Settings()
    assert fresh.tray_enabled is True
    assert fresh.overlay_enabled is True


def test_installer_creates_a_single_shortcut():
    """安装脚本只应建一个快捷方式（打开界面），并清理旧版多出来的那个。"""
    import os

    root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "packaging", "oneclick")
    script = open(os.path.join(root, "install.ps1"), encoding="utf-8").read()
    assert script.count('Args = "gui"') == 2, "桌面 + 开始菜单各一个，都指向界面"
    assert 'Args = "tray"' not in script, "不应再创建启动托盘的快捷方式"
    assert "$AppName-设置.lnk" in script, "应清理旧版残留的第二个快捷方式"


def test_gui_starts_components_from_settings():
    """设置界面启动时必须按设置拉起托盘 / HUD。"""
    import os

    root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "src", "betop_battery")
    gui = open(os.path.join(root, "gui.py"), encoding="utf-8").read()
    assert "ensure_processes(self._settings)" in gui
    assert "self._ensure_processes()" in gui, "run() 里要调用"
