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

    tray_enabled: bool = True
    """是否显示系统托盘图标。"""

    notify_on_low: bool = True
    """是否启用低电量通知。"""

    device_id: str = ""
    """只读取指定型号（留空 = 自动）。"""

    language: str = "auto"
    """界面语言：auto（跟随系统）/ zh（简体中文）/ en（English）。"""

    # ---------------------------------------------------------------- 图标外观

    icon_style: str = "number"
    """托盘图标样式：number（数字）/ ring（圆环）/ battery（电池）。"""

    icon_scheme: str = "auto"
    """配色方案：auto（按电量变色）/ mono（单色）。"""

    icon_show_charging_marker: bool = True
    """是否在图标上显示充电标记。"""

    # ---------------------------------------------------------------- 叠加层

    overlay_enabled: bool = False
    """是否启用 HUD（悬浮叠加层）。"""

    overlay_x: int = -1
    """HUD 横坐标；-1 = 自动放到右下角。"""

    overlay_y: int = -1
    """HUD 纵坐标；-1 = 自动放到右下角。"""

    overlay_reset_token: int = 0
    """位置复位令牌：界面点一次「复位」就 +1。

    为什么用令牌而不是靠 x/y = -1 判断：用户可能**从未拖动过** HUD，
    此时 x/y 本来就是 -1；如果 HUD 每次应用设置都把 -1 当作"复位"，
    那么锁定布局、改字号等操作都会让 HUD 莫名其妙跳回右下角（实测踩过）。
    令牌让"复位"成为一个**显式的、一次性的**信号。"""

    overlay_opacity: float = 0.78
    """HUD 不透明度（0.2~1.0）。"""

    overlay_font_size: int = 14
    """HUD 字号。"""

    overlay_click_through: bool = False
    """HUD 是否锁定布局（鼠标穿透，游戏时开启；同时无法拖动）。"""

    overlay_show_device: bool = True
    """HUD 是否显示手柄型号。"""

    overlay_show_battery: bool = True
    """HUD 是否显示电量。"""

    overlay_bg: str = "#0E0E12"
    """HUD 背景色。"""

    overlay_fg: str = "#FFFFFF"
    """HUD 文字颜色。"""

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

    def icon_style_object(self) -> "IconStyle":
        """把图标相关字段组装成 IconStyle（延迟导入避免循环依赖）。"""
        from .icon import IconStyle

        return IconStyle.from_dict({
            "style": self.icon_style,
            "scheme": self.icon_scheme,
            "show_charging_marker": self.icon_show_charging_marker,
            "low_threshold": self.low_battery_threshold,
        })

    def save(self) -> None:
        """写回磁盘（失败静默 —— 设置保存不了不应影响主功能）。"""
        try:
            with open(config_path(), "w", encoding="utf-8") as fp:
                json.dump(asdict(self), fp, ensure_ascii=False, indent=2)
        except Exception:
            pass
