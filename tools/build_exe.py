# -*- coding: utf-8 -*-
"""用 PyInstaller 打包成 Windows 可执行文件。

产出两个文件：

    dist/betop-battery.exe        托盘版（无控制台窗口）—— 双击即用
    dist/betop-battery-cli.exe    命令行版（保留控制台输出）—— 给脚本/高级用户

为什么要分两个：Windows 上 ``--windowed`` 的程序没有标准输出，
命令行版如果也是 windowed 就看不到任何结果；反之托盘版若是 console，
每次双击都会弹出一个黑窗口。所以分别打包。

关于缺失窗口引导器
------------------
PyInstaller 需要 ``runw.exe``（无控制台引导器）才能做 ``--windowed``。
但在启用了 **智能应用控制（Smart App Control）** 的 Windows 上，
这个未签名的引导器可能被系统静默删除，导致构建直接失败::

    Fatal error: PyInstaller does not include a pre-compiled bootloader

本脚本对此做了兜底：缺少 ``runw.exe`` 时，先用 ``--console`` 构建，
再把 PE 头里的 **Subsystem 字段从 3(CONSOLE) 改成 2(GUI)** ——
Windows 只有在 Subsystem=GUI 时才不分配控制台窗口，
效果与 ``--windowed`` 等价，属于成熟的常规做法。
（``log.py`` 已处理 GUI 子系统下 ``sys.stdout is None`` 的情况。）

用法::

    pip install pyinstaller
    python tools/build_exe.py
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEP = ";" if os.name == "nt" else ":"

# 中文 Windows 上把输出重定向到文件时，默认 GBK 编码遇到 emoji 会直接崩溃。
# 这里统一切到 UTF-8 且永不因编码失败中断（与包内 log.prepare_output 同一策略）。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def _common_args() -> list[str]:
    """两个产物共用的 PyInstaller 参数。"""
    devices = os.path.join(ROOT, "src", "betop_battery", "devices")
    args = [
        "--noconfirm", "--clean",
        "--onefile",
        "--paths", os.path.join(ROOT, "src"),
        "--add-data", f"{devices}{SEP}betop_battery/devices",
        # 托盘后端是按平台动态导入的，显式声明避免漏打
        "--hidden-import", "pystray._win32" if os.name == "nt" else "pystray._xorg",
        # GUI / 叠加层是「子进程再启动自己」的，属于运行时才 import，
        # 静态分析看不到，必须显式声明，否则 exe 里没有 tkinter
        "--hidden-import", "tkinter",
        "--hidden-import", "tkinter.ttk",
        "--hidden-import", "tkinter.colorchooser",
        "--hidden-import", "tkinter.messagebox",
        "--hidden-import", "tkinter.font",
        "--hidden-import", "PIL.ImageTk",
    ]
    # 可选：自定义图标（把 assets/icon.ico 放进仓库即可自动使用）
    icon = os.path.join(ROOT, "assets", "icon.ico")
    if os.path.isfile(icon):
        args += ["--icon", icon]
    return args


def _bootloader_dir() -> str:
    """PyInstaller 自带引导器所在目录。"""
    import PyInstaller

    return os.path.join(os.path.dirname(PyInstaller.__file__), "bootloader",
                        "Windows-64bit-intel" if os.name == "nt" else "Linux-64bit-intel")


def windowed_bootloader_available() -> bool:
    """检查 PyInstaller 是否具备无控制台引导器（runw.exe）。"""
    if os.name != "nt":
        return True  # 非 Windows 平台不涉及此问题
    return os.path.isfile(os.path.join(_bootloader_dir(), "runw.exe"))


def patch_subsystem_to_gui(path: str) -> bool:
    """把 PE 文件的 Subsystem 字段改成 2（Windows GUI），从而不分配控制台。

    PE 结构：``MZ`` 头偏移 0x3C 处是 PE 签名偏移；可选头自 ``pe+24`` 开始，
    其 ``+68`` 处是 2 字节的 Subsystem（2=GUI，3=Console）。

    Args:
        path: 可执行文件路径

    Returns:
        是否修改成功。
    """
    import struct

    try:
        with open(path, "r+b") as fp:
            head = fp.read(0x400)
            if head[:2] != b"MZ":
                return False
            pe = struct.unpack_from("<I", head, 0x3C)[0]
            if head[pe:pe + 4] != b"PE\0\0":
                return False
            offset = pe + 24 + 68
            fp.seek(offset)
            fp.write(struct.pack("<H", 2))
        return True
    except Exception as exc:
        print(f"  ⚠️ 改写 PE 子系统失败：{exc}")
        return False


def build(name: str, entry: str, windowed: bool) -> int:
    """构建单个可执行文件。

    Args:
        name:     产物名（不含扩展名）
        entry:    入口 .py 路径
        windowed: True = 无控制台窗口

    Returns:
        子进程退出码。
    """
    # 缺少窗口引导器时，先用 console 构建，稍后改写 PE 子系统
    fallback = windowed and not windowed_bootloader_available()
    mode = "--console" if (not windowed or fallback) else "--windowed"
    note = "无控制台（PE 子系统改写）" if fallback else ("无控制台" if windowed else "控制台")

    cmd = [sys.executable, "-m", "PyInstaller"]
    cmd += _common_args()
    cmd += ["--name", name, mode, entry]
    print(f"\n=== 构建 {name}（{note}）===")
    if fallback:
        print("    提示：未找到 runw.exe（可能被智能应用控制删除），改用 PE 子系统改写方案")

    code = subprocess.call(cmd, cwd=ROOT)
    if code or not fallback:
        return code

    exe = os.path.join(ROOT, "dist", f"{name}.exe" if os.name == "nt" else name)
    if patch_subsystem_to_gui(exe):
        print(f"    ✅ 已把 {os.path.basename(exe)} 的子系统改为 GUI（双击不再弹控制台）")
        return 0
    return 1


def main() -> int:
    """执行全部打包。"""
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("✗ 未安装 PyInstaller，请先: pip install pyinstaller")
        return 1

    tools = os.path.join(ROOT, "tools")
    results = [
        # 托盘版：双击启动，无黑窗口
        # 入口必须放在包外并使用绝对导入，否则 PyInstaller 会因相对导入失败（见入口文件注释）
        build("betop-battery", os.path.join(tools, "entry_tray.py"), windowed=True),
        # 命令行版：保留输出
        build("betop-battery-cli", os.path.join(tools, "entry_cli.py"), windowed=False),
    ]
    if any(results):
        print("\n✗ 有构建失败，请检查上面的输出。")
        return 1

    dist = os.path.join(ROOT, "dist")
    print("\n✅ 构建完成：")
    for name in sorted(os.listdir(dist)):
        path = os.path.join(dist, name)
        if os.path.isfile(path):
            print(f"   {name}   {os.path.getsize(path) / 1024 / 1024:.1f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
