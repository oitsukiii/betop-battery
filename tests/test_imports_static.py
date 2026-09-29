# -*- coding: utf-8 -*-
"""静态检查：界面模块用到的东西必须真的导入了。

背景：曾经把 ``from .proc import ...`` 误插进一个方法内部（而不是模块级），
结果 HUD 一启动就 ``NameError`` 崩溃 —— 单元测试全绿但真机一跑就挂。
这里用 AST 做检查，不依赖 tkinter（CI/无图形环境也能跑）。
"""

import ast
import os

import pytest

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "src", "betop_battery")

#: proc.py 对外提供的工具函数（界面模块常用）
PROC_NAMES = ("spawn", "focus_window", "pid_alive",
              "acquire_single_instance", "release_single_instance")


def _parse(name: str) -> ast.Module:
    with open(os.path.join(SRC, name), encoding="utf-8") as fp:
        return ast.parse(fp.read(), filename=name)


def _module_level_imports(tree: ast.Module) -> set:
    """模块级导入进来的名字（不含函数内部的局部导入）。"""
    names: set = set()
    for node in tree.body:                      # 只看顶层
        if isinstance(node, ast.ImportFrom):
            names |= {a.asname or a.name for a in node.names}
        elif isinstance(node, ast.Import):
            names |= {(a.asname or a.name).split(".")[0] for a in node.names}
    return names


def _used_names(tree: ast.Module) -> set:
    """源码里真正被当作名字用到的标识符（注释/文档字符串不算）。"""
    used = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            used.add(node.id)
    return used


@pytest.mark.parametrize("filename", ["overlay.py", "tray.py", "gui.py"])
def test_proc_helpers_are_imported_at_module_level(filename):
    """用到 proc 的工具函数时，必须在模块级导入。

    放进函数内部也能跑（Python 允许），但模块级另外用到就会 NameError，
    所以这里强制统一放模块级。
    """
    tree = _parse(filename)
    module_imports = _module_level_imports(tree)
    used = _used_names(tree)
    for name in PROC_NAMES:
        if name in used:
            assert name in module_imports, (
                f"{filename} 用到了 {name}，但没有在模块级从 .proc 导入"
                f"（很可能是插进了某个方法内部）")


@pytest.mark.parametrize("filename", ["overlay.py", "tray.py", "gui.py"])
def test_no_undefined_module_level_helpers(filename):
    """更通用一点：所有在模块级导入过的名字都可用；未导入的常见拼写会被抓到。"""
    tree = _parse(filename)
    used = _used_names(tree)
    module_imports = _module_level_imports(tree)
    defined = {n.name for n in ast.walk(tree)
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
    # 只针对 proc 提供的名字做严格检查（其余靠 pyflakes，避免误报）
    for name in PROC_NAMES:
        if name in used and name not in module_imports and name not in defined:
            raise AssertionError(f"{filename}: {name} 未定义")


def test_open_settings_still_spawns_gui():
    """回归：overlay 的「打开设置」必须还能拉起 gui（曾经被补丁改坏过）。"""
    tree = _parse("overlay.py")
    source = ast.unparse(tree) if hasattr(ast, "unparse") else ""
    if source:
        assert 'spawn("gui")' in source.replace("'", '"')
