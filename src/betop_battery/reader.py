# -*- coding: utf-8 -*-
"""编排层：把”传输层 + 协议层 + 设备描述”串成一次完整的电量读取。

对上层（CLI / 托盘）只暴露一个简单接口 :meth:`BatteryReader.read`，
返回统一的 :class:`BatteryStatus`。上层因此完全不需要知道 HID 细节。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Optional

from .devices import (
    DEVICES_DIR,
    DeviceDescriptor,
    find_device,
    load_descriptors,
)
from .state import DEFAULT_MAX_AGE, SharedStatus, load_status, save_status
from .transport import (
    HIDAPI_AVAILABLE,
    HidError,
    HidInterface,
    HidSession,
    enumerate_interfaces,
)


@dataclass
class BatteryStatus:
    """一次读取的结果（无论成功与否都返回本对象，便于 UI 统一处理）。"""

    device_id: str = ""
    device_name: str = ""
    battery_percent: Optional[int] = None
    charging: Optional[bool] = None
    raw_frame: Optional[bytes] = None
    fields: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        """是否读到了电量。"""
        return self.error is None and self.battery_percent is not None

    def summary(self) -> str:
        """一行人类可读摘要（托盘提示、CLI 都用它）。"""
        if self.error:
            return f"{self.device_name or '手柄'}：{self.error}"
        parts = [self.device_name or "手柄"]
        if self.battery_percent is not None:
            parts.append(f"电量 {self.battery_percent}%")
        if self.charging:
            parts.append("充电中")
        return " · ".join(parts)


class BatteryReader:
    """电量读取器（无状态，可重复调用）。

    ⚠️ **当前版本以单手柄为前提开发**：``find_connected`` 会在所有匹配接口中
    选一个（描述最具体者优先），并只读取它。同时连接多只手柄时，
    行为可能不符合预期（例如只显示其中一只，或读数在两支之间跳变）。
    多手柄支持见 README 的"已知限制"。

    Args:
        descriptors: 指定设备描述；默认自动从 ``devices/`` 目录加载
        device_id:   只使用指定 id 的型号（多手柄时有用）
    """

    def __init__(
        self,
        descriptors: Optional[list[DeviceDescriptor]] = None,
        device_id: Optional[str] = None,
    ) -> None:
        self._descriptors = descriptors if descriptors is not None else load_descriptors(DEVICES_DIR)
        if device_id:
            self._descriptors = [d for d in self._descriptors if d.id == device_id]
        if not self._descriptors:
            raise ValueError(
                "没有可用的设备描述文件（devices/*.json）。"
                "请检查是否缺少文件或 id 过滤条件写错了。"
            )

    # -- 只读查询 ---------------------------------------------------------

    @property
    def supported_devices(self) -> list[DeviceDescriptor]:
        """当前支持的型号列表。"""
        return list(self._descriptors)

    def find_connected(self) -> tuple[Optional[DeviceDescriptor], Optional[HidInterface]]:
        """找出当前已连接的受支持设备。

        Returns:
            ``(descriptor, interface)``；未找到为 ``(None, None)``。
        """
        if not HIDAPI_AVAILABLE:
            return None, None
        return find_device(enumerate_interfaces(), self._descriptors)

    # -- 主流程 -----------------------------------------------------------

    def read(self, timeout: float = 2.0, drain_rounds: int = 30,
             source: str = "", max_cache_age: float = DEFAULT_MAX_AGE) -> BatteryStatus:
        """读取当前连接手柄的电量。

        会优先复用**共享缓存**中足够新的读数：托盘、图形界面、HUD 是三个独立进程，
        若各自去读 HID，会因为手柄电量本身在浮动而出现界面间数值不一致
        （例如设置窗口 94%、托盘图标 93%）。复用同一份读数即可保持一致，
        顺带减少轮询。

        Args:
            timeout:        等待响应帧的秒数
            drain_rounds:   发送查询前先丢弃多少轮旧数据
            source:         调用者标识（tray/gui/hud/cli），仅用于缓存溯源
            max_cache_age:  缓存新鲜度窗口（秒）；``<=0`` 表示禁用缓存

        Returns:
            :class:`BatteryStatus`（失败时 ``error`` 有值，不会抛异常）。
        """
        if max_cache_age > 0:
            cached = load_status()
            if cached is not None and cached.error is None and cached.age() <= max_cache_age:
                return BatteryStatus(
                    device_id=cached.device_id,
                    device_name=cached.device_name,
                    battery_percent=cached.battery_percent,
                    charging=cached.charging,
                    timestamp=cached.timestamp,
                )

        if not HIDAPI_AVAILABLE:
            return BatteryStatus(error="未安装 hidapi（pip install hidapi）")

        try:
            desc, iface = self.find_connected()
        except HidError as exc:
            return BatteryStatus(error=str(exc))

        if desc is None or iface is None:
            return BatteryStatus(
                device_name="未找到手柄",
                error="未检测到受支持的手柄（请按一下手柄按键唤醒，或确认接收器已插好）",
            )

        status = BatteryStatus(device_id=desc.id, device_name=desc.name)
        try:
            with HidSession(iface.path) as session:
                session.drain(rounds=drain_rounds)
                session.write_query(desc.query.payload, lengths=desc.query.write_lengths)
                frame = session.read_until(desc.reply_header, timeout=timeout)
        except HidError as exc:
            status.error = str(exc)
            return status

        if frame is None:
            status.error = "未收到状态响应（手柄可能处于休眠，按一下按键再试）"
            return status

        status.raw_frame = frame.raw
        status.fields = desc.extract(frame)
        percent = status.fields.get("battery_percent")
        status.battery_percent = int(percent) if isinstance(percent, (int, float)) else None
        charging = status.fields.get("charging")
        status.charging = bool(charging) if charging is not None else None

        # 写入共享缓存，供其它界面进程复用（保持显示一致）
        save_status(SharedStatus(
            battery_percent=status.battery_percent,
            charging=status.charging,
            device_id=status.device_id,
            device_name=status.device_name,
            timestamp=status.timestamp,
            source=source,
        ))
        return status
