# -*- coding: utf-8 -*-
"""betop-battery —— 读取北通（BETOP）手柄电量的开源工具。

模块划分（高内聚 / 低耦合）：

    protocol.py   帧编解码（纯函数，无 I/O，可单测）
    transport.py  HID 收发（只认识”字节”，不认识任何型号）
    devices.py    设备描述（JSON 驱动，适配新型号只改这里）
    reader.py     编排：把上面三者串成一次读取
    probe.py      调试/发现工具（贡献者用来适配新型号）
    tray.py       托盘界面（只负责展示）
    config.py     用户设置持久化
    cli.py        命令分发

对外主要接口::

    from betop_battery import BatteryReader

    status = BatteryReader().read()
    print(status.battery_percent, status.charging)
"""

from .reader import BatteryReader, BatteryStatus

__version__ = "1.1.0"
__all__ = ["BatteryReader", "BatteryStatus", "__version__"]
