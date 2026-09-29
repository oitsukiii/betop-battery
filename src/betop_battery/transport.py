# -*- coding: utf-8 -*-
"""HID 传输层：设备枚举、打开、写入查询、读取帧。

设计原则
--------
本模块**只关心”怎么跟 HID 说话”**，完全不知道北通、不知道协议语义、
也不知道电量字段在哪里 —— 那些分别由 :mod:`.devices` 与 :mod:`.protocol` 负责。
这样换平台（Linux/macOS 同样用 hidapi）或换设备都不需要改这里。

hidapi 是可选依赖：没装时本模块仍可导入（便于跑单元测试 / 看帮助），
真正打开设备时才报错。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Iterable, Iterator, Optional

from .protocol import Frame, parse_frame

# ---------------------------------------------------------------------------
# hidapi 可选导入
# ---------------------------------------------------------------------------

try:  # pragma: no cover - 取决于运行环境
    import hid  # type: ignore

    HIDAPI_AVAILABLE = True
    HIDAPI_IMPORT_ERROR: Optional[str] = None
except Exception as exc:  # pragma: no cover
    hid = None  # type: ignore
    HIDAPI_AVAILABLE = False
    HIDAPI_IMPORT_ERROR = str(exc)

#: HID 报告的最大读取长度。北通接收器的报告较短，用 64 足以覆盖。
REPORT_READ_SIZE = 64


class HidError(RuntimeError):
    """HID 相关错误（未安装 hidapi、打不开设备等）。"""


# ---------------------------------------------------------------------------
# 设备枚举
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class HidInterface:
    """一个 HID 接口的只读描述（跨平台统一结构）。"""

    path: bytes
    vendor_id: int
    product_id: int
    usage_page: int
    usage: int
    interface_number: Optional[int]
    product_string: str
    manufacturer_string: str
    serial_number: str

    @property
    def label(self) -> str:
        """适合打印的一行摘要。"""
        return (
            f"VID={self.vendor_id:#06x} PID={self.product_id:#06x} "
            f"usage_page={self.usage_page:#06x} usage={self.usage:#06x} "
            f"iface={self.interface_number} ({self.product_string or '?'})"
        )


def enumerate_interfaces() -> list[HidInterface]:
    """枚举系统上全部 HID 接口。

    Returns:
        HidInterface 列表；未安装 hidapi 时抛 :class:`HidError`。
    """
    if not HIDAPI_AVAILABLE:
        raise HidError(f"未安装 hidapi（{HIDAPI_IMPORT_ERROR}）。请先 pip install hidapi")
    result: list[HidInterface] = []
    for raw in hid.enumerate():
        result.append(
            HidInterface(
                path=raw.get("path") or b"",
                vendor_id=int(raw.get("vendor_id") or 0),
                product_id=int(raw.get("product_id") or 0),
                usage_page=int(raw.get("usage_page") or 0),
                usage=int(raw.get("usage") or 0),
                interface_number=raw.get("interface_number"),
                product_string=raw.get("product_string") or "",
                manufacturer_string=raw.get("manufacturer_string") or "",
                serial_number=raw.get("serial_number") or "",
            )
        )
    return result


# ---------------------------------------------------------------------------
# 会话
# ---------------------------------------------------------------------------

class HidSession:
    """一次 HID 会话（上下文管理器）。

    用法::

        with HidSession(iface.path) as s:
            s.drain()
            s.write_query(frame_bytes, lengths=(64, 32, 16))
            for f in s.read_frames(duration=1.0):
                ...
    """

    def __init__(self, path: bytes) -> None:
        self._path = path
        self._dev = None

    # -- 生命周期 ---------------------------------------------------------

    def __enter__(self) -> "HidSession":
        self.open()
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def open(self) -> None:
        """打开设备并把读操作设为非阻塞。"""
        if not HIDAPI_AVAILABLE:
            raise HidError(f"未安装 hidapi（{HIDAPI_IMPORT_ERROR}）")
        try:
            self._dev = hid.device()
            self._dev.open_path(self._path)
        except Exception as exc:  # hidapi 抛的是普通 Exception
            raise HidError(
                f"打不开 HID 接口（可能被官方客户端独占，或手柄已休眠）：{exc}"
            ) from exc
        try:
            self._dev.set_nonblocking(1)
        except Exception:
            pass  # 个别后端不支持，忽略

    def close(self) -> None:
        """关闭设备（幂等）。"""
        if self._dev is not None:
            try:
                self._dev.close()
            except Exception:
                pass
            self._dev = None

    # -- 读写 -------------------------------------------------------------

    def drain(self, rounds: int = 30, delay: float = 0.004) -> int:
        """丢弃缓冲区里已有的旧帧，避免误判响应。

        Args:
            rounds: 最多读取次数
            delay:  每次之间的间隔（秒）

        Returns:
            丢弃的帧数。
        """
        dropped = 0
        for _ in range(rounds):
            if self._read_once() is not None:
                dropped += 1
            time.sleep(delay)
        return dropped

    def write_query(self, payload: bytes, lengths: Iterable[int] = (64, 32, 16)) -> int:
        """把查询帧写入设备，自动尝试多种写入长度。

        之所以要试多种长度：不同固件对**中断 OUT 报告长度**要求不同，
        写少了会被驱动拒绝。

        Args:
            payload: 查询帧（至少 report_id + header 两字节）
            lengths: 依次尝试的总长度

        Returns:
            实际写入成功时使用的长度。

        Raises:
            HidError: 所有长度都写不进去。
        """
        if self._dev is None:
            raise HidError("设备未打开")
        last_error: Optional[Exception] = None
        for length in lengths:
            if length < len(payload):
                continue
            buf = payload + b"\x00" * (length - len(payload))
            try:
                written = self._dev.write(buf)
                # 有些后端返回写入字节数，0/负数视为失败
                if written is None or written >= 0:
                    return length
            except Exception as exc:
                last_error = exc
        raise HidError(f"写入查询失败（尝试长度 {list(lengths)}）：{last_error}")

    def read_frames(self, duration: float = 1.0, poll: float = 0.004) -> Iterator[Frame]:
        """在给定时间窗口内持续读取并解析帧。

        Args:
            duration: 持续秒数
            poll:     每轮之间的间隔（秒）

        Yields:
            解析成功的 :class:`Frame`。
        """
        deadline = time.monotonic() + duration
        while time.monotonic() < deadline:
            frame = self._read_once()
            if frame is not None:
                yield frame
            time.sleep(poll)

    def read_until(self, expected_header: int, timeout: float = 2.0,
                   poll: float = 0.004) -> Optional[Frame]:
        """等待 header 匹配的响应帧。

        Args:
            expected_header: 期望的 header 字节
            timeout:         超时秒数
            poll:            轮询间隔

        Returns:
            匹配的帧；超时返回 None。
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            frame = self._read_once()
            if frame is not None and frame.header == (expected_header & 0xFF):
                return frame
            time.sleep(poll)
        return None

    # -- 内部 -------------------------------------------------------------

    def _read_once(self) -> Optional[Frame]:
        """读一次（非阻塞）；无数据返回 None。"""
        if self._dev is None:
            return None
        try:
            data = self._dev.read(REPORT_READ_SIZE)
        except Exception:
            return None
        if not data:
            return None
        return parse_frame(bytes(data))
