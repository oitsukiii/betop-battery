# -*- coding: utf-8 -*-
"""HUD：像显卡帧数那样在屏幕角落常驻显示手柄电量的悬浮层。

设计要点
--------
* **只用标准库 tkinter** —— 不引入 Qt 等重依赖，exe 体积可控
* 无边框 + 置顶 + 半透明，形态接近 RTSS/Afterburner 的 HUD
* **锁定布局**（Windows 下设置 ``WS_EX_TRANSPARENT``）：鼠标事件穿过 HUD，
  玩游戏时不挡操作，同时也避免误拖动
* 读取 ``config.json`` 的改动并**热更新**外观 —— 所以图形界面里调参数能立刻看到
* 复用 :mod:`.state` 的共享读数缓存，保证与托盘/设置窗口显示一致
* 电量在**后台线程**读取，通过队列交给主线程刷新（tkinter 不是线程安全的）

独立进程运行（``betop-battery overlay``），避免和托盘的事件循环互相干扰。
"""

from __future__ import annotations

import os
import queue
import threading
import time
import tkinter as tk
from typing import Optional

from .config import Settings, config_path
from .log import make_logger
from .reader import BatteryReader, BatteryStatus
from .state import load_status

#: Windows 扩展窗口样式常量（用于鼠标穿透）
GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020

#: 检查配置变化的时间间隔（毫秒）
CONFIG_POLL_MS = 1500

#: 优先使用支持 emoji 的字体；缺失时回退
FONT_CANDIDATES = ("Segoe UI Emoji", "Segoe UI", "Microsoft YaHei UI")


class OverlayWindow:
    """悬浮 HUD。

    Args:
        reader:   电量读取器
        settings: 初始设置（随后会被 config.json 的热更新覆盖）
    """

    def __init__(self, reader: BatteryReader, settings: Optional[Settings] = None) -> None:
        self._reader = reader
        self._settings = settings or Settings.load()
        self._log = make_logger()
        self._queue: "queue.Queue[BatteryStatus]" = queue.Queue()
        self._status = BatteryStatus(device_name="读取中…")
        self._stop = threading.Event()
        self._drag_origin: Optional[tuple[int, int]] = None
        self._config_mtime = 0.0
        self._original_exstyle: Optional[int] = None
        self._position_initialised = False

        self._root = tk.Tk()
        self._root.title("betop-battery HUD")
        self._build_window()

    # ------------------------------------------------------------------ 构建

    def _build_window(self) -> None:
        """创建无边框、置顶、半透明的窗口。"""
        root = self._root
        root.overrideredirect(True)               # 去掉标题栏与边框
        try:
            root.attributes("-topmost", True)     # 始终置顶（盖在游戏上）
        except Exception:
            pass
        root.configure(bg=self._settings.overlay_bg)

        self._label = tk.Label(root, text="…", justify="left", anchor="w",
                               bg=self._settings.overlay_bg,
                               fg=self._settings.overlay_fg)
        self._label.pack(padx=14, pady=9)

        # 拖动移动位置（锁定布局后收不到事件，这是预期行为）
        self._label.bind("<Button-1>", self._on_press)
        self._label.bind("<B1-Motion>", self._on_drag)
        self._label.bind("<ButtonRelease-1>", self._on_release)
        self._label.bind("<Button-3>", self._on_right_click)

        self._apply_settings(initial=True)

    def _font(self):
        """挑一个能显示 emoji 的字体。"""
        size = self._settings.overlay_font_size
        return (FONT_CANDIDATES[0], size, "bold")

    def _apply_settings(self, initial: bool = False) -> None:
        """把当前设置应用到窗口（位置、透明度、字号、颜色、锁定）。"""
        s = self._settings
        self._root.configure(bg=s.overlay_bg)
        self._label.configure(bg=s.overlay_bg, fg=s.overlay_fg, font=self._font())
        try:
            self._root.attributes("-alpha", max(0.2, min(1.0, s.overlay_opacity)))
        except Exception:
            pass
        self._set_locked(s.overlay_click_through)

        # 位置：-1 表示自动（右下角）。显式复位时也必须回到右下角，
        # 因此这里不再"保留运行期位置"，否则界面上的复位按钮会失效。
        if s.overlay_x < 0 or s.overlay_y < 0:
            self._move_to_default_corner()
            self._position_initialised = True
        else:
            self._root.geometry(f"+{s.overlay_x}+{s.overlay_y}")
            self._position_initialised = True
        if initial:
            self._root.update_idletasks()

    def _move_to_default_corner(self) -> None:
        """放到屏幕右下角（避开任务栏）。"""
        self._root.update_idletasks()
        sw = self._root.winfo_screenwidth()
        sh = self._root.winfo_screenheight()
        w = max(self._root.winfo_width(), 220)
        h = max(self._root.winfo_height(), 40)
        self._root.geometry(f"+{max(0, sw - w - 24)}+{max(0, sh - h - 80)}")

    # ------------------------------------------------------------ 锁定布局

    def _set_locked(self, locked: bool) -> None:
        """锁定布局：鼠标事件穿过 HUD（仅 Windows 有效）。

        锁定的同时也就无法拖动 —— 这正是"锁定"的含义，
        想调整位置时先在设置界面取消勾选。
        """
        if os.name != "nt":
            return
        try:
            import ctypes

            user32 = ctypes.windll.user32
            hwnd = user32.GetParent(self._root.winfo_id()) or self._root.winfo_id()
            current = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            if self._original_exstyle is None:
                self._original_exstyle = current
            target = (current | WS_EX_LAYERED | WS_EX_TRANSPARENT) if locked \
                else (current & ~WS_EX_TRANSPARENT)
            user32.SetWindowLongW(hwnd, GWL_EXSTYLE, target)
        except Exception as exc:
            self._log(f"设置锁定布局失败：{exc}")

    # ---------------------------------------------------------------- 交互

    def _on_press(self, event) -> None:
        self._drag_origin = (event.x_root - self._root.winfo_x(),
                             event.y_root - self._root.winfo_y())

    def _on_drag(self, event) -> None:
        if self._drag_origin is None:
            return
        self._root.geometry(f"+{event.x_root - self._drag_origin[0]}"
                            f"+{event.y_root - self._drag_origin[1]}")

    def _on_release(self, _event) -> None:
        """拖动结束：记住位置，下次启动还在那儿。"""
        self._drag_origin = None
        self._settings.overlay_x = self._root.winfo_x()
        self._settings.overlay_y = self._root.winfo_y()
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
        var_emoji = tk.BooleanVar(value=self._settings.overlay_emoji)
        menu.add_checkbutton(label="显示 emoji 图标", variable=var_emoji,
                             command=lambda: self._toggle("overlay_emoji", var_emoji))
        menu.add_separator()
        for field, label in (("overlay_show_device", "显示型号"),
                             ("overlay_show_battery", "显示电量"),
                             ("overlay_show_charging", "显示充电状态")):
            var = tk.BooleanVar(value=getattr(self._settings, field))
            menu.add_checkbutton(label=label, variable=var,
                                 command=lambda f=field, v=var: self._toggle(f, v))
        menu.add_separator()
        menu.add_command(label="位置复位到右下角", command=self._reset_position)
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
        self._apply_settings()

    def _reset_position(self) -> None:
        """把窗口放回右下角，并把设置也改成"自动"。"""
        self._settings.overlay_x = -1
        self._settings.overlay_y = -1
        self._settings.save()
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
        self._root.destroy()

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
        """后台线程：按**常规设置里的刷新间隔**周期性读取。

        与托盘共用同一个间隔，避免出现"HUD 每 30 秒、托盘每 60 秒"两条节奏。
        """
        while not self._stop.is_set():
            self._read_once()
            interval = max(5, int(self._settings.poll_seconds))
            self._stop.wait(interval)

    # ---------------------------------------------------------------- 主循环

    def _tick(self) -> None:
        """主线程定时任务：取新数据 + 热加载配置 + 复用共享读数。"""
        # 1) 是否有自己的读取结果
        latest = None
        try:
            while True:
                latest = self._queue.get_nowait()
        except queue.Empty:
            pass
        if latest is not None:
            self._status = latest
            self._log(latest.summary())

        # 2) 别的界面（托盘/设置窗口）刚读到的更新的值 → 直接采用，保持显示一致
        shared = load_status()
        if shared is not None and not shared.error and shared.timestamp > self._status.timestamp:
            self._status = BatteryStatus(
                device_id=shared.device_id, device_name=shared.device_name,
                battery_percent=shared.battery_percent, charging=shared.charging,
                timestamp=shared.timestamp,
            )

        # 3) 配置文件是否被图形界面改动
        self._reload_config_if_changed()

        # 4) 刷新文字
        self._render_text()
        self._root.after(300, self._tick)

    def _reload_config_if_changed(self) -> None:
        """检测 config.json 变化并应用（实现"界面里改，HUD 立刻变"）。"""
        try:
            mtime = os.path.getmtime(config_path())
        except OSError:
            return
        if mtime <= self._config_mtime:
            return
        first = self._config_mtime == 0.0
        self._config_mtime = mtime
        if first:
            return  # 首次只记录基准
        self._settings = Settings.load()
        self._apply_settings()
        self._log("检测到设置变更，HUD 已热更新")

    def _render_text(self) -> None:
        """把状态拼成 HUD 文本（可选 emoji 图标）。"""
        s = self._settings
        st = self._status
        parts: list[str] = []
        if s.overlay_show_device and st.device_name:
            name = st.device_name.split("(")[0].strip()   # 去掉括号里的型号代码
            parts.append(("🎮 " if s.overlay_emoji else "") + name)
        if s.overlay_show_battery:
            value = f"{st.battery_percent}%" if st.battery_percent is not None else "--"
            parts.append(("🔋 " if s.overlay_emoji else "") + value)
        if s.overlay_show_charging:
            if st.charging:
                parts.append(("⚡ " if s.overlay_emoji else "") + "充电中")
            else:
                parts.append("电池")
        text = "   ".join(parts) if parts else "betop-battery"
        if st.error:
            text = ("⚠ " if s.overlay_emoji else "") + st.error
        if self._label.cget("text") != text:
            self._label.configure(text=text)

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
