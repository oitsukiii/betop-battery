# -*- coding: utf-8 -*-
"""打包入口：命令行版（有控制台）。

**为什么入口放在包外面、并且用绝对导入**：
PyInstaller 会把入口脚本当作顶层 ``__main__`` 模块执行，此时它**不属于任何包**，
因此 ``from .cli import main`` 这种相对导入会直接报
``attempted relative import with no known parent package``。
入口脚本必须写成 ``from betop_battery.cli import main``（绝对导入），
同时用 ``--paths src`` 让 PyInstaller 找得到这个包。
"""

import sys

from betop_battery.cli import main

if __name__ == "__main__":
    sys.exit(main())
