# -*- coding: utf-8 -*-
"""系统托盘界面：在通知区域显示手柄电量。

职责边界（高内聚）
------------------
本模块只负责**托盘交互与生命周期**：图标、菜单、通知、后台轮询。
图标**怎么画**在 :mod:`.icon`（纯函数、可单测），
电量**怎么读**在 :mod:`.reader`，本模块不碰 HID、不认识协议。

图形设置界面是**独立进程**（``betop-battery gui``），由菜单项拉起 ——
tkinter 要求 GUI 跑在主线程，与托盘的事件循环同处一个进程会互相打架。
"""

from __future__ import annotations

import threading
import time
from typing import Callable, Optional

from .config import Settings
from .icon import ICON_SIZE, render_icon
from .log import make_logger
from .proc import spawn
from .reader import BatteryReader, BatteryStatus


def _require_pystray():
    """加载 pystray（仅托盘模式需要）。"""
    try:
        import pystray
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "托盘模式需要 pystray 与 Pillow：pip install pystray Pillow"
        ) from exc
    return pystray


class TrayApp:
    """托盘应用：后台轮询 + 图标/菜单/通知。

    Args:
        reader:   电量读取器
        settings: 用户设置（会被就地更新并保存）
        on_log:   可选的日志回调（默认写控制台或日志文件）
    """

    def __init__(self, reader: BatteryReader, settings: Optional[Settings] = None,
                 on_log: Optional[Callable[[str], None]] = None) -> None:
        self._reader = reader
        self._settings = settings or Settings.load()
        # 默认用安全日志：打包成无控制台的 exe 后 print 会失败，那时自动写文件
        self._log = on_log or make_logger()
        self._status = BatteryStatus(device_name="正在读取…")
        self._icon = None
        self._stop = threading.Event()
        self._notified_low = False

    # -- 对外 -------------------------------------------------------------

    def run(self) -> int:
        """启动托盘（阻塞直到用户退出）。"""
        pystray = _require_pystray()
        self._icon = pystray.Icon(
            "betop-battery",
            icon=self._render_icon(),
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

    def _render_icon(self):
        """按当前设置渲染托盘图标。"""
        return render_icon(self._status, self._settings.icon_style_object(), ICON_SIZE)

    def _update_icon(self) -> None:
        """把当前状态画到托盘图标上。"""
        if self._icon is None:
            return
        try:
            self._icon.icon = self._render_icon()
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
        pystray = _require_pystray()

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

        def open_settings(icon, item):
            """打开图形设置界面（独立进程）。"""
            if spawn("gui") is None:
                self._log("打开设置界面失败（可能是缺少 tkinter）")

        def toggle_overlay(icon, item):
            """一键启动叠加层，并把状态写进设置。"""
            self._settings.overlay_enabled = not self._settings.overlay_enabled
            self._settings.save()
            if self._settings.overlay_enabled:
                spawn("overlay")
                self._log("已启动叠加层")
            else:
                self._log("已关闭叠加层开关（正在运行的叠加层可在其右键菜单里退出）")

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
            pystray.MenuItem("设置…", open_settings),
            pystray.MenuItem(
                "叠加层",
                toggle_overlay,
                checked=lambda item: self._settings.overlay_enabled,
            ),
            pystray.Menu.SEPARATOR,
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


def run_tray(reader: BatteryReader, settings: Optional[Settings] = None,
             on_log: Optional[Callable[[str], None]] = None) -> int:
    """便捷入口：直接启动托盘。

    Args:
        reader:   电量读取器
        settings: 用户设置
        on_log:   日志回调（默认写控制台，无控制台时写文件）

    Returns:
        退出码。
    """
    return TrayApp(reader, settings, on_log=on_log).run()
