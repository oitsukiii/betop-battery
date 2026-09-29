# -*- coding: utf-8 -*-
"""用户设置：读写一个小 JSON 配置文件。

放在用户目录而不是程序目录，避免”程序装在只读位置”或”更新程序覆盖设置”。
Windows: %APPDATA%\\betop-battery\\config.json
其它系统: ~/.config/betop-battery/config.json
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass

APP_NAME = "betop-battery"


def config_dir() -> str:
    """返回配置目录（不存在时创建）。"""
    if os.name == "nt":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    path = os.path.join(base, APP_NAME)
    os.makedirs(path, exist_ok=True)
    return path


def config_path() -> str:
    """返回配置文件路径。"""
    return os.path.join(config_dir(), "config.json")


@dataclass
class Settings:
    """可调参数。"""

    poll_seconds: int = 60
    """自动刷新间隔（秒）。"""

    low_battery_threshold: int = 20
    """低于该电量时弹通知。"""

    notify_on_low: bool = True
    """是否启用低电量通知。"""

    device_id: str = ""
    """只读取指定型号（留空 = 自动）。"""

    @classmethod
    def load(cls) -> "Settings":
        """从磁盘读取；文件不存在或损坏时返回默认值。"""
        path = config_path()
        try:
            with open(path, encoding="utf-8") as fp:
                data = json.load(fp)
        except FileNotFoundError:
            return cls()
        except Exception:
            # 配置损坏不该让程序起不来
            return cls()
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})

    def save(self) -> None:
        """写回磁盘（失败静默 —— 设置保存不了不应影响主功能）。"""
        try:
            with open(config_path(), "w", encoding="utf-8") as fp:
                json.dump(asdict(self), fp, ensure_ascii=False, indent=2)
        except Exception:
            pass
