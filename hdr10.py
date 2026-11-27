"""Read and write mastering and content-light SEI in Annex B HDR10 streams."""

import re
import struct
from pathlib import Path

from lightlevels import measure_content_light


def _messages(unit):
    data = unit[2:].rstrip(b"\x00").replace(b"\x00\x00\x03", b"\x00\x00")
    pos = 0
    while pos < len(data) - 1:
        fields = []
        for _ in range(2):
            value = 0
            while pos < len(data) and data[pos] == 255:
                value += 255
                pos += 1
            if pos == len(data):
                raise ValueError("静态 HDR10 的 SEI 报文不完整。")
            fields.append(value + data[pos])
            pos += 1
        code, size = fields
        end = pos + size
        if end >= len(data):
            raise ValueError("静态 HDR10 的 SEI 长度超出数据范围。")
        yield code, data[pos:end]
        pos = end
    if data[pos:] != b"\x80":
        raise ValueError("HEVC SEI 结束位无效。")


def _make_sei(header, messages):
    rbsp = bytearray()
    for code, payload in messages:
        for n in (code, len(payload)):
            rbsp.extend(b"\xff" * (n // 255))
            rbsp.append(n % 255)
        rbsp.extend(payload)
    rbsp.append(0x80)
    unit = bytearray(header)
    zeros = 0
    for b in rbsp:
        if zeros == 2 and b <= 3:
            unit.append(3)
            zeros = 0
        unit.append(b)
        zeros = zeros + 1 if b == 0 else 0
    return bytes(unit)


def _units(reader, cancel, on_bytes=None):
    """Yield exact Annex B delimiters and NAL bytes without buffering the file."""
    boundary = re.compile(b"\x00\x00\x00?\x01")
    pending = bytearray()
    start = None
    search = 0
    while chunk := reader.read(1024 * 1024):
        if cancel is not None and cancel.is_set():
            raise InterruptedError("任务已取消。")
        pending.extend(chunk)
        end = 0
        for mark in boundary.finditer(pending, search):
            if start is None:
                if any(pending[:mark.start()]):
                    raise ValueError("无效的 Annex B 起始码。")
                start = bytes(pending[:mark.end()])
            else:
                yield start, bytes(pending[end:mark.start()])
                start = mark.group()
            end = mark.end()
        if end:
            del pending[:end]
        if len(pending) > 128 * 1024 * 1024:
            raise ValueError("HEVC 数据缺少有效的 NAL 边界。")
        search = max(0, len(pending) - 3)
        if on_bytes is not None:
            on_bytes(reader.tell())
    if start is not None:
        yield start, bytes(pending)


def write_content_light(source, destination, levels, cancel=None, *, on_bytes=None):
    """Replace base-layer CLL SEIs and repeat measured values at random access points."""
    if (not isinstance(levels, (list, tuple)) or len(levels) != 2
            or any(type(v) is not int for v in levels) or not 0 <= levels[1] <= levels[0] <= 10000):
        raise ValueError("实测 HDR10 内容亮度超出可表达范围。")
    cll = [(144, struct.pack(">HH", *levels))]
    dest = Path(destination)
    created = False
    wrote = False
    try:
        with Path(source).open("rb") as reader, dest.open("xb") as writer:
            created = True
            for prefix, unit in _units(reader, cancel, on_bytes):
                if len(unit) < 2 or unit[0] & 0x80 or not unit[1] & 7:
                    raise ValueError("HEVC NAL 头无效。")
                kind = (unit[0] >> 1) & 63
                base = not (unit[0] & 1 or unit[1] >> 3)
                if base and kind in (39, 40):
                    messages = list(_messages(unit))
                    keep = [m for m in messages if m[0] != 144]
                    if len(keep) != len(messages):
                        if not keep:
                            continue
                        unit = _make_sei(unit[:2], keep)
                if base and kind < 32 and len(unit) > 2 and unit[2] & 0x80 and (not wrote or 16 <= kind <= 23):
                    writer.write(b"\x00\x00\x00\x01" + _make_sei(bytes([78, unit[1]]), cll))
                    wrote = True
                writer.write(prefix)
                writer.write(unit)
            if not wrote:
                raise ValueError("缺少可写入内容亮度的视频画面。")
    except BaseException:
        if created:
            dest.unlink(missing_ok=True)
        raise


class NativeMetadata:
    """Collect constant mastering and light levels while hashing the source HEVC."""

    def __init__(self):
        self.static = {}

    def observe(self, kind, unit):
        if kind in (62, 63) or (unit[0] & 1) or (unit[1] >> 3):
            raise ValueError("原片含杜比元数据或额外编码层，不能作为单层 HDR10 制作。")
        if kind not in (39, 40):
            return
        for code, payload in _messages(unit):
            if code == 4 and payload.startswith((b"\xb5\x00\x3c\x00\x01\x04", b"\x26\x00\x04\x00\x05")):
                raise ValueError("原片含 HDR10+ 或 HDR Vivid 动态元数据，不能作为静态 HDR10 制作。")
            if code in (137, 144):
                if len(payload) != (24 if code == 137 else 4) or self.static.setdefault(code, payload) != payload:
                    raise ValueError("原片的 HDR10 母版或内容亮度信息不完整，或在片内发生变化。")

    def level6(self):
        if 137 not in self.static:
            raise ValueError("原始 HEVC 缺少 ST 2086 母版显示亮度，无法沿用母版信息。")
        maximum, minimum = struct.unpack(">II", self.static[137][-8:])
        if not 0 <= minimum < maximum <= 100_000_000 or minimum > 65535:
            raise ValueError("母版显示亮度超出可表达范围。")
        peak, fall = struct.unpack(">HH", self.static.get(144, bytes(4)))
        max_nits = (maximum + 5000) // 10000
        if not max_nits:
            raise ValueError("母版峰值无法表示为整数尼特。")
        return dict(mastering_max=max_nits, mastering_min=minimum, max_cll=peak, max_fall=fall)
