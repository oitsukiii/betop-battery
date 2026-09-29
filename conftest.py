# -*- coding: utf-8 -*-
"""pytest 配置：让 `pytest` 在**未安装**本包时也能直接运行。

本项目使用 ``src/`` 布局。正式开发可以 ``pip install -e ".[dev]"``，
但为了让贡献者（以及 AI agent）clone 下来就能跑测试，这里手动把 ``src``
加入 ``sys.path``。
"""

import os
import sys

_SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)
