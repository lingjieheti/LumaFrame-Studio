"""Read static HDR10 SEI for native-base Dolby Vision authoring."""

import struct


class NativeMetadata:
    """Collect constant mastering and light levels while hashing the source HEVC."""

    def __init__(self):
        self.static = {}

    def observe(self, kind, unit):
        if kind in (62, 63) or (unit[0] & 1) or (unit[1] >> 3):
            raise ValueError("原片含杜比元数据或额外编码层，不能作为单层 HDR10 制作。")
        if kind not in (39, 40):
            return
        # Decode the SEI RBSP only. Picture data stays in its original encoding.
        data = unit[2:].replace(b"\x00\x00\x03", b"\x00\x00")
        pos = 0
        while pos < len(data) and not (pos == len(data) - 1 and data[pos] == 0x80):
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
            if end > len(data):
                raise ValueError("静态 HDR10 的 SEI 长度超出数据范围。")
            payload = data[pos:end]
            pos = end
            if code == 4 and payload.startswith((b"\xb5\x00\x3c\x00\x01\x04", b"\x26\x00\x04\x00\x05")):
                raise ValueError("原片含 HDR10+ 或 HDR Vivid 动态元数据，不能作为静态 HDR10 制作。")
            if code in (137, 144):
                if size != (24 if code == 137 else 4) or self.static.setdefault(code, payload) != payload:
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
