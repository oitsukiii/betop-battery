#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""便捷启动器：**无需安装**即可运行本项目。

用法::

    python run.py once          # 读一次电量
    python run.py tray          # 启动托盘
    python run.py probe dump    # 调试

为什么需要它：本项目使用 ``src/`` 布局，直接 ``python -m betop_battery``
会因为包不在 ``sys.path`` 上而失败。正式使用可以 ``pip install -e .``
（之后用 ``betop-battery`` 命令），但 clone 下来就能跑对贡献者更友好。
"""

import os
import sys

_ROOT = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(_ROOT, "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from betop_battery.cli import main  # noqa: E402  (必须在 sys.path 调整之后导入)

if __name__ == "__main__":
    sys.exit(main())
