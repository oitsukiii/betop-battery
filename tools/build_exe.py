#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用 PyInstaller 把本项目打包成单文件 exe。

用法:
    pip install pyinstaller
    python tools/build_exe.py

产物: dist/betop-battery.exe（双击即启动托盘）

注意: 我们把 devices/*.json 一起打进 exe（见 --add-data），
      所以打包后仍然支持数据驱动的设备描述。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEP = ";" if os.name == "nt" else ":"


def main() -> int:
    """执行打包。"""
    devices = os.path.join(ROOT, "src", "betop_battery", "devices")
    if not os.path.isdir(devices):
        print(f"✗ 找不到设备描述目录: {devices}")
        return 1

    if shutil.which("pyinstaller") is None:
        print("✗ 未安装 PyInstaller，请先: pip install pyinstaller")
        return 1

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--onefile",
        "--name", "betop-battery",
        # 托盘程序，双击运行时不要弹控制台窗口
        "--windowed" if os.name == "nt" else "--console",
        "--paths", os.path.join(ROOT, "src"),
        "--add-data", f"{devices}{SEP}betop_battery/devices",
        "--hidden-import", "pystray._win32" if os.name == "nt" else "pystray._xorg",
        os.path.join(ROOT, "src", "betop_battery", "__main__.py"),
    ]
    print("执行:", " ".join(cmd))
    return subprocess.call(cmd, cwd=ROOT)


if __name__ == "__main__":
    sys.exit(main())
