# -*- coding: utf-8 -*-
"""图形设置界面：显示手柄状态，并自定义托盘图标与叠加层。

设计要点
--------
* **只用标准库 tkinter**（配已依赖的 Pillow 做图标预览），不引入 Qt 等重依赖
* 独立进程运行（``betop-battery gui``），避免与托盘的事件循环冲突
* 界面只负责"收集设置 → 写 config.json"；叠加层会监听该文件并热更新，
  因此这里不需要跟叠加层进程直接通信（解耦）
* 电量在后台线程读取，经队列交给主线程刷新（tkinter 非线程安全）
"""

from __future__ import annotations

import os
import queue
import threading
import tkinter as tk
from tkinter import colorchooser, messagebox, ttk
from typing import Optional

from .config import Settings, config_path
from .icon import COLOR_SCHEMES, ICON_SIZE, IconStyle, render_icon
from .log import make_logger
from .proc import spawn
from .reader import BatteryReader, BatteryStatus

WINDOW_TITLE = "betop-battery 设置"
STYLE_LABELS = {
    "number": "数字方块（清晰醒目）",
    "ring": "圆环进度（简洁）",
    "battery": "电池外形（直观）",
}


class SettingsWindow:
    """设置窗口。

    Args:
        reader:   电量读取器
        settings: 初始设置
    """

    def __init__(self, reader: BatteryReader, settings: Optional[Settings] = None) -> None:
        self._reader = reader
        self._settings = settings or Settings.load()
        self._log = make_logger()
        self._status = BatteryStatus(device_name="读取中…")
        self._queue: "queue.Queue[BatteryStatus]" = queue.Queue()
        self._overlay_proc = None
        self._preview_image = None  # 必须持有引用，否则会被 GC 掉导致空白

        self._root = tk.Tk()
        self._root.title(WINDOW_TITLE)
        self._root.minsize(520, 520)
        self._vars: dict[str, tk.Variable] = {}
        self._build()

    # ------------------------------------------------------------------ 构建

    def _build(self) -> None:
        """搭建整个界面。"""
        self._build_status_card(self._root)

        notebook = ttk.Notebook(self._root)
        notebook.pack(fill="both", expand=True, padx=12, pady=(0, 8))

        general = ttk.Frame(notebook)
        icon_tab = ttk.Frame(notebook)
        overlay_tab = ttk.Frame(notebook)
        notebook.add(general, text="  常规  ")
        notebook.add(icon_tab, text="  托盘图标  ")
        notebook.add(overlay_tab, text="  叠加层  ")

        self._build_general(general)
        self._build_icon(icon_tab)
        self._build_overlay(overlay_tab)
        self._build_footer()

    # -- 顶部状态卡 --------------------------------------------------------

    def _build_status_card(self, parent) -> None:
        """顶部：当前手柄状态（大号电量数字）。"""
        card = ttk.LabelFrame(parent, text=" 当前状态 ")
        card.pack(fill="x", padx=12, pady=12)

        self._device_label = ttk.Label(card, text="正在读取…",
                                      font=("Microsoft YaHei UI", 11))
        self._device_label.pack(anchor="w", padx=12, pady=(8, 0))

        row = ttk.Frame(card)
        row.pack(fill="x", padx=12, pady=(0, 8))
        self._battery_label = ttk.Label(row, text="--", foreground="#1e7d22",
                                       font=("Microsoft YaHei UI", 30, "bold"))
        self._battery_label.pack(side="left")
        self._charging_label = ttk.Label(row, text="", font=("Microsoft YaHei UI", 11))
        self._charging_label.pack(side="left", padx=12, pady=(14, 0))
        ttk.Button(row, text="立即刷新", command=self._refresh_now).pack(side="right", pady=(10, 0))

        self._updated_label = ttk.Label(card, text="", foreground="#666",
                                        font=("Microsoft YaHei UI", 9))
        self._updated_label.pack(anchor="w", padx=12, pady=(0, 8))

    # -- 常规 --------------------------------------------------------------

    def _build_general(self, parent) -> None:
        """常规设置：刷新间隔、低电量阈值、通知。"""
        wrap = ttk.Frame(parent, padding=16)
        wrap.pack(fill="both", expand=True)

        ttk.Label(wrap, text="刷新间隔（秒）").grid(row=0, column=0, sticky="w", pady=6)
        self._vars["poll_seconds"] = tk.IntVar(value=self._settings.poll_seconds)
        ttk.Spinbox(wrap, from_=5, to=3600, increment=5, width=10,
                    textvariable=self._vars["poll_seconds"]).grid(row=0, column=1, sticky="w", pady=6)
        ttk.Label(wrap, text="（命令行走一次就退出，不受此影响）",
                  foreground="#777").grid(row=0, column=2, sticky="w", padx=8)

        ttk.Label(wrap, text="低电量提醒阈值（%）").grid(row=1, column=0, sticky="w", pady=6)
        self._vars["low_battery_threshold"] = tk.IntVar(value=self._settings.low_battery_threshold)
        ttk.Spinbox(wrap, from_=1, to=100, width=10,
                    textvariable=self._vars["low_battery_threshold"]).grid(row=1, column=1, sticky="w", pady=6)

        self._vars["notify_on_low"] = tk.BooleanVar(value=self._settings.notify_on_low)
        ttk.Checkbutton(wrap, text="低于阈值时弹出系统通知",
                        variable=self._vars["notify_on_low"]).grid(row=2, column=0, columnspan=3,
                                                                   sticky="w", pady=6)

        ttk.Separator(wrap, orient="horizontal").grid(row=3, column=0, columnspan=3,
                                                      sticky="ew", pady=12)
        ttk.Label(wrap, text="只读取指定型号 id（留空 = 自动识别）").grid(row=4, column=0, sticky="w")
        self._vars["device_id"] = tk.StringVar(value=self._settings.device_id)
        ttk.Entry(wrap, textvariable=self._vars["device_id"], width=24).grid(row=4, column=1,
                                                                             sticky="w", pady=6)
        ttk.Button(wrap, text="查看已支持型号",
                   command=self._show_devices).grid(row=4, column=2, sticky="w", padx=8)

        hint = ("提示：手柄休眠时读不到数据，按一下手柄按键即可。\n"
                "官方客户端可以同时开着，不冲突。")
        ttk.Label(wrap, text=hint, foreground="#777", justify="left").grid(
            row=5, column=0, columnspan=3, sticky="w", pady=(16, 0))

    # -- 托盘图标 ----------------------------------------------------------

    def _build_icon(self, parent) -> None:
        """托盘图标外观：样式、配色、充电标记，并带实时预览。"""
        wrap = ttk.Frame(parent, padding=16)
        wrap.pack(fill="both", expand=True)

        left = ttk.Frame(wrap)
        left.pack(side="left", fill="y", anchor="n")

        ttk.Label(left, text="图标样式", font=("Microsoft YaHei UI", 10, "bold")).pack(anchor="w")
        self._vars["icon_style"] = tk.StringVar(value=self._settings.icon_style)
        for key, label in STYLE_LABELS.items():
            ttk.Radiobutton(left, text=label, value=key, variable=self._vars["icon_style"],
                            command=self._update_preview).pack(anchor="w", pady=3)

        ttk.Label(left, text="配色方案", font=("Microsoft YaHei UI", 10, "bold")).pack(anchor="w",
                                                                                    pady=(14, 2))
        self._vars["icon_scheme"] = tk.StringVar(value=self._settings.icon_scheme)
        for key, label in COLOR_SCHEMES.items():
            ttk.Radiobutton(left, text=label, value=key, variable=self._vars["icon_scheme"],
                            command=self._update_preview).pack(anchor="w", pady=3)

        self._vars["icon_show_charging_marker"] = tk.BooleanVar(
            value=self._settings.icon_show_charging_marker)
        ttk.Checkbutton(left, text="显示充电标记", variable=self._vars["icon_show_charging_marker"],
                        command=self._update_preview).pack(anchor="w", pady=(14, 0))

        right = ttk.LabelFrame(wrap, text=" 预览 ")
        right.pack(side="right", fill="both", expand=True, padx=(20, 0))
        self._preview_label = ttk.Label(right, text="")
        self._preview_label.pack(expand=True, pady=16)
        ttk.Label(right, text="（预览使用当前真实电量）", foreground="#777").pack(pady=(0, 12))

    # -- 叠加层 ------------------------------------------------------------

    def _build_overlay(self, parent) -> None:
        """叠加层：开关、外观、位置、穿透。"""
        wrap = ttk.Frame(parent, padding=16)
        wrap.pack(fill="both", expand=True)

        self._vars["overlay_enabled"] = tk.BooleanVar(value=self._settings.overlay_enabled)
        ttk.Checkbutton(wrap, text="启用悬浮叠加层（在屏幕上常驻显示电量，类似帧数 HUD）",
                        variable=self._vars["overlay_enabled"],
                        command=self._on_overlay_toggle).pack(anchor="w")

        ttk.Separator(wrap, orient="horizontal").pack(fill="x", pady=10)

        grid = ttk.Frame(wrap)
        grid.pack(fill="x")

        ttk.Label(grid, text="透明度").grid(row=0, column=0, sticky="w", pady=5)
        self._vars["overlay_opacity"] = tk.DoubleVar(value=self._settings.overlay_opacity)
        scale = ttk.Scale(grid, from_=0.2, to=1.0, variable=self._vars["overlay_opacity"],
                          command=lambda *_: self._schedule_apply())
        scale.grid(row=0, column=1, sticky="ew", padx=8)

        ttk.Label(grid, text="字号").grid(row=1, column=0, sticky="w", pady=5)
        self._vars["overlay_font_size"] = tk.IntVar(value=self._settings.overlay_font_size)
        ttk.Spinbox(grid, from_=8, to=40, width=8, textvariable=self._vars["overlay_font_size"],
                    command=self._schedule_apply).grid(row=1, column=1, sticky="w", padx=8)

        ttk.Label(grid, text="自身刷新（秒）").grid(row=2, column=0, sticky="w", pady=5)
        self._vars["overlay_refresh_seconds"] = tk.IntVar(value=self._settings.overlay_refresh_seconds)
        ttk.Spinbox(grid, from_=5, to=600, increment=5, width=8,
                    textvariable=self._vars["overlay_refresh_seconds"]).grid(row=2, column=1,
                                                                            sticky="w", padx=8)
        grid.columnconfigure(1, weight=1)

        ttk.Label(wrap, text="显示内容", font=("Microsoft YaHei UI", 10, "bold")).pack(anchor="w",
                                                                                    pady=(12, 2))
        row = ttk.Frame(wrap)
        row.pack(anchor="w")
        for key, label in (("overlay_show_device", "型号"),
                           ("overlay_show_battery", "电量"),
                           ("overlay_show_charging", "充电状态")):
            self._vars[key] = tk.BooleanVar(value=getattr(self._settings, key))
            ttk.Checkbutton(row, text=label, variable=self._vars[key],
                            command=self._schedule_apply).pack(side="left", padx=(0, 12))

        self._vars["overlay_click_through"] = tk.BooleanVar(
            value=self._settings.overlay_click_through)
        ttk.Checkbutton(wrap, text="鼠标穿透（游戏时推荐；开启后需用右键菜单关闭才能拖动）",
                        variable=self._vars["overlay_click_through"],
                        command=self._schedule_apply).pack(anchor="w", pady=(10, 0))

        colors = ttk.Frame(wrap)
        colors.pack(anchor="w", pady=(10, 0))
        ttk.Label(colors, text="背景色").pack(side="left")
        self._bg_btn = tk.Button(colors, width=4, bg=self._settings.overlay_bg,
                                 command=lambda: self._pick_color("overlay_bg", self._bg_btn))
        self._bg_btn.pack(side="left", padx=(6, 16))
        ttk.Label(colors, text="文字色").pack(side="left")
        self._fg_btn = tk.Button(colors, width=4, bg=self._settings.overlay_fg,
                                 command=lambda: self._pick_color("overlay_fg", self._fg_btn))
        self._fg_btn.pack(side="left", padx=6)

        pos = ttk.Frame(wrap)
        pos.pack(anchor="w", pady=(12, 0))
        ttk.Label(pos, text="位置 X / Y").pack(side="left")
        self._vars["overlay_x"] = tk.IntVar(value=self._settings.overlay_x)
        self._vars["overlay_y"] = tk.IntVar(value=self._settings.overlay_y)
        ttk.Spinbox(pos, from_=-1, to=9999, width=6,
                    textvariable=self._vars["overlay_x"]).pack(side="left", padx=4)
        ttk.Spinbox(pos, from_=-1, to=9999, width=6,
                    textvariable=self._vars["overlay_y"]).pack(side="left", padx=4)
        ttk.Button(pos, text="复位到右下角", command=self._reset_overlay_pos).pack(side="left", padx=8)
        ttk.Label(pos, text="(-1 = 自动)", foreground="#777").pack(side="left")

        ttk.Label(wrap, text="叠加层运行时可直接用鼠标拖动调整位置；右键有快捷菜单。",
                  foreground="#777").pack(anchor="w", pady=(12, 0))

    # -- 底部 --------------------------------------------------------------

    def _build_footer(self) -> None:
        """底部按钮与配置文件路径。"""
        footer = ttk.Frame(self._root, padding=(12, 0, 12, 10))
        footer.pack(fill="x")
        ttk.Label(footer, text=f"设置文件：{config_path()}", foreground="#666",
                  font=("Microsoft YaHei UI", 8)).pack(side="left")
        ttk.Button(footer, text="关闭", command=self._root.destroy).pack(side="right")
        ttk.Button(footer, text="保存并应用", command=self._save).pack(side="right", padx=8)

    # ------------------------------------------------------------------ 数据

    def _refresh_now(self) -> None:
        """立刻在后台读一次。"""
        threading.Thread(target=self._read_worker, daemon=True).start()

    def _read_worker(self) -> None:
        """后台线程：读电量 → 队列。"""
        try:
            self._queue.put(self._reader.read())
        except Exception as exc:
            self._queue.put(BatteryStatus(error=str(exc)))

    def _poll_loop(self) -> None:
        """后台线程：按设定间隔持续读取。"""
        while True:
            self._read_worker()
            try:
                interval = max(5, int(self._vars["poll_seconds"].get()))
            except Exception:
                interval = 60
            if self._stop_event.wait(interval):
                return

    _stop_event = threading.Event()

    def _tick(self) -> None:
        """主线程：取新状态、刷新界面。"""
        latest = None
        try:
            while True:
                latest = self._queue.get_nowait()
        except queue.Empty:
            pass
        if latest is not None:
            self._status = latest
            self._render_status(latest)
            self._update_preview()
        self._root.after(400, self._tick)

    def _render_status(self, status: BatteryStatus) -> None:
        """把状态画到顶部卡片。"""
        import time

        if status.error:
            self._device_label.configure(text=status.device_name or "未找到手柄")
            self._battery_label.configure(text="--", foreground="#999")
            self._charging_label.configure(text=status.error)
        else:
            self._device_label.configure(text=status.device_name)
            self._battery_label.configure(
                text=f"{status.battery_percent}%",
                foreground="#1e7d22" if (status.battery_percent or 0) > 20 else "#c62828")
            self._charging_label.configure(text="充电中 ⚡" if status.charging else "使用电池")
        self._updated_label.configure(
            text="更新于 " + time.strftime("%H:%M:%S", time.localtime(status.timestamp)))

    def _update_preview(self) -> None:
        """重绘托盘图标预览。"""
        try:
            from PIL import ImageTk
        except ImportError:
            return
        style = self._current_icon_style()
        img = render_icon(self._status, style, ICON_SIZE * 2)
        self._preview_image = ImageTk.PhotoImage(img)
        self._preview_label.configure(image=self._preview_image)

    def _current_icon_style(self) -> IconStyle:
        """从界面控件读取图标外观设置。"""
        def get(key, default):
            try:
                return self._vars[key].get()
            except Exception:
                return default

        return IconStyle(
            style=get("icon_style", "number"),
            scheme=get("icon_scheme", "auto"),
            show_charging_marker=get("icon_show_charging_marker", True),
            low_threshold=int(get("low_battery_threshold", 20) or 20),
        )

    # ------------------------------------------------------------------ 操作

    def _pick_color(self, field: str, button: tk.Button) -> None:
        """取色器。"""
        current = getattr(self._settings, field)
        chosen = colorchooser.askcolor(color=current, title="选择颜色")
        if chosen and chosen[1]:
            setattr(self._settings, field, chosen[1])
            button.configure(bg=chosen[1])
            self._schedule_apply()

    def _reset_overlay_pos(self) -> None:
        """把叠加层位置设为自动（右下角）。"""
        self._vars["overlay_x"].set(-1)
        self._vars["overlay_y"].set(-1)
        self._schedule_apply()

    def _on_overlay_toggle(self) -> None:
        """勾选/取消"启用叠加层"。"""
        enabled = bool(self._vars["overlay_enabled"].get())
        self._collect_settings()
        self._settings.save()
        if enabled:
            self._overlay_proc = spawn("overlay")
            self._log("已请求启动叠加层")
        elif self._overlay_proc is not None:
            try:
                self._overlay_proc.terminate()
            except Exception:
                pass
            self._overlay_proc = None

    def _schedule_apply(self) -> None:
        """延迟一小会儿再落盘（滑杆拖动时避免频繁写文件）。"""
        if getattr(self, "_apply_job", None):
            try:
                self._root.after_cancel(self._apply_job)
            except Exception:
                pass
        self._apply_job = self._root.after(400, self._apply_now)

    def _apply_now(self) -> None:
        """把界面设置写入 config.json（叠加层会自动热更新）。"""
        self._apply_job = None
        self._collect_settings()
        self._settings.save()

    def _collect_settings(self) -> None:
        """把界面控件的值收集进 Settings 对象。"""
        def get(key, default=None):
            try:
                return self._vars[key].get()
            except Exception:
                return default

        s = self._settings
        s.poll_seconds = int(get("poll_seconds", 60) or 60)
        s.low_battery_threshold = int(get("low_battery_threshold", 20) or 20)
        s.notify_on_low = bool(get("notify_on_low", True))
        s.device_id = str(get("device_id", "") or "").strip()
        s.icon_style = str(get("icon_style", "number"))
        s.icon_scheme = str(get("icon_scheme", "auto"))
        s.icon_show_charging_marker = bool(get("icon_show_charging_marker", True))
        s.overlay_enabled = bool(get("overlay_enabled", False))
        s.overlay_opacity = float(get("overlay_opacity", 0.78) or 0.78)
        s.overlay_font_size = int(get("overlay_font_size", 14) or 14)
        s.overlay_refresh_seconds = int(get("overlay_refresh_seconds", 30) or 30)
        s.overlay_show_device = bool(get("overlay_show_device", True))
        s.overlay_show_battery = bool(get("overlay_show_battery", True))
        s.overlay_show_charging = bool(get("overlay_show_charging", True))
        s.overlay_click_through = bool(get("overlay_click_through", False))
        s.overlay_x = int(get("overlay_x", -1))
        s.overlay_y = int(get("overlay_y", -1))

    def _save(self) -> None:
        """保存设置并提示。"""
        try:
            self._apply_now()
            self._update_preview()
            messagebox.showinfo("已保存", "设置已保存。\n\n"
                                          "· 托盘图标会立即使用新样式\n"
                                          "· 叠加层会自动热更新（若正在运行）",
                                parent=self._root)
        except Exception as exc:
            messagebox.showerror("保存失败", str(exc), parent=self._root)

    def _show_devices(self) -> None:
        """列出内置支持的型号。"""
        lines = [f"{d.id} —— {d.name}" for d in self._reader.supported_devices]
        messagebox.showinfo("已支持的型号",
                            "\n".join(lines) + "\n\n适配新型号请看 docs/adapt-new-device.md",
                            parent=self._root)

    # ------------------------------------------------------------------ 运行

    def run(self) -> int:
        """显示窗口（阻塞直到关闭）。"""
        self._refresh_now()
        threading.Thread(target=self._poll_loop, daemon=True).start()
        self._root.after(300, self._tick)
        try:
            self._root.mainloop()
        except KeyboardInterrupt:
            pass
        finally:
            self._stop_event.set()
        return 0


def run_gui(reader: BatteryReader, settings: Optional[Settings] = None) -> int:
    """便捷入口：打开设置窗口。"""
    return SettingsWindow(reader, settings).run()
