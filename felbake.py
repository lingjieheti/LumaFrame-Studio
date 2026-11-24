"""Compose a Profile 7 enhancement residual into a PQ12 intermediate before scaling."""

import json
import hashlib
import ctypes
import errno
from fractions import Fraction
from pathlib import Path
import shutil
import subprocess
import sys
import time


# Evaluated by the separately distributed, isolated VapourSynth interpreter (bin/fel).
# The payload is supplied by dovi_tool in display order. Reconcile any in-band copy
# from FFMS2 with it, so reordered or mismatched enhancement frames fail closed.
_COMPOSE_RGB = r"""
import json
import mmap
import os
from pathlib import Path
import re
import sys
import vapoursynth as vs

config = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
runtime = Path(sys.executable).parent
core = vs.core
core.num_threads = min(8, os.cpu_count() or 1)
core.max_cache_size = 512
core.std.LoadPlugin(str(runtime / "ffms2.dll"))
core.std.LoadPlugin(str(runtime / "felbaker.dll"))
bl = core.ffms2.Source(config["bl"], threads=4, cache=False)
el = core.ffms2.Source(config["el"], threads=4, cache=False)
nframes = config["frames"]
if bl.num_frames != nframes or el.num_frames != nframes:
    raise ValueError(f"BL/EL frame counts differ: {bl.num_frames}/{el.num_frames}, expected {nframes}")
if (bl.width, bl.height) != (config["width"], config["height"]):
    raise ValueError("Base layer geometry differs from the source stream")
if (el.width, el.height) not in ((bl.width, bl.height), (bl.width // 2, bl.height // 2)):
    raise ValueError("Unsupported enhancement layer geometry")
if any(not clip.format or clip.format.id != vs.YUV420P10 for clip in (bl, el)):
    raise ValueError("Expected two 10-bit YUV420 HEVC layers")

rpu_file = open(config["rpu"], "rb")
rpudata = mmap.mmap(rpu_file.fileno(), 0, access=mmap.ACCESS_READ)
offsets = [m.start() for m in re.finditer(b"\x00\x00\x00\x01", rpudata)]
if not offsets or offsets[0] or len(offsets) != nframes:
    raise ValueError("RPU and video frame counts differ")
offsets.append(len(rpudata))

def supply_rpu(n, f):
    payload = rpudata[offsets[n] + 4:offsets[n + 1]]
    embedded = f.props.get("DolbyVisionRPU", b"")
    # A demuxer may include Annex B trailing_zero_8bits after rbsp_trailing_bits.
    if not payload.startswith(b"\x19") or (embedded and embedded.rstrip(b"\x00") != payload):
        raise ValueError(f"RPU verification failed on enhancement frame {n}")
    if embedded:
        return f
    out = f.copy()
    out.props["DolbyVisionRPU"] = payload
    return out

el = core.std.ModifyFrame(el, clips=el, selector=supply_rpu)
el = core.resize.Spline16(el, width=bl.width, height=bl.height, format=vs.YUV420P10)
baked = core.fel.Bake(bl, el, no_resample=1)
baked = core.resize.Spline16(baked, format=vs.YUV444P12)
rgb = core.fel.ToRGB(baked)
"""
_SCRIPT = (
    _COMPOSE_RGB
    + r"""
out = core.resize.Spline36(rgb, format=vs.YUV420P12, matrix_s="2020ncl",
                           range_in_s="full", range_s="limited", chromaloc_s="top_left",
                           dither_type="ordered")
def tick(current, total):
    if current % 24 == 0 or current == total:
        print(f"frame={current}", file=sys.stderr, flush=True)
out.output(sys.stdout.buffer, y4m=False, progress_update=tick, prefetch=4)
"""
)


def _runtime():
    if sys.platform == "win32" and not ctypes.windll.kernel32.IsProcessorFeaturePresent(40):
        raise ValueError("FEL 像素合成需要支持 AVX2 的处理器。原生 4K 双层可选择原样保留。")
    base = Path(__file__).resolve().parent
    for directory in (base, Path(getattr(sys, "_MEIPASS", base)), Path(sys.executable).parent):
        candidate = directory / "bin" / "fel" / "python.exe"
        if candidate.is_file():
            manifest = candidate.parent / "runtime-sha256.json"
            if not manifest.is_file():
                raise FileNotFoundError("双层杜比组件缺少校验清单，请重新安装。")
            hashes = json.loads(manifest.read_text(encoding="utf-8"))
            required = {
                "python.exe",
                "python312.dll",
                "python3.dll",
                "python312.zip",
                "vapoursynth.pyd",
                "vapoursynth.dll",
                "ffms2.dll",
                "felbaker.dll",
            }
            if not required.issubset(hashes):
                raise ValueError("双层杜比组件校验清单不完整，请重新安装。")
            for name, expected in hashes.items():
                path = candidate.parent / name
                if not path.is_file():
                    raise FileNotFoundError(f"双层杜比运行库缺少 {name}，请重新安装。")
                with path.open("rb") as reader:
                    if hashlib.file_digest(reader, "sha256").hexdigest() != expected:
                        raise ValueError(f"双层杜比运行库 {name} 校验失败，请重新安装。")
            return candidate
    raise FileNotFoundError("缺少双层杜比合成组件，请重新安装完整的软件包。")


def _stop(process):
    if process is not None:
        if process.poll() is None:
            process.kill()
        process.wait()


def bake(
    bl_hevc,
    el_hevc,
    rpu_path,
    workdir,
    fps,
    width,
    height,
    expected_frames,
    run,
    progress,
    *,
    encoder_command=None,
):
    """Pipe composed PQ12 pixels directly to a chosen HEVC encoder, or retain a lossless intermediate."""
    from engine import _check_cancel, _tool

    work = Path(workdir).resolve()
    config = work / "fel-inputs.json"
    script = work / "fel-compose.py"
    rate = Fraction(fps)
    if rate <= 0 or expected_frames <= 0:
        raise ValueError("双层杜比片源缺少有效帧率或帧数。")
    config.write_text(
        json.dumps(
            {
                "bl": str(Path(bl_hevc).resolve()),
                "el": str(Path(el_hevc).resolve()),
                "rpu": str(Path(rpu_path).resolve()),
                "width": width,
                "height": height,
                "frames": expected_frames,
            }
        ),
        encoding="utf-8",
    )
    script.write_text(_SCRIPT, encoding="utf-8")
    color = [
        "-color_primaries",
        "bt2020",
        "-color_trc",
        "smpte2084",
        "-colorspace",
        "bt2020nc",
        "-color_range",
        "tv",
        "-chroma_sample_location",
        "topleft",
    ]
    raw = [
        "-f",
        "rawvideo",
        "-pixel_format",
        "yuv420p12le",
        "-video_size",
        f"{width}x{height}",
        "-framerate",
        str(rate),
        *color,
    ]
    direct = encoder_command is not None
    if direct:
        encode = [str(item) for item in encoder_command]
        if encode.count("-i") != 1 or encode[encode.index("-i") + 1 :][:1] != ["pipe:0"]:
            raise ValueError("FEL 编码器必须从一个标准输入管道读取画面。")
        pos = encode.index("-i")
        encode[pos:pos] = raw
        output = (work / encode[-1]).resolve()
        if output.parent != work or output.suffix.lower() not in {".hevc", ".h265", ".265"}:
            raise ValueError("FEL 编码器必须在任务目录内生成 HEVC 裸流。")
        first, span = 12, 60
    else:
        output = work / "fel-pq12.mkv"
        encode = [
            _tool("ffmpeg"),
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-n",
            *raw,
            "-i",
            "pipe:0",
            "-map",
            "0:v:0",
            "-an",
            "-c:v",
            "ffv1",
            "-level",
            "3",
            "-coder",
            "1",
            "-context",
            "0",
            "-g",
            "16",
            "-threads",
            "8",
            "-slicecrc",
            "1",
            *color,
            str(output),
        ]
        first, span = 6, 5
    if output.exists():
        raise FileExistsError("双层杜比编码目标已存在。")
    common = {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
    cancel = getattr(run, "cancel", None)
    _check_cancel(cancel)
    diagnostic = work / "fel-compose.log"
    codec_log = work / "fel-lossless.log"
    producer = consumer = None
    try:
        with (
            diagnostic.open("wb") as dl,
            codec_log.open("wb") as cl,
            diagnostic.open("rb") as status,
        ):
            producer = subprocess.Popen(
                [str(_runtime()), "-I", str(script), str(config)],
                cwd=work,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=dl,
                **common,
            )
            try:
                consumer = subprocess.Popen(
                    encode,
                    cwd=work,
                    stdin=producer.stdout,
                    stdout=subprocess.DEVNULL,
                    stderr=cl,
                    **common,
                )
            finally:
                producer.stdout.close()
            while producer.poll() is None or consumer.poll() is None:
                _check_cancel(cancel)
                if consumer.poll() not in (None, 0):
                    break
                for line in status.readlines():
                    if line.startswith(b"frame=") and line[6:].strip().isdigit():
                        completed = int(line[6:])
                        stage = "合成杜比增强层并编码" if direct else "合成杜比增强层"
                        progress(
                            first + span * min(1, completed / expected_frames),
                            f"{stage} · {completed}/{expected_frames} 帧",
                        )
                time.sleep(0.1)
        if producer.poll() != 0 or consumer.poll() != 0:
            details = "\n".join(
                p.read_bytes()[-2300:].decode("utf-8", "replace") for p in (diagnostic, codec_log)
            )
            if (
                any(
                    term in details.lower()
                    for term in (
                        "no space left",
                        "disk full",
                        "not enough space",
                        "空间不足",
                        "error number -28",
                    )
                )
                or shutil.disk_usage(work).free < 4096
            ):
                raise OSError(
                    errno.ENOSPC,
                    "目标磁盘空间不足，无法继续编码双层杜比影片。请更换输出磁盘或释放空间。",
                )
            raise ValueError(f"双层 Dolby Vision 合成失败。\n{details.strip()}")
    finally:
        _stop(consumer)
        _stop(producer)
    probe = json.loads(
        run(
            [
                _tool("ffprobe"),
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-count_frames" if direct else "-count_packets",
                "-show_streams",
                "-of",
                "json",
                output,
            ]
        )
    )["streams"][0]
    expected = {
        "codec_name": "hevc" if direct else "ffv1",
        "pix_fmt": "yuv420p10le" if direct else "yuv420p12le",
        "color_range": "tv",
        "color_space": "bt2020nc",
        "color_transfer": "smpte2084",
        "color_primaries": "bt2020",
        ("nb_read_frames" if direct else "nb_read_packets"): str(expected_frames),
    }
    if not direct:
        expected.update(width=width, height=height, chroma_location="topleft")
    if any(probe.get(k) != v for k, v in expected.items()) or any(
        "dv_profile" in sd for sd in probe.get("side_data_list", [])
    ):
        raise ValueError("双层合成输出的帧数、位深、色彩或轨道配置校验失败。")
    return output
