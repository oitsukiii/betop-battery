# -*- coding: utf-8 -*-
"""打包辅助逻辑的测试（不需要 Windows，也不需要 PyInstaller）。

重点覆盖 ``patch_subsystem_to_gui``：它是在**智能应用控制删掉了
PyInstaller 的窗口引导器 runw.exe** 之后的兜底方案 ——
把 PE 头的 Subsystem 从 3(Console) 改成 2(GUI)，双击才不弹黑窗口。
这段逻辑一旦写错，产物会静默变成"有控制台"，所以必须测。
"""

import importlib.util
import os
import struct

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_build_module():
    """按路径加载 tools/build_exe.py（tools 不是包，不能用 import）。"""
    path = os.path.join(ROOT, "tools", "build_exe.py")
    spec = importlib.util.spec_from_file_location("_betop_build_exe", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def build_module():
    return _load_build_module()


def _make_fake_pe(subsystem: int = 3) -> bytes:
    """造一个只含必要头部的最小 PE 文件内容。"""
    data = bytearray(0x400)
    data[0:2] = b"MZ"
    pe_offset = 0x80
    struct.pack_into("<I", data, 0x3C, pe_offset)      # e_lfanew
    data[pe_offset:pe_offset + 4] = b"PE\0\0"
    struct.pack_into("<H", data, pe_offset + 24 + 68, subsystem)
    return bytes(data)


def _read_subsystem(path: str) -> int:
    with open(path, "rb") as fp:
        data = fp.read()
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    assert data[pe:pe + 4] == b"PE\0\0"
    return struct.unpack_from("<H", data, pe + 24 + 68)[0]


def test_patch_subsystem_to_gui_converts_console_to_gui(build_module, tmp_path):
    """核心行为：3(Console) → 2(GUI)。"""
    exe = tmp_path / "fake.exe"
    exe.write_bytes(_make_fake_pe(3))
    assert _read_subsystem(str(exe)) == 3

    assert build_module.patch_subsystem_to_gui(str(exe)) is True
    assert _read_subsystem(str(exe)) == 2, "子系统应被改成 GUI(2)，否则双击仍会弹控制台"


def test_patch_subsystem_is_idempotent(build_module, tmp_path):
    """重复调用不应出错（幂等）。"""
    exe = tmp_path / "fake.exe"
    exe.write_bytes(_make_fake_pe(3))
    assert build_module.patch_subsystem_to_gui(str(exe))
    assert build_module.patch_subsystem_to_gui(str(exe))
    assert _read_subsystem(str(exe)) == 2


def test_patch_subsystem_rejects_non_pe(build_module, tmp_path):
    """非 PE 文件应返回 False，而不是把文件写坏。"""
    junk = tmp_path / "not_pe.bin"
    original = b"this is definitely not a PE file" * 10
    junk.write_bytes(original)
    assert build_module.patch_subsystem_to_gui(str(junk)) is False
    assert junk.read_bytes() == original, "失败时不能改动原文件"


def test_patch_subsystem_handles_missing_file(build_module, tmp_path):
    """文件不存在时返回 False（不抛异常）。"""
    assert build_module.patch_subsystem_to_gui(str(tmp_path / "nope.exe")) is False


def test_build_script_declares_tkinter_hidden_imports(build_module):
    """GUI/叠加层是运行时才 import 的，必须显式声明隐藏导入。

    否则打包出来的 exe 里没有 tkinter，托盘菜单点"设置"会静默失败。
    """
    args = build_module._common_args()
    joined = " ".join(args)
    for name in ("tkinter", "tkinter.ttk", "PIL.ImageTk"):
        assert name in joined, f"缺少 --hidden-import {name}"


def test_build_script_packages_device_descriptors(build_module):
    """设备描述 JSON 必须打进 exe，否则打包后一个型号都不支持。"""
    args = build_module._common_args()
    assert any("betop_battery/devices" in a for a in args), "缺少 --add-data 设备描述"


def test_build_script_targets_two_executables(build_module):
    """必须产出两个入口：无控制台的托盘版 + 带控制台的命令行版。"""
    tools = os.path.join(ROOT, "tools")
    for name in ("entry_tray.py", "entry_cli.py"):
        assert os.path.isfile(os.path.join(tools, name)), f"缺少入口 {name}"


def test_version_is_consistent_across_files():
    """版本号必须在 __init__.py 与 pyproject.toml 里保持一致。

    真机踩过的坑：早期的版本升级脚本用字符串替换且没有断言，
    替换失败时**静默无效果**，结果 git 标签已经是 v1.0.4、代码里却还是 1.0.2，
    打出来的安装包文件名也跟着错。
    """
    import re

    init = open(os.path.join(ROOT, "src", "betop_battery", "__init__.py"),
                encoding="utf-8").read()
    pyproject = open(os.path.join(ROOT, "pyproject.toml"), encoding="utf-8").read()

    v1 = re.search(r'__version__\s*=\s*"([^"]+)"', init).group(1)
    v2 = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.M).group(1)
    assert v1 == v2, f"版本号不一致：__init__.py={v1} pyproject.toml={v2}"


def test_tray_menu_uses_hud_naming():
    """托盘菜单项必须叫 HUD（现在走 i18n，中文也不该出现「叠加层」）。"""
    src = open(os.path.join(ROOT, "src", "betop_battery", "tray.py"),
               encoding="utf-8").read()
    assert "叠加层" not in src, "托盘菜单仍有「叠加层」字样"
    assert 'i18n.t("menu_hud")' in src, "托盘菜单缺少 HUD 项"
    # 两种语言的文案本身也要对
    from betop_battery import i18n

    i18n.set_language("zh")
    assert i18n.t("menu_hud") == "HUD"
    i18n.set_language("en")
    assert i18n.t("menu_hud") == "HUD"


def test_tray_default_action_opens_settings():
    """左键单击/双击托盘图标应打开设置窗口（而不是只刷新）。"""
    src = open(os.path.join(ROOT, "src", "betop_battery", "tray.py"),
               encoding="utf-8").read()
    assert 'i18n.t("menu_settings"), open_settings, default=True' in src, \
        "默认菜单项应设为「设置…」"
