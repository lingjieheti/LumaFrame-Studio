"""Reconstruct the Profile 5 image, then author a single-layer HDR10-compatible Profile 8.1."""

import json
import mmap
from pathlib import Path
import re
import tempfile

from dovi import find_dovi_tool, prepare_rpu
from neural import SHADER, filter_path


def validate_profile5(stream):
    records = [
        r
        for r in stream.get("side_data_list", [])
        if r.get("side_data_type", "").lower() == "dovi configuration record"
    ]
    if (
        stream.get("codec_name") != "hevc"
        or stream.get("pix_fmt") != "yuv420p10le"
        or len(records) != 1
    ):
        raise ValueError("Profile 5 转换要求具备单一 Dolby 配置记录的 HEVC 10-bit 4:2:0 片源。")
    fields = {
        "dv_profile": 5,
        "dv_bl_signal_compatibility_id": 0,
        "rpu_present_flag": 1,
        "bl_present_flag": 1,
        "el_present_flag": 0,
    }
    if any(str(records[0].get(k)) != str(v) for k, v in fields.items()):
        raise ValueError("片源不符合单层 Dolby Vision Profile 5。")
    return dict(records[0])


def prepare_profile5_rpu(elementary_hevc, rpu_out, sw, sh, tw, th, work_dir, run):
    return prepare_rpu(
        elementary_hevc, rpu_out, sw, sh, tw, th, work_dir, run, _convert_profile5=True
    )


def _constant_fields(path):
    """Find fields shared by every exported block, without loading a film's JSON array."""
    shared = None
    # The pinned dovi_tool exports a JSON array of flat integer records. Its
    # CSV writer cannot represent a switch from preset to explicit L9 primaries.
    with path.open("rb") as stream, mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ) as data:
        for match in re.finditer(rb"\{[^{}]*\}", data):
            row = json.loads(match.group())
            del row["frame"]
            if shared is None:
                shared = row
            else:
                shared = {key: val for key, val in shared.items() if row.get(key) == val}
    return shared


def _mastering_chromaticities(gamut):
    index = gamut.get("source_primary_index")
    # Table in dolby_vision::rpu::extension_metadata::primaries, in ST 2086 units.
    registered = {
        0: "G(13250,34500)B(7500,3000)R(34000,16000)WP(15635,16450)",
        1: "G(15000,30000)B(7500,3000)R(32000,16500)WP(15635,16450)",
        2: "G(8500,39850)B(6550,2300)R(35400,14600)WP(15635,16450)",
        3: "G(15500,29750)B(7750,3500)R(31500,17000)WP(15635,16450)",
        4: "G(14500,30000)B(7500,3000)R(32000,16500)WP(15635,16450)",
        5: "G(13250,34500)B(7500,3000)R(34000,16000)WP(15700,17550)",
    }
    if index != 255 or gamut.get("length") != 17:
        return registered.get(index)
    parts = []
    for code, name in (("G", "green"), ("B", "blue"), ("R", "red"), ("WP", "white")):
        pair = [gamut.get(f"source_primary_{name}_{axis}") for axis in "xy"]
        if any(value is None or not 0 <= value <= 32767 for value in pair):
            return None
        # Custom L9 is in 1/32767 units, HEVC mastering display SEI in 1/50000.
        x, y = [(value * 50000 + 16383) // 32767 for value in pair]
        parts.append(f"{code}({x},{y})")
    return "".join(parts)


def static_from_rpu(rpu, work_dir, run, *, source_master=None):
    """Recover static HDR10 metadata only when the RPU explicitly carries it."""
    where = Path(work_dir).resolve()
    if any(c in str(where) for c in "=,"):
        where = Path(tempfile.gettempdir()).resolve()
    if any(c in str(where) for c in "=,"):
        return None, None
    with tempfile.TemporaryDirectory(dir=where, prefix="p5_static_") as folder:
        l6, l9 = (Path(folder) / f"l{n}.json" for n in (6, 9))
        run(
            [
                str(find_dovi_tool()),
                "export",
                "-i",
                str(rpu),
                "--levels-format",
                "json",
                "--levels",
                f"level6={l6},level9={l9}",
            ]
        )
        light, gamut = _constant_fields(l6), _constant_fields(l9)
    if not light:
        return None, None
    cll, fall = light.get("max_content_light_level"), light.get("max_frame_average_light_level")
    cll_text = f"{cll},{fall}" if None not in (cll, fall) and (cll or fall) else None
    coords = _mastering_chromaticities(gamut) if gamut is not None else None
    if gamut is None and source_master:
        # P7 uses a PQ base layer with separately signalled mastering primaries.
        # If no L9 is supplied, retain those coordinates but use L6 luminance.
        coords = source_master.partition("L(")[0]
    peak = light.get("max_display_mastering_luminance", 0) * 10000
    floor = light.get("min_display_mastering_luminance")
    if not coords or not 0 < peak <= 100000000 or floor is None or floor >= peak:
        return None, cll_text
    return f"{coords}L({peak},{floor})", cll_text


def decode_profile5_filter(iw, ih, tw, th, *, neural=False, output_rgb=False):
    """One streaming filtergraph. The neural scaler sees reconstructed PQ rather than input IPT."""
    iw, ih, tw, th = map(int, (iw, ih, tw, th))
    if min(iw, ih, tw, th) <= 0:
        raise ValueError("P5 画面尺寸无效。")
    init = ["-init_hw_device", "vulkan=lf:,vendor_id=0x10de", "-filter_hw_device", "lf"]
    common = (
        "color_primaries=bt2020:color_trc=smpte2084:"
        "tonemapping=clip:inverse_tonemapping=0:peak_detect=0:deband=0:upscaler=spline36:"
        "antiringing=0.75:dither_temporal=0"
    )
    yuv = "colorspace=bt2020nc:range=tv"
    output = ("format=gbrp16le:colorspace=gbr:range=pc:dithering=none"
              if output_rgb else f"format=yuv420p10le:{yuv}")
    do_neural = neural and min(tw / iw, th / ih) > 1.30
    if do_neural:
        if not SHADER.is_file():
            raise FileNotFoundError("缺少 FSRCNNX 神经网络资源。")
        graph = (
            f"libplacebo=w={iw}:h={ih}:apply_dolbyvision=1:format=yuv444p16le:{yuv}:{common},"
            f"libplacebo=w={tw}:h={th}:apply_dolbyvision=0:{output}:{common}:"
            f"custom_shader_path={filter_path(SHADER)}"
        )
    else:
        graph = f"libplacebo=w={tw}:h={th}:apply_dolbyvision=1:{output}:{common}"
    downloaded = "gbrp16le" if output_rgb else "yuv420p10le"
    return init, "hwupload," + graph + ",hwdownload,format=" + downloaded
