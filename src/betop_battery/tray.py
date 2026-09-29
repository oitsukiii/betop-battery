# -*- coding: utf-8 -*-
"""系统托盘界面：在通知区域显示手柄电量。

职责边界（高内聚）
------------------
本模块**只负责展示与交互**：把 :class:`~betop_battery.reader.BatteryStatus`
渲染成图标/菜单/通知。它不碰 HID、不认识协议、也不知道字段偏移。

因此它可以在没有手柄的环境下被导入与单测（图标渲染逻辑是纯函数）。
"""

from __future__ import annotations

import threading
import time
from typing import Callable, Optional

from .config import Settings
from .reader import BatteryReader, BatteryStatus

# 托盘图标尺寸（Windows 通知区域推荐 16/32，取 64 更清晰，系统会缩放）
ICON_SIZE = 64

COLOR_OK = (34, 139, 34)        # 绿色：电量充足
COLOR_WARN = (218, 165, 32)     # 黄色：中等
COLOR_LOW = (200, 40, 40)       # 红色：低电量
COLOR_CHARGING = (30, 120, 220)  # 蓝色：充电中
COLOR_UNKNOWN = (120, 120, 120)  # 灰色：读不到


def _require_pil():
    """只加载 Pillow（绘制图标需要它，但不需要 pystray）。

    这样即使没装 pystray，图标渲染逻辑也能被单独测试。
    """
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("绘制图标需要 Pillow：pip install Pillow") from exc
    return Image, ImageDraw, ImageFont


def _require_gui():
    """托盘模式需要的全部依赖（pystray + Pillow）。"""
    try:
        import pystray
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "托盘模式需要 pystray 与 Pillow：pip install pystray Pillow"
        ) from exc
    Image, ImageDraw, ImageFont = _require_pil()
    return pystray, Image, ImageDraw, ImageFont


def pick_color(status: BatteryStatus, low_threshold: int) -> tuple[int, int, int]:
    """根据状态选图标颜色（纯函数，便于测试）。"""
    if status.error or status.battery_percent is None:
        return COLOR_UNKNOWN
    if status.charging:
        return COLOR_CHARGING
    if status.battery_percent <= low_threshold:
        return COLOR_LOW
    if status.battery_percent <= 50:
        return COLOR_WARN
    return COLOR_OK


def render_icon(status: BatteryStatus, low_threshold: int = 20):
    """把状态渲染成一张 PIL 图片（数字 + 颜色）。

    Args:
        status:        读取结果
        low_threshold: 低电量阈值（影响配色）

    Returns:
        PIL.Image（RGBA）
    """
    Image, ImageDraw, ImageFont = _require_pil()
    color = pick_color(status, low_threshold)
    img = Image.new("RGBA", (ICON_SIZE, ICON_SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # 圆角底
    draw.rounded_rectangle([2, 2, ICON_SIZE - 2, ICON_SIZE - 2], radius=12, fill=color)

    if status.battery_percent is None:
        text = "--"
    else:
        text = str(status.battery_percent)

    # 挑一个能放下的字号
    font = None
    for size in (34, 30, 26, 22, 18):
        try:
            font = ImageFont.truetype("arialbd.ttf", size)
            break
        except Exception:
            font = None
    if font is None:
        try:
            font = ImageFont.load_default()
        except Exception:
            font = None

    if font is not None:
        try:
            box = draw.textbbox((0, 0), text, font=font)
            tw, th = box[2] - box[0], box[3] - box[1]
            draw.text(((ICON_SIZE - tw) / 2 - box[0], (ICON_SIZE - th) / 2 - box[1]),
                      text, font=font, fill=(255, 255, 255, 255))
        except Exception:
            draw.text((12, 16), text, fill=(255, 255, 255, 255))

    # 充电中画一条闪电状的横条做提示
    if status.charging:
        draw.rectangle([10, ICON_SIZE - 12, ICON_SIZE - 10, ICON_SIZE - 8],
                       fill=(255, 255, 255, 220))
    return img


class TrayApp:
    """托盘应用：后台轮询 + 图标/菜单/通知。

    Args:
        reader:   电量读取器
        settings: 用户设置（会被就地更新并保存）
        on_log:   可选的日志回调（默认 print）
    """

    def __init__(self, reader: BatteryReader, settings: Optional[Settings] = None,
                 on_log: Optional[Callable[[str], None]] = None) -> None:
        self._reader = reader
        self._settings = settings or Settings.load()
        self._log = on_log or print
        self._status = BatteryStatus(device_name="正在读取…")
        self._icon = None
        self._stop = threading.Event()
        self._notified_low = False

    # -- 对外 -------------------------------------------------------------

    def run(self) -> int:
        """启动托盘（阻塞直到用户退出）。"""
        pystray, _, _, _ = _require_gui()
        self._icon = pystray.Icon(
            "betop-battery",
            icon=render_icon(self._status, self._settings.low_battery_threshold),
            title="北通手柄电量",
            menu=self._build_menu(),
        )
        threading.Thread(target=self._poll_loop, daemon=True).start()
        self._log("托盘已启动，右键图标可查看菜单。")
        self._icon.run()
        return 0

    # -- 轮询 -------------------------------------------------------------

    def _poll_loop(self) -> None:
        """后台轮询循环：定时读取并刷新图标。"""
        while not self._stop.is_set():
            self.refresh()
            # 可被 refresh 中的设置变更影响，所以每轮重新读取间隔
            self._stop.wait(max(5, int(self._settings.poll_seconds)))

    def refresh(self) -> BatteryStatus:
        """立即读一次并刷新界面。"""
        status = self._reader.read()
        self._status = status
        self._log(status.summary())
        self._update_icon()
        self._maybe_notify(status)
        return status

    # -- 界面更新 ---------------------------------------------------------

    def _update_icon(self) -> None:
        """把当前状态画到托盘图标上。"""
        if self._icon is None:
            return
        try:
            self._icon.icon = render_icon(self._status, self._settings.low_battery_threshold)
            self._icon.title = self._tooltip()
        except Exception as exc:  # pragma: no cover
            self._log(f"刷新图标失败：{exc}")

    def _tooltip(self) -> str:
        """悬浮提示文本。"""
        status = self._status
        if status.error:
            return f"北通手柄电量\n{status.error}"
        lines = [status.device_name or "北通手柄"]
        if status.battery_percent is not None:
            lines.append(f"电量：{status.battery_percent}%")
        if status.charging is not None:
            lines.append("状态：充电中" if status.charging else "状态：使用电池")
        lines.append("更新：" + time.strftime("%H:%M:%S", time.localtime(status.timestamp)))
        return "\n".join(lines)

    def _maybe_notify(self, status: BatteryStatus) -> None:
        """低电量时弹一次通知（充电后重置，避免反复打扰）。"""
        if not self._settings.notify_on_low or self._icon is None:
            return
        if status.charging or status.battery_percent is None:
            self._notified_low = False
            return
        if status.battery_percent <= self._settings.low_battery_threshold:
            if not self._notified_low:
                self._notified_low = True
                try:
                    self._icon.notify(
                        f"电量仅剩 {status.battery_percent}%，该充电了",
                        "北通手柄电量",
                    )
                except Exception:
                    pass
        else:
            self._notified_low = False

    # -- 菜单 -------------------------------------------------------------

    def _build_menu(self):
        """构造右键菜单。"""
        pystray, _, _, _ = _require_gui()

        def set_interval(seconds: int):
            def handler(icon, item):
                self._settings.poll_seconds = seconds
                self._settings.save()
                self._log(f"刷新间隔已设为 {seconds}s")
            return handler

        def set_threshold(value: int):
            def handler(icon, item):
                self._settings.low_battery_threshold = value
                self._settings.save()
                self._log(f"低电量阈值已设为 {value}%")
                self._notified_low = False
            return handler

        def toggle_notify(icon, item):
            self._settings.notify_on_low = not self._settings.notify_on_low
            self._settings.save()
            self._log(f"低电量通知：{'开' if self._settings.notify_on_low else '关'}")

        def do_refresh(icon, item):
            self.refresh()

        def do_quit(icon, item):
            self._stop.set()
            icon.stop()

        interval_menu = pystray.Menu(
            *[
                pystray.MenuItem(
                    f"{sec} 秒",
                    set_interval(sec),
                    checked=lambda item, s=sec: self._settings.poll_seconds == s,
                    radio=True,
                )
                for sec in (30, 60, 120, 300)
            ]
        )
        threshold_menu = pystray.Menu(
            *[
                pystray.MenuItem(
                    f"{pct}%",
                    set_threshold(pct),
                    checked=lambda item, p=pct: self._settings.low_battery_threshold == p,
                    radio=True,
                )
                for pct in (10, 20, 30, 40)
            ]
        )
        return pystray.Menu(
            pystray.MenuItem("立即刷新", do_refresh, default=True),
            pystray.MenuItem("刷新间隔", interval_menu),
            pystray.MenuItem("低电量提醒", threshold_menu),
            pystray.MenuItem(
                "启用通知",
                toggle_notify,
                checked=lambda item: self._settings.notify_on_low,
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("退出", do_quit),
        )


def run_tray(reader: BatteryReader, settings: Optional[Settings] = None) -> int:
    """便捷入口：直接启动托盘。"""
    return TrayApp(reader, settings).run()
