# -*- coding: utf-8 -*-
"""共享读数缓存：让托盘、图形界面、HUD **显示同一个电量**。

为什么需要
----------
三个界面是**独立进程**，各自按自己的节奏去读 HID。手柄电量会随时间上下浮动
（例如 94% → 93%），于是很容易出现"设置窗口显示 94%、托盘图标显示 93%"的情况，
看起来像 bug。

解决办法：谁读到就把结果写进 ``state.json``（带时间戳），
其它进程在**新鲜度窗口**内直接采用这个值，不再重复读取。
副作用是顺便减少了 HID 轮询次数。

写入使用"临时文件 + 原子替换"，避免读到半截 JSON。
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from dataclasses import dataclass
from typing import Optional

from .config import config_dir

#: 缓存文件相对配置目录的位置（与设置分开，避免互相覆盖）
STATE_FILENAME = "state.json"

#: 默认新鲜度窗口（秒）：缓存比这更旧就重新读取设备
DEFAULT_MAX_AGE = 20.0


def state_path() -> str:
    """返回共享状态文件路径。"""
    return os.path.join(config_dir(), STATE_FILENAME)


@dataclass
class SharedStatus:
    """一次读数的快照。"""

    battery_percent: Optional[int] = None
    charging: Optional[bool] = None
    device_id: str = ""
    device_name: str = ""
    error: Optional[str] = None
    timestamp: float = 0.0
    source: str = ""          # 谁读的：tray / gui / hud / cli

    # -- 序列化 -----------------------------------------------------------

    def to_dict(self) -> dict:
        """转成可 JSON 序列化的字典。"""
        return {
            "battery_percent": self.battery_percent,
            "charging": self.charging,
            "device_id": self.device_id,
            "device_name": self.device_name,
            "error": self.error,
            "timestamp": self.timestamp,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "SharedStatus":
        """从字典还原（忽略未知键，容忍缺字段）。"""
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in (data or {}).items() if k in known})

    def age(self) -> float:
        """距读取时刻经过的秒数（时钟异常时返回一个很大的值）。"""
        delta = time.time() - (self.timestamp or 0.0)
        return delta if delta >= 0 else float("inf")


def save_status(status: SharedStatus) -> None:
    """原子写入共享状态（失败静默：缓存不可用不应影响主功能）。"""
    path = state_path()
    try:
        directory = os.path.dirname(path)
        fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as fp:
            json.dump(status.to_dict(), fp, ensure_ascii=False)
        os.replace(tmp, path)   # 原子替换，避免别人读到半个文件
    except Exception:
        try:
            os.unlink(tmp)      # noqa: F821 - tmp 可能未定义，下面统一忽略
        except Exception:
            pass


def load_status() -> Optional[SharedStatus]:
    """读取共享状态；文件不存在或损坏时返回 None。"""
    try:
        with open(state_path(), encoding="utf-8") as fp:
            return SharedStatus.from_dict(json.load(fp))
    except Exception:
        return None
