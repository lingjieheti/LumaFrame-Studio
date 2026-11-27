"""Display-referred SDR expansion and shot-by-shot estimates for newly authored metadata."""

import re
import shutil
import tempfile
import time
from array import array
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


def _shot_starts(marks, levels, *, cancel=None):
    """Keep scdet hard cuts and recover isolated weaker changes in shot brightness."""
    means = memoryview(levels)[2::3]
    differences = array("f")
    cuts = set()
    index = -1
    with marks.open(encoding="utf-8", errors="replace") as src:
        for line in src:
            if line.startswith("frame:"):
                index = int(line[6:].split()[0])
                if index % 1024 == 0 and cancel is not None and cancel.is_set():
                    raise InterruptedError("任务已取消。")
                if index != len(differences):
                    raise ValueError("分镜检测的帧序号不连续。")
            elif line.startswith("lavfi.scd.mafd="):
                differences.append(float(line.split("=", 1)[1]))
            elif line.startswith("lavfi.scd.time="):
                cuts.add(index)
    if len(differences) != len(means):
        raise ValueError("分镜检测和亮度测量的帧数不同。")
    for i in range(3, len(means) - 3):
        if i % 1024 == 0 and cancel is not None and cancel.is_set():
            raise InterruptedError("任务已取消。")
        change = differences[i]
        if change < 4 or i in cuts:
            continue
        # Compare with motion before AND after the edit. A second reverse
        # transition up to three frames later should not promote a brief flash.
        motion_before = sum(differences[i - 3:i]) / 3
        motion_after = sum(differences[i + 1:i + 4]) / 3
        if change < 4 * max(motion_before, motion_after):
            continue
        # Compare shot means on both sides. Motion that merely relocates a
        # pattern may change RGB differences sharply while leaving L1 stable.
        mean_before = (means[i - 2] + means[i - 1]) * 0.5
        mean_after = (means[i] + means[i + 1]) * 0.5
        if abs(mean_after - mean_before) >= 0.02 * 65535:
            cuts.add(i)
    return sorted(cuts)


def measure_output(base_file, work_dir, run, *, active_area=None, expected_frames=None, progress=None):
    if expected_frames is not None and (type(expected_frames) is not int or expected_frames < 1):
        raise ValueError("逐镜分析需要有效的预期帧数。")
    exe = ROOT / "bin" / "ffmpeg.exe"
    ffmpeg = str(exe) if exe.exists() else shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("找不到 FFmpeg，无法测量输出亮度。")
    # Reduce full-size PQ MaxRGB tiles on the GPU and expose the three result
    # planes to signalstats. mergeplanes creates fresh frames without scene tags.
    # gbrp stores G (minimum), B (mean), R (maximum), in that order.
    measurement_shader = ROOT / "shaders" / "Measure_MaxRGB.glsl"
    init = ["-init_hw_device", "vulkan=lf:,vendor_id=0x10de", "-filter_hw_device", "lf"]
    vf = (
        "[measure]hwupload,"
        "libplacebo=w=ceil(iw/8):h=ceil(ih/8):format=gbrp16le:"
        "apply_dolbyvision=0:peak_detect=0:deband=0:dithering=none:"
        "color_primaries=bt2020:colorspace=gbr:color_trc=smpte2084:range=pc:custom_shader_path="
        + filter_path(measurement_shader)
        + ",hwdownload,format=gbrp16le,mergeplanes=map0p=0:map1p=1:map2p=2:format=yuv444p16le,"
        "signalstats,metadata=mode=print:file=-:direct=1[out]"
    )
    # Analysis and cut detection share a decode. The two filter branches may
    # run ahead of each other, so join by presentation-frame index after EOF.
    # Three doubles per frame use about 8 MiB for four hours at 24 fps.
    levels = array("d")
    fields = {}
    next_update = 0.0
    if progress is not None:
        progress(0, expected_frames, "scenes")

    def accept():
        nonlocal next_update
        if "frame" not in fields:
            return
        if int(fields["frame"]) != len(levels) // 3:
            raise ValueError("亮度分析的帧序号不连续。")
        levels.extend(float(fields[k]) for k in (
            "lavfi.signalstats.YMIN", "lavfi.signalstats.VMAX", "lavfi.signalstats.UAVG"
        ))
        count = len(levels) // 3
        if progress is not None and (expected_frames is None or count < expected_frames):
            now = time.monotonic()
            if now >= next_update:
                progress(count, expected_frames, "scenes")
                next_update = now + 0.5

    def parse_line(line):
        nonlocal fields
        if line.startswith("frame:"):
            accept()
            fields = {"frame": re.match(r"frame:(\d+)", line).group(1)}
        elif line.startswith(("lavfi.signalstats.YMIN=", "lavfi.signalstats.VMAX=",
                              "lavfi.signalstats.UAVG=")):
            key, _, value = line.partition("=")
            fields[key] = value

    with tempfile.TemporaryDirectory(dir=work_dir, prefix="scene_cuts_") as folder:
        marks = Path(folder) / "cuts.txt"
        graph_options = []
        # scdet uses only luma on planar YUV. A compact RGB picture makes
        # abrupt isoluminant colour edits measurable without another 4K copy.
        # Pad each end so the centred three-frame median keeps the original
        # frame numbers. Only the cut detector suppresses isolated flashes.
        source = "[0:v:0]"
        if active_area and any(active_area):
            left, right, top, bottom = active_area
            # Chroma taps must see neighbouring rows across FFmpeg's slice boundaries.
            graph_options = ["-filter_complex_threads", "1"]
            # Reconstruct full-resolution PQ chroma before cropping at the
            # exact luma-pixel offset, including odd 4:2:0 boundaries.
            source += ("zscale=filter=spline36:matrixin=2020_ncl:rangein=limited:matrix=gbr:range=full,"
                       f"format=gbrp16le,crop=w=iw-{left}-{right}:h=ih-{top}-{bottom}:x={left}:y={top}:exact=1,")
        detector = source + (
            "split[measure][detect];[detect]"
            "scale=w=min(iw\\,320):h=min(ih\\,180):force_original_aspect_ratio=decrease:"
            "flags=area:in_color_matrix=bt2020:out_range=full,"
            "format=gbrp,tpad=start=1:stop=1:start_mode=clone:stop_mode=clone,"
            "tmedian=radius=1,format=rgb24,scdet=threshold=8,"
            "metadata=mode=print:file="
            + filter_path(marks) + ",nullsink;"
        )
        run([ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", *init, *graph_options,
             "-i", str(base_file), "-filter_complex", detector + vf, "-map", "[out]",
             "-an", "-sn", "-f", "null", "-"], parse_line, collect=False)
        accept()
        if expected_frames is not None and len(levels) // 3 != expected_frames:
            raise ValueError("逐镜亮度分析的实际帧数与输出视频不一致。")
        cancel = getattr(run, "cancel", None)
        cuts = _shot_starts(marks, levels, cancel=cancel)
    frames = len(levels) // 3
    if not frames:
        raise ValueError("没有可分析的输出画面。")
    starts = [0, *cuts, frames]
    scenes = []
    for start, end in zip(starts, starts[1:]):
        if cancel is not None and cancel.is_set():
            raise InterruptedError("任务已取消。")
        if end <= start:
            raise ValueError("分镜帧序号与亮度分析不一致。")
        scene = memoryview(levels)[start * 3:end * 3]
        scenes.append(_scene(start, end - start, min(scene[0::3]), max(scene[1::3]), sum(scene[2::3])))
    if progress is not None:
        progress(frames, frames, "scenes")
    return scenes
