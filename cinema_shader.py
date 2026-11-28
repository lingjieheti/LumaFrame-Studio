"""Render shot-locked ITM-LUT coefficients on top of the SDR scene transform."""

from array import array
import math
from pathlib import Path
from struct import pack
import sys

from neural import ROOT, SHADER


_GRID = 17
_TEXELS = _GRID ** 3
_MAX_SIDE = 4096
_BRANCHES = 3
_BASES = 5
_RECORD_WORDS = 6


def _unit(value):
    if type(value) not in (int, float) or not 0 <= value <= 1:
        raise ValueError("学习型调色参数必须是零到一之间的有限数值。")
    return value


def _check_cancel(cancel):
    if cancel is not None and cancel.is_set():
        raise InterruptedError("任务已取消。")


def _basis_atlas(path):
    # On disk the model stores [branch, basis, output channel, blue, green, red].
    # Fold branch/basis into depth. Each 17-deep subvolume samples only its own
    # texel centers, so filtering cannot leak between the fifteen basis LUTs.
    path = Path(path)
    count = _BRANCHES * _BASES * _TEXELS
    if path.stat().st_size != count * 3 * 4:
        raise ValueError("学习型调色基础 LUT 尺寸不正确。")
    planes = array("f")
    planes.frombytes(path.read_bytes())
    if sys.byteorder != "little":
        planes.byteswap()
    if not all(map(math.isfinite, planes)):
        raise ValueError("学习型调色基础 LUT 存在无效数值。")
    rgba = array("f", [0.0]) * (count * 4)
    for lut in range(_BRANCHES * _BASES):
        begin = lut * _TEXELS
        for channel in range(3):
            plane = begin * 3 + channel * _TEXELS
            rgba[4 * begin + channel:4 * (begin + _TEXELS):4] = planes[plane:plane + _TEXELS]
    if sys.byteorder != "little":
        rgba.byteswap()
    return rgba.tobytes()


def _texture(name, size, fmt, contents, *, linear=False):
    dims = " ".join(str(x) for x in size)
    sampling = "LINEAR" if linear else "NEAREST"
    return f"//!TEXTURE {name}\n//!SIZE {dims}\n//!FORMAT {fmt}\n//!FILTER {sampling}\n{contents.hex()}\n"


def _table(name, fmt, contents, texel_bytes):
    entries = len(contents) // texel_bytes
    width = min(entries, _MAX_SIDE)
    height = max(2, (entries + width - 1) // width)
    if height > _MAX_SIDE:
        raise ValueError("学习型调色的帧数或镜头数超出支持范围。")
    contents.extend(bytes((width * height - entries) * texel_bytes))
    return _texture(name, (width, height), fmt, contents), width


def write_cinema_shader(folder, basis_path, records, *, neural=False, cancel=None):
    """Write one shader. The fifteen full-precision 3D textures are shared by all shots."""
    _check_cancel(cancel)
    timeline = bytearray()
    params = bytearray()
    frames = 0
    extended = False
    for index, shot in enumerate(records):
        _check_cancel(cancel)
        length = shot["length"]
        if type(length) is not int or length < 1 or shot["start"] != frames:
            raise ValueError("逐镜调色的帧序号或高光强度无效。")
        if frames + length > _MAX_SIDE ** 2 or (index + 1) * _RECORD_WORDS > _MAX_SIDE ** 2:
            raise ValueError("学习型调色的帧数或镜头数超出支持范围。")
        reserve = _unit(shot.get("headroom", 0))
        extended |= reserve > 0
        # Match the 8-bit parameters in the established scene shader exactly.
        look = [round(_unit(v) * 255) / 255 for v in (shot["strength"], shot["high_key"], reserve)]
        timeline.extend(pack("<f", index) * length)
        frames += length
        if shot.get("auto_cinema") is False:
            # Zero mix takes the shader's early return; there are no model weights for this shot.
            params.extend(pack("<4f", *look, 0) + bytes(16 * (_RECORD_WORDS - 1)))
            continue
        means = shot["means"]
        weights = shot["weights"]
        if len(means) != 3 or len(weights) != 3 or any(len(branch) != 5 for branch in weights):
            raise ValueError("学习型调色的模型参数维度不正确。")
        red, green, blue = map(_unit, means)
        checked = [tuple(map(_unit, row)) for row in weights]
        mean_y = 0.2126 * red + 0.7152 * green + 0.0722 * blue
        t = min(1.0, max(0.0, (mean_y - 0.70) / (0.86 - 0.70)))
        amount = 0.0 if reserve else 1.0 - t*t*(3.0 - 2.0*t)
        params.extend(pack("<8f", *look, amount, red, green, blue, 0))
        for branch in checked:
            params.extend(pack("<4f", *branch[:4]))
        params.extend(pack("<4f", *(branch[4] for branch in checked), 0))
    if not frames:
        raise ValueError("逐镜调色的帧数超出支持范围。")
    frame_table, frame_width = _table("LF_FRAME_INDEX", "r32f", timeline, 4)
    shot_table, shot_width = _table("LF_COEFFICIENTS", "rgba32f", params, 16)
    basis = _texture("LF_BASES", (17, 17, 255), "rgba32f", _basis_atlas(basis_path), linear=True)
    _check_cancel(cancel)
    upscale = SHADER.read_text(encoding="utf-8") + "\n" if neural else ""
    stride = 1 + upscale.count("//!HOOK LUMA\n")
    base_hdr = (ROOT / "shaders" / "SDR_highlights_1000.glsl").read_text(encoding="utf-8")
    header, base_hook = base_hdr.split("\n\n", 1)
    constants = (
        f"#define LF_FRAME_W {frame_width}\n#define LF_SHOT_W {shot_width}\n#define LF_STRIDE {stride}\n"
        "#define LF_SHOT_GRADE\n" + ("#define LF_EXTENDED_WHITE\n" if extended else "")
    )
    code = (ROOT / "shaders" / "Cinema_LUT.glsl").read_text(encoding="utf-8")
    # Reuse the normal 1.6.1 PQ transform as the fallback, including its real
    # super-white shoulder. Both modes read from the same SDR texel on this pass.
    base_section = "#define hook lf_scene_hook\n" + base_hook + "\n#undef hook"
    shader = Path(folder) / "cinema_grade.glsl"
    shader.write_text(upscale + basis + frame_table + shot_table + header +
                      "\n//!BIND LF_FRAME_INDEX\n//!BIND LF_COEFFICIENTS\n//!BIND LF_BASES\n\n" +
                      constants + code.replace("/* LF_SCENE_TRANSFORM */", base_section), encoding="utf-8")
    return shader
