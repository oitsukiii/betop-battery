# -*- coding: utf-8 -*-
"""托盘图标的绘制：支持多种样式与配色方案。

从 ``tray.py`` 拆出来，原因是**绘制**与**托盘交互**是两件不同的事：
前者是纯函数（可单测、可被 GUI 预览复用），后者依赖 pystray 与系统事件循环。

样式（可在图形界面里切换）：
    number   圆角方块 + 大号数字（默认，最清晰）
    ring     环形进度条 + 中间数字
    battery  电池外形 + 填充比例
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Optional, Tuple

from .reader import BatteryStatus

#: 图标画布尺寸（Windows 通知区域会自动缩放）
ICON_SIZE = 64

RGB = Tuple[int, int, int]

# ---------------------------------------------------------------------------
# 配色
# ---------------------------------------------------------------------------

#: 配色方案名 → 说明
COLOR_SCHEMES = {
    "auto": "自动（绿/黄/红，充电蓝）",
    "mono": "单色（浅灰）",
}

# 自动配色
COLOR_OK: RGB = (34, 139, 34)
COLOR_WARN: RGB = (218, 165, 32)
COLOR_LOW: RGB = (200, 40, 40)
COLOR_CHARGING: RGB = (30, 120, 220)
COLOR_UNKNOWN: RGB = (120, 120, 120)
COLOR_MONO: RGB = (70, 70, 80)


@dataclass
class IconStyle:
    """托盘图标外观设置（会持久化到 config.json）。"""

    style: str = "number"          # number | ring | battery
    scheme: str = "auto"           # auto | mono
    show_charging_marker: bool = True
    low_threshold: int = 20        # 低于该值视为低电量（影响配色）

    @classmethod
    def from_dict(cls, data: Optional[dict]) -> "IconStyle":
        """从字典构造（忽略未知键，容忍损坏的配置）。"""
        data = data or {}
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in data.items() if k in known})

    def to_dict(self) -> dict:
        """转成可 JSON 序列化的字典。"""
        return asdict(self)


def pick_color(status: BatteryStatus, low_threshold: int, scheme: str = "auto") -> RGB:
    """根据状态选颜色。

    Args:
        status:        电量状态
        low_threshold: 低电量阈值
        scheme:        配色方案（auto / mono）

    Returns:
        RGB 三元组。
    """
    if scheme == "mono":
        return COLOR_MONO
    if status.error or status.battery_percent is None:
        return COLOR_UNKNOWN
    if status.charging:
        return COLOR_CHARGING
    if status.battery_percent <= low_threshold:
        return COLOR_LOW
    if status.battery_percent <= 50:
        return COLOR_WARN
    return COLOR_OK


# ---------------------------------------------------------------------------
# 字体
# ---------------------------------------------------------------------------

def _load_font(size: int):
    """挑一个可用的粗体字体（失败返回默认字体）。"""
    from PIL import ImageFont

    for name in ("arialbd.ttf", "segoeuib.ttf", "msyhbd.ttc", "DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            continue
    try:
        return ImageFont.load_default()
    except Exception:
        return None


def _draw_centered(draw, text: str, font, box, fill) -> None:
    """在给定矩形内居中绘制文本。"""
    if font is None:
        return
    try:
        b = draw.textbbox((0, 0), text, font=font)
        w, h = b[2] - b[0], b[3] - b[1]
        draw.text((box[0] + (box[2] - box[0] - w) / 2 - b[0],
                   box[1] + (box[3] - box[1] - h) / 2 - b[1]),
                  text, font=font, fill=fill)
    except Exception:
        draw.text((box[0] + 4, box[1] + 4), text, fill=fill)


def _draw_centered_stroked(draw, text: str, font, box, fill, stroke=None,
                           stroke_width: int = 2) -> None:
    """居中绘制文字，可选**描边**。

    为什么需要：16px 的通知区域里，纯色文字的笔画只有 1~2 像素，
    叠在同色系背景上几乎看不清。加一圈细描边能显著提升辨识度。
    """
    if font is None:
        return
    try:
        b = draw.textbbox((0, 0), text, font=font)
        w, h = b[2] - b[0], b[3] - b[1]
        x = box[0] + (box[2] - box[0] - w) / 2 - b[0]
        y = box[1] + (box[3] - box[1] - h) / 2 - b[1]
    except Exception:
        x, y = box[0] + 4, box[1] + 4

    if stroke is not None and stroke_width > 0:
        for dx in range(-stroke_width, stroke_width + 1):
            for dy in range(-stroke_width, stroke_width + 1):
                if dx or dy:
                    draw.text((x + dx, y + dy), text, font=font, fill=stroke)
    draw.text((x, y), text, font=font, fill=fill)


def _label(status: BatteryStatus) -> str:
    """图标上显示的文字。"""
    return "--" if status.battery_percent is None else str(status.battery_percent)


# ---------------------------------------------------------------------------
# 各样式
# ---------------------------------------------------------------------------

def _render_number(draw, status, color: RGB, size: int) -> None:
    """圆角方块 + 大号数字。"""
    draw.rounded_rectangle([2, 2, size - 2, size - 2], radius=int(size * 0.19), fill=color)
    font = _load_font(int(size * 0.53))
    _draw_centered(draw, _label(status), font, (0, 0, size, size), (255, 255, 255, 255))


def _render_ring(draw, status, color: RGB, size: int) -> None:
    """环形进度 + 中间数字。

    在 16px 的通知区域里，**细环和小数字都会糊成一团**（实测）：
    因此环尽量贴边并加粗，数字也尽量放大，让"形状"和"数值"都还认得出来。
    """
    width = max(3, int(size * 0.20))          # 环的粗细：约为画布的 1/5
    inset = max(1, int(size * 0.02))          # 环的**外沿**只留一点点边距
    # 注意：Pillow 的 ellipse(outline, width) 是**向内**画的，
    # 所以外接框就是 box 本身 —— 之前把 inset 又加了 width//2，
    # 导致环只占画布 78%（实测），在 16px 下更糊。
    box = [inset, inset, size - 1 - inset, size - 1 - inset]
    # 底环（浅色轨道，表示"总量"）
    draw.ellipse(box, outline=(206, 208, 216, 255), width=width)
    percent = status.battery_percent
    if percent is not None and not status.charging:
        # Pillow 的 arc 用角度制，0° 在 3 点方向，顺时针；这里从 12 点开始
        end = -90 + int(360 * max(0, min(100, percent)) / 100)
        if end > -90:
            draw.arc(box, start=-90, end=end, fill=color, width=width)
    else:
        draw.ellipse(box, outline=color, width=width)
    # 环内铺一层浅色内盘：否则放大后的数字会压在环上（绿字压绿环 = 看不见）
    # 内盘半径 = 环外半径 − 环宽（留 1px 余量），精确贴合环的内沿
    center = size / 2.0
    r_outer = (size - 1 - 2 * inset) / 2.0
    r_plate = max(2.0, r_outer - width + 1)
    draw.ellipse([center - r_plate, center - r_plate,
                  center + r_plate, center + r_plate], fill=(248, 249, 252, 255))
    # 数字放大到画布的 ~56%，尽量占满内盘
    font = _load_font(int(size * 0.56))
    _draw_centered(draw, _label(status), font, (0, 0, size, size), color)


def _render_battery(draw, status, color: RGB, size: int) -> None:
    """电池外形 + 内部填充 + 数字。

    同样为了小尺寸可读性：电池**占满画布**、轮廓加粗、数字放大到约 40%。
    """
    outline_w = max(3, int(size * 0.10))
    left, top = int(size * 0.02), int(size * 0.13)
    right, bottom = int(size * 0.86), int(size * 0.87)
    cap_w, cap_h = int(size * 0.09), int(size * 0.30)
    # 外壳
    draw.rounded_rectangle([left, top, right, bottom], radius=int(size * 0.11),
                           outline=color, width=outline_w)
    # 正极帽
    draw.rounded_rectangle([right + 2, (top + bottom) // 2 - cap_h // 2,
                            right + 2 + cap_w, (top + bottom) // 2 + cap_h // 2],
                           radius=max(1, int(size * 0.02)), fill=color)
    percent = status.battery_percent
    if percent is not None:
        inner_l = left + outline_w + 2
        inner_r = left + outline_w + 2 + int(
            (right - left - 2 * outline_w - 4) * max(0, min(100, percent)) / 100)
        if inner_r > inner_l:
            draw.rounded_rectangle([inner_l, top + outline_w + 2, inner_r, bottom - outline_w - 2],
                                   radius=int(size * 0.05), fill=color)
    # 数字：填充过半时用白色（描绿色边），否则用深色（描白边），保证任何电量下都清晰
    filled = (percent or 0) >= 50
    font = _load_font(int(size * 0.42))
    if filled:
        _draw_centered_stroked(draw, _label(status), font, (left, top, right, bottom),
                               fill=(255, 255, 255, 255), stroke=color,
                               stroke_width=max(1, int(size * 0.02)))
    else:
        _draw_centered_stroked(draw, _label(status), font, (left, top, right, bottom),
                               fill=(45, 45, 55, 255), stroke=(255, 255, 255, 255),
                               stroke_width=max(1, int(size * 0.02)))


_RENDERERS = {
    "number": _render_number,
    "ring": _render_ring,
    "battery": _render_battery,
}


# ---------------------------------------------------------------------------
# 对外接口
# ---------------------------------------------------------------------------

def render_icon(status: BatteryStatus, style: Optional[IconStyle] = None,
                size: int = ICON_SIZE):
    """把状态渲染成 PIL 图片。

    Args:
        status: 电量状态
        style:  外观设置（None 用默认）
        size:   画布边长（像素）

    Returns:
        PIL.Image（RGBA）。失败时返回一个纯色占位图，绝不抛异常 ——
        托盘图标画不出来不该让整个程序崩掉。
    """
    style = style or IconStyle()
    try:
        from PIL import Image, ImageDraw
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("绘制图标需要 Pillow：pip install Pillow") from exc

    try:
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        color = pick_color(status, style.low_threshold, style.scheme)
        renderer = _RENDERERS.get(style.style, _render_number)
        renderer(draw, status, color, size)

        # 充电标记：底部一条白色横条（样式不同位置略有差异）
        if style.show_charging_marker and status.charging:
            draw.rectangle([int(size * 0.28), size - int(size * 0.14),
                            int(size * 0.72), size - int(size * 0.08)],
                           fill=(255, 255, 255, 235))
        return img
    except Exception:
        # 兜底：纯灰底图，保证托盘仍能显示
        img = Image.new("RGBA", (size, size), (128, 128, 128, 255))
        return img
