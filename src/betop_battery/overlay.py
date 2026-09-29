# -*- coding: utf-8 -*-
"""叠加层悬浮窗（HUD）：像显卡帧数那样在游戏画面上常驻显示手柄电量。

设计要点
--------
* **只用标准库 tkinter** —— 不引入 PySide6/Qt 之类的重依赖，exe 体积可控
* 无边框 + 置顶 + 半透明，形态接近 RTSS/Afterburner 的 HUD
* **可选鼠标穿透**（Windows 下设置 ``WS_EX_TRANSPARENT``），游戏时不会挡住操作
* 读取 ``config.json`` 的改动并**热更新**外观，所以图形界面里调参数能立刻看到效果
* 电量在**后台线程**读取，通过队列交给主线程刷新 —— tkinter 不是线程安全的

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

#: Windows 扩展窗口样式常量（用于鼠标穿透）
GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020

#: 检查配置变化的时间间隔（毫秒）
CONFIG_POLL_MS = 1500


class OverlayWindow:
    """悬浮叠加层。

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

        self._root = tk.Tk()
        self._root.title("betop-battery overlay")
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

        # 文本用 Label 便于换内容；字号/颜色随后由 _apply_settings 统一设置
        self._label = tk.Label(root, text="…", justify="left", anchor="w",
                               bg=self._settings.overlay_bg,
                               fg=self._settings.overlay_fg)
        self._label.pack(padx=12, pady=8)

        # 拖动移动位置（鼠标穿透开启时收不到事件，这是预期行为）
        self._label.bind("<Button-1>", self._on_press)
        self._label.bind("<B1-Motion>", self._on_drag)
        self._label.bind("<ButtonRelease-1>", self._on_release)
        self._label.bind("<Button-3>", self._on_right_click)

        self._apply_settings(initial=True)

    def _apply_settings(self, initial: bool = False) -> None:
        """把当前设置应用到窗口（位置、透明度、字号、颜色、穿透）。"""
        s = self._settings
        self._root.configure(bg=s.overlay_bg)
        self._label.configure(bg=s.overlay_bg, fg=s.overlay_fg,
                              font=("Microsoft YaHei UI", s.overlay_font_size, "bold"))
        try:
            self._root.attributes("-alpha", max(0.2, min(1.0, s.overlay_opacity)))
        except Exception:
            pass
        self._set_click_through(s.overlay_click_through)
        if initial or s.overlay_x < 0 or s.overlay_y < 0:
            self._move_to_default_corner()
        else:
            self._root.geometry(f"+{s.overlay_x}+{s.overlay_y}")

    def _move_to_default_corner(self) -> None:
        """默认放到屏幕右下角（避开任务栏）。"""
        self._root.update_idletasks()
        sw = self._root.winfo_screenwidth()
        sh = self._root.winfo_screenheight()
        w = max(self._root.winfo_width(), 190)
        h = max(self._root.winfo_height(), 40)
        self._root.geometry(f"+{sw - w - 24}+{sh - h - 80}")

    # ------------------------------------------------------------ 鼠标穿透

    def _set_click_through(self, enabled: bool) -> None:
        """开关鼠标穿透（仅 Windows 有效）。"""
        if os.name != "nt":
            return
        try:
            import ctypes

            user32 = ctypes.windll.user32
            hwnd = user32.GetParent(self._root.winfo_id()) or self._root.winfo_id()
            current = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            if self._original_exstyle is None:
                self._original_exstyle = current
            if enabled:
                target = current | WS_EX_LAYERED | WS_EX_TRANSPARENT
            else:
                target = current & ~WS_EX_TRANSPARENT
            user32.SetWindowLongW(hwnd, GWL_EXSTYLE, target)
        except Exception as exc:
            self._log(f"设置鼠标穿透失败：{exc}")

    # ---------------------------------------------------------------- 交互

    def _on_press(self, event) -> None:
        self._drag_origin = (event.x_root - self._root.winfo_x(),
                             event.y_root - self._root.winfo_y())

    def _on_drag(self, event) -> None:
        if self._drag_origin is None:
            return
        x = event.x_root - self._drag_origin[0]
        y = event.y_root - self._drag_origin[1]
        self._root.geometry(f"+{x}+{y}")

    def _on_release(self, _event) -> None:
        """拖动结束：记住位置，下次启动还在那儿。"""
        self._drag_origin = None
        self._settings.overlay_x = self._root.winfo_x()
        self._settings.overlay_y = self._root.winfo_y()
        self._settings.save()
        self._log(f"叠加层位置已保存：({self._settings.overlay_x}, {self._settings.overlay_y})")

    def _on_right_click(self, _event) -> None:
        """右键菜单：常用开关。"""
        menu = tk.Menu(self._root, tearoff=0)
        menu.add_command(label="立即刷新", command=self._request_refresh)
        menu.add_separator()
        var_ct = tk.BooleanVar(value=self._settings.overlay_click_through)
        menu.add_checkbutton(label="鼠标穿透（游戏时推荐）", variable=var_ct,
                             command=lambda: self._toggle("overlay_click_through", var_ct))
        var_dev = tk.BooleanVar(value=self._settings.overlay_show_device)
        menu.add_checkbutton(label="显示型号", variable=var_dev,
                             command=lambda: self._toggle("overlay_show_device", var_dev))
        var_bat = tk.BooleanVar(value=self._settings.overlay_show_battery)
        menu.add_checkbutton(label="显示电量", variable=var_bat,
                             command=lambda: self._toggle("overlay_show_battery", var_bat))
        var_chg = tk.BooleanVar(value=self._settings.overlay_show_charging)
        menu.add_checkbutton(label="显示充电状态", variable=var_chg,
                             command=lambda: self._toggle("overlay_show_charging", var_chg))
        menu.add_separator()
        menu.add_command(label="复位位置（右下角）", command=self._reset_position)
        menu.add_separator()
        menu.add_command(label="关闭叠加层", command=self._quit)
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
        """把窗口放回右下角。"""
        self._settings.overlay_x = -1
        self._settings.overlay_y = -1
        self._settings.save()
        self._move_to_default_corner()

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
            self._queue.put(self._reader.read())
        except Exception as exc:
            self._queue.put(BatteryStatus(error=str(exc)))

    def _poll_loop(self) -> None:
        """后台线程：按间隔周期性读取。"""
        while not self._stop.is_set():
            self._read_once()
            interval = max(5, int(self._settings.overlay_refresh_seconds))
            self._stop.wait(interval)

    # ---------------------------------------------------------------- 主循环

    def _tick(self) -> None:
        """主线程定时任务：取新数据 + 热加载配置。"""
        # 1) 是否有新的读取结果
        latest = None
        try:
            while True:
                latest = self._queue.get_nowait()
        except queue.Empty:
            pass
        if latest is not None:
            self._status = latest
            self._log(latest.summary())

        # 2) 配置文件是否被图形界面改动
        self._reload_config_if_changed()

        # 3) 刷新文字
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
            return  # 首次只是记录基准，避免自己刚保存又重复应用
        new_settings = Settings.load()
        # 保留运行期由拖动产生的位置（若界面没改位置）
        if new_settings.overlay_x < 0:
            new_settings.overlay_x = self._settings.overlay_x
            new_settings.overlay_y = self._settings.overlay_y
        self._settings = new_settings
        self._apply_settings()
        self._log("检测到设置变更，已热更新")

    def _render_text(self) -> None:
        """把状态拼成 HUD 文本。"""
        s = self._settings
        st = self._status
        parts: list[str] = []
        if s.overlay_show_device and st.device_name:
            name = st.device_name.split("(")[0].strip()  # 去掉括号里的型号代码，保持简洁
            parts.append(name)
        if s.overlay_show_battery:
            parts.append(f"电量 {st.battery_percent}%" if st.battery_percent is not None else "电量 --")
        if s.overlay_show_charging:
            parts.append("充电中" if st.charging else "电池")
        text = "  ·  ".join(parts) if parts else "betop-battery"
        if st.error:
            text = f"⚠ {st.error}"
        if self._label.cget("text") != text:
            self._label.configure(text=text)

    # ---------------------------------------------------------------- 对外

    def run(self) -> int:
        """启动叠加层（阻塞）。"""
        self._log("叠加层已启动：拖动可移动，右键有菜单")
        threading.Thread(target=self._poll_loop, daemon=True).start()
        self._root.after(200, self._tick)
        try:
            self._root.mainloop()
        except KeyboardInterrupt:
            pass
        return 0


def run_overlay(reader: BatteryReader, settings: Optional[Settings] = None) -> int:
    """便捷入口：启动叠加层。"""
    return OverlayWindow(reader, settings).run()
