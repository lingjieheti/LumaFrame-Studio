"""Measure HDR10 content light from the final decoded HEVC base layer."""

import json
import math
import tempfile
from pathlib import Path

import dovi
from neural import filter_path


def _levels_from_stats(path, expected_frames, pixels, cancel):
    maximum = average = 0.0
    frames = 0
    fields = {}
    with path.open(encoding="ascii") as lines:
        for line in lines:
            if line.startswith("frame:"):
                if cancel is not None and cancel.is_set():
                    raise InterruptedError("任务已取消。")
                if fields or int(line.split()[0][6:]) != frames:
                    raise ValueError("内容亮度分析的画面顺序或统计字段不完整。")
            elif line.startswith("lavfi.astats."):
                name, _, value = line.strip().partition("=")
                fields[name.rsplit(".", 1)[1]] = float(value)
                if len(fields) == 3:
                    peak, mean = fields["Max_level"], fields["DC_offset"]
                    if fields["Number_of_samples"] != pixels or not 0 <= mean <= peak + 0.000001 or not 0 <= peak <= 10000:
                        raise ValueError("内容亮度分析返回了无效的像素数量或亮度。")
                    maximum = max(maximum, peak)
                    average = max(average, min(mean, peak))
                    frames += 1
                    fields.clear()
    if fields or frames != expected_frames:
        raise ValueError("内容亮度分析的解码帧数与输出视频不一致。")
    return maximum, average


def _write_pq_table(path):
    # Sample ST 2084 in double precision. Linear interpolation at this density
    # contributes less than 0.00004 nit of error even at the top of the curve.
    with path.open("w", encoding="ascii", newline="\n") as table:
        table.write("LUT_1D_SIZE 65536\n")
        for i in range(65536):
            p = (i / 65535) ** (32 / 2523)
            linear = (max(0, p - 3424 / 4096) / (2413 / 128 - 2392 / 128 * p)) ** (16384 / 2610)
            sample = format(linear, ".12g")
            table.write(f"{sample} {sample} {sample}\n")


def measure_content_light(encoded, canvas, offsets, expected_frames, workdir, run, *, progress=None):
    """Measure MaxCLL and MaxFALL from all active pixels, in whole nits (rounded up)."""
    from engine import _tool
    from profile7 import run_ffmpeg_pipe

    width, height = canvas
    left, right, top, bottom = dovi.validate_active_area(offsets, width, height)
    if width % 2 or height % 2 or type(expected_frames) is not int or expected_frames < 1:
        raise ValueError("内容亮度测量需要有效的 4:2:0 画布和解码帧数。")
    stream = json.loads(run([_tool("ffprobe"), "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=codec_name,width,height,pix_fmt,color_range,color_transfer,color_space,color_primaries,chroma_location",
        "-of", "json", encoded]))["streams"][0]
    if (stream["width"], stream["height"], stream["codec_name"], stream["pix_fmt"], stream.get("color_range"),
        stream.get("color_transfer"), stream.get("color_space"), stream.get("color_primaries")) != (
        width, height, "hevc", "yuv420p10le", "tv", "smpte2084", "bt2020nc", "bt2020"):
        raise ValueError("内容亮度测量所需的 HEVC 10 位 BT.2020/PQ 编码、范围或实际画布不符。")
    # HEVC infers chroma_sample_loc_type = 0 when omitted in VUI.
    location = stream.get("chroma_location", "unspecified")
    chroma = {"unspecified": "left", "left": "left", "center": "center", "top": "top",
              "topleft": "topleft", "bottom": "bottom", "bottomleft": "bottomleft"}
    if location not in chroma:
        raise ValueError("未知的 HEVC 色度采样位置。")
    active_w, active_h = width - left - right, height - top - bottom
    pixels = active_w * active_h
    to_rgb = (
        f"zscale=filter=spline36:chromalin={chroma[location]}:min=2020_ncl:rin=limited:m=gbr:r=full:"
        "tin=smpte2084:t=smpte2084:agamma=0,format=gbrpf32le,"
        f"crop={active_w}:{active_h}:{left}:{top}:exact=1,"
    )
    max_rgb = ("extractplanes=r+g+b[r][g][b];[r][g]blend=all_mode=lighten[rg];"
               "[rg][b]blend=all_mode=lighten,format=grayf32le")
    ff = [_tool("ffmpeg"), "-nostdin", "-v", "error", "-xerror", "-threads", "4"]
    # One grayf32 sample per pixel has the same binary representation as mono f32.
    # Group exactly one picture per block. astats computes the peak and arithmetic
    # mean with a double accumulator. No interpolation, mixing, or audio resampling.
    with tempfile.TemporaryDirectory(dir=workdir, prefix="cll_") as tmp:
        stats = Path(tmp, "frames.txt")
        table = Path(tmp, "pq.cube")
        _write_pq_table(table)
        filters = ("asoftclip=type=hard:threshold=1,volume=10000:precision=float,"
                   f"asetnsamples=n={pixels}:p=0,astats=metadata=1:reset=1:"
                   "measure_overall=Number_of_samples:measure_perchannel=Max_level+DC_offset,"
                   f"ametadata=print:file={filter_path(stats)}")
        measure = [*ff, "-f", "f32le", "-ar", "48000", "-ac", "1", "-i", "pipe:0",
                   "-filter_threads", "1", "-af", filters, "-f", "null", "-"]
        def analyze(graph, phase):
            # Splitting a chroma reconstruction into independently processed
            # slices can change peaks near slice boundaries. Decode in parallel,
            # but run this spatial filter on the uninterrupted source raster.
            decode = [*ff, "-noauto_conversion_filters", "-filter_complex_threads", "1", "-i", str(encoded),
                      "-filter_complex", graph + "[maxrgb]", "-map", "[maxrgb]", "-fps_mode", "passthrough",
                      "-threads", "1", "-f", "rawvideo", "pipe:1"]
            def report(frames):
                # The consumer still has to validate all per-frame pixel counts.
                if frames < expected_frames:
                    progress(frames, expected_frames, phase)

            if progress is not None:
                progress(0, expected_frames, phase)
            run_ffmpeg_pipe(decode, measure, run, tmp, on_frame=report if progress is not None else None,
                            failure="HDR10 内容亮度测量失败。")
            result = _levels_from_stats(stats, expected_frames, pixels, getattr(run, "cancel", None))
            if progress is not None:
                progress(expected_frames, expected_frames, phase)
            return result

        levels = analyze(to_rgb + f"lut1d=file={filter_path(table)}:interp=linear,format=gbrpf32le," + max_rgb, "light")
        # Resolve an integer signalling boundary using zimg's non-approximated
        # EOTF. LUT interpolation and float arithmetic cannot cross a boundary
        # outside this margin. Black and PQ reference peak are exact endpoints.
        if any(0 < n < 10000 and abs(n - round(n)) < 0.02 for n in levels):
            levels = analyze(to_rgb + max_rgb + ",setparams=colorspace=bt2020nc:color_trc=smpte2084:range=full,"
                "zscale=min=2020_ncl:m=2020_ncl:rin=full:r=full:tin=smpte2084:t=linear:"
                "npl=10000:agamma=0,format=grayf32le", "light_refine")
        peak, mean = levels
        # Text statistics have six fractional decimal places. A single very
        # dim pixel on a black UHD frame still makes its true mean positive.
        return math.ceil(peak), max(int(peak > 0), math.ceil(mean))
