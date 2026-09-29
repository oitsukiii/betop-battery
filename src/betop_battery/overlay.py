# -*- coding: utf-8 -*-
"""HUD：像显卡帧数那样在屏幕角落常驻显示手柄电量的悬浮层。

关键设计：**背景与文字分成两个窗口**
------------------------------------
tkinter 的 ``-alpha`` 对**整个窗口**生效，所以直接用它会让文字也一起变淡
（对比 NVIDIA/RTSS 的叠加层：背景半透明、文字依旧锐利）。

标准解法是拆成两层：

* **背景层**：整块深色面板，``-alpha`` 用用户设定的透明度 → 半透明
* **文字层**：背景设为**透明色键**（``-transparentcolor``）+ ``-alpha 1.0``
  → 只有字形像素可见，且**完全不受透明度影响**

两层位置同步、一起拖动、一起锁定。

其它要点
--------
* 只用标准库 tkinter（配合 Pillow 不需要额外依赖），exe 体积可控
* **emoji 与中文用不同字体**：中文用微软雅黑，emoji 用 Segoe UI Emoji ——
  否则微软雅黑没有 emoji 字形，会显示成方框
* 电量在后台线程读取，经队列交给主线程刷新（tkinter 不是线程安全的）
* 复用 :mod:`.state` 的共享读数缓存，保证与托盘/设置窗口显示一致
* 设置里关掉"启用 HUD"后，本进程会**自行退出**
"""

from __future__ import annotations

import os
import queue
import threading
import tkinter as tk
from typing import Optional

from .config import Settings, config_path
from .log import make_logger
from .reader import BatteryReader, BatteryStatus
from .state import load_status

#: Windows 扩展窗口样式常量（用于锁定布局 / 鼠标穿透）
GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020

#: 中文与数字所用字体（用户指定微软雅黑，字形比默认字体清晰）
TEXT_FONT_FAMILY = "Microsoft YaHei UI"

#: emoji 专用字体（微软雅黑不含 emoji 字形，必须单独指定）
EMOJI_FONT_FAMILY = "Segoe UI Emoji"

#: 面板内边距
PAD_X, PAD_Y = 14, 8


def emoji_for(charging: Optional[bool]) -> str:
    """按充电状态选择图标：充电显示闪电，用电池显示电池。

    Args:
        charging: 是否充电中

    Returns:
        emoji 字符。
    """
    return "⚡" if charging else "🔋"


class OverlayWindow:
    """悬浮 HUD（双窗口实现）。

    Args:
        reader:   电量读取器
        settings: 初始设置（随后会被 config.json 的热更新覆盖）
    """

    def __init__(self, reader: BatteryReader, settings: Optional[Settings] = None) -> None:
        self._reader = reader
        self._settings = settings or Settings.load()
        self._log = make_logger()
        self._queue: "queue.Queue[BatteryStatus]" = queue.Queue()
        self._status = BatteryStatus(device_name="读取中…", timestamp=0.0)   # timestamp=0：保证任何真实读数都能覆盖占位状态
        self._stop = threading.Event()
        self._drag_origin: Optional[tuple[int, int]] = None
        self._config_mtime = 0.0
        self._original_exstyle: dict[int, int] = {}

        # 透明色键：与背景同色系，这样文字抗锯齿边缘不会出现异色描边
        self._key_color = "#0E0E12"

        self._root = tk.Tk()
        self._root.withdraw()                      # 主窗口只做容器，不显示
        self._root.title("betop-battery HUD root")   # 隐藏的容器窗口（标题与可见层区分开）

        self._build_windows()
        self._apply_settings(initial=True)

    # ------------------------------------------------------------------ 构建

    def _build_windows(self) -> None:
        """创建背景层与文字层两个无边框置顶窗口。"""
        # ---- 背景层（受透明度影响）----
        self._bg = tk.Toplevel(self._root)
        self._bg.title("betop-battery HUD bg")   # 无边框不显示标题，但便于外部脚本定位窗口
        self._bg.overrideredirect(True)
        self._bg.configure(bg=self._settings.overlay_bg)
        self._bg.attributes("-topmost", True)

        # ---- 文字层（不受透明度影响）----
        self._fg = tk.Toplevel(self._root)
        self._fg.title("betop-battery HUD")
        self._fg.overrideredirect(True)
        self._fg.configure(bg=self._key_color)
        self._fg.attributes("-topmost", True)
        self._fg.attributes("-alpha", 1.0)
        try:
            # 让色键像素完全透明，只留下字形
            self._fg.attributes("-transparentcolor", self._key_color)
        except Exception as exc:
            self._log(f"当前环境不支持透明色键，文字将与背景一起变淡：{exc}")

        row = tk.Frame(self._fg, bg=self._key_color)
        row.pack(padx=PAD_X, pady=PAD_Y)

        size = self._settings.overlay_font_size
        emoji_font = (EMOJI_FONT_FAMILY, size)
        text_font = (TEXT_FONT_FAMILY, size, "bold")

        # 逐段放置：emoji 与文字用各自字体，避免缺字形显示成方框
        self._lbl_icon1 = tk.Label(row, text="🎮", font=emoji_font,
                                   bg=self._key_color, fg=self._settings.overlay_fg)
        self._lbl_name = tk.Label(row, text="", font=text_font,
                                  bg=self._key_color, fg=self._settings.overlay_fg)
        self._lbl_icon2 = tk.Label(row, text="🔋", font=emoji_font,
                                   bg=self._key_color, fg=self._settings.overlay_fg)
        self._lbl_value = tk.Label(row, text="", font=text_font,
                                   bg=self._key_color, fg=self._settings.overlay_fg)

        for widget in (self._lbl_icon1, self._lbl_name, self._lbl_icon2, self._lbl_value):
            widget.pack(side="left")

        # 拖动与右键菜单要绑到**两层所有可见部位**上。
        # 原来只绑了文字标签，导致必须精确瞄准文字才能拖动（用户反馈很难瞄准）；
        # 现在点到背景面板的任意位置都能拖。
        targets = [self._bg, row,
                   self._lbl_icon1, self._lbl_name, self._lbl_icon2, self._lbl_value]
        for widget in targets:
            widget.bind("<Button-1>", self._on_press)
            widget.bind("<B1-Motion>", self._on_drag)
            widget.bind("<ButtonRelease-1>", self._on_release)
            widget.bind("<Button-3>", self._on_right_click)

    # ------------------------------------------------------------------ 应用设置

    def _apply_settings(self, initial: bool = False, reset_position: bool = False) -> None:
        """应用外观设置（颜色、字号、透明度、锁定、位置）。"""
        s = self._settings
        size = s.overlay_font_size
        emoji_font = (EMOJI_FONT_FAMILY, size)
        text_font = (TEXT_FONT_FAMILY, size, "bold")

        self._bg.configure(bg=s.overlay_bg)
        for widget in (self._lbl_icon1, self._lbl_name, self._lbl_icon2, self._lbl_value):
            widget.configure(bg=self._key_color, fg=s.overlay_fg)
        self._lbl_icon1.configure(font=emoji_font)
        self._lbl_icon2.configure(font=emoji_font)
        self._lbl_name.configure(font=text_font)
        self._lbl_value.configure(font=text_font)

        # 透明度**只作用于背景层**，文字层固定 1.0
        try:
            self._bg.attributes("-alpha", max(0.2, min(1.0, s.overlay_opacity)))
        except Exception:
            pass

        self._render_text()
        self._sync_geometry()

        self._set_locked(s.overlay_click_through)

        # 位置处理：**只在首次启动或用户显式点「复位」时**才移动窗口。
        # 否则锁定布局/改字号等操作会让 HUD 跳回右下角（用户实测反馈的问题）。
        if initial:
            if s.overlay_x < 0 or s.overlay_y < 0:
                self._move_to_default_corner()
            else:
                self._move_both(s.overlay_x, s.overlay_y)
            self._fg.lift()
        elif reset_position:
            self._move_to_default_corner()

    def _sync_geometry(self) -> None:
        """让背景层的尺寸与文字层一致（文字层决定实际大小）。

        注意：改完 Label 文本后，Tk 的请求尺寸要等布局结算才准确；
        只调用一次 ``update_idletasks`` 有时会拿到旧宽度，导致面板比文字窄
        （表现为右侧文字露在面板外）。因此这里结算后再延迟校正一次。
        """
        self._fg.update_idletasks()
        w = max(self._fg.winfo_width(), self._fg.winfo_reqwidth())
        h = max(self._fg.winfo_height(), self._fg.winfo_reqheight())
        self._bg.geometry(f"{w}x{h}")
        self._bg.update_idletasks()

    def _sync_geometry_soon(self) -> None:
        """在下一轮事件循环里再同步一次尺寸（等字体度量稳定）。"""
        try:
            self._fg.after(80, self._sync_geometry)
        except Exception:
            pass

    def _move_both(self, x: int, y: int) -> None:
        """两层一起移动，并保证文字层在上。"""
        self._bg.geometry(f"+{x}+{y}")
        self._fg.geometry(f"+{x}+{y}")
        try:
            self._fg.lift()
        except Exception:
            pass

    def _clamp_to_screen(self) -> None:
        """内容变宽/位置靠边时把面板收回屏幕内。

        典型场景：HUD 先按"读取中…"的窄宽度定位到右下角，随后文字变长，
        面板就有一段露到屏幕外（实测右侧电量被裁掉）。
        """
        try:
            sw = self._fg.winfo_screenwidth()
            sh = self._fg.winfo_screenheight()
            x, y = self._bg.winfo_x(), self._bg.winfo_y()
            w = max(self._bg.winfo_width(), 1)
            h = max(self._bg.winfo_height(), 1)
            nx = min(x, max(0, sw - w - 8)) if x + w > sw else x
            ny = min(y, max(0, sh - h - 8)) if y + h > sh else y
            if (nx, ny) != (x, y):
                self._move_both(nx, ny)
        except Exception:
            pass

    def _ensure_aligned(self) -> None:
        """自愈：把文字层对齐到背景层。

        为什么需要：Tk 在"隐藏的 root + 多个 Toplevel"组合下，
        ``geometry("+x+y")`` 对 Toplevel 并不总是立即生效（实测文字层会留在 (0,0)，
        背景层却已经移到了右下角，结果只看到一块空面板）。
        每轮 tick 校正一次，代价可以忽略，却能彻底消除这种漂移。
        """
        try:
            if (self._fg.winfo_x() != self._bg.winfo_x()
                    or self._fg.winfo_y() != self._bg.winfo_y()):
                self._fg.geometry(f"+{self._bg.winfo_x()}+{self._bg.winfo_y()}")
        except Exception:
            pass

    def _move_to_default_corner(self) -> None:
        """默认位置：屏幕右下角（避开任务栏）。"""
        self._sync_geometry()
        sw = self._fg.winfo_screenwidth()
        sh = self._fg.winfo_screenheight()
        w = max(self._fg.winfo_width(), 200)
        h = max(self._fg.winfo_height(), 36)
        self._move_both(max(0, sw - w - 24), max(0, sh - h - 80))

    # ------------------------------------------------------------ 锁定布局

    def _set_locked(self, locked: bool) -> None:
        """锁定布局：两个窗口都不接收鼠标事件（仅 Windows 有效）。

        锁定的同时也就无法拖动 —— 这正是"锁定"的含义。
        """
        if os.name != "nt":
            return
        try:
            import ctypes

            user32 = ctypes.windll.user32
            for window in (self._bg, self._fg):
                hwnd = user32.GetParent(window.winfo_id()) or window.winfo_id()
                current = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
                self._original_exstyle.setdefault(hwnd, current)
                target = (current | WS_EX_LAYERED | WS_EX_TRANSPARENT) if locked \
                    else (current & ~WS_EX_TRANSPARENT)
                user32.SetWindowLongW(hwnd, GWL_EXSTYLE, target)
        except Exception as exc:
            self._log(f"设置锁定布局失败：{exc}")

    # ---------------------------------------------------------------- 交互

    def _on_press(self, event) -> None:
        self._drag_origin = (event.x_root - self._fg.winfo_x(),
                             event.y_root - self._fg.winfo_y())

    def _on_drag(self, event) -> None:
        if self._drag_origin is None:
            return
        self._move_both(event.x_root - self._drag_origin[0],
                        event.y_root - self._drag_origin[1])

    def _on_release(self, _event) -> None:
        """拖动结束：记住位置。"""
        if self._drag_origin is None:
            return
        self._drag_origin = None
        self._settings.overlay_x = self._fg.winfo_x()
        self._settings.overlay_y = self._fg.winfo_y()
        self._settings.save()
        self._log(f"HUD 位置已保存：({self._settings.overlay_x}, {self._settings.overlay_y})")

    def _on_right_click(self, _event) -> None:
        """右键菜单：常用开关。"""
        menu = tk.Menu(self._root, tearoff=0)
        menu.add_command(label="立即刷新", command=self._request_refresh)
        menu.add_separator()
        var_lock = tk.BooleanVar(value=self._settings.overlay_click_through)
        menu.add_checkbutton(label="锁定布局（鼠标穿透）", variable=var_lock,
                             command=lambda: self._toggle("overlay_click_through", var_lock))
        for field, label in (("overlay_show_device", "显示型号"),
                             ("overlay_show_battery", "显示电量")):
            var = tk.BooleanVar(value=getattr(self._settings, field))
            menu.add_checkbutton(label=label, variable=var,
                                 command=lambda f=field, v=var: self._toggle(f, v))
        menu.add_separator()
        menu.add_command(label="复位到右下角", command=self._reset_position)
        menu.add_command(label="打开设置界面", command=self._open_settings)
        menu.add_separator()
        menu.add_command(label="关闭 HUD", command=self._quit)
        try:
            menu.tk_popup(_event.x_root, _event.y_root)
        finally:
            menu.grab_release()

    def _toggle(self, field: str, var: "tk.BooleanVar") -> None:
        """右键菜单开关：改设置并落盘。"""
        setattr(self._settings, field, bool(var.get()))
        self._settings.save()
        self._render_text()
        self._sync_geometry()

    def _reset_position(self) -> None:
        """位置复位到右下角。"""
        fresh = Settings.load()
        fresh.overlay_x = -1
        fresh.overlay_y = -1
        try:
            fresh.save()
        except Exception:
            pass
        self._settings.overlay_x = -1
        self._settings.overlay_y = -1
        self._move_to_default_corner()

    def _open_settings(self) -> None:
        """拉起设置界面（独立进程）。"""
        from .proc import spawn

        spawn("gui")

    def _quit(self) -> None:
        """退出程序。"""
        self._settings.overlay_enabled = False
        self._settings.save()
        self._stop.set()
        try:
            self._root.destroy()
        except Exception:
            pass

    # ---------------------------------------------------------------- 数据

    def _request_refresh(self) -> None:
        """让后台线程立刻读一次。"""
        threading.Thread(target=self._read_once, daemon=True).start()

    def _read_once(self) -> None:
        """后台线程：读电量并放入队列。"""
        try:
            self._queue.put(self._reader.read(source="hud"))
        except Exception as exc:
            self._queue.put(BatteryStatus(error=str(exc)))

    def _poll_loop(self) -> None:
        """后台线程：按**常规设置里的刷新间隔**周期性读取。"""
        while not self._stop.is_set():
            self._read_once()
            self._stop.wait(max(5, int(self._settings.poll_seconds)))

    # ---------------------------------------------------------------- 主循环

    def _tick(self) -> None:
        """主线程定时任务：取新数据 + 热加载配置 + 复用共享读数。"""
        latest = None
        try:
            while True:
                latest = self._queue.get_nowait()
        except queue.Empty:
            pass
        if latest is not None and latest.timestamp >= self._status.timestamp:
            self._status = latest
            self._log(latest.summary())

        shared = load_status()
        if shared is not None and not shared.error and shared.timestamp > self._status.timestamp:
            self._status = BatteryStatus(
                device_id=shared.device_id, device_name=shared.device_name,
                battery_percent=shared.battery_percent, charging=shared.charging,
                timestamp=shared.timestamp,
            )

        if self._reload_config_if_changed():
            return  # 已退出
        self._render_text()
        self._ensure_aligned()          # 修正两层位置漂移
        self._clamp_to_screen()         # 内容变宽后收回屏幕内
        self._root.after(300, self._tick)

    def _reload_config_if_changed(self) -> bool:
        """检测 config.json 变化并应用。

        Returns:
            True 表示已按要求退出（调用方应停止后续处理）。
        """
        try:
            mtime = os.path.getmtime(config_path())
        except OSError:
            return False
        if mtime <= self._config_mtime:
            return False
        first = self._config_mtime == 0.0
        self._config_mtime = mtime
        if first:
            return False

        new_settings = Settings.load()
        # 设置里关掉了 HUD → 自行退出（这修好了"取消勾选不生效"的问题）
        if not new_settings.overlay_enabled:
            self._settings = new_settings
            self._log("设置里已关闭 HUD，正在退出")
            self._quit()
            return True

        reset_requested = (new_settings.overlay_reset_token
                           != self._settings.overlay_reset_token)
        self._settings = new_settings
        self._apply_settings(reset_position=reset_requested)
        self._log("检测到设置变更，HUD 已热更新"
                  + ("（位置已复位）" if reset_requested else ""))
        return False

    def _render_text(self) -> None:
        """刷新文字（图标随充电状态切换：🔋 用电池 / ⚡ 充电中）。"""
        s = self._settings
        st = self._status

        if st.error:
            self._lbl_icon1.configure(text="⚠")
            self._lbl_name.configure(text=st.error)
            self._lbl_icon2.configure(text="")
            self._lbl_value.configure(text="")
            return

        show_device = s.overlay_show_device and bool(st.device_name)
        show_battery = s.overlay_show_battery

        self._lbl_icon1.configure(text="🎮" if show_device else "")
        self._lbl_name.configure(
            text=("" if not show_device else st.device_name.split("(")[0].strip() + "   "))

        # 充电状态**始终展示**，方式是把电量图标换成闪电
        self._lbl_icon2.configure(text=emoji_for(st.charging) if show_battery else "")
        self._lbl_value.configure(
            text=("" if not show_battery
                  else (f"{st.battery_percent}%" if st.battery_percent is not None else "--")))

        self._sync_geometry()
        self._sync_geometry_soon()

    # ---------------------------------------------------------------- 对外

    def run(self) -> int:
        """启动 HUD（阻塞）。"""
        self._log("HUD 已启动：拖动可移动，右键有菜单")
        threading.Thread(target=self._poll_loop, daemon=True).start()
        self._root.after(200, self._tick)
        try:
            self._root.mainloop()
        except KeyboardInterrupt:
            pass
        return 0


def run_overlay(reader: BatteryReader, settings: Optional[Settings] = None) -> int:
    """便捷入口：启动 HUD。"""
    return OverlayWindow(reader, settings).run()
