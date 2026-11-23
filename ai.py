"""HEVC Main10 encode commands for the HDR10-compatible Dolby Vision base layer."""

import json
from pathlib import Path
import sys


def app_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def nvenc_exe():
    return app_dir() / "bin" / "nvenc" / "NVEncC64.exe"


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
):
    """Return argv. `source` is a verified limited-range BT.2020/PQ main layer."""
    width, height = int(width), int(height)
    if min(width, height) <= 0 or width % 2 or height % 2:
        raise ValueError("4:2:0 输出宽高必须为正偶数。")
    if not 0 <= int(quality) <= 51 or not 1000 <= int(max_bitrate) <= 240000:
        raise ValueError("编码质量或最大码率超出范围。")
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
        )
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

    return run_worker(json.loads(argv[0]))


if __name__ == "__main__" and sys.argv[1:2] == ["--rtx-worker"]:
    sys.exit(rtx_worker(sys.argv[2:]))
