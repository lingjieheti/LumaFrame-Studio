"""HEVC Main10 encode commands for the HDR10-compatible Dolby Vision base layer."""

import json
from fractions import Fraction
from pathlib import Path
import sys


def app_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def nvenc_exe():
    return app_dir() / "bin" / "nvenc" / "NVEncC64.exe"


def rtx_status(line):
    if line == "status=download_vfx":
        return "正在从 NVIDIA 下载并验证 VFX 运行库（416 MiB）…"
    if line.startswith("NVIDIA RTX Video"):
        return line[:140]
    return None


def nvenc_command(
    source,
    output,
    width,
    height,
    rpu,
    *,
    mode="nis",
    quality=18,
    max_bitrate=60000,
    mastering=None,
    max_cll=None,
    hw_decode=True,
    sdr_to_hdr=False,
    fel_input=None,
    sdr_mapping="expand",
    compressed_source=False,
    speed="quality",
):
    """Return argv. `source` is a verified limited-range BT.2020/PQ main layer."""
    width, height = int(width), int(height)
    if speed not in ("quality", "fast"):
        raise ValueError("未知的 RTX 处理速度。")
    if min(width, height) <= 0 or width % 2 or height % 2:
        raise ValueError("4:2:0 输出宽高必须为正偶数。")
    if not 0 <= int(quality) <= 51 or not 1000 <= int(max_bitrate) <= 240000:
        raise ValueError("编码质量或最大码率超出范围。")
    if compressed_source and (mode != "rtx" or sdr_to_hdr or fel_input is not None):
        raise ValueError("压缩片源修复仅适用于需要超分的杜比视界 Profile 8.1。")
    exe = nvenc_exe()
    if not exe.is_file():
        raise FileNotFoundError(f"找不到 NVEncC 编码器：{exe}")
    if mode == "rtx":
        payload = dict(
            source=str(source),
            output=str(output),
            width=width,
            height=height,
            rpu=str(rpu) if rpu else None,
            quality=quality,
            max_bitrate=max_bitrate,
            mastering=mastering,
            max_cll=max_cll,
            sdr_to_hdr=sdr_to_hdr,
            sdr_mapping=sdr_mapping,
        )
        if compressed_source:
            payload["compressed_source"] = True
        if speed == "fast":
            payload["speed"] = speed
        if fel_input is not None:
            iw, ih, fps = fel_input
            if source != "pipe:0" or sdr_to_hdr or min(iw, ih) < 2 or iw % 2 or ih % 2:
                raise ValueError("FEL 流式输入需要偶数尺寸的 PQ12 画面。")
            rate = Fraction(fps)
            if rate <= 0:
                raise ValueError("FEL 流式输入需要正帧率。")
            payload["fel_input"] = {"width": int(iw), "height": int(ih), "fps": str(rate)}
        prefix = (
            [sys.executable]
            if getattr(sys, "frozen", False)
            else [sys.executable, str(Path(__file__).resolve())]
        )
        return [*prefix, "--rtx-worker", json.dumps(payload, ensure_ascii=True)]
    filters = {"nis": "algo=nis,hdr=pq,sharpness=0.2", "spline36": "spline36"}
    # A Y4M stream from the VFX worker already has the final dimensions.
    if mode == "_scaled":
        source_args = ["--y4m", "-i", "-"]
    elif mode in filters:
        source_args = [
            "--avhw" if hw_decode else "--avsw",
            "-i",
            str(source),
            "--output-res",
            f"{width}x{height}",
            "--vpp-resize",
            filters[mode],
        ]
    else:
        raise ValueError(f"未知的放大算法：{mode}")
    args = [
        str(exe),
        *source_args,
        "--codec",
        "hevc",
        "--profile",
        "main10",
        "--output-depth",
        "10",
        "--preset",
        "P6",
        "--vbr",
        "0",
        "--vbr-quality",
        str(quality),
        "--max-bitrate",
        str(max_bitrate),
        "--vbv-bufsize",
        str(max_bitrate),
        "--colormatrix",
        "bt2020nc",
        "--transfer",
        "smpte2084",
        "--colorprim",
        "bt2020",
        "--colorrange",
        "limited",
        "--sar",
        "1:1",
        "--repeat-headers",
        "--aud",
    ]
    if mastering or mode != "_scaled":
        args.extend(["--master-display", str(mastering or "copy")])
    if max_cll or mode != "_scaled":
        args.extend(["--max-cll", str(max_cll or "copy")])
    if rpu:
        args.extend(["--dolby-vision-profile", "8.1", "--dolby-vision-rpu", str(rpu)])
    return [*args, "-o", str(output)]


def rtx_worker(argv):
    from rtx_backend import run_worker

    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    return run_worker(json.loads(argv[0]))


if __name__ == "__main__" and sys.argv[1:2] == ["--rtx-worker"]:
    sys.exit(rtx_worker(sys.argv[2:]))
