"""Frame-accurate HEVC/HDR rendering and verified Matroska delivery."""

import hashlib
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections import Counter, deque
from fractions import Fraction
from pathlib import Path

import dovi


def _tool(name):
    base = Path(__file__).resolve().parent
    roots = [base, Path(getattr(sys, "_MEIPASS", base)), Path(sys.executable).parent]
    for root in roots:
        for directory in ("bin", "bin/mkvtoolnix", "bin/ffmpeg", "bin/ffmpeg/bin"):
            candidate = root / directory / (name + ".exe")
            if candidate.is_file():
                return str(candidate)
    result = shutil.which(name)
    if not result:
        raise FileNotFoundError(f"缺少 {name}，请重新解压完整安装包。")
    return result


def _check_cancel(cancel):
    if cancel is not None and cancel.is_set():
        raise InterruptedError("任务已取消。")


class _Stage:
    """Map actual tool counters to a small part of export progress."""

    def __init__(self, progress, key, label, unit, start, end):
        self.progress, self.key, self.label, self.unit = progress, key, label, unit
        self.start, self.end = start, end
        self.last, self.last_at = -1, 0
        self.first, self.first_at = None, None

    def __call__(self, current, total):
        if total <= 0:
            return
        current = max(0, min(current, total))
        if current <= self.last:
            return
        now = time.monotonic()
        if self.last >= 0 and current < total and now - self.last_at < 0.4:
            return
        if self.first is None:
            self.first, self.first_at = current, now
        self.last, self.last_at = current, now
        label = self.label
        stage = getattr(self.progress, "stage", None)
        if stage is not None:
            stage(self.key, current, total, self.unit)
        elif self.unit == "frames":
            label += f" · {current:,} / {total:,} 帧"
            if current > self.first and now > self.first_at:
                label += f" · {(current - self.first) / (now - self.first_at):.1f} fps"
        elif self.unit == "bytes":
            label += f" · {current / 1048576:,.1f} / {total / 1048576:,.1f} MiB"
        else:
            label += f" · {100 * current / total:.1f}%"
        self.progress(self.start + (self.end - self.start) * current / total, label)


class _Runner:
    """Read pipes concurrently and stop active tools even when they produce no progress."""

    def __init__(self, cancel):
        self.cancel = cancel
        self.worker_cancel = None

    def __call__(self, args, line_callback=None, *, collect=True, on_poll=None):
        _check_cancel(self.cancel)
        args = [str(item) for item in args]
        if (
            Path(args[0]).name.casefold() in ("ffmpeg", "ffmpeg.exe", "ffprobe", "ffprobe.exe")
            and not Path(args[0]).is_absolute()
        ):
            args[0] = _tool(Path(args[0]).stem)
        worker = "--rtx-worker" in args
        env = None
        if worker and self.worker_cancel:
            env = dict(os.environ, CINEMA_RTX_CANCEL=str(self.worker_cancel))
        process = subprocess.Popen(
            args,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            env=env,
            stderr=subprocess.STDOUT,
            encoding="utf-8",
            errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        messages = queue.Queue()

        def read_output():
            try:
                for line in process.stdout:
                    messages.put(line)
            finally:
                messages.put(None)

        reader = threading.Thread(target=read_output, daemon=True)
        reader.start()
        lines = [] if collect else deque(maxlen=32)
        mkv_warnings = deque(maxlen=8)
        is_mux = Path(args[0]).stem.lower() == "mkvmerge"
        next_poll = 0
        try:
            while True:
                _check_cancel(self.cancel)
                if on_poll is not None and time.monotonic() >= next_poll:
                    on_poll()
                    next_poll = time.monotonic() + 0.5
                try:
                    line = messages.get(timeout=0.2)
                except queue.Empty:
                    continue
                if line is None:
                    break
                lines.append(line)
                if is_mux and ("Warning:" in line or line.startswith("#GUI#warning")):
                    mkv_warnings.append(line)
                if line_callback:
                    line_callback(line.strip())
            code = process.wait()
            result = "".join(lines)
            if code != 0:
                if is_mux and code == 1:
                    # A warning may indicate discarded packets or an unsupported track.
                    detail = "".join(mkv_warnings) or result[-1800:]
                    raise RuntimeError("MKV 封装发出警告，未发布文件。\n" + detail[-1800:])
                raise RuntimeError(f"{Path(args[0]).name} 返回 {code}。\n{result[-2200:]}")
            return result
        finally:
            if process.poll() is None:
                if worker and self.worker_cancel:
                    self.worker_cancel.touch(exist_ok=True)
                    try:
                        process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        pass
                if worker and os.name == "nt" and process.poll() is None:
                    subprocess.run(
                        ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    )
                if process.poll() is None:
                    process.kill()
            process.wait()
            reader.join()
            process.stdout.close()


def _probe(path, run):
    data = json.loads(
        run(
            [
                _tool("ffprobe"),
                "-v",
                "error",
                "-show_streams",
                "-show_format",
                "-show_chapters",
                "-of",
                "json",
                path,
            ]
        )
    )
    videos = [
        s
        for s in data["streams"]
        if s["codec_type"] == "video" and not s.get("disposition", {}).get("attached_pic")
    ]
    if len(videos) == 2:
        import profile7

        pair = profile7.track_pair(path, data, videos)
        if pair:
            data["_p7_bl"], data["_p7_el"] = (s["index"] for s in pair)
    return data


def _chapter_streams(path, data):
    """Only discard a QuickTime text data track explicitly referenced as a chapter track."""
    candidates = [
        s for s in data["streams"]
        if s.get("codec_type") == "data" and s.get("codec_tag_string") == "text" and s.get("id")
    ]
    if not candidates or not data.get("chapters") or "mov" not in data["format"]["format_name"].split(","):
        return []
    from profile7 import _boxes

    candidates_by_id = {int(s["id"], 0): s for s in candidates}
    chapter_ids = set()
    with path.open("rb") as reader:
        regions = [(0, path.stat().st_size)]
        for name in (b"moov", b"trak", b"tref", b"chap"):
            regions = [
                (start, end) for a, b in regions
                for tag, start, end in _boxes(reader, a, b) if tag == name
            ]
        for start, end in regions:
            if (end - start) % 4:
                continue
            reader.seek(start)
            for _ in range(start, end, 4):
                track_id = int.from_bytes(reader.read(4), "big")
                if track_id in candidates_by_id:
                    chapter_ids.add(track_id)
    return [stream for track_id, stream in candidates_by_id.items() if track_id in chapter_ids]


def _video(data):
    videos = [
        s
        for s in data["streams"]
        if s["codec_type"] == "video" and not s.get("disposition", {}).get("attached_pic")
    ]
    if len(videos) != 1:
        if "_p7_bl" in data:
            return next(s for s in videos if s["index"] == data["_p7_bl"])
        raise ValueError("需要恰好一条主视频轨。含多角度、辅助视频轨或无视频轨的文件目前无法处理。")
    return videos[0]


def _ratio(rate):
    try:
        return Fraction(rate)
    except (ValueError, ZeroDivisionError, TypeError):
        return Fraction(0)


def _geometry(w, h, sar):
    if 3840 <= w <= 4096 and h <= 2160 and sar == 1 and not (w % 2 or h % 2):
        return w, h
    ratio = Fraction(w, h) * sar
    if ratio >= Fraction(16, 9):
        return 3840, max(2, 2 * round(1920 / ratio))
    return max(2, 2 * round(1080 * ratio)), 2160


def _describe(path, data):
    v = _video(data)
    w, h = v["width"], v["height"]
    sar = _ratio((v.get("sample_aspect_ratio") or "1:1").replace(":", "/")) or Fraction(1)
    target_w, target_h = _geometry(w, h, sar)
    fps = _ratio(v.get("avg_frame_rate")) or _ratio(v.get("r_frame_rate"))
    info = dict(
        path=str(path),
        width=w,
        height=h,
        target_width=target_w,
        target_height=target_h,
        fps=str(fps),
        profile="",
        dv_profile=None,
        can_keep7=False,
        can_copy8=False,
        el_index=data.get("_p7_el"),
        duration=float(data.get("format", {}).get("duration") or 0),
        source_type="other",
        supported=False,
        reason="",
        assumptions=[],
        audio_count=sum(s["codec_type"] == "audio" for s in data["streams"]),
        subtitle_count=sum(s["codec_type"] == "subtitle" for s in data["streams"]),
    )
    try:
        if fps <= 0:
            raise ValueError("无法读取视频帧率。")
        if w > 4096 or h > 2160:
            raise ValueError("片源已超出 4K 的工作画布。")
        if any(s.get("rotation", 0) % 360 for s in v.get("side_data_list", [])):
            raise ValueError("此文件带旋转矩阵，暂不支持旋转后导出。")
        if any(
            any(s.get("crop_" + side, 0) for side in ("top", "bottom", "left", "right"))
            for s in v.get("side_data_list", [])
            if s.get("side_data_type", "").lower() == "frame cropping"
        ):
            raise ValueError("容器带额外的轨道裁切信息，无法用现有几何参数安全放大。")
        if v.get("field_order") not in (None, "unknown", "progressive"):
            raise ValueError("隔行视频需要先去隔行。")
        signal = next((s for s in data["streams"] if s["index"] == info["el_index"]), v)
        dv = next((s for s in signal.get("side_data_list", []) if "dv_profile" in s), None)
        if dv:
            p, c = dv.get("dv_profile"), dv.get("dv_bl_signal_compatibility_id")
            info["profile"] = f"Dolby Vision Profile {p}" + (f".{c}" if p == 8 else "")
            info["dv_profile"] = p
            if p == 5:
                from profile5 import validate_profile5

                validate_profile5(v)
            elif p == 7:
                import profile7

                profile7.validate_base(
                    v, profile7.config(signal), split=info["el_index"] is not None
                )
                info["profile"] += " 双层"
            else:
                dovi.validate_profile(v)
            if sar != 1:
                raise ValueError("杜比视界片源需使用方形像素，以保持有效画面与元数据一致。")
            info["can_copy8"] = p == 8 and (w, h) == (target_w, target_h)
            info["source_type"] = "dv"
        elif v.get("color_transfer") == "smpte2084":
            if (v.get("codec_name") != "hevc" or v.get("profile") != "Main 10" or v.get("pix_fmt") != "yuv420p10le"
                    or (v.get("color_primaries"), v.get("color_space"), v.get("color_range"))
                    != ("bt2020", "bt2020nc", "tv") or sar != 1
                    or w > 3840):
                raise ValueError("HDR10 需要 HEVC Main 10、方形像素、有限范围 BT.2020/PQ，画宽上限为 3840。")
            info.update(source_type="hdr10", profile="HDR10 · 静态 PQ")
        else:
            p = v.get("pix_fmt", "")
            if p not in {
                "yuv420p",
                "yuv420p10le",
                "p010le",
                "yuvj420p",
                "yuv422p",
                "yuv422p10le",
                "yuv444p",
                "yuv444p10le",
                "yuvj422p",
                "yuvj444p",
                "nv12",
            }:
                raise ValueError(f"暂不支持此像素格式（{p}）。")
            fields = ("color_space", "color_primaries", "color_transfer")
            missing = [key for key in fields if v.get(key) in (None, "unknown", "unspecified")]
            if any(v.get(key) != "bt709" for key in fields if key not in missing):
                raise ValueError(
                    "SDR 转换仅支持 BT.709；已有 HDR10、HLG 或其它色彩空间不能按 SDR 处理。"
                )
            if missing and w < 1280 and h < 720:
                raise ValueError("低分辨率片源缺少完整 BT.709 标记，无法确认色彩空间。")
            if missing:
                info["assumptions"].append("HD 片源缺少部分色彩标记，按 SDR BT.709 解释。")
            info.update(source_type="sdr", profile="SDR BT.709")
        info["supported"] = True
        info["reason"] = " ".join(info["assumptions"])
    except ValueError as exc:
        info["reason"] = str(exc)
    return info


def _inspect(path, data, run, *, sample_layers=True):
    info = _describe(path, data)
    if info["supported"] and info["dv_profile"] == 7:
        info["can_keep7"] = 3840 <= info["width"] <= 4096 and (info["width"], info["height"]) == (
            info["target_width"],
            info["target_height"],
        )
        if sample_layers:
            import profile7

            try:
                with tempfile.TemporaryDirectory(prefix="lf-p7-") as directory:
                    *_, layer, _ = profile7.prepare(
                        path,
                        _video(data)["index"],
                        info["el_index"],
                        Path(directory),
                        info["width"],
                        info["height"],
                        None,
                        _extract,
                        _tool("ffprobe"),
                        run,
                        limit=12,
                    )
                info["profile"] += f" · {layer}"
            except (ValueError, RuntimeError) as exc:
                info.update(supported=False, can_keep7=False, reason=str(exc))
    if info["source_type"] in ("sdr", "hdr10"):
        # Transport streams can retain in-band RPU even when their VUI says BT.709.
        idx = _video(data)["index"]
        records = run(
            [
                _tool("ffprobe"),
                "-v",
                "error",
                "-select_streams",
                str(idx),
                "-read_intervals",
                "%+#32",
                "-show_frames",
                "-show_entries",
                "frame=pts_time:frame_side_data=side_data_type",
                "-of",
                "json",
                path,
            ]
        )
        markers = ("dolby vision", "dovi", "hdr dynamic", "smpte2094", "smpte 2094")
        if info["assumptions"]:
            markers += ("mastering display", "content light")
        if any(key in records.lower() for key in markers):
            info.update(
                supported=False,
                source_type="other",
                profile="",
                assumptions=[],
                reason=("片源已有动态 HDR 元数据，无法按静态 HDR10 制作。"
                        if info["source_type"] == "hdr10" else
                        "帧内 HDR 元数据与 SDR 色彩标记或推定的 BT.709 冲突，请核对片源。"),
            )
        elif info["source_type"] == "hdr10" and "mastering display" not in records.lower() and not any(
            s.get("side_data_type") == "Mastering display metadata" for s in _video(data).get("side_data_list", [])
        ):
            info.update(supported=False, reason="HDR10 原片缺少可读取的 ST 2086 母版显示信息。")
    return info


def inspect_source(path, *, cancel=None):
    path = Path(path).resolve(strict=True)
    run = _Runner(cancel)
    return _inspect(path, _probe(path, run), run)


def _times(path, index, run, *, coded_order=False, with_end=False, on_position=None, on_frame=None, pgs=None):
    # Packet PTS is in presentation order after sorting, even with B-frame reordering.
    times = []
    last_pts = end = None

    def video_packet(fields):
        nonlocal last_pts, end
        token = fields[0].strip()
        if not token:
            return
        if not re.fullmatch(r"-?\d+(\.\d+)?", token):
            raise ValueError("视频缺少可还原的显示时间戳。")
        pts = round(float(token) * 1_000_000)
        times.append(pts)
        if on_frame is not None:
            on_frame(len(times))
        if on_position is not None:
            pos_index = 2 if with_end else 1
            if len(fields) > pos_index and fields[pos_index].isdigit():
                on_position(int(fields[pos_index]))
        if with_end and (last_pts is None or pts > last_pts):
            last_pts, end = pts, None
            if len(fields) > 1 and re.fullmatch(r"\d+(\.\d+)?", fields[1]):
                duration = round(float(fields[1]) * 1_000_000)
                if duration > 0:
                    end = pts + duration

    if pgs:
        # Collect the subtitle timeline during the existing video scan. No separate
        # pass per language (or per file) is needed, even for a late first cue.
        def video_and_subtitles(line):
            stream, _, tail = line.partition(",")
            if not stream.isdigit():
                # ffprobe may put video packet side-data on a continuation line.
                if not stream.strip() or stream.startswith("SHA256:"):
                    return
                raise ValueError("视频缺少可还原的显示时间戳。")
            track = int(stream)
            if track != index and track not in pgs:
                return
            fields = tail.split(",")
            if track == index:
                selected = [fields[0], fields[2], fields[4]] if with_end else [fields[0], fields[4]]
                video_packet(selected)
            else:
                # An empty side-data section can add a trailing CSV column.
                if (len(fields) < 6 or fields[0] == "N/A" or
                        not re.fullmatch(r"SHA256:[0-9a-fA-F]{64}", fields[5])):
                    raise ValueError("PGS 字幕数据包缺少有效的时间戳或内容校验值。")
                pts, dts, duration = (None if value == "N/A" else round(float(value) * 1_000_000)
                                      for value in fields[:3])
                pgs[track].append((pts, dts, duration, bytes.fromhex(fields[5][7:])))

        run([_tool("ffprobe"), "-v", "error", "-show_packets", "-show_data_hash", "sha256",
             "-show_entries", "packet=stream_index,pts_time,dts_time,duration_time,size,pos,data_hash:packet_side_data=",
             "-of", "csv=p=0", path], video_and_subtitles, collect=False)
    else:
        entries = "packet=pts_time" + (",duration_time" if with_end else "")
        if on_position is not None:
            entries += ",pos"
        run([_tool("ffprobe"), "-v", "error", "-select_streams", str(index), "-show_packets",
             "-show_entries", entries + ":packet_side_data=", "-of", "csv=p=0", path],
            lambda line: video_packet(line.split(",")), collect=False)
    if not times or len(set(times)) != len(times):
        raise ValueError("视频显示时间戳为空或重复，无法保证逐帧同步。")
    if not coded_order:
        times.sort()
    return (times, end) if with_end else times


def _static_hdr(path, video, run):
    keys = "side_data_type,red_x,red_y,green_x,green_y,blue_x,blue_y,white_point_x,white_point_y,min_luminance,max_luminance,max_content,max_average"
    data = json.loads(
        run(
            [
                _tool("ffprobe"),
                "-v",
                "error",
                "-select_streams",
                str(video["index"]),
                "-read_intervals",
                "%+#24",
                "-show_frames",
                "-show_entries",
                "frame=pts_time:frame_side_data=" + keys,
                "-of",
                "json",
                path,
            ]
        )
    )
    items = list(video.get("side_data_list", []))
    for frame in data.get("frames", []):
        items.extend(frame.get("side_data_list", []))
    master, cll = None, None
    for item in items:
        if item.get("side_data_type") == "Mastering display metadata" and "red_x" in item:

            def point(prefix):
                return ",".join(
                    str(round(Fraction(item[prefix + axis]) * 50000)) for axis in ("_x", "_y")
                )

            luminance = ",".join(
                str(round(Fraction(item[x + "_luminance"]) * 10000)) for x in ("max", "min")
            )
            master = f"G({point('green')})B({point('blue')})R({point('red')})WP({point('white_point')})L({luminance})"
        if item.get("side_data_type") == "Content light level metadata" and "max_content" in item:
            cll = f"{item['max_content']},{item['max_average']}"
    return master, cll


def _ffmpeg_frames(on_frame):
    def report(line):
        key, _, value = line.partition("=")
        if key == "frame" and value.strip().isdigit():
            on_frame(int(value))
    return report


def _extract(source, video_index, dest, run, *, limit=None, downstream=None, on_frame=None):
    args = [
        _tool("ffmpeg"),
        "-nostdin",
        "-v",
        "error",
        "-i",
        source,
        "-map",
        f"0:{video_index}",
        *(["-frames:v", str(limit)] if limit else []),
        "-c:v",
        "copy",
        "-bsf:v",
        "hevc_mp4toannexb",
        "-f",
        "hevc",
        dest,
    ]
    if downstream is None:
        if on_frame is None:
            run(args)
        else:
            args[1:1] = ["-nostats", "-progress", "pipe:1"]
            run(args, _ffmpeg_frames(on_frame), collect=False)
    else:
        import profile7

        profile7.run_ffmpeg_pipe(args, downstream, run, Path(downstream[-1]).parent, on_frame=on_frame)


def _vbv_limit(info):
    # Dolby's High tier limits are 50/70/130 Mbit/s for levels 1–2/3–5/6–9.
    # Leave room for RPU NALs and rounding in VFR/container rate reporting.
    w, h = info["target_width"], info["target_height"]
    pixels_per_second = float(_ratio(info["fps"])) * w * h
    if w <= 1280 and pixels_per_second <= 27_648_000 * 1.01:
        return 45_000
    if pixels_per_second <= 124_416_000 * 1.01:
        return 65_000
    return 120_000


def _check_dv_tier(path, video, config, cap, run):
    # Matroska embeds the HEVC decoder configuration record (hvcC). Its second
    # byte includes general_tier_flag, directly comparable to VPS/SPS trace_headers.
    record = json.loads(
        run(
            [
                _tool("ffprobe"),
                "-v",
                "error",
                "-select_streams",
                str(video["index"]),
                "-show_entries",
                "stream=extradata",
                "-show_data",
                "-of",
                "json",
                path,
            ]
        )
    )
    header = re.search(r"00000000:\s+01([0-9a-fA-F]{2})", record["streams"][0].get("extradata", ""))
    if not header:
        raise ValueError("无法校验 HEVC 头中的档位。")
    high = bool(int(header[1], 16) & 32)
    # (maximum width, Main tier Mbps, High tier Mbps), Dolby Online Delivery Kit.
    groups = [
        (1280, 20, 50),
        (1280, 20, 50),
        (1920, 20, 70),
        (2560, 20, 70),
        (3840, 20, 70),
        (3840, 25, 130),
        (3840, 25, 130),
        (3840, 40, 130),
        (3840, 40, 130),
        (3840, 60, 240),
        (7680, 60, 240),
        (7680, 120, 480),
        (7680, 240, 800),
    ]
    level = int(config["dv_level"])
    if not 1 <= level <= len(groups):
        raise ValueError(f"无法核验杜比视界等级 {level}。")
    width, main_rate, high_rate = groups[level - 1]
    if video["width"] > width or (cap is not None and cap > (high_rate if high else main_rate) * 1000):
        raise ValueError("输出信令的级别或档位与实际画宽、编码码率约束不符。")
    return ("High" if high else "Main"), level


def _gpu_command(source, output, info, mode, target, rpu, master, cll, *, fel_input=None):
    from ai import nvenc_command

    cap = _vbv_limit(info)
    return nvenc_command(
        source, output, info["target_width"], info["target_height"],
        rpu if target == "preserve" else None,
        mode="rtx" if mode == "rtx" else "nis",
        quality=15 if mode == "rtx" else 18,
        max_bitrate=cap if mode == "rtx" else min(60000, cap),
        mastering=master, max_cll=cll,
        hw_decode=min(info["width"], info["height"]) >= 128,
        sdr_to_hdr=info.get("source_type") == "sdr",
        sdr_mapping=info.get("sdr_mapping", "expand"),
        scene_shader=info.get("scene_shader"),
        fel_input=fel_input,
        compressed_source=info.get("compressed_source", False),
        speed=info.get("speed", "quality"),
    )


def _software_command(source, out, info, video, mode, target, master, cll):
    w, h = info["target_width"], info["target_height"]
    if target == "preserve" and info["dv_profile"] == 5:
        from profile5 import decode_profile5_filter

        init_args, vf = decode_profile5_filter(
            info["width"], info["height"], w, h, neural=mode == "neural"
        )
    elif (target == "preserve" or info.get("source_type") == "hdr10") and mode == "reference":
        dither = "error_diffusion" if info.get("enhancement_layer") == "FEL" else "none"
        init_args, vf = [], f"zscale=w={w}:h={h}:filter=spline36:dither={dither},format=yuv420p10le"
    elif target == "preserve" or info.get("source_type") == "hdr10":
        from neural import ff_filter

        init_args, vf = ff_filter(info["width"], info["height"], w, h)
    else:
        from hdr import sdr_filter

        init_args, vf = sdr_filter(info["width"], info["height"], w, h, mode,
                                   mapping=info.get("sdr_mapping", "expand"), scene_shader=info.get("scene_shader"))
        full = video.get("color_range") == "pc" or video["pix_fmt"].startswith("yuvj")
        vf = (
            "setparams=range="
            + ("full" if full else "limited")
            + ":colorspace=bt709:color_primaries=bt709:color_trc=bt709,"
            + vf
        )
    if info.get("source_type") == "hdr10":
        vf += ",sidedata=mode=delete:type=CONTENT_LIGHT_LEVEL"
    params = [
        "hdr10=1",
        "hdr10-opt=1",
        "repeat-headers=1",
        "aud=1",
        "colorprim=bt2020",
        "transfer=smpte2084",
        "colormatrix=bt2020nc",
        "range=limited",
    ]
    cap = _vbv_limit(info)
    params += [f"vbv-maxrate={cap}", f"vbv-bufsize={cap}", "high-tier=1"]
    if master:
        params.append("master-display=" + master)
    if cll:
        params.append("max-cll=" + cll)
    return [
        _tool("ffmpeg"),
        "-hide_banner",
        "-nostdin",
        "-v",
        "error",
        "-progress",
        "pipe:1",
        *init_args,
        "-noautorotate",
        "-i",
        source,
        "-map",
        f"0:{video['index']}",
        "-an",
        "-sn",
        "-dn",
        "-vf",
        vf + ",setsar=1",
        "-c:v",
        "libx265",
        "-preset",
        "slow",
        "-crf",
        "14" if mode == "neural" or info["dv_profile"] == 7 else "16",
        "-dolbyvision",
        "0",
        "-x265-params",
        ":".join(params),
        "-pix_fmt",
        "yuv420p10le",
        "-color_range",
        "tv",
        "-colorspace",
        "bt2020nc",
        "-color_primaries",
        "bt2020",
        "-color_trc",
        "smpte2084",
        "-fps_mode",
        "passthrough",
        "-f",
        "hevc",
        out,
    ]


def _mkv_inventory(path, run):
    return json.loads(run([_tool("mkvmerge"), "-J", path]))


def _audio_starts(path, data, run):
    result = []
    for s in data["streams"]:
        if s["codec_type"] == "audio":
            records = run(
                [
                    _tool("ffprobe"),
                    "-v",
                    "error",
                    "-select_streams",
                    str(s["index"]),
                    "-read_intervals",
                    "%+#8",
                    "-show_packets",
                    "-show_data_hash",
                    "sha256",
                    "-show_entries",
                    "packet=pts_time,data_hash:packet_side_data=skip_samples",
                    "-of",
                    "json",
                    path,
                ]
            )
            result.append(json.loads(records).get("packets", []))
    return result


def _hash_container(path, cancel, progress, *, copy_to=None, bounds=None):
    """Hash the copy as it is written, then independently hash the file read back."""
    digest = hashlib.sha256()
    size = path.stat().st_size
    if copy_to is not None:
        stage = _Stage(progress, "copy", "复制并计算原始 MKV 校验值", "bytes", *(bounds or (84, 90)))
    else:
        stage = _Stage(progress, "readback", "逐字节回读并核对 MKV 校验值", "bytes", *(bounds or (95, 99)))
    stage(0, size)
    with path.open("rb") as incoming:
        while block := incoming.read(8 * 1024 * 1024):
            _check_cancel(cancel)
            if copy_to is not None:
                copy_to.write(block)
            digest.update(block)
            stage(incoming.tell(), size)
    return digest.hexdigest()


def _chapter_xml(path, chapters, language):
    from xml.etree import ElementTree as ET

    def timecode(value, time_base):
        ns = round(int(value) * Fraction(time_base) * 1_000_000_000)
        sec, frac = divmod(ns, 1_000_000_000)
        return f"{sec // 3600:02}:{sec // 60 % 60:02}:{sec % 60:02}.{frac:09}"

    root = ET.Element("Chapters")
    edition = ET.SubElement(root, "EditionEntry")
    for ch in chapters:
        atom = ET.SubElement(edition, "ChapterAtom")
        for key, tag in (("start", "ChapterTimeStart"), ("end", "ChapterTimeEnd")):
            ET.SubElement(atom, tag).text = timecode(ch[key], ch["time_base"])
        display = ET.SubElement(atom, "ChapterDisplay")
        ET.SubElement(display, "ChapterString").text = ch.get("tags", {}).get("title", "")
        ET.SubElement(display, "ChapterLanguage").text = language
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


def _mux(source, encoded, out, info, timing, original, run, audio_starts, chapter_file=None, *, progress=None):
    video = [t for t in original["tracks"] if t["type"] == "video"][info.get("base_ordinal", 0)]
    props = video["properties"]
    fps = _ratio(info["fps"])
    args = [_tool("mkvmerge"), "--timestamp-scale", "100000", "-o", out]
    if original["container"]["properties"].get("title"):
        args += ["--title", original["container"]["properties"]["title"]]
    args += [
        "--timestamps",
        "0:" + str(timing),
        "--default-duration",
        f"0:{fps.numerator}/{fps.denominator}fps",
        "--display-dimensions",
        f"0:{info['target_width']}x{info['target_height']}",
        "--language",
        "0:" + (props.get("language_ietf") or props.get("language") or "und"),
    ]
    if props.get("track_name"):
        args += ["--track-name", "0:" + props["track_name"]]
    for flag, option in (
        ("default_track", "default-track-flag"),
        ("forced_track", "forced-display-flag"),
        ("flag_original", "original-flag"),
        ("flag_commentary", "commentary-flag"),
        ("flag_visual_impaired", "visual-impaired-flag"),
    ):
        if flag in props:
            args += ["--" + option, "0:" + str(int(props[flag]))]
    if chapter_file:
        args += ["--chapters", chapter_file]
    args += [encoded, "--no-video"]
    if chapter_file:
        args.append("--no-chapters")
    if original["container"].get("type") == "MP4/QuickTime":
        # Matroska has no AAC edit list. mkvmerge keeps the priming access unit
        # and shifts the next audible packet forward unless explicitly corrected.
        tracks = [t for t in original["tracks"] if t["type"] == "audio"]
        earliest = 0.0
        for track, packets in zip(tracks, audio_starts):
            if track["codec"] == "AAC" and packets:
                first = packets[0]
                pts = float(first.get("pts_time", 0))
                if pts < 0 and any(
                    s.get("skip_samples", 0) for s in first.get("side_data_list", [])
                ):
                    earliest = min(earliest, pts)
        if earliest < 0:
            # The MP4 reader shifts all selected tracks onto the same non-negative
            # timeline, including delayed commentary and subtitles.
            for track in original["tracks"]:
                if track["type"] in {"audio", "subtitles"}:
                    args += ["--sync", f"{track['id']}:{round(earliest * 1000)}"]
    args.append(source)
    if progress is None:
        run(args)
    else:
        args.insert(1, "--gui-mode")
        def status(line):
            match = re.fullmatch(r"#GUI#progress (\d+)%", line)
            if match:
                progress(int(match[1]), 100)
        run(args, status, collect=False)


def _text_cues(path, indexes, run):
    """Use FFmpeg's decoded subtitle events; tx3g also contains empty clearing samples."""
    rows = [[] for _ in indexes]
    mapping = [part for index in indexes for part in ("-map", f"0:{index}")]

    def receive(line):
        if not line or line.startswith("#"):
            return
        track, _, pts, duration, _, _ = line.split(",", 5)
        rows[int(track)].append((int(pts), int(duration)))

    run([_tool("ffmpeg"), "-v", "error", "-nostdin", "-copyts", "-i", path, *mapping,
         "-c:s", "srt", "-enc_time_base:s", "1:1000000", "-f", "framehash", "pipe:1"],
        receive, collect=False)
    return rows


def _pgs_tracks(data):
    return {s["index"]: [] for s in data["streams"] if s.get("codec_name") == "hdmv_pgs_subtitle"}


def _same_pgs(before, after):
    # Our MKV timecode scale is 100 microseconds. Keep exact times when the
    # original already falls on that grid; otherwise allow container rounding.
    def same_time(a, b):
        return a == b or (a is not None and b is not None and a % 100 != 0 and abs(a - b) < 100)

    return len(before) == len(after) and all(
        a[3] == b[3] and all(same_time(x, y) for x, y in zip(a[:3], b[:3]))
        for a, b in zip(before, after)
    )


def _verify_other(source, output, original, result, chapter_streams, explicit_chapter_ends,
                  source_path, output_path, run, *, pgs_packets=None):
    def tracks(data):
        return [t for t in data["tracks"] if t["type"] != "video"]

    src, dst = tracks(original), tracks(result)
    if len(src) != len(dst):
        raise ValueError("封装后副轨数量变化。")
    for s, d in zip(src, dst):
        if (s["type"], s["properties"].get("codec_id") or s["codec"]) != (
            d["type"],
            d["properties"].get("codec_id") or d["codec"],
        ):
            # MP4 does not always expose Matroska CodecID. Verify normalized code names below.
            if s["type"] != d["type"]:
                raise ValueError("音轨或字幕轨顺序发生变化。")
        for key in (
            "language",
            "language_ietf",
            "track_name",
            "default_track",
            "forced_track",
            "flag_hearing_impaired",
            "flag_visual_impaired",
            "flag_text_descriptions",
            "flag_original",
            "flag_commentary",
        ):
            a = s["properties"].get(key)
            if a not in (None, "", "und") and d["properties"].get(key) != a:
                raise ValueError(f"副轨属性未保留：{key}。")

    def nonvideo(data):
        return [s for s in data["streams"] if s["codec_type"] in ("audio", "subtitle", "data")]

    a = [s for s in nonvideo(source) if s not in chapter_streams]
    b = nonvideo(output)
    if len(a) != len(b):
        raise ValueError("封装后音频、字幕或数据轨数量变化。")
    converted = []
    for s, d in zip(a, b):
        sc, dc = s.get("codec_name"), d.get("codec_name")
        if sc != dc and (sc, dc) not in {("mov_text", "subrip"), ("text", "subrip")}:
            raise ValueError(f"副轨编码变化：{sc} → {dc}。")
        if s["codec_type"] == "audio" and (s.get("channels"), s.get("sample_rate")) != (
            d.get("channels"),
            d.get("sample_rate"),
        ):
            raise ValueError("音轨声道数或采样率发生变化。")
        if (sc, dc) == ("mov_text", "subrip"):
            converted.append((s["index"], d["index"]))
        elif sc == "hdmv_pgs_subtitle" and pgs_packets is not None:
            # Sparse subtitle stream.start_time depends on probing depth. Verify the
            # displayed and cleared subtitles themselves, including their order.
            before, after = pgs_packets
            if not _same_pgs(before[s["index"]], after[d["index"]]):
                raise ValueError("PGS 字幕的内容、显示时间、时长或顺序发生了变化。")
        elif s["codec_type"] == "subtitle" and "start_time" in s and "start_time" in d:
            if abs(float(s["start_time"]) - float(d["start_time"])) > 0.003:
                raise ValueError("字幕轨的起始时间发生了变化。")
    if converted:
        src_idx, dst_idx = zip(*converted)
        for before, after in zip(_text_cues(source_path, src_idx, run),
                                 _text_cues(output_path, dst_idx, run)):
            if len(before) != len(after) or any(abs(p - q) > 3000 or abs(d - e) > 3000
                                              for (p, d), (q, e) in zip(before, after)):
                raise ValueError("转换后字幕的显示时间、时长或条数发生了变化。")

    def attachments(data):
        return Counter((t.get("file_name"), t.get("size")) for t in data.get("attachments", []))

    if attachments(original) != attachments(result):
        raise ValueError("附件名称或字节数校验失败。")
    src_ch, dst_ch = source.get("chapters", []), output.get("chapters", [])
    if len(src_ch) != len(dst_ch):
        raise ValueError("章节数量校验失败。")
    for x, y in zip(src_ch, dst_ch):
        if abs(float(x["start_time"]) - float(y["start_time"])) > 0.003:
            raise ValueError("章节时间校验失败。")
        if explicit_chapter_ends and abs(float(x["end_time"]) - float(y["end_time"])) > 0.003:
            raise ValueError("章节结束时间校验失败。")
        if x.get("tags", {}).get("title") != y.get("tags", {}).get("title"):
            raise ValueError("章节名称校验失败。")


def render(path, destination, mode, progress, cancel, *, target="preserve", sdr_mapping="expand",
           active_area=None, speed="quality"):
    source = Path(path).resolve(strict=True)
    before = source.stat()
    dest = Path(destination).resolve()
    if dest == source or dest.exists():
        raise FileExistsError("目标文件已存在，请选择新的输出文件名。")
    if dest.suffix.lower() != ".mkv" or not dest.parent.is_dir():
        raise ValueError("请选择现有文件夹中的 .mkv 输出文件。")
    if speed not in {"quality", "fast"}:
        raise ValueError("未知的 RTX 速度模式。")
    if speed == "fast" and (mode not in {"rtx", "rtx_compressed"} or target == "keep7"):
        raise ValueError("并行加速仅用于 RTX 超分导出。")
    if mode not in {"quality", "reference", "neural", "rtx", "rtx_compressed", "copy"} or target not in {
        "preserve",
        "keep7",
        "hdr10",
        "newdv",
    }:
        raise ValueError("不支持的处理模式或输出格式。")
    run = _Runner(cancel)
    progress(0, "检查片源与轨道")
    data = _probe(source, run)
    info = _inspect(source, data, run, sample_layers=False)
    if not info["supported"]:
        raise ValueError(info["reason"])
    if active_area is not None and target not in {"hdr10", "newdv"}:
        raise ValueError("分析边距仅用于新制作的 HDR10 或杜比视界。")
    canvas = info["target_width"], info["target_height"]
    offsets = dovi.validate_active_area(active_area, *canvas)
    area_options = {"active_area": offsets, "canvas": canvas} if active_area is not None else {}
    is_hdr10 = info["source_type"] == "hdr10"
    same_size = (info["width"], info["height"]) == canvas
    if mode == "rtx_compressed":
        compatible = (info["dv_profile"] == 8 and target == "preserve") or (is_hdr10 and target in {"hdr10", "newdv"})
        if not compatible or same_size:
            raise ValueError("压缩片源修复仅适用于需要放大的静态 HDR10 或 Dolby Vision Profile 8.1。")
        info["compressed_source"] = True
        mode = "rtx"
    if sdr_mapping == "auto" and info["source_type"] != "sdr":
        sdr_mapping = "expand"
    if sdr_mapping not in {"expand", "reference", "scene", "cinema", "auto"} or (sdr_mapping != "expand" and info["source_type"] != "sdr"):
        raise ValueError("亮度映射仅适用于 SDR 片源，请选择有效的映射方式。")
    if sdr_mapping == "cinema":
        from cinema_lut import require_model

        require_model()
    info["sdr_mapping"] = sdr_mapping
    info["speed"] = speed
    native = target == "keep7"
    author_native = is_hdr10 and same_size
    if author_native and (mode != "copy" or target != "newdv"):
        raise ValueError("原生 HDR10 请选择生成杜比视界并保留原始编码画面。")
    copy_video = mode == "copy" and not author_native
    if copy_video and (target != "preserve" or not info["can_copy8"]):
        raise ValueError("原码流无损封装需要尺寸已匹配的 Profile 8.1 片源。")
    if native and not info["can_keep7"]:
        raise ValueError("双层原样保留仅适用于原生 4K Profile 7 片源。")
    if native:
        mode = "reference"
    if (target in {"preserve", "keep7"}) != (info["source_type"] == "dv"):
        raise ValueError(
            "保留杜比需要 Profile 5、7 或 8.1。生成 HDR10 或新杜比视界请选择 BT.709 SDR 或静态 HDR10 片源。"
        )
    if mode == "quality" and info["dv_profile"] in {5, 7}:
        raise ValueError(
            "此杜比视界配置需要先处理色彩或增强层，请选择 RTX AI、FSRCNNX 或参考模式。"
        )
    if mode == "quality" and target != "preserve":
        raise ValueError(
            "NIS 模式仅处理现有 Profile 8.1 片源；SDR 片源可选择 RTX AI、FSRCNNX 或参考模式。"
        )
    if (
        mode == "neural"
        and min(info["target_width"] / info["width"], info["target_height"] / info["height"])
        <= 1.30
    ):
        raise ValueError("FSRCNNX 需要超过 1.30 倍的放大；请改用参考模式或可用的 RTX AI。")
    if mode == "rtx" and (info["width"], info["height"]) == (
        info["target_width"],
        info["target_height"],
    ):
        raise ValueError("此片源已达到输出尺寸，请使用参考模式在原分辨率处理。")
    video = _video(data)
    original = _mkv_inventory(source, run)
    copy_container = (
        (native or copy_video) and info["el_index"] is None and original["container"].get("type") == "Matroska"
    )
    measure_light = info["source_type"] == "sdr" or (is_hdr10 and not author_native)
    # The percentage describes the work in this route. Optional scans get their
    # own range only when needed; copying a native track has no encoding phase.
    scan_end = 5 if native or copy_video or author_native else 3
    prep_end = 20 if copy_container else 42 if native or copy_video or author_native else 8
    encode_start = scan_end if info["source_type"] == "sdr" else prep_end
    encode_end = 32 if measure_light else 85
    mux_start = prep_end if native or copy_video else 90
    verify_start = 58 if copy_container else 78 if native or copy_video else 94
    timeline_end = 65 if copy_container else 85 if native or copy_video else 96
    if not original.get("container", {}).get("supported") or sum(
        t["type"] == "video" for t in original.get("tracks", [])
    ) != (2 if info["el_index"] is not None else 1):
        raise ValueError("封装工具无法完整读取此文件的单一主视频轨。")
    if any(
        t.get("disposition", {}).get("attached_pic") for t in data["streams"]
    ) and not original.get("attachments"):
        raise ValueError("此容器封面无法按原附件保留。")
    with tempfile.TemporaryDirectory(dir=dest.parent, prefix=".hdr-render-") as folder:
        scratch = Path(folder)
        run.worker_cancel = scratch / "cancel.rtx"
        split_timeline = info["el_index"] is not None
        base_end = (1 + scan_end) / 2 if split_timeline else scan_end
        scan = _Stage(progress, "scan", "读取原片逐帧时间戳", "bytes", 1, base_end)
        scan(0, before.st_size)
        input_pgs = {} if copy_container else _pgs_tracks(data)
        coded_times, video_end = _times(source, video["index"], run, coded_order=True, with_end=True,
                                        on_position=lambda n: scan(n, before.st_size), pgs=input_pgs)
        if info["el_index"] is not None:
            el_scan = _Stage(progress, "scan_el", "核对增强层逐帧时间戳", "bytes", base_end, scan_end)
            el_scan(0, before.st_size)
            el_times = _times(source, info["el_index"], run, coded_order=True,
                              on_position=lambda n: el_scan(n, before.st_size))
            if len(coded_times) != len(el_times) or any(
                abs(a - b) > 2 for a, b in zip(coded_times, el_times)
            ):
                raise ValueError("双轨 BL/EL 的逐帧顺序或时间戳不匹配。")
            video_tracks = [
                s["index"]
                for s in data["streams"]
                if s["codec_type"] == "video" and not s.get("disposition", {}).get("attached_pic")
            ]
            info["base_ordinal"] = video_tracks.index(video["index"])
            del el_times
        coded_times.sort()
        times = coded_times
        timing = scratch / "timecodes.txt"
        if not copy_container:
            with timing.open("w", encoding="ascii", newline="\n") as output_times:
                output_times.write("# timestamp format v2\n")
                output_times.writelines(f"{t / 1000:.3f}\n" for t in times)
                if video_end is not None:
                    # mkvmerge uses the extra boundary to set the final frame's BlockDuration.
                    output_times.write(f"{video_end / 1000:.3f}\n")
        rpu = scratch / "scaled.rpu"
        encoded = scratch / "encoded.hevc"
        picture, picture_video = source, video
        streamed = False
        signatures = None
        picture_signature = None
        base_layer_copied = False
        generation_levels = {}
        measured_levels = None
        frame_total = len(times)
        if sdr_mapping in {"scene", "cinema", "auto"}:
            from hdr import write_scene_shader
            from shot_grade import analyze_shots

            encode_start = prep_end
            automatic = sdr_mapping == "auto"
            scan_grade_end = (scan_end + encode_start) / 2 if sdr_mapping != "scene" else encode_start
            grade = _Stage(progress, "grade", "分析 SDR 镜头与高光分布", "frames", scan_end, scan_grade_end)
            grade(0, frame_total)
            source_offsets = tuple(round(offset * dimension / output) for offset, dimension, output in zip(
                offsets, (info["width"], info["width"], info["height"], info["height"]),
                (canvas[0], canvas[0], canvas[1], canvas[1])))
            shots = analyze_shots(source, video, times, scratch, run, progress=grade, active_area=source_offsets, auto=automatic)
            use_model = sdr_mapping == "cinema"
            if automatic:
                from cinema_lut import ensure_auto_model

                candidates = sum(shot["auto_cinema"] for shot in shots)
                use_model = candidates > 0 and ensure_auto_model()
                model_shots = candidates if use_model else 0
            if use_model:
                from cinema_lut import prepare_grades
                from cinema_shader import write_cinema_shader

                learn = _Stage(progress, "cinema_lut", "计算逐镜电影调色模型", "frames", scan_grade_end, encode_start)
                basis, records = prepare_grades(source, video, times, shots, scratch, run, progress=learn,
                                               active_area=source_offsets, selected_only=automatic)
                info["scene_shader"] = write_cinema_shader(scratch, basis, records, neural=mode == "neural", cancel=cancel)
            else:
                info["scene_shader"] = write_scene_shader(scratch, shots, neural=mode == "neural")
        prep_mid = (scan_end + prep_end) / 2
        prep = _Stage(progress, "extract", "提取原始视频轨", "frames", scan_end, prep_mid)

        def extract_original(src, index, out, runner, **options):
            # A split EL is a second stream; use a distinct stage and numerator.
            if split_timeline:
                is_el = index == info["el_index"]
                middle = (scan_end + prep_mid) / 2
                meter = _Stage(progress, "extract_el" if is_el else "extract",
                               "提取增强层视频轨" if is_el else "提取基础层视频轨", "frames",
                               middle if is_el else scan_end, prep_mid if is_el else middle)
            else:
                meter = prep
            meter(0, frame_total)
            return _extract(src, index, out, runner, on_frame=lambda n: meter(n, frame_total), **options)

        if is_hdr10:
            from hdr10 import NativeMetadata
            from profile7 import signature

            original_es = encoded if author_native else scratch / "original-hdr10.hevc"
            extract_original(source, video["index"], original_es, run)
            metadata = NativeMetadata()
            original_check = _Stage(progress, "source_hash", "核对原始 HDR10 编码画面与母版信息",
                                    "bytes", prep_mid, prep_end)
            source_signature = signature(original_es, cancel, inspect_unit=metadata.observe, progress=original_check)
            if source_signature[0] != len(times):
                raise ValueError("HDR10 基础层图像数与显示时间戳数不符。")
            generation_levels = metadata.level6()
            master, cll = _static_hdr(source, video, run)
            if author_native:
                picture_signature = source_signature
                base_layer_copied = streamed = True
            else:
                original_es.unlink()
                # Source CLL/FALL describe the original pixels. A resize can
                # change both. Do not advertise them as measured output values.
                cll = None
        elif info["dv_profile"] == 7:
            import profile7

            base, el, original_rpu, layer, _ = profile7.prepare(
                source,
                video["index"],
                info["el_index"],
                scratch,
                info["width"],
                info["height"],
                len(times),
                extract_original,
                _tool("ffprobe"),
                run,
            )
            info["enhancement_layer"] = layer
            master, cll = _static_hdr(source, video, run)
            if native:
                middle = (prep_mid + prep_end) / 2
                signatures = (
                    profile7.signature(base, cancel, progress=_Stage(
                        progress, "base_hash", "核对原始基础层", "bytes", prep_mid, middle)),
                    profile7.signature(el, cancel, progress=_Stage(
                        progress, "el_hash", "核对原始增强层", "bytes", middle, prep_end)),
                )
                if any(frames != len(times) for frames, _ in signatures):
                    raise ValueError("双层编码图像数与原片显示帧数不符。")
                if not copy_container:
                    dovi._run(run, "mux", "--bl", base, "--el", el, "-o", encoded)
                rpu = original_rpu
            else:
                if layer not in ("FEL", "MEL"):
                    raise ValueError("混合 FEL/MEL 请使用原生 4K 双层原样保留模式。")
                dovi.prepare_rpu(
                    el,
                    rpu,
                    info["width"],
                    info["height"],
                    info["target_width"],
                    info["target_height"],
                    scratch,
                    run,
                    profile7_layer=layer,
                    original_rpu=original_rpu,
                )
                if layer == "FEL":
                    from felbake import bake
                    from profile5 import static_from_rpu

                    dv_master, dv_cll = static_from_rpu(rpu, scratch, run, source_master=master)
                    master, cll = dv_master or master, dv_cll or cll
                    fel = _Stage(progress, "encode", "逐帧合成杜比增强层并编码", "frames", encode_start, encode_end)
                    fel(0, frame_total)
                    streamed = True
                    if mode == "rtx":
                        command = _gpu_command(
                            "pipe:0", encoded, info, mode, target, rpu, master, cll,
                            fel_input=(info["width"], info["height"], info["fps"]),
                        )
                    else:
                        command = _software_command(
                            "pipe:0", encoded, info, {"index": 0}, mode, target, master, cll
                        )
                    bake(
                        base,
                        el,
                        original_rpu,
                        scratch,
                        info["fps"],
                        info["width"],
                        info["height"],
                        len(times),
                        run,
                        lambda _, message: progress(encode_start, message) if fel.last <= 0 else None,
                        encoder_command=command,
                        on_frame=lambda n: fel(n, frame_total),
                    )
                elif layer == "MEL" and mode == "reference" and info["can_keep7"]:
                    # MEL has no picture residual. Mode 2 above keeps its mapping
                    # and changes the RPU to P8.1; the HDR10 base stays bit exact.
                    picture_signature = profile7.signature(base, cancel, ignore_rpu=True, progress=_Stage(
                        progress, "base_hash", "核对 MEL 原始基础层", "bytes", prep_mid, encode_end))
                    if picture_signature[0] != len(times):
                        raise ValueError("MEL 基础层的编码图像数与原片显示帧数不符。")
                    encoded = base
                    base_layer_copied = True
                    streamed = True
                    progress(encode_end, "已保留 MEL 原始基础层，正在转换逐帧动态元数据")
                else:
                    picture = scratch / "base.mkv"
                    run(
                        [
                            _tool("mkvmerge"),
                            "-q",
                            "-o",
                            picture,
                            "--default-duration",
                            "0:" + info["fps"] + "fps",
                            base,
                        ]
                    )
                if not streamed:
                    picture_video = _video(_probe(picture, run))
            if base != encoded:
                base.unlink()
            el.unlink()
        elif target == "preserve":
            progress(scan_end, "提取杜比视界元数据并换算有效画面尺寸")
            # dovi_tool reads single-track Matroska directly. A native MKV
            # therefore needs no temporary copy of the full elementary stream.
            source_es = source if copy_container else scratch / "original.hevc"
            if source_es != source:
                extract_original(source, video["index"], source_es, run)
            if info["dv_profile"] == 5:
                from profile5 import prepare_profile5_rpu

                prepare = prepare_profile5_rpu
            else:
                prepare = dovi.prepare_rpu
            meta = prepare(
                source_es,
                rpu,
                info["width"],
                info["height"],
                info["target_width"],
                info["target_height"],
                scratch,
                run,
            )
            if copy_video and not copy_container:
                from profile7 import signature

                encoded = source_es
                picture_signature = signature(encoded, cancel, progress=_Stage(
                    progress, "source_hash", "核对原始视频码流", "bytes", prep_mid, prep_end))
                if picture_signature[0] != len(times):
                    raise ValueError("原片编码图像数和显示帧数不一致。")
            elif source_es != source:
                source_es.unlink()
            if meta["frame_count"] != len(times):
                raise ValueError("原片 RPU 数量与视频显示帧数不符。")
            master, cll = _static_hdr(source, video, run)
            if info["dv_profile"] == 5 and (not master or not cll):
                from profile5 import static_from_rpu

                fallback_master, fallback_cll = static_from_rpu(rpu, scratch, run)
                master, cll = master or fallback_master, cll or fallback_cll
        else:
            master, cll = (
                "G(8500,39850)B(6550,2300)R(35400,14600)WP(15635,16450)L(10000000,1)",
                None,
            )
        has_rpu = target == "preserve" and mode in {"quality", "rtx"}
        if copy_video:
            progress(prep_end, "已保留原始单层视频与逐帧动态元数据")
        elif native:
            progress(prep_end, "已保留原始双层视频")
        elif not streamed and (has_rpu or mode == "rtx"):
            from ai import rtx_status

            cmd = _gpu_command(picture, encoded, info, mode, target, rpu, master, cll)
            rtx = mode == "rtx"
            encode = _Stage(progress, "encode", "RTX 超分与 HEVC 编码" if rtx else "NVIDIA 高位深缩放与 HEVC 编码",
                            "frames" if rtx else "percent", encode_start, encode_end)
            encode(0, frame_total if rtx else 100)

            def nv_progress(line):
                status = rtx_status(line)
                if status and encode.last <= 0:
                    progress(encode_start, status)
                if line.startswith("progress_frame=") and line[15:].isdigit():
                    encode(int(line[15:]), frame_total)
                    return
                if rtx:
                    return
                m = re.match(r"\[?\s*(\d+(?:\.\d+)?)%", line)
                if m:
                    encode(float(m[1]), 100)

            run(cmd, nv_progress, collect=False)
        elif not streamed:
            name = "神经网络超分" if mode == "neural" else "高位深缩放"
            encode = _Stage(progress, "encode", name + "与 x265 编码", "frames", encode_start, encode_end)
            encode(0, frame_total)
            cmd = _software_command(picture, encoded, info, picture_video, mode, target, master, cll)
            run(cmd, _ffmpeg_frames(lambda n: encode(n, frame_total)), collect=False)
        if picture != source:
            picture.unlink()
        scene_start = prep_end
        if measure_light:
            from hdr10 import measure_content_light, write_content_light

            primary_end, refine_end = (75, 81) if target == "newdv" else (84, 88)
            primary = _Stage(progress, "light", "逐帧测量 MaxCLL 与 MaxFALL", "frames", encode_end, primary_end)
            refine = _Stage(progress, "light_refine", "精测临界亮度值", "frames", primary_end, refine_end)
            primary(0, frame_total)
            def light_progress(current, total, key):
                (refine if key == "light_refine" else primary)(current, total)
            measured_levels = measure_content_light(encoded, canvas, offsets, frame_total, scratch, run,
                                                    progress=light_progress)
            signalled = scratch / "measured-light.hevc"
            write_start = refine_end if refine.last >= 0 else primary_end
            scene_start = write_start + 2 if target == "newdv" else mux_start
            write = _Stage(progress, "light_write", "写入实测 HDR10 亮度", "bytes", write_start, scene_start)
            encoded_size = encoded.stat().st_size
            write(0, encoded_size)
            write_content_light(encoded, signalled, measured_levels, cancel, on_bytes=lambda n: write(n, encoded_size))
            encoded.unlink()
            encoded = signalled
            peak, frame_average = measured_levels
            cll = f"{peak},{frame_average}"
            generation_levels.update(max_cll=peak, max_fall=frame_average)
        if target == "newdv":
            from hdr import measure_output

            scenes = _Stage(progress, "scenes", "测量杜比视界逐镜亮度", "frames", scene_start, 88)
            scenes(0, frame_total)
            measurements = measure_output(encoded, scratch, run, active_area=offsets, expected_frames=frame_total,
                                          progress=lambda n, total, key: scenes(n, total))
            progress(88, "生成逐帧杜比视界元数据")
            meta = dovi.generate_rpu_from_measurements(measurements, rpu, scratch, run,
                                                       **generation_levels, **area_options)
            if meta["frame_count"] != len(times):
                raise ValueError("新生成的 RPU 帧数与原视频不一致。")
        if target not in {"hdr10", "keep7"} and not has_rpu and not copy_video:
            progress(89, "写入逐帧杜比视界元数据")
            injected = scratch / "with-rpu.hevc"
            run([dovi.find_dovi_tool(), "inject-rpu", "-i", encoded, "-r", rpu, "-o", injected])
            encoded.unlink()
            encoded = injected
        progress(mux_start, "读取封装所需的音轨与章节")
        muxed = scratch / "verified.mkv"
        starts = _audio_starts(source, data, run)
        chapter_streams = _chapter_streams(source, data)
        chapter_file = None
        if len(chapter_streams) == 1 and all("end" in ch for ch in data["chapters"]):
            chapter_file = scratch / "chapters.xml"
            language = chapter_streams[0].get("tags", {}).get("language") or "und"
            _chapter_xml(chapter_file, data["chapters"], language)
        container_digest = None
        if copy_container:
            with muxed.open("xb") as outgoing:
                container_digest = _hash_container(source, cancel, progress, copy_to=outgoing,
                                                   bounds=(mux_start, verify_start))
        else:
            mux = _Stage(progress, "mux", "按原时间戳封装视频及原片轨道", "percent", mux_start, verify_start)
            mux(0, 100)
            _mux(source, encoded, muxed, info, timing, original, run, starts, chapter_file, progress=mux)
            encoded.unlink()
        progress(verify_start, "复读输出并核验尺寸与色彩信令")
        out_data = _probe(muxed, run)
        out_video = _video(out_data)
        if (out_video["width"], out_video["height"]) != (
            info["target_width"],
            info["target_height"],
        ):
            raise ValueError("导出尺寸校验失败。")
        if any(
            out_video.get(k) != x
            for k, x in {
                "pix_fmt": "yuv420p10le",
                "color_space": "bt2020nc",
                "color_transfer": "smpte2084",
                "color_primaries": "bt2020",
                "color_range": "tv",
            }.items()
        ):
            raise ValueError("输出的 10 位 HDR10/PQ 色彩信令未通过校验。")
        tier, dv_level = None, None
        if native:
            cfg = profile7.config(out_video)
            profile7.validate_base(out_video, cfg)
            dv_level = cfg.get("dv_level")
        elif copy_video:
            cfg = dovi.validate_profile(out_video)
            dv_level = cfg["dv_level"]
            if dv_level != dovi.validate_profile(video)["dv_level"]:
                raise ValueError("无损封装后杜比视界等级发生变化。")
        elif target != "hdr10":
            cfg = dovi.validate_profile(out_video)
            cap = None if base_layer_copied else _vbv_limit(info)
            if mode == "quality":
                cap = min(60000, cap)
            tier, dv_level = _check_dv_tier(muxed, out_video, cfg, cap, run)
        elif any("dv_profile" in s for s in out_video.get("side_data_list", [])):
            raise ValueError("HDR10 输出意外携带杜比视界配置。")
        timeline = _Stage(progress, "verify", "复核成片逐帧显示时间", "frames", verify_start, timeline_end)
        timeline(0, frame_total)
        output_pgs = {} if copy_container else _pgs_tracks(out_data)
        out_times, out_end = _times(muxed, out_video["index"], run, with_end=True,
                                    on_frame=lambda n: timeline(n, frame_total), pgs=output_pgs)
        if len(out_times) != len(times) or max(abs(a - b) for a, b in zip(times, out_times)) > 1200:
            raise ValueError("输出帧数或逐帧显示时间与原片不一致。")
        if video_end is not None and (out_end is None or abs(video_end - out_end) > 1200):
            raise ValueError("输出视频的末帧显示时长与原片不一致。")
        progress(timeline_end, "核验 HDR 母版、音字幕轨与章节")
        out_master, out_cll = _static_hdr(muxed, out_video, run)
        if (master and master != out_master) or (cll and cll != out_cll):
            raise ValueError("成片中的静态 HDR 母版或内容亮度与预期不符。")
        _verify_other(
            data, out_data, original, _mkv_inventory(muxed, run), chapter_streams,
            bool(chapter_file), source, muxed, run,
            pgs_packets=None if copy_container else (input_pgs, output_pgs),
        )
        for a, b in zip(starts, _audio_starts(muxed, out_data, run)):
            # Match encoded packets by hash, not by index: container-specific
            # codec priming can be removed on remux without removing audible audio.
            valid = {
                s["data_hash"]: float(s["pts_time"])
                for s in a
                if "pts_time" in s
                and not any(d.get("skip_samples", 0) for d in s.get("side_data_list", []))
            }
            if len(valid) < len([s for s in a if "pts_time" in s and not s.get("side_data_list")]):
                continue  # Identical silence packets do not identify unique times.
            for packet in b:
                if packet.get("data_hash") in valid and "pts_time" in packet:
                    if abs(float(packet["pts_time"]) - valid[packet["data_hash"]]) > 0.003:
                        raise ValueError("封装后音轨相对视频发生时移。")
        digest = None
        if copy_container:
            if _hash_container(muxed, cancel, progress, bounds=(timeline_end, 99)) != container_digest:
                raise ValueError("原样保留的 MKV 整文件 SHA-256 回读不一致。")
            digest = dovi._digest(rpu)
        elif native:
            layers = _Stage(progress, "verify_layers", "拆分成片并核验双层视频", "frames", timeline_end, 92)
            layers(0, frame_total)
            def extract_layers(src, index, out, runner, **options):
                return _extract(src, index, out, runner, on_frame=lambda n: layers(n, frame_total), **options)
            review = scratch / "readback"
            review.mkdir()
            bl_out, el_out, rpu_out, out_layer, _ = profile7.prepare(
                muxed,
                out_video["index"],
                None,
                review,
                info["width"],
                info["height"],
                len(times),
                extract_layers,
                _tool("ffprobe"),
                run,
            )
            verified_layers = (
                profile7.signature(bl_out, cancel, progress=_Stage(
                    progress, "verify_base", "回读并核对成片基础层", "bytes", 92, 96)),
                profile7.signature(el_out, cancel, progress=_Stage(
                    progress, "verify_el", "回读并核对成片增强层", "bytes", 96, 99)),
            )
            digest = dovi._digest(rpu_out)
            if (
                verified_layers != signatures
                or out_layer != info["enhancement_layer"]
                or digest != dovi._digest(rpu)
            ):
                raise ValueError("成片 BL、EL 或原始 RPU 字节校验失败。")
        elif target != "hdr10":
            rpu_start = timeline_end
            if copy_video or base_layer_copied:
                from profile7 import signature

                readback = scratch / "readback.hevc"
                midpoint = timeline_end + (99 - timeline_end) / 3
                rpu_start = timeline_end + 2 * (99 - timeline_end) / 3
                frames = _Stage(progress, "verify_extract", "提取成片视频并复核编码画面", "frames",
                                timeline_end, midpoint)
                frames(0, frame_total)
                _extract(muxed, out_video["index"], readback, run, on_frame=lambda n: frames(n, frame_total))
                picture_check = _Stage(progress, "verify_picture", "核对成片编码画面与参数集",
                                       "bytes", midpoint, rpu_start)
                if signature(readback, cancel, ignore_rpu=base_layer_copied, progress=picture_check) != picture_signature:
                    raise ValueError("无损封装后的 HEVC 编码画面、参数集或附加数据发生变化。")
            expected_size = rpu.stat().st_size
            rpu_check = _Stage(progress, "verify_rpu", "回读成片杜比视界元数据", "bytes", rpu_start, 99)
            def verify_tool(args):
                if args[1] != "extract-rpu":
                    return run(args)
                extracted = Path(args[args.index("-o") + 1])
                def report_bytes():
                    # dovi_tool creates this file after it has parsed the video.
                    try:
                        rpu_check(extracted.stat().st_size, expected_size)
                    except FileNotFoundError:
                        pass
                rpu_check(0, expected_size)
                output = run(args, on_poll=report_bytes)
                report_bytes()
                progress(99, "核对杜比视界帧数及 SHA-256")
                return output
            verification = dovi.verify_rpu(rpu, muxed, scratch, verify_tool, **area_options)
            digest = verification["rpu_sha256"]
        _check_cancel(cancel)
        after = source.stat()
        if (before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_ino, after.st_size, after.st_mtime_ns
        ):
            raise ValueError("片源在导出期间发生了变化，请重新导出。")
        if os.name == "nt":
            os.rename(muxed, dest)  # Atomic and fails if another job claimed the path.
        else:
            os.link(muxed, dest)
        result = dict(
            path=str(dest),
            destination=str(dest),
            frame_count=len(times),
            width=info["target_width"],
            height=info["target_height"],
            target=target,
            mode="rtx_compressed" if info.get("compressed_source") else mode,
            rpu_sha256=digest,
            verified=True,
            hevc_tier=tier,
            dv_level=dv_level,
            audio_count=info["audio_count"],
            subtitle_count=info["subtitle_count"],
        )
        if mode == "rtx":
            result["speed"] = speed
        if info["dv_profile"] == 7:
            result.update(
                input_dv_profile=7,
                enhancement_layer=info["enhancement_layer"],
                output_dv_profile=7 if native else 8,
            )
        if signatures:
            result.update(bl_sha256=signatures[0][1], el_sha256=signatures[1][1])
        if base_layer_copied:
            result.update(base_layer_copied=True, bl_sha256=picture_signature[1])
        if copy_video and picture_signature:
            result["video_sha256"] = picture_signature[1]
        if measured_levels is not None:
            result["measured_content_light"] = dict(max_cll_nits=peak, max_fall_nits=frame_average)
        if info["source_type"] == "sdr":
            result["sdr_mapping"] = sdr_mapping
            if sdr_mapping in {"scene", "cinema", "auto"}:
                result["graded_shots"] = len(shots)
                result["extended_white_shots"] = sum(1 for shot in shots if shot.get("headroom"))
                if sdr_mapping == "auto":
                    result["auto_model_shots"] = model_shots
                    result["auto_adaptive_shots"] = len(shots) - model_shots
                    result["auto_model_unavailable_shots"] = candidates - model_shots
        if target in {"hdr10", "newdv"}:
            result["active_area"] = dict(zip(("left", "right", "top", "bottom"), offsets))
        if container_digest:
            result["container_sha256"] = container_digest
    progress(100, "导出与校验均已完成")
    return result
