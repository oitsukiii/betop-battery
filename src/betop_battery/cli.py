# -*- coding: utf-8 -*-
"""命令行入口与命令分发。

命令一览::

    betop-battery                读一次电量并打印（默认）
    betop-battery once           同上，可加 --json
    betop-battery tray           启动托盘图标（通知区域）
    betop-battery gui            打开图形设置界面
    betop-battery overlay        启动悬浮叠加层（HUD）
    betop-battery devices        列出已支持的型号
    betop-battery list           列出系统上的 HID 接口
    betop-battery probe <子命令> 调试工具（适配新型号用）

设计说明：本模块只做”解析参数 → 调用对应模块 → 决定退出码”，
不含业务逻辑，便于测试与替换界面。
"""

from __future__ import annotations

import argparse
import json as jsonlib
import sys
from typing import Optional

from .config import Settings, config_path
from .devices import DEVICES_DIR, is_vendor_usage_page, load_descriptors
from .protocol import CMD_STATUS, SUB_STATUS, encode_header
from .reader import BatteryReader, BatteryStatus

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_USAGE = 2


def _build_parser() -> argparse.ArgumentParser:
    """构造参数解析器。"""
    parser = argparse.ArgumentParser(
        prog="betop-battery",
        description="读取北通（BETOP）手柄电量 —— 开源、无需官方客户端。",
        epilog="适配新型号请看 docs/adapt-new-device.md；欢迎提 PR。",
    )
    parser.add_argument("--device", default="", help="只使用指定型号 id（见 devices 命令）")
    parser.add_argument("--version", action="version", version=_version_text())
    parser.add_argument("--debug", action="store_true", help="出错时打印完整堆栈（调试用）")

    sub = parser.add_subparsers(dest="command")

    # once -----------------------------------------------------------------
    p_once = sub.add_parser("once", help="读一次电量并打印（默认命令）")
    p_once.add_argument("--json", action="store_true", help="以 JSON 输出")
    p_once.add_argument("--timeout", type=float, default=2.0, help="等待响应的秒数")

    # tray -----------------------------------------------------------------
    p_tray = sub.add_parser("tray", help="启动系统托盘图标")
    p_tray.add_argument("--interval", type=int, default=None, help="刷新间隔秒数")
    p_tray.add_argument("--threshold", type=int, default=None, help="低电量阈值百分比")

    # gui ------------------------------------------------------------------
    sub.add_parser("gui", help="打开图形设置界面（含图标样式与叠加层设置）")

    # overlay --------------------------------------------------------------
    p_ov = sub.add_parser("overlay", help="启动悬浮叠加层（类似帧数 HUD）")
    p_ov.add_argument("--opacity", type=float, default=None, help="不透明度 0.2~1.0")
    p_ov.add_argument("--font-size", type=int, default=None, dest="font_size", help="字号")
    p_ov.add_argument("--click-through", action="store_true", default=None,
                      dest="click_through", help="鼠标穿透（游戏时推荐）")

    # devices --------------------------------------------------------------
    sub.add_parser("devices", help="列出已支持的型号")

    # list -----------------------------------------------------------------
    p_list = sub.add_parser("list", help="列出系统上的 HID 接口")
    p_list.add_argument("--all", action="store_true", help="列出全部厂商（默认只看北通）")

    # probe ----------------------------------------------------------------
    p_probe = sub.add_parser("probe", help="调试工具（适配新型号）")
    probe_sub = p_probe.add_subparsers(dest="probe_command")
    pi = probe_sub.add_parser("interfaces", help="列出 HID 接口")
    pi.add_argument("--all", action="store_true", help="列出全部厂商")
    pd = probe_sub.add_parser("dump", help="发送查询并转储响应帧")
    pd.add_argument("--seconds", type=float, default=2.0)
    pd.add_argument("--iface", type=int, default=None, help="接口序号（见 interfaces）")
    pw = probe_sub.add_parser("watch", help="周期性抓取，观察字节变化")
    pw.add_argument("--rounds", type=int, default=10)
    pw.add_argument("--interval", type=float, default=2.0)
    pw.add_argument("--iface", type=int, default=None)
    ps = probe_sub.add_parser("scan", help="尝试各类状态查询命令（只读类）")
    ps.add_argument("--iface", type=int, default=None)
    pg = probe_sub.add_parser("suggest", help="生成设备描述文件草稿")
    pg.add_argument("--seconds", type=float, default=3.0)
    pg.add_argument("--iface", type=int, default=None)

    return parser


def _version_text() -> str:
    """版本字符串。"""
    from . import __version__

    return f"betop-battery {__version__}"


# ---------------------------------------------------------------------------
# 各命令实现
# ---------------------------------------------------------------------------

def _make_reader(device_id: str = "") -> BatteryReader:
    """构造读取器（统一的错误提示）。"""
    try:
        return BatteryReader(device_id=device_id or None)
    except ValueError as exc:
        print(f"✗ {exc}")
        raise SystemExit(EXIT_FAIL)


def cmd_once(args) -> int:
    """读一次并打印。"""
    reader = _make_reader(args.device)
    status = reader.read(timeout=args.timeout)

    if args.json:
        print(jsonlib.dumps(
            {
                "ok": status.ok,
                "device_id": status.device_id,
                "device_name": status.device_name,
                "battery_percent": status.battery_percent,
                "charging": status.charging,
                "error": status.error,
                "raw_frame": status.raw_frame.hex(" ") if status.raw_frame else None,
                "timestamp": status.timestamp,
            },
            ensure_ascii=False,
            indent=2,
        ))
        return EXIT_OK if status.ok else EXIT_FAIL

    if status.error:
        print(f"✗ {status.error}")
        return EXIT_FAIL
    print(f"设备：{status.device_name}")
    print(f"电量：{status.battery_percent}%")
    print(f"状态：{'充电中' if status.charging else '使用电池'}")
    if status.raw_frame:
        print("原始帧：" + " ".join(f"{b:02X}" for b in status.raw_frame[:16]))
    return EXIT_OK


def cmd_tray(args) -> int:
    """启动托盘。"""
    settings = Settings.load()
    if args.interval:
        settings.poll_seconds = args.interval
    if args.threshold:
        settings.low_battery_threshold = args.threshold
    settings.save()

    reader = _make_reader(settings.device_id or args.device)
    try:
        from .log import log_file_path, make_logger
        from .tray import run_tray
    except Exception as exc:
        print(f"✗ 无法加载托盘组件：{exc}")
        return EXIT_FAIL
    logger = make_logger()
    logger(f"日志位置：{log_file_path()}")
    try:
        return run_tray(reader, settings, on_log=logger)
    except RuntimeError as exc:
        print(f"✗ {exc}")
        return EXIT_FAIL


def cmd_gui(args) -> int:
    """打开图形设置界面。"""
    settings = Settings.load()
    reader = _make_reader(settings.device_id or args.device)
    try:
        from .gui import run_gui
    except Exception as exc:
        print(f"✗ 无法加载图形界面（需要 tkinter）：{exc}")
        return EXIT_FAIL
    try:
        return run_gui(reader, settings)
    except Exception as exc:
        print(f"✗ 图形界面启动失败：{exc}")
        return EXIT_FAIL


def cmd_overlay(args) -> int:
    """启动叠加层（命令行参数会覆盖配置并保存）。"""
    settings = Settings.load()
    if args.opacity is not None:
        settings.overlay_opacity = args.opacity
    if args.font_size is not None:
        settings.overlay_font_size = args.font_size
    if args.click_through:
        settings.overlay_click_through = True
    settings.overlay_enabled = True
    settings.save()

    reader = _make_reader(settings.device_id or args.device)
    try:
        from .overlay import run_overlay
    except Exception as exc:
        print(f"✗ 无法加载叠加层（需要 tkinter）：{exc}")
        return EXIT_FAIL
    try:
        return run_overlay(reader, settings)
    except Exception as exc:
        print(f"✗ 叠加层启动失败：{exc}")
        return EXIT_FAIL


def cmd_devices(args) -> int:
    """列出支持的型号。"""
    descriptors = load_descriptors(DEVICES_DIR)
    if not descriptors:
        print("没有找到任何设备描述文件。")
        return EXIT_FAIL
    print(f"内置支持 {len(descriptors)} 个型号（描述目录：{DEVICES_DIR}）\n")
    for desc in descriptors:
        print(f"● {desc.id}  ——  {desc.name}")
        print(f"    匹配: VID={desc.match.vendor_id:#06x} PID={desc.match.product_id:#06x}"
              + (f" usage_page={desc.match.usage_page:#06x}" if desc.match.usage_page else "")
              + (f" usage={desc.match.usage:#04x}" if desc.match.usage else ""))
        print(f"    查询: report_id={desc.query.report_id:#04x} header={desc.query.header:#04x}")
        if desc.tested:
            t = desc.tested
            print(f"    实测: {t.get('os', '?')} / {t.get('connection', '?')} / {t.get('date', '?')}")
        print()
    print("想加新型号？看 docs/adapt-new-device.md —— 只需新增一个 JSON。")
    return EXIT_OK


def cmd_list(args) -> int:
    """列出 HID 接口（复用 probe 的实现）。"""
    from .probe import cmd_interfaces

    return cmd_interfaces(vendor_id=None if args.all else 0x20BC)


def cmd_probe(args) -> int:
    """调试子命令分发。"""
    from . import probe
    from .transport import enumerate_interfaces

    sub = getattr(args, "probe_command", None)
    if sub is None:
        print("用法：betop-battery probe {interfaces|dump|watch|scan|suggest}")
        return EXIT_USAGE

    if sub == "interfaces":
        return probe.cmd_interfaces(vendor_id=None if args.all else 0x20BC)

    # 其余子命令需要一个接口
    vendor_id = None if getattr(args, "all", False) else 0x20BC
    ifaces = [i for i in enumerate_interfaces() if (vendor_id is None or i.vendor_id == vendor_id)]
    if not ifaces:
        print("✗ 没有找到 HID 接口。请确认接收器插好、按一下手柄按键。")
        return EXIT_FAIL
    # 默认优先厂商接口（usage_page >= 0xFF00）
    if getattr(args, "iface", None) is not None:
        try:
            iface = ifaces[args.iface]
        except IndexError:
            print(f"✗ 接口序号 {args.iface} 超出范围（共 {len(ifaces)} 个）")
            return EXIT_USAGE
    else:
        vendor = [i for i in ifaces if is_vendor_usage_page(i.usage_page)]
        iface = (vendor or ifaces)[0]
        if not vendor:
            print("提示：这个设备没有 0xFF00 厂商接口，结果可能不完整。")

    # 已知型号就带上它的查询命令
    from .devices import find_device

    desc, _ = find_device([iface], load_descriptors(DEVICES_DIR))

    if sub == "dump":
        return probe.cmd_dump(iface, desc, seconds=args.seconds)
    if sub == "watch":
        return probe.cmd_watch(iface, desc, rounds=args.rounds, interval=args.interval)
    if sub == "scan":
        return probe.cmd_scan(iface)
    if sub == "suggest":
        return probe.cmd_suggest(iface, desc, seconds=args.seconds)
    print(f"✗ 未知 probe 子命令：{sub}")
    return EXIT_USAGE


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main(argv: Optional[list[str]] = None) -> int:
    """程序入口。

    Args:
        argv: 参数列表（默认取 sys.argv[1:]）

    Returns:
        进程退出码。
    """
    # 中文 Windows 上防止输出重定向时因编码崩溃（详见 log.prepare_output）
    from .log import prepare_output

    prepare_output()

    parser = _build_parser()
    args = parser.parse_args(argv)

    command = args.command or "once"
    try:
        if command == "once":
            return cmd_once(args)
        if command == "tray":
            return cmd_tray(args)
        if command == "gui":
            return cmd_gui(args)
        if command == "overlay":
            return cmd_overlay(args)
        if command == "devices":
            return cmd_devices(args)
        if command == "list":
            return cmd_list(args)
        if command == "probe":
            return cmd_probe(args)
    except KeyboardInterrupt:
        print("\n已取消。")
        return EXIT_FAIL
    except Exception as exc:  # 兜底：给出友好提示而不是堆栈
        print(f"✗ 出错：{exc}")
        if "--debug" in (argv or sys.argv):
            raise
        return EXIT_FAIL

    parser.print_help()
    return EXIT_USAGE


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
