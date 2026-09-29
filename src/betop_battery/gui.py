# -*- coding: utf-8 -*-
"""图形设置界面：显示手柄状态，并自定义托盘图标与 HUD。

设计要点
--------
* **只用标准库 tkinter**（配已依赖的 Pillow 做图标预览），不引入 Qt 等重依赖
* 独立进程运行（``betop-battery gui``），避免与托盘的事件循环冲突
* 界面只负责"收集设置 → 写 config.json"；托盘与 HUD 会监听该文件并热更新，
  因此这里不需要跟它们直接通信（解耦）
* 电量在后台线程读取，经队列交给主线程刷新（tkinter 非线程安全）；
  并复用 :mod:`.state` 的共享读数，保证与托盘/HUD 显示一致

布局约定
--------
底部按钮区**先于**内容区 pack（``side="bottom"``），这样窗口被缩小到极限时，
"应用/关闭"仍然可见 —— 否则内容会把它挤掉。每个标签页内部用
:class:`ScrollableFrame`，内容超出时可滚动。
"""

from __future__ import annotations

import queue
import threading
import time
import tkinter as tk
from tkinter import colorchooser, messagebox, ttk
from typing import Optional

from .config import Settings, config_path
from .icon import COLOR_SCHEMES, ICON_SIZE, IconStyle, render_icon
from .log import make_logger
from .proc import spawn
from .reader import BatteryReader, BatteryStatus
from .state import load_status

WINDOW_TITLE = "betop-battery 设置"

STYLE_LABELS = {
    "number": "数字方块（清晰醒目）",
    "ring": "圆环进度（简洁）",
    "battery": "电池外形（直观）",
}

#: 刷新间隔预设（秒）—— 用下拉而不是自由输入，避免填出无意义的数值
INTERVAL_PRESETS = (15, 30, 60, 120, 300, 600)

#: 低电量提醒阈值预设（%）
THRESHOLD_PRESETS = (10, 20, 30, 40, 50)

class ScrollableFrame(ttk.Frame):
    """带垂直滚动条的容器：窗口变小时内容不会被裁掉。"""

    def __init__(self, parent) -> None:
        super().__init__(parent)
        self._canvas = tk.Canvas(self, highlightthickness=0, borderwidth=0)
        scrollbar = ttk.Scrollbar(self, orient="vertical", command=self._canvas.yview)
        self._canvas.configure(yscrollcommand=scrollbar.set)

        self._canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        #: 真正放控件的容器
        self.inner = ttk.Frame(self._canvas)
        self._window = self._canvas.create_window((0, 0), window=self.inner, anchor="nw")

        self.inner.bind("<Configure>", self._on_inner_configure)
        self._canvas.bind("<Configure>", self._on_canvas_configure)
        self._bind_wheel()

    def _on_inner_configure(self, _event) -> None:
        """内容尺寸变化时更新滚动范围。"""
        self._canvas.configure(scrollregion=self._canvas.bbox("all"))

    def _on_canvas_configure(self, event) -> None:
        """让内部容器宽度跟随画布，避免出现横向滚动。"""
        self._canvas.itemconfigure(self._window, width=event.width)

    def _bind_wheel(self) -> None:
        """绑定滚轮（Windows/macOS 用 <MouseWheel>，Linux 用 Button-4/5）。"""
        def on_wheel(event):
            delta = getattr(event, "delta", 0)
            if delta:
                self._canvas.yview_scroll(int(-delta / 120), "units")
            return "break"

        def on_button(event, direction: int):
            self._canvas.yview_scroll(direction, "units")
            return "break"

        for widget in (self._canvas, self.inner):
            widget.bind("<MouseWheel>", on_wheel)
            widget.bind("<Button-4>", lambda e: on_button(e, -1))
            widget.bind("<Button-5>", lambda e: on_button(e, 1))


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
        self._status = BatteryStatus(device_name="读取中…", timestamp=0.0)   # timestamp=0：保证任何真实读数都能覆盖占位状态
        self._queue: "queue.Queue[BatteryStatus]" = queue.Queue()
        self._stop_event = threading.Event()
        self._hud_proc = None           # 由本界面启动的 HUD 进程（用于关闭时结束它）
        self._tray_proc = None          # 由本界面启动的托盘进程
        self._preview_image = None      # 必须持有引用，否则会被 GC 掉
        self._apply_job = None
        self._last_read_ts = 0.0        # 自己上次发起 HID 读取的时间
        self._last_render_signature = None   # 用于避免重复重绘预览

        self._root = tk.Tk()
        self._root.title(WINDOW_TITLE)
        self._root.geometry("620x560")
        self._root.minsize(480, 380)
        self._vars: dict[str, tk.Variable] = {}
        self._build()

    # ------------------------------------------------------------------ 构建

    def _build(self) -> None:
        """搭建整个界面。

        顺序很关键：**先 pack 底部按钮与顶部状态卡，最后 pack 标签页**，
        这样窗口缩小时被压缩的是中间内容，按钮始终可见。
        """
        self._build_footer()                                   # side=bottom
        self._build_status_card()                              # side=top

        notebook = ttk.Notebook(self._root)
        notebook.pack(fill="both", expand=True, padx=12, pady=(0, 4))

        general = ScrollableFrame(notebook)
        icon_tab = ScrollableFrame(notebook)
        hud_tab = ScrollableFrame(notebook)
        notebook.add(general, text="  常规  ")
        notebook.add(icon_tab, text="  托盘图标  ")
        notebook.add(hud_tab, text="  HUD  ")

        self._build_general(general.inner)
        self._build_icon(icon_tab.inner)
        self._build_hud(hud_tab.inner)

    # -- 底部按钮（最先 pack，保证不被遮挡）--------------------------------

    def _build_footer(self) -> None:
        """底部：应用 / 关闭 + 状态提示。"""
        footer = ttk.Frame(self._root, padding=(12, 6, 12, 10))
        footer.pack(side="bottom", fill="x")

        ttk.Button(footer, text="关闭", command=self._on_close).pack(side="right")
        ttk.Button(footer, text="应用", command=self._apply_now).pack(side="right", padx=8)

        self._footer_hint = ttk.Label(footer, text="", foreground="#1e7d22",
                                      font=("Microsoft YaHei UI", 9))
        self._footer_hint.pack(side="left")

        path_label = ttk.Label(footer, text=f"设置文件：{config_path()}", foreground="#888",
                               font=("Microsoft YaHei UI", 8))
        path_label.pack(side="left", padx=(12, 0))

    # -- 顶部状态卡 --------------------------------------------------------

    def _build_status_card(self) -> None:
        """顶部：当前手柄状态（大号电量数字）。"""
        card = ttk.LabelFrame(self._root, text=" 当前状态 ")
        card.pack(side="top", fill="x", padx=12, pady=(10, 6))

        self._device_label = ttk.Label(card, text="正在读取…",
                                       font=("Microsoft YaHei UI", 11))
        self._device_label.pack(anchor="w", padx=12, pady=(6, 0))

        row = ttk.Frame(card)
        row.pack(fill="x", padx=12, pady=(0, 6))
        self._battery_label = ttk.Label(row, text="--", foreground="#1e7d22",
                                        font=("Microsoft YaHei UI", 28, "bold"))
        self._battery_label.pack(side="left")
        self._charging_label = ttk.Label(row, text="", font=("Microsoft YaHei UI", 11))
        self._charging_label.pack(side="left", padx=12, pady=(12, 0))
        ttk.Button(row, text="立即刷新", command=self._refresh_now).pack(side="right", pady=(8, 0))

        self._updated_label = ttk.Label(card, text="", foreground="#666",
                                        font=("Microsoft YaHei UI", 9))
        self._updated_label.pack(anchor="w", padx=12, pady=(0, 6))

    # -- 常规 --------------------------------------------------------------

    def _build_general(self, parent) -> None:
        """常规设置。"""
        ttk.Label(parent, text="刷新间隔（托盘与 HUD 共用）",
                  font=("Microsoft YaHei UI", 10, "bold")).grid(row=0, column=0, sticky="w",
                                                                padx=16, pady=(14, 2))
        # 预设之外的旧值（例如早期版本允许自由输入 5 秒）也要出现在下拉里，
        # 否则控件会显示一个"列表里选不到"的值，用户会困惑。
        choices = list(INTERVAL_PRESETS)
        if self._settings.poll_seconds not in choices:
            choices.append(self._settings.poll_seconds)
            choices.sort()
        interval = ttk.Combobox(parent, state="readonly", width=12,
                                values=[f"{sec} 秒" for sec in choices])
        interval.set(f"{self._settings.poll_seconds} 秒")
        interval.grid(row=0, column=1, sticky="w", padx=8, pady=(14, 2))
        self._interval_box = interval

        ttk.Label(parent, text="低电量提醒阈值").grid(row=1, column=0, sticky="w",
                                                      padx=16, pady=6)
        # 预设之外的历史值也要出现在下拉里（早期版本是自由输入）
        thresholds = list(THRESHOLD_PRESETS)
        if self._settings.low_battery_threshold not in thresholds:
            thresholds.append(self._settings.low_battery_threshold)
            thresholds.sort()
        threshold_box = ttk.Combobox(parent, state="readonly", width=10,
                                     values=[f"{pct}%" for pct in thresholds])
        threshold_box.set(f"{self._settings.low_battery_threshold}%")
        threshold_box.grid(row=1, column=1, sticky="w", padx=8)
        threshold_box.bind("<<ComboboxSelected>>", lambda _e: self._schedule_apply())
        self._threshold_box = threshold_box

        self._vars["notify_on_low"] = tk.BooleanVar(value=self._settings.notify_on_low)
        ttk.Checkbutton(parent, text="低于阈值时弹出系统通知",
                        variable=self._vars["notify_on_low"]).grid(row=2, column=0, columnspan=2,
                                                                   sticky="w", padx=16, pady=4)

        ttk.Separator(parent, orient="horizontal").grid(row=3, column=0, columnspan=3,
                                                        sticky="ew", padx=16, pady=12)

        self._vars["device_id"] = tk.StringVar(value=self._settings.device_id)
        ttk.Label(parent, text="只读取指定型号 id（留空 = 自动识别）").grid(row=4, column=0,
                                                                           sticky="w", padx=16)
        ttk.Entry(parent, textvariable=self._vars["device_id"], width=22).grid(row=4, column=1,
                                                                               sticky="w", padx=8)
        ttk.Button(parent, text="查看已支持型号",
                   command=self._show_devices).grid(row=4, column=2, sticky="w", padx=4)

        hint = ("提示：手柄休眠时读不到数据，按一下手柄按键即可。\n"
                "官方客户端可以同时开着，不冲突。\n"
                "当前版本针对「单只手柄」开发，同时连接多只手柄可能出现意外表现。")
        ttk.Label(parent, text=hint, foreground="#777", justify="left").grid(
            row=5, column=0, columnspan=3, sticky="w", padx=16, pady=(18, 12))

    # -- 托盘图标 ----------------------------------------------------------

    def _build_icon(self, parent) -> None:
        """托盘图标外观：启用开关、样式、配色、充电标记，带实时预览。"""
        self._vars["tray_enabled"] = tk.BooleanVar(value=self._settings.tray_enabled)
        ttk.Checkbutton(parent, text="启用托盘图标（通知区域显示电量）",
                        variable=self._vars["tray_enabled"],
                        command=self._on_tray_toggle).grid(row=0, column=0, columnspan=2,
                                                           sticky="w", padx=16, pady=(14, 2))
        ttk.Label(parent, text="（勾选立即出现，取消立即消失）",
                  foreground="#888", font=("Microsoft YaHei UI", 8)).grid(
            row=1, column=0, columnspan=2, sticky="w", padx=32, pady=(0, 10))

        ttk.Label(parent, text="图标样式", font=("Microsoft YaHei UI", 10, "bold")).grid(
            row=2, column=0, sticky="w", padx=16, pady=(6, 2))
        self._vars["icon_style"] = tk.StringVar(value=self._settings.icon_style)
        for offset, (key, label) in enumerate(STYLE_LABELS.items()):
            ttk.Radiobutton(parent, text=label, value=key, variable=self._vars["icon_style"],
                            command=self._update_preview).grid(row=3 + offset, column=0,
                                                               sticky="w", padx=32, pady=2)

        base = 3 + len(STYLE_LABELS)
        ttk.Label(parent, text="配色方案", font=("Microsoft YaHei UI", 10, "bold")).grid(
            row=base + 1, column=0, sticky="w", padx=16, pady=(12, 2))
        self._vars["icon_scheme"] = tk.StringVar(value=self._settings.icon_scheme)
        for offset, (key, label) in enumerate(COLOR_SCHEMES.items()):
            ttk.Radiobutton(parent, text=label, value=key, variable=self._vars["icon_scheme"],
                            command=self._update_preview).grid(row=base + 2 + offset, column=0,
                                                               sticky="w", padx=32, pady=2)

        self._vars["icon_show_charging_marker"] = tk.BooleanVar(
            value=self._settings.icon_show_charging_marker)
        ttk.Checkbutton(parent, text="显示充电标记", variable=self._vars["icon_show_charging_marker"],
                        command=self._update_preview).grid(row=base + 4, column=0, sticky="w",
                                                           padx=16, pady=(12, 10))

        preview = ttk.LabelFrame(parent, text=" 预览（使用当前真实电量） ")
        preview.grid(row=2, column=1, rowspan=base + 4, sticky="n", padx=20, pady=(14, 10))
        self._preview_label = ttk.Label(preview, text="")
        self._preview_label.pack(expand=True, pady=18, padx=18)

    # -- HUD ---------------------------------------------------------------

    def _build_hud(self, parent) -> None:
        """HUD 设置：开关、外观、显示内容、锁定布局、位置复位。"""
        self._vars["overlay_enabled"] = tk.BooleanVar(value=self._settings.overlay_enabled)
        ttk.Checkbutton(parent, text="启用 HUD（在屏幕上常驻显示电量，类似帧数叠加层）",
                        variable=self._vars["overlay_enabled"],
                        command=self._on_hud_toggle).grid(row=0, column=0, columnspan=2,
                                                          sticky="w", padx=16, pady=(14, 2))
        ttk.Label(parent, text="（刷新间隔与「常规」页共用）", foreground="#888",
                  font=("Microsoft YaHei UI", 8)).grid(row=1, column=0, columnspan=2,
                                                       sticky="w", padx=32, pady=(0, 10))

        ttk.Label(parent, text="透明度").grid(row=2, column=0, sticky="w", padx=16, pady=6)
        self._vars["overlay_opacity"] = tk.DoubleVar(value=self._settings.overlay_opacity)
        ttk.Scale(parent, from_=0.2, to=1.0, variable=self._vars["overlay_opacity"],
                  command=lambda *_: self._schedule_apply()).grid(row=2, column=1, sticky="ew",
                                                                  padx=8, pady=6)

        ttk.Label(parent, text="字号").grid(row=3, column=0, sticky="w", padx=16, pady=6)
        self._vars["overlay_font_size"] = tk.IntVar(value=self._settings.overlay_font_size)
        ttk.Spinbox(parent, from_=8, to=40, width=8, textvariable=self._vars["overlay_font_size"],
                    command=self._schedule_apply).grid(row=3, column=1, sticky="w", padx=8)

        ttk.Label(parent, text="显示内容", font=("Microsoft YaHei UI", 10, "bold")).grid(
            row=4, column=0, sticky="w", padx=16, pady=(14, 2))
        checks = ttk.Frame(parent)
        checks.grid(row=5, column=0, columnspan=2, sticky="w", padx=32)
        for key, label in (("overlay_show_device", "型号"),
                           ("overlay_show_battery", "电量")):
            self._vars[key] = tk.BooleanVar(value=getattr(self._settings, key))
            ttk.Checkbutton(checks, text=label, variable=self._vars[key],
                            command=self._schedule_apply).pack(side="left", padx=(0, 14))

        ttk.Label(parent, text="充电状态始终展示：用电池显示 🔋，充电中显示 ⚡",
                  foreground="#666", font=("Microsoft YaHei UI", 9)).grid(
            row=6, column=0, columnspan=2, sticky="w", padx=32, pady=(6, 0))

        self._vars["overlay_click_through"] = tk.BooleanVar(
            value=self._settings.overlay_click_through)
        ttk.Checkbutton(parent, text="锁定布局（鼠标穿透，游戏时推荐；锁定后需先取消才能拖动）",
                        variable=self._vars["overlay_click_through"],
                        command=self._schedule_apply).grid(row=7, column=0, columnspan=2,
                                                           sticky="w", padx=16, pady=(8, 2))

        colors = ttk.Frame(parent)
        colors.grid(row=8, column=0, columnspan=2, sticky="w", padx=16, pady=(12, 0))
        ttk.Label(colors, text="背景色").pack(side="left")
        self._bg_btn = tk.Button(colors, width=4, bg=self._settings.overlay_bg,
                                 command=lambda: self._pick_color("overlay_bg", self._bg_btn))
        self._bg_btn.pack(side="left", padx=(6, 18))
        ttk.Label(colors, text="文字色").pack(side="left")
        self._fg_btn = tk.Button(colors, width=4, bg=self._settings.overlay_fg,
                                 command=lambda: self._pick_color("overlay_fg", self._fg_btn))
        self._fg_btn.pack(side="left", padx=6)

        pos = ttk.Frame(parent)
        pos.grid(row=9, column=0, columnspan=2, sticky="w", padx=16, pady=(14, 4))
        ttk.Button(pos, text="复位", command=self._reset_hud_position).pack(side="left")
        ttk.Label(pos, text="（初始位置在屏幕右下角；平时直接用鼠标拖动 HUD 即可，位置会自动记住）",
                  foreground="#888", font=("Microsoft YaHei UI", 8)).pack(side="left", padx=10)

        ttk.Label(parent, text="提示：若游戏以独占全屏运行，HUD 可能不可见 —— 请把游戏设为「无边框窗口」。",
                  foreground="#777", justify="left", wraplength=520).grid(
            row=10, column=0, columnspan=2, sticky="w", padx=16, pady=(14, 12))
        parent.columnconfigure(1, weight=1)

    # ------------------------------------------------------------------ 数据

    def _refresh_now(self) -> None:
        """立刻在后台读一次（绕过缓存）。"""
        threading.Thread(target=lambda: self._read_worker(force=True), daemon=True).start()

    def _read_worker(self, force: bool = False) -> None:
        """后台线程：读电量 → 队列。

        Args:
            force: 为 True 时忽略共享缓存，真正去读设备（"立即刷新"用）
        """
        try:
            self._queue.put(self._reader.read(source="gui",
                                              max_cache_age=0 if force else 20.0))
        except Exception as exc:
            self._queue.put(BatteryStatus(error=str(exc)))

    def _poll_loop(self) -> None:
        """后台线程：按设定间隔持续读取。"""
        while not self._stop_event.is_set():
            self._read_worker()
            try:
                interval = self._current_interval()
            except Exception:
                interval = 60
            if self._stop_event.wait(interval):
                return

    def _tick(self) -> None:
        """主线程：取新状态、采纳共享读数、刷新界面。"""
        latest = None
        try:
            while True:
                latest = self._queue.get_nowait()
        except queue.Empty:
            pass
        if latest is not None and latest.timestamp >= self._status.timestamp:
            self._status = latest

        # 采纳其它界面（托盘/HUD）刚读到的更新的值，保证三处显示一致
        shared = load_status()
        if shared is not None and not shared.error and shared.timestamp > self._status.timestamp:
            self._status = BatteryStatus(
                device_id=shared.device_id, device_name=shared.device_name,
                battery_percent=shared.battery_percent, charging=shared.charging,
                timestamp=shared.timestamp,
            )

        self._render_status(self._status)
        # 只在电量/状态真的变了才重绘预览，避免每 400ms 无谓地重画位图
        signature = (self._status.battery_percent, self._status.charging,
                     self._status.error, self._status.device_name)
        if signature != self._last_render_signature:
            self._last_render_signature = signature
            self._update_preview()
        self._root.after(400, self._tick)

    def _render_status(self, status: BatteryStatus) -> None:
        """把状态画到顶部卡片。"""
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
        img = render_icon(self._status, self._current_icon_style(), ICON_SIZE * 2)
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
            low_threshold=self._current_threshold(),
        )

    def _current_threshold(self) -> int:
        """从下拉框解析低电量阈值（%）。"""
        try:
            return max(1, int(str(self._threshold_box.get()).rstrip("%")))
        except Exception:
            return 20

    def _current_interval(self) -> int:
        """从下拉框解析刷新间隔（秒）。"""
        try:
            text = str(self._interval_box.get())
            return max(5, int(text.split()[0]))
        except Exception:
            return 60

    # ------------------------------------------------------------------ 操作

    def _pick_color(self, field: str, button: tk.Button) -> None:
        """取色器。"""
        chosen = colorchooser.askcolor(color=getattr(self._settings, field), title="选择颜色")
        if chosen and chosen[1]:
            setattr(self._settings, field, chosen[1])
            button.configure(bg=chosen[1])
            self._schedule_apply()

    def _reset_hud_position(self) -> None:
        """请求 HUD 复位到右下角。

        通过**令牌 +1** 发出一次性信号：HUD 只在令牌变化时才移动位置，
        这样锁定布局、改字号等操作不会让它乱跳（用户反馈的问题）。
        """
        fresh = Settings.load()
        self._collect_settings_into(fresh)      # 带上界面上的其它改动
        fresh.overlay_x = -1
        fresh.overlay_y = -1
        fresh.overlay_reset_token += 1
        fresh.save()
        self._settings = fresh
        self._flash_hint("已请求 HUD 复位到右下角")

    def _on_tray_toggle(self) -> None:
        """勾选/取消「启用托盘图标」。

        与 HUD 的开关保持**完全一致的逻辑**：
          勾选 → 立即启动；取消 → 立即结束（同时配置文件也会写 false，
          让托盘自己发现后退出，两条路都走，保证一定生效）。
        """
        enabled = bool(self._vars["tray_enabled"].get())
        self._apply_now()
        if enabled:
            self._tray_proc = spawn("tray")
            self._flash_hint("已启动托盘图标")
        else:
            if self._tray_proc is not None:
                try:
                    self._tray_proc.terminate()
                except Exception:
                    pass
                self._tray_proc = None
            self._flash_hint("已关闭托盘图标")

    def _on_hud_toggle(self) -> None:
        """勾选/取消"启用 HUD"。

        两条路一起走，确保真的生效：
          1. 本界面记录并结束自己启动的 HUD 进程
          2. HUD 自身也会检测到"启用 HUD"被关掉而退出
             （即使它不是本界面启动的，例如由托盘或 exe 启动）
        """
        enabled = bool(self._vars["overlay_enabled"].get())
        self._apply_now()
        if enabled:
            self._hud_proc = spawn("overlay")
            self._flash_hint("已启动 HUD")
        else:
            if self._hud_proc is not None:
                try:
                    self._hud_proc.terminate()
                except Exception:
                    pass
                self._hud_proc = None
            self._flash_hint("已关闭 HUD")

    def _schedule_apply(self) -> None:
        """延迟一小会儿再落盘（滑杆拖动时避免频繁写文件）。"""
        if self._apply_job:
            try:
                self._root.after_cancel(self._apply_job)
            except Exception:
                pass
        self._apply_job = self._root.after(400, self._apply_now)

    def _apply_now(self) -> None:
        """把界面设置写入 config.json（托盘/HUD 会自动热更新）。

        **以磁盘上的最新配置为基准**再覆盖界面管理的字段：
        HUD 拖动后会把自己的坐标写进配置，如果这里拿着界面打开时的旧快照整份覆盖，
        就会把 HUD 的位置改回去（表现为"拖完又跳回来"）。
        """
        self._apply_job = None
        try:
            fresh = Settings.load()
            self._collect_settings_into(fresh)   # 不包含 X/Y 与复位令牌
            fresh.save()
            self._settings = fresh
            self._flash_hint("已应用 ✓")
        except Exception as exc:
            self._flash_hint(f"应用失败：{exc}", error=True)

    def _flash_hint(self, text: str, error: bool = False) -> None:
        """在底部显示一条短提示（几秒后自动消失）。"""
        self._footer_hint.configure(text=text, foreground="#c62828" if error else "#1e7d22")
        self._root.after(2500, lambda: self._footer_hint.configure(text=""))

    def _collect_settings_into(self, s: Settings) -> None:
        """把界面控件的值收集进给定的 Settings 对象。

        Args:
            s: 目标对象（通常是刚从磁盘读到的最新配置）

        注意：**不覆盖** ``overlay_x`` / ``overlay_y`` / ``overlay_reset_token``
        —— 位置由 HUD 自身拖动或「复位」按钮决定。
        """
        def get(key, default=None):
            try:
                return self._vars[key].get()
            except Exception:
                return default

        s.poll_seconds = self._current_interval()
        s.low_battery_threshold = self._current_threshold()
        s.notify_on_low = bool(get("notify_on_low", True))
        s.device_id = str(get("device_id", "") or "").strip()

        s.tray_enabled = bool(get("tray_enabled", True))
        s.icon_style = str(get("icon_style", "number"))
        s.icon_scheme = str(get("icon_scheme", "auto"))
        s.icon_show_charging_marker = bool(get("icon_show_charging_marker", True))

        s.overlay_enabled = bool(get("overlay_enabled", False))
        s.overlay_opacity = float(get("overlay_opacity", 0.78) or 0.78)
        s.overlay_font_size = int(get("overlay_font_size", 14) or 14)
        s.overlay_show_device = bool(get("overlay_show_device", True))
        s.overlay_show_battery = bool(get("overlay_show_battery", True))
        s.overlay_click_through = bool(get("overlay_click_through", False))
        # 位置（overlay_x/y）与复位令牌刻意不覆盖

    def _on_close(self) -> None:
        """关闭前把当前界面上的设置保存下来。"""
        self._apply_now()
        self._stop_event.set()
        self._root.destroy()

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
