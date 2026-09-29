# -*- coding: utf-8 -*-
"""打包入口：托盘版（无控制台窗口）。

双击 exe 时**不应**走默认的 ``once`` 命令（那会输出到不存在的控制台然后退出），
而应直接启动托盘。因此显式以 ``tray`` 参数调用 CLI，
复用同一套参数解析、设置加载与错误处理。

注意：与 ``entry_cli.py`` 一样，必须使用**绝对导入**（原因见该文件注释）。
"""

import sys

from betop_battery.cli import main

if __name__ == "__main__":
    # 无参数 → 托盘；带参数 → 按参数执行（便于托盘进程再拉起 gui / overlay）
    sys.exit(main(sys.argv[1:] or ["tray"]))
