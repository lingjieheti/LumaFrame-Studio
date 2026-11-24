"""Reconstruct the Profile 5 image, then author a single-layer HDR10-compatible Profile 8.1."""

import csv
from pathlib import Path
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


def _constant_values(path):
    """A single static SEI cannot represent shot-specific mastering settings."""
    first = None
    with path.open(encoding="utf-8", newline="") as stream:
        for row in csv.DictReader(stream):
            row.pop("frame")
            if first is not None and first != row:
                return None
            first = row
    return first


def static_from_rpu(rpu, work_dir, run, *, source_master=None):
    """Recover static HDR10 metadata only when the RPU explicitly carries it."""
    where = Path(work_dir).resolve()
    if any(c in str(where) for c in "=,"):
        where = Path(tempfile.gettempdir()).resolve()
    if any(c in str(where) for c in "=,"):
        return None, None
    with tempfile.TemporaryDirectory(dir=where, prefix="p5_static_") as folder:
        l6, l9 = (Path(folder) / f"l{n}.csv" for n in (6, 9))
        run(
            [
                str(find_dovi_tool()),
                "export",
                "-i",
                str(rpu),
                "--levels",
                f"level6={l6},level9={l9}",
            ]
        )
        light, gamut = _constant_values(l6), _constant_values(l9)
    if not light:
        return None, None
    cll, fall = int(light["max_content_light_level"]), int(light["max_frame_average_light_level"])
    cll_text = f"{cll},{fall}" if cll or fall else None
    # Most common registered mastering displays: DCI-P3 D65, BT.709 and BT.2020.
    gamuts = {
        0: "G(13250,34500)B(7500,3000)R(34000,16000)WP(15635,16450)",
        1: "G(15000,30000)B(7500,3000)R(32000,16500)WP(15635,16450)",
        2: "G(8500,39850)B(6550,2300)R(35400,14600)WP(15635,16450)",
    }
    coords = gamuts.get(int(gamut["source_primary_index"])) if gamut else None
    if coords is None and source_master:
        # P7 uses a PQ base layer with separately signalled mastering primaries.
        # If no L9 is supplied, retain those coordinates but use L6 luminance.
        coords = source_master.partition("L(")[0]
    peak = int(light["max_display_mastering_luminance"]) * 10000
    floor = int(light["min_display_mastering_luminance"])
    if not coords or not 0 < peak <= 100000000 or floor >= peak:
        return None, cll_text
    return f"{coords}L({peak},{floor})", cll_text


def decode_profile5_filter(iw, ih, tw, th, *, neural=False):
    """One streaming filtergraph. The neural scaler sees reconstructed PQ rather than input IPT."""
    iw, ih, tw, th = map(int, (iw, ih, tw, th))
    if min(iw, ih, tw, th) <= 0:
        raise ValueError("P5 画面尺寸无效。")
    init = ["-init_hw_device", "vulkan=lf:,vendor_id=0x10de", "-filter_hw_device", "lf"]
    common = (
        "colorspace=bt2020nc:color_primaries=bt2020:color_trc=smpte2084:range=tv:"
        "tonemapping=clip:inverse_tonemapping=0:peak_detect=0:deband=0:upscaler=spline36:"
        "antiringing=0.75:dither_temporal=0"
    )
    do_neural = neural and min(tw / iw, th / ih) > 1.30
    if do_neural:
        if not SHADER.is_file():
            raise FileNotFoundError("缺少 FSRCNNX 神经网络资源。")
        graph = (
            f"libplacebo=w={iw}:h={ih}:apply_dolbyvision=1:format=yuv444p16le:{common},"
            f"libplacebo=w={tw}:h={th}:apply_dolbyvision=0:format=yuv420p10le:{common}:"
            f"custom_shader_path={filter_path(SHADER)}"
        )
    else:
        graph = f"libplacebo=w={tw}:h={th}:apply_dolbyvision=1:format=yuv420p10le:{common}"
    return init, "hwupload," + graph + ",hwdownload,format=yuv420p10le"
