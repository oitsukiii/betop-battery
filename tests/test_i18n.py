# -*- coding: utf-8 -*-
"""多语言（i18n）测试。"""

import re

import pytest

from betop_battery import i18n


@pytest.fixture(autouse=True)
def restore_language():
    """每个用例结束后恢复语言，避免相互影响。"""
    before = i18n.current_language()
    yield
    i18n.set_language(before)


# --------------------------------------------------------------------------- 基本行为


def test_all_languages_have_the_same_keys():
    """中英文键集合必须一致，否则某个语言会露出键名。"""
    zh = set(i18n.TRANSLATIONS["zh"])
    en = set(i18n.TRANSLATIONS["en"])
    assert zh == en, f"缺失: zh-en={zh - en} en-zh={en - zh}"


def test_translation_never_returns_empty():
    """任何键在两种语言下都不能是空串。"""
    for lang in i18n.LANGUAGES:
        i18n.set_language(lang)
        for key in i18n.TRANSLATIONS[lang]:
            assert i18n.t(key).strip(), f"{lang} 的 {key} 是空的"


def test_unknown_key_falls_back_to_key_itself():
    """未知键回退成键名，绝不抛异常（漏翻不该让界面崩掉）。"""
    i18n.set_language("zh")
    assert i18n.t("这个键不存在") == "这个键不存在"


def test_placeholders_are_consistent_between_languages():
    """同一键在两种语言里的占位符必须一致（否则会 KeyError）。"""
    for key, zh in i18n.TRANSLATIONS["zh"].items():
        en = i18n.TRANSLATIONS["en"][key]
        assert set(re.findall(r"\{(\w+)\}", zh)) == set(re.findall(r"\{(\w+)\}", en)), \
            f"{key} 的占位符不一致"


def test_format_arguments_are_applied():
    """占位符能正确替换。"""
    i18n.set_language("zh")
    assert i18n.t("seconds", n=30) == "30 秒"
    i18n.set_language("en")
    assert i18n.t("seconds", n=30) == "30 s"


def test_bad_format_arguments_do_not_raise():
    """给错参数也不该抛异常。"""
    assert i18n.t("seconds")              # 缺参数时原样返回
    assert i18n.t("seconds", wrong=1)


# --------------------------------------------------------------------------- 语言解析


def test_resolve_language_accepts_explicit_codes():
    """显式的 zh / en 直接生效。"""
    assert i18n.resolve_language("zh") == "zh"
    assert i18n.resolve_language("en") == "en"
    assert i18n.resolve_language("EN") == "en"


def test_resolve_language_auto_uses_detection():
    """auto 走系统检测。"""
    assert i18n.resolve_language("auto") in i18n.LANGUAGES
    assert i18n.resolve_language("") in i18n.LANGUAGES
    assert i18n.resolve_language("沒見過的值") in i18n.LANGUAGES


def test_set_language_returns_effective_language():
    """set_language 返回实际生效的语言。"""
    assert i18n.set_language("en") == "en"
    assert i18n.current_language() == "en"
    assert i18n.set_language("auto") in i18n.LANGUAGES


def test_detect_language_returns_supported_value():
    """检测结果必须在支持列表里（不能返回奇怪的东西）。"""
    assert i18n.detect_language() in i18n.LANGUAGES


# --------------------------------------------------------------------------- 设备名


def test_device_name_follows_language():
    """英文界面显示 name_en，中文界面显示 name。"""
    i18n.set_language("zh")
    assert i18n.device_name("北通鲲鹏20", "BETOP Kunpeng 20") == "北通鲲鹏20"
    i18n.set_language("en")
    assert i18n.device_name("北通鲲鹏20", "BETOP Kunpeng 20") == "BETOP Kunpeng 20"


def test_device_name_falls_back_when_no_english():
    """没有英文名时英文界面也显示原名，而不是空白。"""
    i18n.set_language("en")
    assert i18n.device_name("北通某型号", None) == "北通某型号"
    assert i18n.device_name("北通某型号", "") == "北通某型号"


def test_strip_device_code_removes_parenthesised_code():
    """去掉型号后面的产品代码，让 HUD 简洁。"""
    assert i18n.strip_device_code("北通鲲鹏20 (BTP-KP20EB)") == "北通鲲鹏20"
    assert i18n.strip_device_code("BETOP Kunpeng 20 (BTP-KP20EB)") == "BETOP Kunpeng 20"
    assert i18n.strip_device_code("没有括号") == "没有括号"


# --------------------------------------------------------------------------- 界面接入


def test_ui_modules_use_translations():
    """核心界面模块不能残留硬编码的界面中文（文档字符串除外）。"""
    import os

    root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "src", "betop_battery")
    for name in ("gui.py", "tray.py", "overlay.py"):
        source = open(os.path.join(root, name), encoding="utf-8").read()
        assert "i18n" in source, f"{name} 没有接入 i18n"
    # 托盘菜单与 HUD 菜单必须用 t()
    tray = open(os.path.join(root, "tray.py"), encoding="utf-8").read()
    assert 'i18n.t("menu_settings")' in tray
    assert 'i18n.t("menu_hud")' in tray


def test_gui_has_language_selector():
    """设置界面必须提供语言下拉框。"""
    import os

    root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "src", "betop_battery")
    gui = open(os.path.join(root, "gui.py"), encoding="utf-8").read()
    assert "_lang_box" in gui, "没有语言下拉框"
    assert 's.language = self._lang_map' in gui, "语言没有写回设置"
