#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成应用图标 `assets/icon.ico`。

用途：
  * Windows 桌面快捷方式的图标
  * PyInstaller 打包时的 exe 图标（`tools/build_exe.py` 会自动使用）

只用 Pillow 绘制，不依赖美术资源，改颜色/形状直接改这里即可。
"""

from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, "assets")
OUT_FILE = os.path.join(OUT_DIR, "icon.ico")

#: 画布尺寸（会输出多分辨率 ICO）
SIZE = 256

BG_TOP = (38, 44, 66)        # 背景渐变起色
BG_BOTTOM = (18, 20, 32)     # 背景渐变终色
PAD_COLOR = (232, 236, 245)  # 手柄主色
STICK_COLOR = (60, 70, 100)  # 摇杆颜色
ACCENT = (90, 200, 120)      # 电量点缀色（绿）


def draw_icon():
    """绘制图标并返回 PIL.Image。"""
    from PIL import Image, ImageDraw

    # 竖直渐变背景（逐行画线，避免依赖 numpy）
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    grad = Image.new("RGBA", (SIZE, SIZE))
    gd = ImageDraw.Draw(grad)
    for y in range(SIZE):
        t = y / (SIZE - 1)
        gd.line(
            [(0, y), (SIZE, y)],
            fill=(
                int(BG_TOP[0] + (BG_BOTTOM[0] - BG_TOP[0]) * t),
                int(BG_TOP[1] + (BG_BOTTOM[1] - BG_TOP[1]) * t),
                int(BG_TOP[2] + (BG_BOTTOM[2] - BG_TOP[2]) * t),
                255,
            ),
        )

    # 圆角遮罩
    mask = Image.new("L", (SIZE, SIZE), 0)
    ImageDraw.Draw(mask).rounded_rectangle([8, 8, SIZE - 8, SIZE - 8], radius=52, fill=255)
    img.paste(grad, (0, 0), mask)

    draw = ImageDraw.Draw(img)

    # ---- 手柄主体：一个圆角横条 + 两侧握把 ----
    body_top, body_bottom = int(SIZE * 0.36), int(SIZE * 0.64)
    draw.rounded_rectangle([int(SIZE * 0.20), body_top, int(SIZE * 0.80), body_bottom],
                           radius=int(SIZE * 0.14), fill=PAD_COLOR)
    # 左右握把（下方两个圆角块）
    grip = int(SIZE * 0.115)
    draw.rounded_rectangle([int(SIZE * 0.155), int(SIZE * 0.50),
                            int(SIZE * 0.155) + grip, int(SIZE * 0.72)],
                           radius=int(grip * 0.45), fill=PAD_COLOR)
    draw.rounded_rectangle([int(SIZE * 0.845) - grip, int(SIZE * 0.50),
                            int(SIZE * 0.845), int(SIZE * 0.72)],
                           radius=int(grip * 0.45), fill=PAD_COLOR)

    # ---- 左摇杆 + 右按键区 ----
    r = int(SIZE * 0.070)
    cx, cy = int(SIZE * 0.335), int(SIZE * 0.485)
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=STICK_COLOR)
    for dx, dy in ((0.60, 0.395), (0.66, 0.485), (0.60, 0.575), (0.54, 0.485)):
        bx, by = int(SIZE * dx), int(SIZE * dy)
        br = int(SIZE * 0.030)
        draw.ellipse([bx - br, by - br, bx + br, by + br], fill=STICK_COLOR)

    # ---- 十字方向键 ----
    d_w, d_h = int(SIZE * 0.030), int(SIZE * 0.085)
    dx, dy = int(SIZE * 0.265), int(SIZE * 0.575)
    draw.rectangle([dx - d_w // 2, dy - d_h // 2, dx + d_w // 2, dy + d_h // 2],
                   fill=STICK_COLOR)
    draw.rectangle([dx - d_h // 2, dy - d_w // 2, dx + d_h // 2, dy + d_w // 2],
                   fill=STICK_COLOR)

    # ---- 电量点缀：右上角小电池 ----
    bx0, by0 = int(SIZE * 0.66), int(SIZE * 0.20)
    bw, bh = int(SIZE * 0.20), int(SIZE * 0.105)
    draw.rounded_rectangle([bx0, by0, bx0 + bw, by0 + bh], radius=6,
                           outline=ACCENT, width=max(3, int(SIZE * 0.018)))
    draw.rounded_rectangle([bx0 + 6, by0 + 6, bx0 + int(bw * 0.75), by0 + bh - 6],
                           radius=4, fill=ACCENT)
    draw.rectangle([bx0 + bw + 3, by0 + bh // 3, bx0 + bw + 10, by0 + bh * 2 // 3],
                   fill=ACCENT)
    return img


def main() -> int:
    """生成 ICO 文件。"""
    try:
        from PIL import Image  # noqa: F401
    except ImportError:
        print("✗ 需要 Pillow：pip install Pillow")
        return 1

    os.makedirs(OUT_DIR, exist_ok=True)
    img = draw_icon()
    sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    img.save(OUT_FILE, format="ICO", sizes=sizes)
    # 同时输出一张 PNG 便于在 README 里展示
    img.save(os.path.join(OUT_DIR, "icon.png"), format="PNG")
    print(f"✅ 已生成 {OUT_FILE}")
    print(f"✅ 已生成 {os.path.join(OUT_DIR, 'icon.png')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
