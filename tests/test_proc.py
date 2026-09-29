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
