"""Display-referred SDR expansion and shot-by-shot estimates for newly authored metadata."""

import re
import shutil
import tempfile
from functools import lru_cache
from pathlib import Path

from neural import ROOT, SHADER, filter_path, gpu_filter


@lru_cache(maxsize=3)
def _combined_shader(neural, reference):
    # Compose FSRCNNX and the chosen display transform in one render pass.
    # The temporary directory must outlive the FFmpeg process reading it.
    folder = tempfile.TemporaryDirectory(prefix="hdrconvert_shaders_")
    combined = Path(folder.name) / "upscale_hdr.glsl"
    hdr = ROOT / "shaders" / "SDR_highlights_1000.glsl"
    source = hdr.read_text(encoding="utf-8")
    if reference:
        # The first paragraph is the libplacebo hook header. Insert the define
        # in the GLSL body; both looks then use the same matrix, EOTF and PQ.
        header, body = source.split("\n\n", 1)
        source = header + "\n\n#define LF_REFERENCE_WHITE\n" + body
    if neural:
        source = SHADER.read_text(encoding="utf-8") + "\n" + source
    combined.write_text(source, encoding="utf-8")
    return folder, combined


def sdr_filter(width, height, target_width, target_height, mode, *, mapping="expand", output_rgb=False):
    if mapping not in {"expand", "reference"}:
        raise ValueError("未知的 SDR 到 HDR 亮度映射。")
    shader = ROOT / "shaders" / "SDR_highlights_1000.glsl"
    neural = mode == "neural" and min(target_width / width, target_height / height) > 1.30
    if neural or mapping == "reference":
        shader = _combined_shader(neural, mapping == "reference")[1]
    init, graph = gpu_filter(
        width, height, target_width, target_height, neural=False, inverse=False,
        shader_override=shader, output_rgb=output_rgb,
    )
    # The MAIN shader gets nonlinear SDR RGB through the true source matrix.
    # It produces actual PQ/BT.2020 RGB values, so the following color-management
    # stages need that transfer and primaries and must not convert it a second time.
    return init, "setparams=colorspace=bt709:color_primaries=bt2020:color_trc=smpte2084," + graph


def _scene(start, length, minimum, maximum, total):
    # The measurement planes carry normalized PQ in unsigned full-range 16-bit.
    def pq(code):
        return max(0, min(4095, round(code * 4095 / 65535)))

    return {
        "start": start,
        "length": length,
        "min_pq": pq(minimum),
        "max_pq": pq(maximum),
        "avg_pq": pq(total / length),
    }


def measure_output(base_file, work_dir, run):
    exe = ROOT / "bin" / "ffmpeg.exe"
    ffmpeg = str(exe) if exe.exists() else shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("找不到 FFmpeg，无法测量输出亮度。")
    # Detect cuts on the decoded picture. Reduce full-size PQ MaxRGB tiles on
    # the GPU, then expose the three RGB result planes to signalstats directly.
    # gbrp stores G (minimum), B (mean), R (maximum), in that order.
    measurement_shader = ROOT / "shaders" / "Measure_MaxRGB.glsl"
    init = ["-init_hw_device", "vulkan=lf:,vendor_id=0x10de", "-filter_hw_device", "lf"]
    vf = (
        "scdet=threshold=8,hwupload,"
        "libplacebo=w=ceil(iw/8):h=ceil(ih/8):format=gbrp16le:"
        "apply_dolbyvision=0:peak_detect=0:deband=0:dithering=none:"
        "color_primaries=bt2020:colorspace=gbr:color_trc=smpte2084:range=pc:custom_shader_path="
        + filter_path(measurement_shader)
        + ",hwdownload,format=gbrp16le,mergeplanes=map0p=0:map1p=1:map2p=2:format=yuv444p16le,"
        "signalstats,metadata=mode=print:file=-:direct=1"
    )
    frames = 0
    start, length, minimum, maximum, total = 0, 0, 65535.0, 0.0, 0.0
    scenes, fields = [], {}

    def accept():
        nonlocal frames, start, length, minimum, maximum, total
        if "frame" not in fields:
            return
        if int(fields["frame"]) != frames:
            raise ValueError("亮度分析的帧序号不连续。")
        if float(fields.get("lavfi.scd.score", 0)) >= 8 and length:
            scenes.append(_scene(start, length, minimum, maximum, total))
            start, length, minimum, maximum, total = frames, 0, 65535.0, 0.0, 0.0
        minimum = min(minimum, float(fields["lavfi.signalstats.YMIN"]))
        maximum = max(maximum, float(fields["lavfi.signalstats.VMAX"]))
        total += float(fields["lavfi.signalstats.UAVG"])
        length += 1
        frames += 1

    def parse_line(line):
        nonlocal fields
        if line.startswith("frame:"):
            accept()
            fields = {"frame": re.match(r"frame:(\d+)", line).group(1)}
        elif line.startswith(("lavfi.signalstats.YMIN=", "lavfi.signalstats.VMAX=",
                              "lavfi.signalstats.UAVG=", "lavfi.scd.score=")):
            key, _, value = line.partition("=")
            fields[key] = value

    run(
        [
            ffmpeg,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            *init,
            "-i",
            str(base_file),
            "-map",
            "0:v:0",
            "-an",
            "-sn",
            "-vf",
            vf,
            "-f",
            "null",
            "-",
        ],
        parse_line,
        collect=False,
    )
    accept()
    if not frames:
        raise ValueError("没有可分析的输出画面。")
    scenes.append(_scene(start, length, minimum, maximum, total))
    return scenes
