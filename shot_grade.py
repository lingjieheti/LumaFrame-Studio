"""Plan SDR highlight expansion in display order before the HDR render."""

from array import array
from pathlib import Path
import re
import tempfile
import time

from hdr import _shot_starts
from neural import filter_path
from auto_grade import compressed_luma, use_cinema


def _shot_look(coverage, near_white, means):
    # Protect broad areas of near-white. Measure the upper quartile across
    # the shot so a rare flash does not dim the surrounding frames.
    ordered = sorted(coverage)
    area = ordered[(3 * (len(ordered) - 1) + 2) // 4]
    t = min(1.0, max(0.0, (area - 0.015) / (0.30 - 0.015)))
    diffuse_protection = 0.92 * t * t * (3.0 - 2.0 * t)
    # Broad highlights also occur in deliberately high-key cinematography.
    # For a persistently bright shot gradually restore the expansive look.
    # Use the lower median so brief flashes cannot establish a high-key look.
    ordered_means = sorted(means)
    mean = ordered_means[(len(ordered_means) - 1) // 2] / 65535
    t = min(1.0, max(0.0, (mean - 0.72) / (0.90 - 0.72)))
    high_key = t * t * (3.0 - 2.0 * t)
    # A bright tunnel exit against a dark surround can occupy the same
    # area as a white wall. If most of the highlight signal is near its
    # ceiling, preserve the brighter look. A lower temporal median ignores
    # brief flashes; a small area floor rejects isolated hot pixels.
    ratios = sorted(peak / max(broad, 0.03) for peak, broad in zip(near_white, coverage))
    ceiling_share = ratios[(len(ratios) - 1) // 2]
    t = min(1.0, max(0.0, (ceiling_share - 0.35) / (0.75 - 0.35)))
    high_key = max(high_key, t * t * (3.0 - 2.0 * t))
    strength = 1.0 - diffuse_protection * (1.0 - high_key)
    return round(strength, 5), round(high_key, 5)


def _without_flashes(cuts, levels):
    # Median filtering already removes a single flash. Suppress paired
    # strong cuts bounding two or three bright frames if both surrounding
    # shots have the same mean luma and highlight coverage.
    areas = memoryview(levels)[0::3]
    means = memoryview(levels)[2::3]
    keep = []
    i = 0
    while i < len(cuts):
        start = cuts[i]
        if i + 1 < len(cuts):
            end = cuts[i + 1]
            if start >= 2 and end - start <= 3 and end + 1 < len(means):
                before = sum(means[start - 2:start]) / 2
                after = sum(means[end:end + 2]) / 2
                before_area = sum(areas[start - 2:start]) / 2
                after_area = sum(areas[end:end + 2]) / 2
                flash_mean = sum(means[start:end]) / (end - start)
                if (abs(after - before) < 0.025 * 65535 and abs(after_area - before_area) < 0.025
                        and flash_mean > max(before, after) + 0.12 * 65535):
                    i += 2
                    continue
        keep.append(start)
        i += 1
    return keep


def _read_headroom(path, times, cancel):
    # Each value is the maximum spatially averaged excursion above video white.
    # Join by both ordinal and PTS on the same decode as the ordinary shot scan.
    peaks = bytearray()
    frame = None
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            if line.startswith("frame:"):
                match = re.match(r"frame:(\d+)\s+pts:(-?\d+)\s", line)
                index = len(peaks)
                if (frame is not None or not match or index >= len(times) or int(match[1]) != index
                        or abs(int(match[2]) - times[index]) > 1):
                    raise ValueError("逐镜调色的解码帧与源画面时间戳不一致。")
                frame = index
                if index % 1024 == 0 and cancel is not None and cancel.is_set():
                    raise InterruptedError("任务已取消。")
            elif line.startswith("lavfi.signalstats.YMAX="):
                value = int(line.partition("=")[2])
                if frame is None or not 0 <= value <= 255:
                    raise ValueError("逐镜调色缺少有效的画面测量值。")
                peaks.append(value)
                frame = None
    if frame is not None or len(peaks) != len(times):
        raise ValueError("逐镜调色的分析帧数与源画面不一致。")
    return peaks


def _persistent_headroom(values):
    # Require a sustained signal in the same shot. A flash or isolated ringing
    # should not rebalance a whole scene. Code excursions <= 1.5 are negligible.
    if len(values) < 3:
        peak = min(values)
    else:
        peak = max(map(min, values, values[1:], values[2:]))
    return peak / 255 if peak >= 20 else 0.0


def analyze_shots(source, video, times, scratch, run, progress=None, *, active_area=None, auto=False):
    """Return contiguous {start, length, strength, high_key} ranges by decoded frame.

    times contains the original presentation timestamps in microseconds, e.g.
    engine._times(). active_area holds left/right/top/bottom offsets in
    SOURCE pixels. In auto mode each shot also carries an auto_cinema boolean.
    Never schedule grades by nominal FPS, even for a preview.
    """
    from engine import _tool

    if not times:
        raise ValueError("逐镜调色缺少源画面时间戳。")
    total = len(times)
    w, h = int(video["width"]), int(video["height"])
    offsets = (0, 0, 0, 0) if active_area is None else active_area
    if (len(offsets) != 4 or any(type(v) is not int or v < 0 for v in offsets)
            or sum(offsets[:2]) >= w or sum(offsets[2:]) >= h):
        raise ValueError("逐镜调色的源画面有效区域无效。")
    left, right, top, bottom = offsets
    cancel = getattr(run, "cancel", None)
    if progress:
        progress(0, total)
    # Sparse areas of near-white merit more expansion than broad diffuse
    # whites. Two soft masks measure broad highlights and signal near its
    # ceiling. Plane U retains the original mean luma for cut/flash tests.
    levels = array("f")
    candidates = bytearray()
    fields = ("YAVG", "UAVG", "VAVG", "ULOW", "UHIGH", "UMAX") if auto else ("YAVG", "UAVG", "VAVG")
    metadata_keys = tuple(f"lavfi.signalstats.{field}=" for field in fields)
    next_update = 0.0
    current = {}

    def accept():
        nonlocal next_update
        if not current:
            return
        index = len(levels) // 3
        if index >= total or current["frame"] != index or abs(current["pts"] - times[index]) > 1:
            raise ValueError("逐镜调色的解码帧与源画面时间戳不一致。")
        if not all(0 <= current.get(key, -1) <= 255 for key in fields):
            raise ValueError("逐镜调色缺少有效的画面测量值。")
        levels.extend((current["YAVG"] / 255, current["VAVG"] / 255, current["UAVG"] * 257))
        if auto:
            candidates.append(compressed_luma(current["ULOW"], current["UAVG"], current["UHIGH"], current["UMAX"]))
        if progress and index + 1 < total and time.monotonic() >= next_update:
            progress(index + 1, total)
            next_update = time.monotonic() + 0.5

    def parse(line):
        if line.startswith("frame:"):
            accept()
            match = re.match(r"frame:(\d+)\s+pts:(-?\d+)\s", line)
            if match is None:
                raise ValueError("逐镜调色缺少有效的解码时间戳。")
            current.clear()
            current.update(frame=int(match[1]), pts=int(match[2]))
        elif line.startswith(metadata_keys):
            key, value = line.split("=", 1)
            current[key.rsplit(".", 1)[1]] = float(value)

    source_range = "full" if video.get("color_range") == "pc" or video["pix_fmt"].startswith("yuvj") else "limited"
    with tempfile.TemporaryDirectory(dir=scratch, prefix="grade_") as folder:
        marks = Path(folder) / "cuts.txt"
        excursions = Path(folder) / "headroom.txt"
        # For explicitly limited-range sources inspect the coded luma before
        # its conversion to full range. Full-range RGB white uses all its codes.
        inspect_headroom = video.get("color_range") == "tv" and not video["pix_fmt"].startswith("yuvj")
        range_branch = ""
        if inspect_headroom:
            range_branch = (
                "split[grade][extended];[extended]"
                "scale=w=min(iw\\,320):h=min(ih\\,180):force_original_aspect_ratio=decrease:"
                "flags=area:in_range=full:out_range=full,format=yuv444p,"
                "lutyuv=y='clip((val-235)*255/20,0,255)':u=128:v=128,"
                "scale=w=min(iw\\,40):h=min(ih\\,24):flags=area:in_range=full:out_range=full,"
                "signalstats,metadata=mode=print:key=lavfi.signalstats.YMAX:file="
                + filter_path(excursions) + ",nullsink;[grade]"
            )
        # Reconfiguration on a changed input raster would reset a frame-indexed
        # render shader. Force crop to reject any actual raster other than the
        # one we probed (a crop wider than the input is invalid).
        graph = (
            f"[0:{int(video['index'])}]settb=AVTB,"
            f"crop=w='if(eq(iw,{w})*eq(ih,{h}),iw-{left + right},iw+2)':"
            f"h=ih-{top + bottom}:x={left}:y={top}:exact=1," + range_branch +
            "scale=w=min(iw\\,320):h=min(ih\\,180):force_original_aspect_ratio=decrease:"
            f"flags=area:in_color_matrix=bt709:out_color_matrix=bt709:in_range={source_range}:out_range=full,"
            "format=yuv444p,split[read][cut];"
            "[cut]scale=in_color_matrix=bt709:in_range=full:out_range=full,"
            "format=gbrp,tpad=start=1:stop=1:start_mode=clone:stop_mode=clone,"
            "tmedian=radius=1,format=rgb24,scdet=threshold=8,"
            f"metadata=mode=print:file={filter_path(marks)},nullsink;"
            "[read]shuffleplanes=0:0:0,lut=y='clip((val-202)*255/38,0,255)':u=val:"
            "v='clip((val-240)*255/10,0,255)',"
            "signalstats,metadata=mode=print:file=-:direct=1[out]"
        )
        run([_tool("ffmpeg"), "-nostdin", "-hide_banner", "-loglevel", "error",
             "-copyts", "-noautorotate", "-reinit_filter", "1", "-i", str(source),
             "-filter_complex_threads", "1",
             "-filter_complex", graph, "-map", "[out]", "-fps_mode", "passthrough",
             "-an", "-sn", "-f", "null", "-"], parse, collect=False)
        accept()
        if len(levels) // 3 != total:
            raise ValueError("逐镜调色的分析帧数与源画面不一致。")
        cuts = _without_flashes(_shot_starts(marks, levels, cancel=cancel), levels)
        peaks = _read_headroom(excursions, times, cancel) if inspect_headroom else None
    starts = [0, *cuts, total]
    shots = []
    for start, end in zip(starts, starts[1:]):
        if cancel is not None and cancel.is_set():
            raise InterruptedError("任务已取消。")
        if not 0 <= start < end <= total:
            raise ValueError("逐镜调色的分镜帧序号无效。")
        samples = memoryview(levels)[start * 3:end * 3]
        strength, high_key = _shot_look(samples[0::3], samples[1::3], samples[2::3])
        shot = dict(start=start, length=end - start, strength=strength, high_key=high_key)
        if peaks is not None:
            reserve = _persistent_headroom(memoryview(peaks)[start:end])
            if reserve:
                shot["headroom"] = reserve
        if auto:
            shot["auto_cinema"] = use_cinema(memoryview(candidates)[start:end], samples[2::3],
                                             times[end - 1] - times[start], shot.get("headroom", 0))
        shots.append(shot)
    if progress:
        progress(total, total)
    return shots
