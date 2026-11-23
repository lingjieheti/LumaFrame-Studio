"""Display-referred SDR expansion and shot-by-shot estimates for newly authored metadata."""

import re
import shutil
import tempfile
from functools import lru_cache
from pathlib import Path

from neural import ROOT, SHADER, filter_path, gpu_filter


@lru_cache(maxsize=1)
def _combined_shader():
    # Load both hooks in one render pass. Keep this tempdir for the lifetime of
    # the process since sdr_filter returns before FFmpeg opens its shader file.
    folder = tempfile.TemporaryDirectory(prefix="hdrconvert_shaders_")
    combined = Path(folder.name) / "upscale_hdr.glsl"
    hdr = ROOT / "shaders" / "SDR_highlights_1000.glsl"
    combined.write_bytes(SHADER.read_bytes() + b"\n" + hdr.read_bytes())
    return folder, combined


def sdr_filter(width, height, target_width, target_height, mode):
    shader = ROOT / "shaders" / "SDR_highlights_1000.glsl"
    if mode == "neural" and min(target_width / width, target_height / height) > 1.30:
        shader = _combined_shader()[1]
    init, graph = gpu_filter(
        width, height, target_width, target_height, neural=False, inverse=False, shader_override=shader
    )
    # The MAIN shader gets nonlinear SDR RGB through the true source matrix.
    # It produces actual PQ/BT.2020 RGB values, so the following color-management
    # stages need that transfer and primaries and must not convert it a second time.
    return init, "setparams=colorspace=bt709:color_primaries=bt2020:color_trc=smpte2084," + graph


def _scene(start, length, minimum, maximum, total):
    # signalstats runs on neutral 10-bit limited-range PQ(CIE Y) measurements.
    def pq(code):
        return max(0, min(4095, round((code - 64) * 4095 / 876)))

    return {
        "start": start,
        "length": length,
        "min_pq": pq(minimum),
        "max_pq": pq(maximum),
        "avg_pq": pq(total / length),
    }


def measure_output(base_file, work_dir, run):
    stats = Path(work_dir) / "luminance.txt"
    exe = ROOT / "bin" / "ffmpeg.exe"
    ffmpeg = str(exe) if exe.exists() else shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("找不到 FFmpeg，无法测量输出亮度。")
    # Detect cuts before converting decoded PQ pixels to linear-light luminance.
    # A 480-pixel analysis copy makes long-form use practical; tiny peaks can be missed.
    measurement_shader = ROOT / "shaders" / "Measure_CIE_Y.glsl"
    init = ["-init_hw_device", "vulkan=lf:,vendor_id=0x10de", "-filter_hw_device", "lf"]
    vf = (
        "scale=w=480:h=-2:flags=area,format=yuv420p10le,scdet=threshold=8,hwupload,"
        "libplacebo=format=yuv420p10le:apply_dolbyvision=0:peak_detect=0:deband=0:dithering=none:"
        "color_primaries=bt2020:colorspace=bt2020nc:color_trc=smpte2084:range=tv:custom_shader_path="
        + filter_path(measurement_shader)
        + ",hwdownload,format=yuv420p10le,"
        "signalstats,metadata=mode=print:file=" + filter_path(stats)
    )
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
        ]
    )
    frames = 0
    start, length, minimum, maximum, total = 0, 0, 1023.0, 0.0, 0.0
    scenes, fields = [], {}

    def accept():
        nonlocal frames, start, length, minimum, maximum, total
        if "frame" not in fields:
            return
        if int(fields["frame"]) != frames:
            raise ValueError("亮度分析的帧序号不连续。")
        if float(fields.get("lavfi.scd.score", 0)) >= 8 and length:
            scenes.append(_scene(start, length, minimum, maximum, total))
            start, length, minimum, maximum, total = frames, 0, 1023.0, 0.0, 0.0
        minimum = min(minimum, float(fields["lavfi.signalstats.YMIN"]))
        maximum = max(maximum, float(fields["lavfi.signalstats.YMAX"]))
        total += float(fields["lavfi.signalstats.YAVG"])
        length += 1
        frames += 1

    with stats.open(encoding="utf-8") as records:
        for raw in records:
            line = raw.strip()
            if line.startswith("frame:"):
                accept()
                fields = {"frame": re.match(r"frame:(\d+)", line).group(1)}
            elif "=" in line:
                key, value = line.split("=", 1)
                fields[key] = value
        accept()
    if not frames:
        raise ValueError("没有可分析的输出画面。")
    scenes.append(_scene(start, length, minimum, maximum, total))
    return scenes
