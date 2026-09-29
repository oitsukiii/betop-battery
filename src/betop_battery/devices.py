# -*- coding: utf-8 -*-
"""设备描述层：用 **JSON 描述文件** 表达”某型号怎么读电量”。

设计原则
--------
这是本项目最容易被人扩展的一层。**适配新型号 = 新增/修改一个 JSON 文件**，
不需要改动任何 Python 代码。这样贡献门槛极低，也让 AI agent 能自动生成描述。

一个描述文件长这样（简化）::

    {
      "id": "betop-kp20",
      "name": "北通鲲鹏20",
      "match":  { "vendor_id": "0x20BC", "product_id": "0x5191", "usage_page": "0xFF00" },
      "query":  { "report_id": "0x02", "header": "0x15" },
      "reply_header": "0x15",
      "fields": {
        "battery_percent": { "offset": 2, "type": "uint8" },
        "charging":        { "offset": 4, "type": "uint8", "mask": "0x0F", "boolean": true }
      }
    }

所有 ``offset`` 都是**整帧偏移**（0 = report_id），与厂商脚本里的
``data.substr(n*2, 2)`` 一一对应，便于对照。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from . import i18n
from .protocol import Frame
from .transport import HidInterface

#: 描述文件所在目录（打包后同样从包内读取）
DEVICES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "devices")


class DescriptorError(ValueError):
    """描述文件格式错误。"""


# ---------------------------------------------------------------------------
# 解析辅助：JSON 里允许写 "0xFF00" 这种十六进制字符串
# ---------------------------------------------------------------------------

def _as_int(value: Any, *, default: Optional[int] = None) -> Optional[int]:
    """把 JSON 里的数或字符串（支持 0x 前缀）转成 int。"""
    if value is None:
        return default
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        text = value.strip()
        try:
            return int(text, 0)  # 支持 0x / 0b / 十进制
        except ValueError as exc:
            raise DescriptorError(f"无法解析整数: {value!r}") from exc
    raise DescriptorError(f"无法解析整数: {value!r}")


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

def swap16(value: int) -> int:
    """交换 16 位值的两个字节（0xFF00 ↔ 0x00FF）。

    为什么需要它：HID 规范里厂商自定义用途页是 ``0xFF00 ~ 0xFFFF``，
    但不同 hidapi 后端对 ``usage_page`` 的字节序处理不一致 ——
    实测 Windows 后端把 ``0xFF00`` 报成 ``0x00FF``。
    为了让人按规范写描述文件也能跨平台工作，匹配时两种形式都接受。
    """
    return ((value & 0x00FF) << 8) | ((value & 0xFF00) >> 8)


def is_vendor_usage_page(value: int) -> bool:
    """判断用途页是否属于厂商自定义区间（兼容字节序差异）。"""
    return value >= 0xFF00 or swap16(value) >= 0xFF00


@dataclass(frozen=True)
class MatchSpec:
    """怎么在系统里认出这个型号。"""

    vendor_id: int
    product_id: int
    usage_page: Optional[int] = None
    usage: Optional[int] = None
    interface_number: Optional[int] = None
    product_string_contains: Optional[str] = None

    def matches(self, iface: HidInterface) -> bool:
        """判断某个 HID 接口是否属于本型号。"""
        if iface.vendor_id != self.vendor_id or iface.product_id != self.product_id:
            return False
        if self.usage_page is not None:
            # 兼容 hidapi 后端把 usage_page 字节序颠倒的情况
            if iface.usage_page not in (self.usage_page, swap16(self.usage_page)):
                return False
        if self.usage is not None and iface.usage != self.usage:
            return False
        if self.interface_number is not None and iface.interface_number != self.interface_number:
            return False
        if self.product_string_contains:
            needle = self.product_string_contains.lower()
            if needle not in (iface.product_string or "").lower():
                return False
        return True

    @property
    def score(self) -> int:
        """匹配精细度：描述得越具体分越高，用于在多个候选中挑最合适的。"""
        return sum(
            1
            for v in (
                self.usage_page,
                self.usage,
                self.interface_number,
                self.product_string_contains,
            )
            if v is not None
        )


@dataclass(frozen=True)
class QuerySpec:
    """怎么发起查询。"""

    report_id: int
    header: int
    write_lengths: tuple[int, ...] = (64, 32, 16)

    @property
    def payload(self) -> bytes:
        """查询帧的前两字节。"""
        return bytes([self.report_id & 0xFF, self.header & 0xFF])


@dataclass(frozen=True)
class FieldSpec:
    """响应帧里的一个字段怎么取。"""

    offset: int
    type: str = "uint8"          # uint8 | uint16le
    mask: Optional[int] = None
    boolean: bool = False        # True 时把结果转成 bool
    scale: float = 1.0           # 线性缩放（例如 0~255 映射到 0~100）

    def extract(self, frame: Frame) -> Any:
        """从帧里取出本字段的值。

        Args:
            frame: 已解析的响应帧

        Returns:
            取到的值；越界返回 None。
        """
        if self.type == "uint16le":
            raw = frame.u16_at(self.offset)
        else:
            raw = frame.byte_at(self.offset)
        if raw is None:
            return None
        if self.mask is not None:
            raw = raw & self.mask
        if self.boolean:
            return bool(raw)
        if self.scale != 1.0:
            return raw * self.scale
        return raw


@dataclass(frozen=True)
class DeviceDescriptor:
    """一个型号的完整读取方案。"""

    id: str
    name: str
    match: MatchSpec
    query: QuerySpec
    reply_header: int
    fields: dict[str, FieldSpec] = field(default_factory=dict)
    notes: str = ""
    tested: dict[str, Any] = field(default_factory=dict)
    source: str = ""             # 来源文件名，便于报错定位
    name_en: str = ""            # 英文界面下显示的型号名（可选）

    @property
    def display_name(self) -> str:
        """按当前界面语言返回型号名。"""
        return i18n.device_name(self.name, self.name_en or None)

    # 便捷访问器 -----------------------------------------------------------
    @property
    def battery_field(self) -> Optional[FieldSpec]:
        """电量字段（约定键名 ``battery_percent``）。"""
        return self.fields.get("battery_percent")

    @property
    def charging_field(self) -> Optional[FieldSpec]:
        """充电状态字段（约定键名 ``charging``）。"""
        return self.fields.get("charging")

    def extract(self, frame: Frame) -> dict[str, Any]:
        """按定义提取所有字段。

        Args:
            frame: 响应帧

        Returns:
            字段名 → 值 的字典。
        """
        return {name: spec.extract(frame) for name, spec in self.fields.items()}


# ---------------------------------------------------------------------------
# 加载
# ---------------------------------------------------------------------------

def parse_descriptor(data: dict[str, Any], source: str = "<memory>") -> DeviceDescriptor:
    """把 JSON 字典转成 :class:`DeviceDescriptor`。

    Args:
        data:   已解析的 JSON
        source: 来源标识（文件名），仅用于报错信息

    Returns:
        DeviceDescriptor

    Raises:
        DescriptorError: 缺少必填字段或格式错误。
    """
    try:
        match_raw = data["match"]
        query_raw = data["query"]
    except KeyError as exc:
        raise DescriptorError(f"{source}: 缺少必填字段 {exc}") from exc

    match = MatchSpec(
        vendor_id=_as_int(match_raw.get("vendor_id")) or 0,
        product_id=_as_int(match_raw.get("product_id")) or 0,
        usage_page=_as_int(match_raw.get("usage_page")),
        usage=_as_int(match_raw.get("usage")),
        interface_number=_as_int(match_raw.get("interface_number")),
        product_string_contains=match_raw.get("product_string_contains"),
    )
    if not match.vendor_id:
        raise DescriptorError(f"{source}: match.vendor_id 必填")

    query = QuerySpec(
        report_id=_as_int(query_raw.get("report_id")) or 0,
        header=_as_int(query_raw.get("header")) or 0,
        write_lengths=tuple(
            _as_int(x) for x in (query_raw.get("write_lengths") or [64, 32, 16])
        ),
    )

    fields: dict[str, FieldSpec] = {}
    for name, spec_raw in (data.get("fields") or {}).items():
        fields[name] = FieldSpec(
            offset=_as_int(spec_raw.get("offset")) or 0,
            type=str(spec_raw.get("type") or "uint8"),
            mask=_as_int(spec_raw.get("mask")),
            boolean=bool(spec_raw.get("boolean", False)),
            scale=float(spec_raw.get("scale", 1.0)),
        )

    reply_header = _as_int(data.get("reply_header"), default=query.header) or 0
    return DeviceDescriptor(
        id=str(data.get("id") or os.path.splitext(os.path.basename(source))[0]),
        name=str(data.get("name") or data.get("id") or "未知设备"),
        match=match,
        query=query,
        reply_header=reply_header,
        fields=fields,
        notes=str(data.get("notes") or ""),
        tested=dict(data.get("tested") or {}),
        source=source,
        name_en=str(data.get("name_en") or ""),
    )


def load_descriptors(directory: str = DEVICES_DIR) -> list[DeviceDescriptor]:
    """加载目录下所有 ``*.json`` 设备描述（跳过 ``_`` 开头的模板）。

    Args:
        directory: 描述文件目录

    Returns:
        按文件名排序的描述列表。单个文件解析失败只跳过并提示，不中断整体。
    """
    result: list[DeviceDescriptor] = []
    if not os.path.isdir(directory):
        return result
    for name in sorted(os.listdir(directory)):
        if not name.endswith(".json") or name.startswith("_"):
            continue
        path = os.path.join(directory, name)
        try:
            with open(path, encoding="utf-8") as fp:
                data = json.load(fp)
            result.append(parse_descriptor(data, source=path))
        except Exception as exc:  # 坏文件不该拖垮整个程序
            print(f"⚠️  跳过无法解析的描述文件 {name}: {exc}")
    return result


# ---------------------------------------------------------------------------
# 匹配
# ---------------------------------------------------------------------------

def find_device(
    interfaces: Iterable[HidInterface],
    descriptors: Iterable[DeviceDescriptor],
) -> tuple[Optional[DeviceDescriptor], Optional[HidInterface]]:
    """在已枚举的接口里找出”描述得最具体且匹配”的型号 + 接口。

    Args:
        interfaces:  枚举到的 HID 接口
        descriptors: 已加载的设备描述

    Returns:
        ``(descriptor, interface)``；没匹配到则为 ``(None, None)``。
    """
    best: tuple[Optional[DeviceDescriptor], Optional[HidInterface], int] = (None, None, -1)
    iface_list = list(interfaces)
    for desc in descriptors:
        for iface in iface_list:
            if not desc.match.matches(iface):
                continue
            score = desc.match.score
            if score > best[2]:
                best = (desc, iface, score)
    return best[0], best[1]
