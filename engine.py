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


class _Runner:
    """Read pipes concurrently and stop active tools even when they produce no progress."""

    def __init__(self, cancel):
        self.cancel = cancel
        self.worker_cancel = None

    def __call__(self, args, line_callback=None, *, collect=True):
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
        try:
            while True:
                _check_cancel(self.cancel)
                try:
                    line = messages.get(timeout=0.2)
                except queue.Empty:
                    continue
                if line is None:
                    break
                lines.append(line)
                if line_callback:
                    line_callback(line.strip())
            code = process.wait()
            result = "".join(lines)
            if code != 0:
                if Path(args[0]).stem.lower() == "mkvmerge" and code == 1:
                    # A warning may indicate discarded packets or an unsupported track.
                    detail = "\n".join(l for l in lines if "Warning:" in l) or result[-1800:]
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
            info["source_type"] = "dv"
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
    if info["source_type"] == "sdr" and info["assumptions"]:
        # An elementary HEVC stream can carry RPU NALs without a container
        # configuration record. Do not let the HD/BT.709 fallback consume them.
        idx = _video(data)["index"]
        records = run(
            [
                _tool("ffprobe"),
                "-v",
                "error",
                "-select_streams",
                str(idx),
                "-read_intervals",
                "%+#12",
                "-show_frames",
                "-show_entries",
                "frame=pts_time:frame_side_data=side_data_type",
                "-of",
                "json",
                path,
            ]
        )
        markers = ("dolby vision", "dovi", "mastering display", "hdr dynamic", "content light")
        if any(key in records.lower() for key in markers):
            info.update(
                supported=False,
                source_type="other",
                profile="",
                assumptions=[],
                reason="画面包含 HDR 元数据但容器缺少可确认的色彩标记，无法按 SDR 处理。",
            )
    return info


def inspect_source(path):
    path = Path(path).resolve(strict=True)
    run = _Runner(None)
    return _inspect(path, _probe(path, run), run)


def _times(path, index, run, *, coded_order=False):
    # Packet PTS is in presentation order after sorting, even with B-frame reordering.
    times = []

    def packet(line):
        token = line.partition(",")[0].strip()
        if not token:
            return
        if not re.fullmatch(r"-?\d+(\.\d+)?", token):
            raise ValueError("视频缺少可还原的显示时间戳。")
        times.append(round(float(token) * 1_000_000))

    run(
        [
            _tool("ffprobe"),
            "-v",
            "error",
            "-select_streams",
            str(index),
            "-show_packets",
            "-show_entries",
            "packet=pts_time:packet_side_data=",
            "-of",
            "csv=p=0",
            path,
        ],
        packet,
        collect=False,
    )
    if not times or len(set(times)) != len(times):
        raise ValueError("视频显示时间戳为空或重复，无法保证逐帧同步。")
    if not coded_order:
        times.sort()
    return times


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


def _extract(source, video_index, dest, run, *, limit=None, downstream=None):
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
        run(args)
    else:
        import profile7

        profile7.run_pipe(args, downstream, run, Path(downstream[-1]).parent)


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
    if video["width"] > width or cap > (high_rate if high else main_rate) * 1000:
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
        sdr_to_hdr=target != "preserve",
        fel_input=fel_input,
    )


def _software_command(source, out, info, video, mode, target, master, cll):
    w, h = info["target_width"], info["target_height"]
    if target == "preserve" and info["dv_profile"] == 5:
        from profile5 import decode_profile5_filter

        init_args, vf = decode_profile5_filter(
            info["width"], info["height"], w, h, neural=mode == "neural"
        )
    elif target == "preserve" and mode == "reference":
        dither = "error_diffusion" if info.get("enhancement_layer") == "FEL" else "none"
        init_args, vf = [], f"zscale=w={w}:h={h}:filter=spline36:dither={dither},format=yuv420p10le"
    elif target == "preserve":
        from neural import ff_filter

        init_args, vf = ff_filter(info["width"], info["height"], w, h)
    else:
        from hdr import sdr_filter

        init_args, vf = sdr_filter(info["width"], info["height"], w, h, mode)
        full = video.get("color_range") == "pc" or video["pix_fmt"].startswith("yuvj")
        vf = (
            "setparams=range="
            + ("full" if full else "limited")
            + ":colorspace=bt709:color_primaries=bt709:color_trc=bt709,"
            + vf
        )
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


def _software_encode(source, out, info, video, mode, target, master, cll, run, progress):
    name = "神经网络超分" if mode == "neural" else "高位深缩放"

    def report(line):
        if line.startswith("out_time_us=") and info["duration"] > 0:
            val = line.split("=", 1)[1]
            if val.lstrip("-").isdigit():
                progress(
                    12 + max(0, min(1, int(val) / 1e6 / info["duration"])) * 60,
                    name + "与 x265 高质量编码",
                )

    run(_software_command(source, out, info, video, mode, target, master, cll), report)


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


def _hash_container(path, cancel, progress, *, copy_to=None):
    """Hash the copy as it is written, then independently hash the file read back."""
    digest = hashlib.sha256()
    size = path.stat().st_size
    first, text = (84, "复制并校验原始 MKV") if copy_to else (95, "逐字节回读双层 MKV")
    last = -1
    with path.open("rb") as incoming:
        while block := incoming.read(8 * 1024 * 1024):
            _check_cancel(cancel)
            if copy_to:
                copy_to.write(block)
            digest.update(block)
            percent = first + int(4 * incoming.tell() / max(size, 1))
            if percent != last:
                progress(percent, text)
                last = percent
    return digest.hexdigest()


def _mux(source, encoded, out, info, timing, original, run, audio_starts):
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
    args += [encoded, "--no-video"]
    if original["container"].get("type") == "MP4/QuickTime":
        # Matroska has no AAC edit list. mkvmerge keeps the priming access unit
        # and shifts the next audible packet forward unless explicitly corrected.
        tracks = [t for t in original["tracks"] if t["type"] == "audio"]
        for track, packets in zip(tracks, audio_starts):
            if track["codec"] == "AAC" and packets:
                first = packets[0]
                pts = float(first.get("pts_time", 0))
                if pts < 0 and any(
                    s.get("skip_samples", 0) for s in first.get("side_data_list", [])
                ):
                    args += ["--sync", f"{track['id']}:{round(pts * 1000)}"]
    args.append(source)
    run(args)


def _verify_other(source, output, original, result, common_offset):
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

    a, b = nonvideo(source), nonvideo(output)
    if len(a) != len(b):
        raise ValueError("封装后音频、字幕或数据轨数量变化。")
    for s, d in zip(a, b):
        sc, dc = s.get("codec_name"), d.get("codec_name")
        if sc != dc and (sc, dc) not in {("mov_text", "subrip"), ("text", "subrip")}:
            raise ValueError(f"副轨编码变化：{sc} → {dc}。")
        if s["codec_type"] == "audio" and (s.get("channels"), s.get("sample_rate")) != (
            d.get("channels"),
            d.get("sample_rate"),
        ):
            raise ValueError("音轨声道数或采样率发生变化。")
        if s["codec_type"] == "subtitle" and "start_time" in s and "start_time" in d:
            if abs(float(s["start_time"]) - float(d["start_time"])) > 0.003:
                raise ValueError("字幕轨的起始时间发生了变化。")

    def attachments(data):
        return Counter((t.get("file_name"), t.get("size")) for t in data.get("attachments", []))

    if attachments(original) != attachments(result):
        raise ValueError("附件名称或字节数校验失败。")
    src_ch, dst_ch = source.get("chapters", []), output.get("chapters", [])
    if len(src_ch) != len(dst_ch):
        raise ValueError("章节数量校验失败。")
    for x, y in zip(src_ch, dst_ch):
        if abs(float(x["start_time"]) + common_offset - float(y["start_time"])) > 0.003:
            raise ValueError("章节时间校验失败。")
        if x.get("tags", {}).get("title") != y.get("tags", {}).get("title"):
            raise ValueError("章节名称校验失败。")


def render(path, destination, mode, progress, cancel, *, target="preserve"):
    source = Path(path).resolve(strict=True)
    before = source.stat()
    dest = Path(destination).resolve()
    if dest == source or dest.exists():
        raise FileExistsError("目标文件已存在，请选择新的输出文件名。")
    if dest.suffix.lower() != ".mkv" or not dest.parent.is_dir():
        raise ValueError("请选择现有文件夹中的 .mkv 输出文件。")
    if mode not in {"quality", "reference", "neural", "rtx"} or target not in {
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
    native = target == "keep7"
    if native and not info["can_keep7"]:
        raise ValueError("双层原样保留仅适用于原生 4K Profile 7 片源。")
    if native:
        mode = "reference"
    if (target in {"preserve", "keep7"}) != (info["source_type"] == "dv"):
        raise ValueError(
            "杜比视界保留需要 Profile 5、7 或 8.1 片源；HDR10 与新杜比视界制作需要 SDR 片源。"
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
        native and info["el_index"] is None and original["container"].get("type") == "Matroska"
    )
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
        progress(2, "读取原片逐帧时间戳")
        coded_times = _times(source, video["index"], run, coded_order=True)
        if info["el_index"] is not None:
            el_times = _times(source, info["el_index"], run, coded_order=True)
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
        rpu = scratch / "scaled.rpu"
        encoded = scratch / "encoded.hevc"
        picture, picture_video = source, video
        streamed = False
        signatures = None
        if info["dv_profile"] == 7:
            import profile7

            progress(4, "拆分并逐帧核对 Profile 7 基础层、增强层与 RPU")
            base, el, original_rpu, layer, _ = profile7.prepare(
                source,
                video["index"],
                info["el_index"],
                scratch,
                info["width"],
                info["height"],
                len(times),
                _extract,
                _tool("ffprobe"),
                run,
            )
            info["enhancement_layer"] = layer
            master, cll = _static_hdr(source, video, run)
            if native:
                progress(10, "校验原始双层视频并复制编码画面")
                signatures = tuple(profile7.signature(p, cancel) for p in (base, el))
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
                    progress(8, "逐帧合成基础层与 FEL 残差")
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
                        progress,
                        encoder_command=command,
                    )
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
            base.unlink()
            el.unlink()
        elif target == "preserve":
            progress(5, "提取杜比视界元数据并换算有效画面尺寸")
            source_es = scratch / "original.hevc"
            _extract(source, video["index"], source_es, run)
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
        if not native and not streamed:
            progress(12, "生成 4K 10 位 PQ 视频")
        has_rpu = target == "preserve" and mode in {"quality", "rtx"}
        if native:
            progress(74, "已保留原始双层视频")
        elif not streamed and (has_rpu or mode == "rtx"):
            cmd = _gpu_command(picture, encoded, info, mode, target, rpu, master, cll)

            def nv_progress(line):
                if "VFX" in line or "NVIDIA 下载" in line:
                    progress(12, line[:140])
                if line.startswith("progress_frame=") and line[15:].isdigit():
                    current = int(line[15:])
                    progress(
                        12 + min(1, current / len(times)) * 60,
                        f"RTX 超分  ·  {current:,} / {len(times):,} 帧",
                    )
                    return
                if mode == "rtx":
                    return
                m = re.search(r"(\d+(?:\.\d+)?)%", line)
                if m:
                    progress(12 + min(100, float(m[1])) * 0.6, "NVIDIA 高位深缩放与 HEVC 编码")

            run(cmd, nv_progress)
        elif not streamed:
            _software_encode(
                picture, encoded, info, picture_video, mode, target, master, cll, run, progress
            )
        if picture != source:
            picture.unlink()
        if target == "newdv":
            from hdr import measure_output

            progress(73, "测量新 HDR 画面的逐镜亮度")
            measurements = measure_output(encoded, scratch, run)
            meta = dovi.generate_rpu_from_measurements(measurements, rpu, scratch, run)
            if meta["frame_count"] != len(times):
                raise ValueError("新生成的 RPU 帧数与原视频不一致。")
        if target not in {"hdr10", "keep7"} and not has_rpu:
            progress(81, "写入逐帧杜比视界元数据")
            injected = scratch / "with-rpu.hevc"
            run([dovi.find_dovi_tool(), "inject-rpu", "-i", encoded, "-r", rpu, "-o", injected])
            encoded.unlink()
            encoded = injected
        progress(84, "按原时间戳封装视频及原片轨道")
        muxed = scratch / "verified.mkv"
        starts = _audio_starts(source, data, run)
        container_digest = None
        if copy_container:
            with muxed.open("xb") as outgoing:
                container_digest = _hash_container(source, cancel, progress, copy_to=outgoing)
        else:
            _mux(source, encoded, muxed, info, timing, original, run, starts)
            encoded.unlink()
        progress(90, "复读输出并核验逐帧时间、色彩与副轨")
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
        elif target != "hdr10":
            cfg = dovi.validate_profile(out_video)
            cap = min(60000, _vbv_limit(info)) if mode == "quality" else _vbv_limit(info)
            tier, dv_level = _check_dv_tier(muxed, out_video, cfg, cap, run)
        elif any("dv_profile" in s for s in out_video.get("side_data_list", [])):
            raise ValueError("HDR10 输出意外携带杜比视界配置。")
        out_times = _times(muxed, out_video["index"], run)
        if len(out_times) != len(times) or max(abs(a - b) for a, b in zip(times, out_times)) > 1200:
            raise ValueError("输出帧数或逐帧显示时间与原片不一致。")
        out_master, out_cll = _static_hdr(muxed, out_video, run)
        if (master and master != out_master) or (cll and cll != out_cll):
            raise ValueError("输出静态 HDR 母版或亮度元数据未保留。")
        _verify_other(data, out_data, original, _mkv_inventory(muxed, run), 0)
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
            if _hash_container(muxed, cancel, progress) != container_digest:
                raise ValueError("双层 MKV 整文件 SHA-256 回读不一致。")
            digest = dovi._digest(rpu)
        elif native:
            progress(95, "逐层复核基础层、增强层和原始动态元数据")
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
                _extract,
                _tool("ffprobe"),
                run,
            )
            verified_layers = tuple(profile7.signature(p, cancel) for p in (bl_out, el_out))
            digest = dovi._digest(rpu_out)
            if (
                verified_layers != signatures
                or out_layer != info["enhancement_layer"]
                or digest != dovi._digest(rpu)
            ):
                raise ValueError("成片 BL、EL 或原始 RPU 字节校验失败。")
        elif target != "hdr10":
            progress(95, "逐帧复核最终文件内的 RPU")
            verification = dovi.verify_rpu(rpu, muxed, scratch, run)
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
            mode=mode,
            rpu_sha256=digest,
            verified=True,
            hevc_tier=tier,
            dv_level=dv_level,
            audio_count=info["audio_count"],
            subtitle_count=info["subtitle_count"],
        )
        if info["dv_profile"] == 7:
            result.update(
                input_dv_profile=7,
                enhancement_layer=info["enhancement_layer"],
                output_dv_profile=7 if native else 8,
            )
        if signatures:
            result.update(bl_sha256=signatures[0][1], el_sha256=signatures[1][1])
        if container_digest:
            result["container_sha256"] = container_digest
    progress(100, "导出与校验均已完成")
    return result
