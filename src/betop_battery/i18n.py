# -*- coding: utf-8 -*-
"""界面多语言（中文 / English）。

设计
----
* 语言由 :func:`set_language` 设置，接受 ``auto`` / ``zh`` / ``en``；
  ``auto`` 会按系统界面语言解析（Windows 上优先用系统 API，最可靠）
* 所有界面文案都走 :func:`t`，键缺失时**回退成键名本身**，绝不抛异常 ——
  漏翻一条不该让界面崩掉
* 设备描述文件可提供 ``name_en``，英文界面下显示英文型号名
"""

from __future__ import annotations

import os
import re
from typing import Any

#: 支持的语言（顺序即界面下拉框的顺序）
LANGUAGES = ("zh", "en")

#: 语言显示名（自身语言书写）
LANGUAGE_NAMES = {"zh": "简体中文", "en": "English"}

#: ``auto`` 在界面上的显示名
AUTO_NAME = {"zh": "跟随系统", "en": "Follow system"}

_ZH: dict[str, str] = {
    # ---- 通用 / 状态 ----
    "app_name": "北通手柄电量",
    "app_name_short": "手柄电量",
    "window_title": "betop-battery 设置",
    "device_default": "北通手柄",
    "reading": "读取中…",
    "no_device": "未找到手柄",
    "charging": "充电中",
    "on_battery": "使用电池",
    "updated_at": "更新于 {time}",
    "current_status": "当前状态",
    "refresh_now": "立即刷新",
    # ---- 标签页 ----
    "tab_general": "常规",
    "tab_tray": "托盘图标",
    "tab_hud": "HUD",
    # ---- 常规页 ----
    "language": "界面语言",
    "hint_restart": "（托盘/HUD 将在其下次刷新时同步）",
    "choose_color": "选择颜色",
    "refresh_interval": "刷新间隔（托盘与 HUD 共用）",
    "low_battery_threshold": "低电量提醒阈值",
    "notify_on_low": "低于阈值时弹出系统通知",
    "device_filter": "只读取指定型号 id（留空 = 自动识别）",
    "view_devices": "查看已支持型号",
    "hint_sleep": "提示：手柄休眠时读不到数据，按一下手柄按键即可。",
    "hint_official": "官方客户端可以同时开着，不冲突。",
    "hint_single": "当前版本针对「单只手柄」开发，同时连接多只手柄可能出现意外表现。",
    # ---- 托盘图标页 ----
    "tray_enabled": "启用托盘图标（通知区域显示电量）",
    "tray_enabled_hint": "（勾选立即出现，取消立即消失）",
    "icon_style": "图标样式",
    "style_number": "数字方块（清晰醒目）",
    "style_ring": "圆环进度（简洁）",
    "style_battery": "电池外形（直观）",
    "color_scheme": "配色方案",
    "scheme_auto": "自动（绿/黄/红，充电蓝）",
    "scheme_mono": "单色（浅灰）",
    "show_charging_marker": "显示充电标记",
    "preview": "预览（使用当前真实电量）",
    # ---- HUD 页 ----
    "hud_enabled": "启用 HUD（在屏幕上常驻显示电量，类似帧数叠加层）",
    "hud_interval_hint": "（刷新间隔与「常规」页共用）",
    "opacity": "透明度",
    "font_size": "字号",
    "display_content": "显示内容",
    "show_device": "型号",
    "show_battery": "电量",
    "charging_always": "充电状态始终展示：用电池显示 🔋，充电中显示 ⚡",
    "lock_layout": "锁定布局（鼠标穿透，游戏时推荐；锁定后需先取消才能拖动）",
    "bg_color": "背景色",
    "fg_color": "文字色",
    "reset": "复位",
    "reset_hint": "（初始位置在屏幕右下角；平时直接用鼠标拖动 HUD 即可，位置会自动记住）",
    "fullscreen_hint": "提示：若游戏以独占全屏运行，HUD 可能不可见 —— 请把游戏设为「无边框窗口」。",
    # ---- 底部 ----
    "settings_file": "设置文件：",
    "apply": "应用",
    "close": "关闭",
    "applied": "已应用 ✓",
    "apply_failed": "应用失败：{err}",
    "saved_title": "已保存",
    "saved_body": "设置已保存。\n\n· 托盘图标会立即使用新样式\n· HUD 会自动热更新（若正在运行）",
    "save_failed": "保存失败",
    "supported_models": "已支持的型号",
    "adapt_hint": "适配新型号请看 docs/adapt-new-device.md",
    "started_tray": "已启动托盘图标",
    "stopped_tray": "已关闭托盘图标",
    "started_hud": "已启动 HUD",
    "stopped_hud": "已关闭 HUD",
    "reset_requested": "已请求 HUD 复位到右下角",
    # ---- 托盘菜单 / 提示 ----
    "menu_settings": "设置…",
    "menu_hud": "HUD",
    "menu_interval": "刷新间隔",
    "menu_threshold": "低电量提醒",
    "menu_notify": "启用通知",
    "menu_quit": "退出",
    "seconds": "{n} 秒",
    "tooltip_battery": "电量：{n}%",
    "tooltip_status": "状态：{s}",
    "tooltip_updated": "更新：{time}",
    "notify_low_body": "电量仅剩 {n}%，该充电了",
    "settings_foreground": "设置窗口已在前台",
    "settings_open_failed": "打开设置界面失败（可能是缺少 tkinter）",
    "tray_started": "托盘已启动，右键图标可查看菜单。",
    "tray_disabled": "设置里已关闭托盘图标，正在退出",
    "hud_started": "HUD 已启动：拖动可移动，右键有菜单",
    "hud_disabled": "设置里已关闭 HUD，正在退出",
    "settings_changed_tray": "检测到设置变更，托盘已热更新",
    "settings_changed_hud": "检测到设置变更，HUD 已热更新",
    "position_saved": "HUD 位置已保存：({x}, {y})",
    # ---- HUD 文本 ----
    "hud_battery_mode": "电池",
    # ---- 读取器错误 ----
    "err_no_hidapi": "未安装 hidapi（pip install hidapi）",
    "err_no_device": "未检测到受支持的手柄（请按一下手柄按键唤醒，或确认接收器已插好）",
    "err_no_response": "未收到状态响应（手柄可能处于休眠，按一下按键再试）",
    "err_no_descriptors": "没有可用的设备描述文件（devices/*.json）",
    "err_open_failed": "打不开 HID 接口（可能被官方客户端独占，或手柄已休眠）：{err}",
    "err_write_failed": "写入查询失败（尝试长度 {lengths}）：{err}",
}

_EN: dict[str, str] = {
    # ---- common / status ----
    "app_name": "BETOP Battery",
    "app_name_short": "Gamepad battery",
    "window_title": "betop-battery Settings",
    "device_default": "BETOP gamepad",
    "reading": "Reading…",
    "no_device": "No gamepad found",
    "charging": "Charging",
    "on_battery": "On battery",
    "updated_at": "Updated {time}",
    "current_status": "Current status",
    "refresh_now": "Refresh now",
    # ---- tabs ----
    "tab_general": "General",
    "tab_tray": "Tray icon",
    "tab_hud": "HUD",
    # ---- general ----
    "language": "Language",
    "hint_restart": "(tray/HUD pick it up on their next refresh)",
    "choose_color": "Choose a colour",
    "refresh_interval": "Refresh interval (shared by tray and HUD)",
    "low_battery_threshold": "Low battery threshold",
    "notify_on_low": "Show a system notification below the threshold",
    "device_filter": "Only read this model id (empty = auto detect)",
    "view_devices": "View supported models",
    "hint_sleep": "Tip: the gamepad does not report while asleep — press any button on it.",
    "hint_official": "The official client can stay open at the same time; they do not conflict.",
    "hint_single": "This release targets a single controller; connecting several may behave unexpectedly.",
    # ---- tray icon tab ----
    "tray_enabled": "Enable the tray icon (battery in the notification area)",
    "tray_enabled_hint": "(appears immediately when checked, disappears when unchecked)",
    "icon_style": "Icon style",
    "style_number": "Number tile (clearest)",
    "style_ring": "Progress ring (compact)",
    "style_battery": "Battery shape (intuitive)",
    "color_scheme": "Colour scheme",
    "scheme_auto": "Auto (green/yellow/red, blue while charging)",
    "scheme_mono": "Monochrome (light grey)",
    "show_charging_marker": "Show a charging marker",
    "preview": "Preview (uses your real battery level)",
    # ---- HUD tab ----
    "hud_enabled": "Enable the HUD (battery overlay on screen, like a frame counter)",
    "hud_interval_hint": "(the interval is shared with the General tab)",
    "opacity": "Opacity",
    "font_size": "Font size",
    "display_content": "Show",
    "show_device": "Model",
    "show_battery": "Battery",
    "charging_always": "Charging state is always shown: 🔋 on battery, ⚡ while charging",
    "lock_layout": "Lock layout (mouse click-through, recommended in games; unlock to drag)",
    "bg_color": "Background",
    "fg_color": "Text",
    "reset": "Reset",
    "reset_hint": "(defaults to the bottom-right corner; just drag the HUD — the position is remembered)",
    "fullscreen_hint": "Tip: with exclusive fullscreen the HUD may be hidden — use borderless windowed mode.",
    # ---- footer ----
    "settings_file": "Settings file: ",
    "apply": "Apply",
    "close": "Close",
    "applied": "Applied ✓",
    "apply_failed": "Apply failed: {err}",
    "saved_title": "Saved",
    "saved_body": "Settings saved.\n\n· The tray icon uses the new style immediately\n· The HUD hot-reloads if it is running",
    "save_failed": "Save failed",
    "supported_models": "Supported models",
    "adapt_hint": "See docs/adapt-new-device.md to add a model",
    "started_tray": "Tray icon started",
    "stopped_tray": "Tray icon stopped",
    "started_hud": "HUD started",
    "stopped_hud": "HUD stopped",
    "reset_requested": "Asked the HUD to move back to the bottom-right corner",
    # ---- tray menu / tooltip ----
    "menu_settings": "Settings…",
    "menu_hud": "HUD",
    "menu_interval": "Refresh interval",
    "menu_threshold": "Low battery alert",
    "menu_notify": "Enable notifications",
    "menu_quit": "Quit",
    "seconds": "{n} s",
    "tooltip_battery": "Battery: {n}%",
    "tooltip_status": "Status: {s}",
    "tooltip_updated": "Updated: {time}",
    "notify_low_body": "Only {n}% left — time to charge",
    "settings_foreground": "Settings window brought to front",
    "settings_open_failed": "Could not open the settings window (tkinter missing?)",
    "tray_started": "Tray started — right-click the icon for the menu.",
    "tray_disabled": "Tray icon disabled in settings, exiting",
    "hud_started": "HUD started — drag to move, right-click for the menu",
    "hud_disabled": "HUD disabled in settings, exiting",
    "settings_changed_tray": "Settings changed, tray updated",
    "settings_changed_hud": "Settings changed, HUD updated",
    "position_saved": "HUD position saved: ({x}, {y})",
    # ---- HUD text ----
    "hud_battery_mode": "Battery",
    # ---- reader errors ----
    "err_no_hidapi": "hidapi is not installed (pip install hidapi)",
    "err_no_device": "No supported gamepad detected (press a button on it, or check the receiver)",
    "err_no_response": "No status reply (the gamepad may be asleep — press a button and retry)",
    "err_no_descriptors": "No device descriptors found (devices/*.json)",
    "err_open_failed": "Cannot open the HID interface (owned by the vendor client, or asleep): {err}",
    "err_write_failed": "Failed to write the query (tried lengths {lengths}): {err}",
}

#: 全部翻译
TRANSLATIONS: dict[str, dict[str, str]] = {"zh": _ZH, "en": _EN}

#: 当前语言（模块级状态；由 set_language 设置）
_current = "zh"


def detect_language() -> str:
    """按系统界面语言猜一个语言代码。

    Windows 上优先用 ``GetUserDefaultUILanguage``（最可靠，不受控制台代码页影响），
    其它平台看 ``LANG`` / ``locale``。

    Returns:
        ``"zh"`` 或 ``"en"``。
    """
    if os.name == "nt":
        try:
            import ctypes

            langid = ctypes.windll.kernel32.GetUserDefaultUILanguage()
            primary = langid & 0x3FF            # 主语言 ID
            if primary == 0x04:                 # LANG_CHINESE
                return "zh"
            return "en"
        except Exception:
            pass
    for value in (os.environ.get("LC_ALL"), os.environ.get("LC_MESSAGES"),
                  os.environ.get("LANG"), os.environ.get("LANGUAGE")):
        if value and value.lower().startswith("zh"):
            return "zh"
    try:
        import locale

        loc = str(locale.getlocale()[0] or "")
        if loc.lower().startswith("zh") or "chinese" in loc.lower():
            return "zh"
    except Exception:
        pass
    return "en"


def resolve_language(setting: str) -> str:
    """把设置值（auto/zh/en）解析成实际语言。"""
    value = (setting or "auto").strip().lower()
    if value in LANGUAGES:
        return value
    return detect_language()


def set_language(setting: str) -> str:
    """设置当前语言。

    Args:
        setting: ``auto`` / ``zh`` / ``en``

    Returns:
        实际生效的语言代码。
    """
    global _current
    _current = resolve_language(setting)
    return _current


def current_language() -> str:
    """当前生效的语言代码。"""
    return _current


def t(key: str, **kwargs: Any) -> str:
    """取一条翻译。

    Args:
        key:    文案键
        kwargs: 占位符替换，例如 ``t("seconds", n=30)``

    Returns:
        翻译后的文案；键不存在时回退成键名本身（不抛异常）。
    """
    table = TRANSLATIONS.get(_current) or _ZH
    text = table.get(key) or _ZH.get(key) or key
    if kwargs:
        try:
            return text.format(**kwargs)
        except Exception:
            return text
    return text


def device_name(name: str, name_en: str | None = None) -> str:
    """按当前语言挑选设备显示名。"""
    if _current == "en" and name_en:
        return name_en
    return name


def strip_device_code(name: str) -> str:
    """去掉型号名后面括号里的产品代码，让 HUD 更简洁。"""
    return re.sub(r"\s*[（(].*?[)）]\s*$", "", name).strip() or name
