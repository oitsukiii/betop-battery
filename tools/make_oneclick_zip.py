#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""打包「一键安装包」：把源码与安装脚本打成一个 zip，供普通用户下载。

产物：``dist/betop-battery-oneclick-v<版本>.zip``

解压后的结构（用户只需要双击最外层的「安装.bat」）::

    betop-battery/
    ├── 安装.bat          ← 双击这个
    ├── 卸载.bat
    ├── 说明.txt
    ├── packaging/oneclick/{install.ps1,uninstall.ps1}
    ├── run.py  src/  tools/  docs/  ...

**关键细节**：PowerShell 5.1 对 .ps1 的默认编码是 ANSI，
UTF-8 无 BOM 的中文会全部乱码。因此本脚本写入 zip 时会**给 .ps1 补上 BOM**。
"""

from __future__ import annotations

import os
import re
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIST = os.path.join(ROOT, "dist")
ZIP_PREFIX = "betop-battery/"

#: 打进 zip 的内容（相对仓库根目录）
INCLUDE_FILES = [
    "run.py", "conftest.py", "requirements.txt", "pyproject.toml",
    "README.md", "README.en.md", "LICENSE", "CHANGELOG.md", "AGENTS.md",
    "说明.txt",
]
INCLUDE_DIRS = ["src", "tools", "docs", "packaging"]
EXCLUDE_DIRS = {"__pycache__", ".git", ".pytest_cache", ".github", "dist", "build", "assets"}
EXCLUDE_EXT = {".pyc", ".pyo", ".spec"}


def _version() -> str:
    """从包源码里读出当前版本号。"""
    init = os.path.join(ROOT, "src", "betop_battery", "__init__.py")
    with open(init, encoding="utf-8") as fp:
        match = re.search(r'__version__\s*=\s*"([^"]+)"', fp.read())
    return match.group(1) if match else "0.0.0"


def _iter_files():
    """产出 (绝对路径, zip 内相对路径)。"""
    for name in INCLUDE_FILES:
        path = os.path.join(ROOT, name)
        if os.path.isfile(path):
            yield path, name
    for dirname in INCLUDE_DIRS:
        base = os.path.join(ROOT, dirname)
        if not os.path.isdir(base):
            continue
        for current, dirs, files in os.walk(base):
            dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
            for fname in files:
                if os.path.splitext(fname)[1] in EXCLUDE_EXT:
                    continue
                full = os.path.join(current, fname)
                rel = os.path.relpath(full, ROOT).replace(os.sep, "/")
                yield full, rel


def main() -> int:
    """构建一键安装包 zip。"""
    version = _version()
    os.makedirs(DIST, exist_ok=True)
    out = os.path.join(DIST, f"betop-battery-oneclick-v{version}.zip")

    # 安装脚本放在最外层 + packaging 目录里各一份（方便用户直接双击）
    extra_root = [
        (os.path.join(ROOT, "packaging", "oneclick", "安装.bat"), "安装.bat"),
        (os.path.join(ROOT, "packaging", "oneclick", "卸载.bat"), "卸载.bat"),
    ]

    count = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for path, rel in list(_iter_files()) + extra_root:
            if not os.path.isfile(path):
                continue
            if path.endswith(".ps1"):
                # .ps1 必须带 BOM，否则 PowerShell 5.1 下中文乱码
                with open(path, encoding="utf-8") as fp:
                    data = fp.read()
                zf.writestr(ZIP_PREFIX + rel, "\ufeff" + data)
            else:
                zf.write(path, ZIP_PREFIX + rel)
            count += 1

    size = os.path.getsize(out) / 1024
    print(f"✅ 已生成 {out}")
    print(f"   包含 {count} 个文件，压缩后 {size:.0f} KB")
    print("   用户操作：解压 → 双击「安装.bat」")
    return 0


if __name__ == "__main__":
    sys.exit(main())
