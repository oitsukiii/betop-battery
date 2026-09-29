# -*- coding: utf-8 -*-
"""以子进程方式启动本程序的其它子命令。

为什么需要：托盘、图形界面、叠加层是三个**独立进程**，各自有独立的事件循环。
把 tkinter 窗口塞进托盘的进程会踩到"GUI 必须跑在主线程"的限制，
分开进程后彼此互不干扰，崩一个也不影响另一个。

本模块负责屏蔽"打包成 exe"与"直接跑源码"两种情形的差异。
"""

from __future__ import annotations

import os
import subprocess
import sys
from typing import Optional


def _root_dir() -> str:
    """源码运行时的仓库根目录。"""
    # 本文件位于 <root>/src/betop_battery/proc.py
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def build_command(*args: str) -> list[str]:
    """构造"再启动一个自己"的命令行。

    Args:
        *args: 要传给子命令的参数，例如 ``("gui",)``

    Returns:
        命令与参数列表，可直接交给 :class:`subprocess.Popen`。
    """
    if getattr(sys, "frozen", False):
        # 打包后：sys.executable 就是本 exe，直接带参数再跑一次
        return [sys.executable, *args]
    # 源码运行：用 run.py 作为入口（它会正确设置 sys.path）
    return [sys.executable, os.path.join(_root_dir(), "run.py"), *args]


def spawn(*args: str, hidden: bool = True) -> Optional[subprocess.Popen]:
    """启动一个本程序的子进程。

    Args:
        args:   子命令参数
        hidden: Windows 上是否隐藏窗口（避免弹控制台）

    Returns:
        Popen 对象；启动失败返回 None（调用方通常只需忽略）。
    """
    try:
        kwargs: dict = {}
        if os.name == "nt":
            # DETACHED_PROCESS 让子进程脱离当前控制台；CREATE_NO_WINDOW 抑制窗口
            kwargs["creationflags"] = 0x00000008 | (0x08000000 if hidden else 0)
        else:
            kwargs["start_new_session"] = True
        return subprocess.Popen(build_command(*args), **kwargs)
    except Exception:
        return None
