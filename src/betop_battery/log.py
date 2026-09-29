# -*- coding: utf-8 -*-
"""安全的日志输出。

为什么需要这个模块
------------------
打包成 **`--windowed`（无控制台）** 的 exe 后，``sys.stdout`` 可能是 ``None``。
此时任何 ``print()`` 都会抛 ``AttributeError``，把后台线程直接搞死。
（托盘程序的后台轮询正是在这种环境下运行。）

因此统一用 :func:`make_logger` 取得一个**永远可用**的日志函数：
有控制台就打印，没有就写文件。
"""

from __future__ import annotations

import os
import sys
import time
from typing import Callable

from .config import config_dir

#: 无控制台时的日志文件名
LOG_FILENAME = "betop-battery.log"

#: 日志文件上限（超过就截断，避免无限增长）
MAX_LOG_BYTES = 256 * 1024


def prepare_output() -> None:
    """让标准输出在中文 Windows 上**永不因编码崩溃**。

    问题背景：中文 Windows 的默认输出编码是 GBK。当输出被**重定向到文件/管道**时，
    只要打印了 GBK 无法表示的字符（如 ``✅`` / ``🔋``），Python 就会抛
    ``UnicodeEncodeError`` 并中断整个程序 —— 这在 ``probe dump > out.txt`` 这种
    常见用法下必然踩到。

    处理策略：
      * 输出到**控制台**：保留原编码（控制台能正确显示中文），只把错误策略改成
        ``replace``，保证不崩。
      * 输出被**重定向**：切到 UTF-8（便于脚本/工具处理），同样不崩。
    """
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is None:
            continue
        try:
            isatty = bool(stream.isatty())
        except Exception:
            isatty = False
        try:
            if isatty:
                stream.reconfigure(errors="replace")
            else:
                stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass  # 不支持 reconfigure 的流（如被替换过的对象）直接忽略


def _stdout_usable() -> bool:
    """判断当前是否真的有可写的标准输出。"""
    stream = getattr(sys, "stdout", None)
    if stream is None:
        return False
    try:
        # windowed 模式下偶尔 stdin/stdout 存在但不可写
        return not stream.closed
    except Exception:
        return False


def log_file_path() -> str:
    """返回日志文件路径。"""
    return os.path.join(config_dir(), LOG_FILENAME)


def _append_to_file(message: str) -> None:
    """把一行写入日志文件（失败则彻底静默）。"""
    try:
        path = log_file_path()
        # 简单的体积控制：超过上限就重新开始
        if os.path.exists(path) and os.path.getsize(path) > MAX_LOG_BYTES:
            os.replace(path, path + ".old")
        with open(path, "a", encoding="utf-8") as fp:
            fp.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {message}\n")
    except Exception:
        pass  # 日志失败绝不能影响主功能


def make_logger(force_file: bool = False) -> Callable[[str], None]:
    """返回一个安全的日志函数。

    Args:
        force_file: 强制写文件（调试用）

    Returns:
        接受单个字符串的可调用对象。**永不抛异常。**
    """
    prepare_output()
    use_file = force_file or not _stdout_usable()

    def logger(message: str) -> None:
        if not use_file:
            try:
                print(message, flush=True)
                return
            except Exception:
                pass  # 落到文件
        _append_to_file(message)

    return logger
