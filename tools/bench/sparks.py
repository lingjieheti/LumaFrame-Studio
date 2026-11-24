"""Opt-in online benchmark. Run: python tools/bench/sparks.py SCRATCH --scene sparks

Downloads a 200–250 MiB byte range from a Netflix Open Content IMF. All footage and
intermediates remain in SCRATCH. Use an empty directory with at least 3 GiB free.
Requires numpy, ffmpeg, the application tools and GPU runtimes for selected modes.
The footage is © Netflix and used under CC BY 4.0: https://opencontent.netflix.com/
https://creativecommons.org/licenses/by/4.0/ ; this script crops and rescales it.
"""

import argparse
import concurrent.futures
import hashlib
import json
import re
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import patch
from urllib.request import Request, urlopen

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import ai
import dovi
import engine
import hdr

BASE = "https://s3.amazonaws.com/download.opencontent.netflix.com/TechblogAssets/"
MERIDIAN = (
    BASE + "Meridian/MERIDIAN_SHR_C_EN-XX_US-NR_51_LTRT_UHD_ML7_SL4_20160915_OV/"
    "MER_SHR_C_EN-XX_US-NR_51_LTRT_UHD_ML7_SL4_20160915_OV_00.mxf"
)
SCENES = {
    "sparks": (
        BASE + "Sparks/Sparks_DolbyVision_P3D65_PQ_5994fps_4096x2160_LtRt_IMF_20170214/"
        "Sparks_20170214.mxf",
        8_001_853_218,
        8_257_328_372,
        2356,
    ),
    "meridian": (
        MERIDIAN,
        17_000_625_208,
        17_211_500_000,
        5662,
    ),
    "meridian-holdout": (MERIDIAN, 27_000_141_597, 27_200_264_319, 8660),
}
COUNT = 60
FIRST_FRAME_SHA256 = {
    "sparks": "bee22c9a464763c82ff36eed8ddd1046bad689c6b0792a6b57fe16169693765a",
    "meridian": "56b8334f8a7cc7bf29249bf02e1395fd1c6bc63183bdf03ef624de792d767f3d",
    "meridian-holdout": "e6f4c1d77a0ee6f2387fe5078d4f97c2137786c05a4de01858b55927969f8f8a",
}


def download_frames(folder, asset, first, last, start):
    width = 4 * 1024**2
    chunks = [(i, min(i + width - 1, last)) for i in range(first, last + 1, width)]

    def fetch(bounds):
        a, b = bounds
        req = Request(asset, headers={"Range": f"bytes={a}-{b}"})
        for attempt in range(3):
            try:
                with urlopen(req, timeout=120) as response:
                    data = response.read()
                    if response.status != 206 or len(data) != b - a + 1:
                        raise RuntimeError("Incomplete range response")
                    return data
            except OSError:
                if attempt == 2:
                    raise

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        block = b"".join(pool.map(fetch, chunks))
    starts = [m.start() for m in re.finditer(b"\xff\x4f\xff\x51", block)]
    for i in range(COUNT):
        a, limit = starts[i], starts[i + 1] if i + 1 < len(starts) else len(block)
        end = block.index(b"\xff\xd9", a, limit) + 2
        # Each codestream is followed by its Dolby Vision XML record in the IMF.
        label = re.search(rb"<FrameNumber>(\d+)</FrameNumber>", block[end:limit])
        if not label or int(label[1]) != start + i:
            raise ValueError(f"Unexpected IMF frame {i}")
        (folder / f"frame-{i:03}.j2c").write_bytes(block[a:end])


def yuv_frames(path):
    command = [
        engine._tool("ffmpeg"),
        "-nostdin",
        "-v",
        "error",
        "-i",
        str(path),
        "-map",
        "0:v:0",
        "-fps_mode",
        "passthrough",
        "-enc_time_base",
        "1/60000",
        "-pix_fmt",
        "yuv420p10le",
        "-f",
        "rawvideo",
        "-",
    ]
    with subprocess.Popen(command, stdout=subprocess.PIPE) as p:
        try:
            while True:
                buf = p.stdout.read(3840 * 2160 * 3)
                if not buf:
                    break
                if len(buf) != 3840 * 2160 * 3:
                    raise ValueError("Incomplete 4K YUV frame")
                yield np.frombuffer(buf, dtype="<u2").astype(np.int16)
            if p.wait() != 0:
                raise RuntimeError("Failed to read 10-bit 4:2:0 samples")
        finally:
            if p.poll() is None:
                p.kill()


def luma_gradients(frame):
    y = frame[:3840 * 2160].reshape(2160, 3840)
    center = y[:-1:2, :-1:2]
    return np.stack((y[:-1:2, 1::2] - center, y[1::2, :-1:2] - center))


def compare(truth, output, run):
    errors, motion_errors, previous = np.zeros(3), 0.0, None
    edge_error, edge_samples = 0.0, 0
    pixels = 3840 * 2160
    count = 0
    for count, (a, b) in enumerate(zip(yuv_frames(truth), yuv_frames(output), strict=True), 1):
        planes = np.split(b - a, (pixels, pixels + pixels // 4))
        for i, plane in enumerate(planes):
            errors[i] += np.square(plane, dtype=np.float32).mean(dtype=np.float64)
        residual = planes[0]
        if (count - 1) % 5 == 0:
            # At every other pixel sample horizontal and vertical first differences.
            # Select edges on the master, so over-sharpening cannot select its own test pixels.
            reference = luma_gradients(a)
            magnitude = np.abs(reference).sum(axis=0)
            strongest = magnitude >= max(1, np.percentile(magnitude, 90))
            error = (luma_gradients(b) - reference)[:, strongest]
            edge_error += np.square(error, dtype=np.float32).sum(dtype=np.float64)
            edge_samples += error.size
        if previous is not None:
            motion_errors += np.abs(residual - previous).mean()
        previous = residual
    if count != COUNT:
        raise ValueError("Frame count mismatch")
    stats = run(
        [
            engine._tool("ffmpeg"),
            "-nostdin",
            "-hide_banner",
            "-i",
            truth,
            "-i",
            output,
            "-filter_complex",
            "[0:v:0][1:v:0]ssim",
            "-an",
            "-f",
            "null",
            "-",
        ]
    )
    ssim = float(re.findall(r"SSIM Y:([\d.]+)", stats)[-1])
    psnr = 10 * np.log10(1023**2 / (errors / count))
    return dict(
        y_psnr_db=float(psnr[0]),
        cb_psnr_db=float(psnr[1]),
        cr_psnr_db=float(psnr[2]),
        y_ssim=ssim,
        delta_y_mae=motion_errors / (count - 1),
        strong_edge_gradient_rmse=float(np.sqrt(edge_error / edge_samples)) if edge_samples else None,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workdir", type=Path)
    parser.add_argument("--scene", choices=SCENES, default="sparks")
    parser.add_argument(
        "--run-name", default="", help="Optional filename prefix for additional comparison runs"
    )
    parser.add_argument("--modes", nargs="+", default=["reference", "quality", "neural", "rtx"])
    parser.add_argument("--rtx-quality", type=float, metavar="0..51",
                        help="Override only the RTX delivery encoder's VBR quality in this experiment.")
    parser.add_argument("--input-crf", type=int, choices=range(1, 52), metavar="1..51",
                        help="Apply an additional, lossy HEVC encode to the 1080p test input.")
    args = parser.parse_args()
    if args.rtx_quality is not None and (
        not 0 <= args.rtx_quality <= 51 or set(args.modes) - {"rtx", "rtx_compressed"}
    ):
        parser.error("--rtx-quality requires RTX modes and a value between 0 and 51")
    if not re.fullmatch("[a-zA-Z0-9_-]*", args.run_name):
        parser.error("--run-name accepts letters, digits, hyphen and underscore")
    prefix = args.run_name + "-" if args.run_name else ""
    p = args.workdir.resolve()
    asset, first, last, frame_start = SCENES[args.scene]
    p.mkdir(parents=True, exist_ok=True)
    truth, low = p / "truth.mkv", p / "low.mkv"
    run = engine._Runner(None)
    ff = [engine._tool("ffmpeg"), "-nostdin", "-hide_banner", "-v", "error"]
    if not (p / "frame-059.j2c").is_file():
        download_frames(p, asset, first, last, frame_start)
    checksum = hashlib.sha256((p / "frame-000.j2c").read_bytes()).hexdigest()
    if checksum != FIRST_FRAME_SHA256[args.scene]:
        raise ValueError(
            "The first frame does not match the requested scene. Use its own scratch folder."
        )
    if not truth.exists():
        rgb_to_2020 = (
            "format=gbrp16le,crop=3840:2160:(iw-3840)/2:0,zscale=rangein=full:matrixin=gbr:"
            "primariesin=smpte432:transferin=smpte2084:matrix=bt2020nc:primaries=bt2020:"
            "transfer=smpte2084:range=limited:dither=ordered,format=yuv420p10le"
        )
        run(
            [
                *ff,
                "-framerate",
                "60000/1001",
                "-i",
                p / "frame-%03d.j2c",
                "-vf",
                rgb_to_2020,
                "-c:v",
                "ffv1",
                "-level",
                "3",
                "-color_primaries",
                "bt2020",
                "-color_trc",
                "smpte2084",
                "-colorspace",
                "bt2020nc",
                "-color_range",
                "tv",
                truth,
            ]
        )
    if not low.exists():
        run(
            [
                *ff,
                "-i",
                truth,
                "-vf",
                "zscale=w=1920:h=1080:filter=lanczos,format=yuv420p10le",
                "-c:v",
                "libx265",
                "-preset",
                "fast",
                "-x265-params",
                "lossless=1:repeat-headers=1",
                low,
            ]
        )
    source = p / "bench-p81.mkv"
    if not source.exists():
        # The benchmark evaluates the PQ base picture. This RPU exercises container delivery.
        # It is newly made from this downsample, not the filmmaker's original metadata.
        measurements = hdr.measure_output(low, p, run)
        meta = p / "bench.rpu"
        dovi.generate_rpu_from_measurements(measurements, meta, p, run)
        raw, tagged = p / "low.hevc", p / "tagged.hevc"
        engine._extract(low, 0, raw, run)
        run([dovi.find_dovi_tool(), "inject-rpu", "-i", raw, "-r", meta, "-o", tagged])
        run([engine._tool("mkvmerge"), "-o", source, "--default-duration", "0:60000/1001fps", tagged])
    if args.input_crf is not None:
        prefix += f"crf{args.input_crf}-"
        compressed_source = p / f"bench-p81-crf{args.input_crf}.mkv"
        if not compressed_source.exists():
            with tempfile.TemporaryDirectory(prefix="compress-", dir=p) as temp:
                scratch = Path(temp)
                raw, tagged = scratch / "compressed.hevc", scratch / "tagged.hevc"
                run([*ff, "-i", low, "-c:v", "libx265", "-preset", "medium", "-crf", str(args.input_crf),
                     "-x265-params", "pools=8:frame-threads=3:hdr10=1:repeat-headers=1:bframes=4",
                     "-an", "-f", "hevc", raw])
                run([dovi.find_dovi_tool(), "inject-rpu", "-i", raw, "-r", p / "bench.rpu", "-o", tagged])
                muxed = scratch / "input.mkv"
                run([engine._tool("mkvmerge"), "-o", muxed, "--default-duration", "0:60000/1001fps", tagged])
                muxed.replace(compressed_source)
        source = compressed_source
    results = {}
    for mode in args.modes:
        dest = p / (prefix + mode + ".mkv")
        start = time.perf_counter()
        if dest.exists():
            raise FileExistsError(f"Already rendered: {dest}. Pick another folder or a new mode.")
        if args.rtx_quality is None:
            engine.render(source, dest, mode, lambda *_: None, threading.Event())
        else:
            default_command = ai.nvenc_command

            def rate_control(*positional, **options):
                options["quality"] = args.rtx_quality
                return default_command(*positional, **options)

            with patch.object(ai, "nvenc_command", rate_control):
                engine.render(source, dest, mode, lambda *_: None, threading.Event())
        elapsed = time.perf_counter() - start
        results[mode] = dict(
            wall_seconds=elapsed, bytes=dest.stat().st_size, **compare(truth, dest, run)
        )
        print(mode, results[mode], flush=True)
        (p / ("metrics-" + prefix + mode + ".json")).write_text(
            json.dumps(results[mode], indent=2), encoding="utf-8"
        )
    print(
        json.dumps(
            {
                "source": asset,
                "frames": [frame_start, frame_start + COUNT - 1],
                "fps": "60000/1001",
                "j2k_first_sha256": checksum,
                "results": results,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
