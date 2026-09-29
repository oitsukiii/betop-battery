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

import os
import threading
import time
from typing import Callable, Optional

from . import i18n
from .config import Settings, config_path
from .icon import ICON_SIZE, render_icon
from .log import make_logger
from .proc import focus_window, spawn
from .reader import BatteryReader, BatteryStatus
from .state import load_status


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
        i18n.set_language(self._settings.language)
        # 默认用安全日志：打包成无控制台的 exe 后 print 会失败，那时自动写文件
        self._log = on_log or make_logger()
        self._status = BatteryStatus(device_name=i18n.t("reading"), timestamp=0.0)   # timestamp=0：保证任何真实读数都能覆盖占位状态
        self._icon = None
        self._stop = threading.Event()
        self._notified_low = False
        self._last_read = 0.0        # 上次真正读 HID 的时间
        self._config_mtime = 0.0     # 用于检测设置文件变化

    # -- 对外 -------------------------------------------------------------

    def run(self) -> int:
        """启动托盘（阻塞直到用户退出）。"""
        pystray = _require_pystray()
        self._icon = pystray.Icon(
            "betop-battery",
            icon=self._render_icon(),
            title=i18n.t("app_name"),
            menu=self._build_menu(),
        )
        threading.Thread(target=self._poll_loop, daemon=True).start()
        self._log(i18n.t("tray_started"))
        self._icon.run()
        return 0

    # -- 轮询 -------------------------------------------------------------

    def _poll_loop(self) -> None:
        """统一的后台循环。

        每 2 秒做一次**轻量检查**，只有到了刷新间隔才真正去读 HID：
          1. 采纳共享缓存中更新的读数（别的界面刚读的）→ 三处显示保持一致
          2. 检测 config.json 变化并热更新（图标样式、阈值、间隔等）
          3. 响应"启用托盘图标"开关
          4. 到点读取设备
        """
        while not self._stop.is_set():
            self._sync_from_shared()
            self._reload_settings_if_changed()

            if not self._settings.tray_enabled:
                self._log(i18n.t("tray_disabled"))
                self._stop_icon()
                return

            if time.time() - self._last_read >= max(5, int(self._settings.poll_seconds)):
                self.refresh()
            # 轻量检查间隔：读一个文件的 mtime + 一个小 JSON，开销可忽略。
            # 原来是 2 秒，导致在设置界面换图标样式后要等 1~2 秒才生效（用户反馈卡顿）。
            self._stop.wait(0.35)

    def _sync_from_shared(self) -> None:
        """采用共享缓存中比当前更新的读数（保持界面间一致）。"""
        shared = load_status()
        if shared is None or shared.error or shared.timestamp <= self._status.timestamp:
            return
        self._status = BatteryStatus(
            device_id=shared.device_id, device_name=shared.device_name,
            battery_percent=shared.battery_percent, charging=shared.charging,
            timestamp=shared.timestamp,
        )
        self._update_icon()

    def _reload_settings_if_changed(self) -> None:
        """检测 config.json 变化并应用（例如在设置界面换了图标样式）。"""
        try:
            mtime = os.path.getmtime(config_path())
        except OSError:
            return
        if mtime <= self._config_mtime:
            return
        first = self._config_mtime == 0.0
        self._config_mtime = mtime
        if first:
            return
        new_settings = Settings.load()
        # 位置类字段由 HUD 自己管理，这里只关心与托盘相关的
        self._settings = new_settings
        i18n.set_language(new_settings.language)
        self._log(i18n.t("settings_changed_tray"))
        self._update_icon()

    def _stop_icon(self) -> None:
        """收起托盘图标并结束进程。"""
        self._stop.set()
        if self._icon is not None:
            try:
                self._icon.stop()
            except Exception:
                pass

    def refresh(self) -> BatteryStatus:
        """立即读一次并刷新界面。"""
        status = self._reader.read(source="tray")
        self._last_read = time.time()
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
            return i18n.t("app_name") + "\n" + status.error
        lines = [status.device_name or i18n.t("device_default")]
        if status.battery_percent is not None:
            lines.append(i18n.t("tooltip_battery", n=status.battery_percent))
        if status.charging is not None:
            state = i18n.t("charging") if status.charging else i18n.t("on_battery")
            lines.append(i18n.t("tooltip_status", s=state))
        stamp = time.strftime("%H:%M:%S", time.localtime(status.timestamp))
        lines.append(i18n.t("tooltip_updated", time=stamp))
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
                        i18n.t("notify_low_body", n=status.battery_percent),
                        i18n.t("app_name"),
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
                self._log(i18n.t("menu_interval") + ": " + i18n.t("seconds", n=seconds))
            return handler

        def set_threshold(value: int):
            def handler(icon, item):
                self._settings.low_battery_threshold = value
                self._settings.save()
                self._log(i18n.t("low_battery_threshold") + ": " + str(value) + "%")
                self._notified_low = False
            return handler

        def toggle_notify(icon, item):
            self._settings.notify_on_low = not self._settings.notify_on_low
            self._settings.save()
            self._log(i18n.t("menu_notify") + ": " + ("on" if self._settings.notify_on_low else "off"))

        def do_refresh(icon, item):
            self.refresh()

        def open_settings(icon, item):
            """打开图形设置界面（独立进程）。

            已经开着就把它拉到前台，避免重复弹出多个设置窗口。
            """
            if focus_window(i18n.t("window_title")):
                self._log(i18n.t("settings_foreground"))
                return
            if spawn("gui") is None:
                self._log(i18n.t("settings_open_failed"))

        def toggle_overlay(icon, item):
            """一键开关 HUD，并把状态写进设置（供界面与下次启动使用）。"""
            self._settings.overlay_enabled = not self._settings.overlay_enabled
            self._settings.save()
            if self._settings.overlay_enabled:
                spawn("overlay")
                self._log(i18n.t("started_hud"))
            else:
                self._log(i18n.t("stopped_hud"))

        def do_quit(icon, item):
            self._stop_icon()

        interval_menu = pystray.Menu(
            *[
                pystray.MenuItem(
                    i18n.t("seconds", n=sec),
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
                for pct in (10, 20, 30, 40, 50)
            ]
        )
        return pystray.Menu(
            # default=True 的项会在**左键单击 / 双击图标**时触发。
            # 按 Windows 习惯，默认动作设成"打开设置窗口"。
            pystray.MenuItem(i18n.t("menu_settings"), open_settings, default=True),
            pystray.MenuItem(i18n.t("refresh_now"), do_refresh),
            pystray.MenuItem(
                i18n.t("menu_hud"),
                toggle_overlay,
                checked=lambda item: self._settings.overlay_enabled,
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(i18n.t("menu_interval"), interval_menu),
            pystray.MenuItem(i18n.t("menu_threshold"), threshold_menu),
            pystray.MenuItem(
                i18n.t("menu_notify"),
                toggle_notify,
                checked=lambda item: self._settings.notify_on_low,
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(i18n.t("menu_quit"), do_quit),
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
