"""Generate a deterministic moving PQ chart and benchmark the installed Windows application.

Run with NumPy and the companion tools present, for example:
python tools/bench/motion_grain.py SCRATCH --app "C:/path/LumaFrameStudio.exe"
"""

import argparse
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys
import threading
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import dovi
import engine
from hdr_difference import compare as colour_difference
from sparks import yuv_frames


WIDTH, HEIGHT, FRAMES = 3840, 2160, 24
COLOUR = ["-color_range", "tv", "-colorspace", "bt2020nc", "-color_trc", "smpte2084", "-color_primaries", "bt2020"]
DOWNSAMPLE = "zscale=w=1920:h=1080:filter=lanczos,format=yuv420p10le"
MODES = ("reference", "quality", "neural", "rtx", "rtx_compressed")
AMD_DEVICE = ["-init_hw_device", "d3d11va=radeon:,vendor_id=0x1002",
              "-init_hw_device", "amf=amf@radeon", "-filter_hw_device", "amf"]


def fixture(folder):
    ff = engine._tool("ffmpeg")
    truth = folder / "truth.mkv"
    source = folder / "input.mkv"
    # Keep full-resolution moving detail, sharp edges, and independent grain in separate strips.
    x = np.arange(1280, dtype=np.float64)[None, :]
    y = np.arange(HEIGHT, dtype=np.float64)[:, None]
    rng = np.random.Generator(np.random.PCG64(20261125))
    chroma = np.full((HEIGHT // 2, WIDTH // 2), 512, dtype="<u2").tobytes()
    with subprocess.Popen([ff, "-nostdin", "-v", "error", "-f", "rawvideo", "-pixel_format", "yuv420p10le",
                           "-video_size", "3840x2160", "-framerate", "24", *COLOUR, "-i", "pipe:0", "-c:v", "ffv1",
                           "-slices", "16", "-level", "3", *COLOUR, truth], stdin=subprocess.PIPE) as proc:
        for t in range(FRAMES):
            texture = 485 + 78 * np.sin(2*np.pi*((x - 3*t)/9 + y/29)) + 22 * np.sin(2*np.pi*(x/3.2 + (y-t)/31))
            edge = 420 + 135 * (((x - 6*t + y/8) % 164) < 82) + 18 * np.cos(2*np.pi*(x+y-4*t)/41)
            grain = 470 + rng.normal(0, 10, size=(HEIGHT, 1280))
            luma = np.rint(np.concatenate((texture, edge, grain), axis=1)).astype("<u2")
            proc.stdin.write(luma.tobytes())
            proc.stdin.write(chroma)
            proc.stdin.write(chroma)
        proc.stdin.close()
        if proc.wait():
            raise RuntimeError("Failed to create the synthetic master")
    raw, rpu, tagged = (folder / name for name in ("1080.hevc", "chart.rpu", "tagged.hevc"))
    subprocess.run([ff, "-nostdin", "-v", "error", "-i", truth, "-vf",
                    DOWNSAMPLE,
                    "-c:v", "libx265", "-preset", "medium", "-crf", "31", "-x265-params",
                    "pools=8:frame-threads=3:bframes=4:repeat-headers=1:hdr10=1:log-level=error", *COLOUR, raw], check=True)
    # The synthetic metadata exercises byte-preserving delivery; the chart only evaluates base pixels.
    dovi.generate_rpu_from_measurements([dict(start=0, length=FRAMES, min_pq=1000, max_pq=2800, avg_pq=1900)],
                                        rpu, folder, engine._Runner(None))
    subprocess.run([dovi.find_dovi_tool(), "inject-rpu", "-i", raw, "-r", rpu, "-o", tagged], check=True, stdout=subprocess.DEVNULL)
    subprocess.run([engine._tool("mkvmerge"), "-q", "-o", source, "--default-duration", "0:24fps", tagged], check=True)
    return truth, source, sha256(rpu.read_bytes()).hexdigest()


def measure(truth, output):
    n = 0
    prev = None
    mse = np.zeros(3)
    flicker = np.zeros(3)
    moments = np.zeros(3)
    for n, (a, b) in enumerate(zip(yuv_frames(truth), yuv_frames(output), strict=True), 1):
        # Exclude transitions between bands, the outer border and small edge-effects at picture boundaries.
        aa = a[:WIDTH*HEIGHT].reshape(HEIGHT, 3, 1280)[32:-32, :, 32:-32]
        bb = b[:WIDTH*HEIGHT].reshape(HEIGHT, 3, 1280)[32:-32, :, 32:-32]
        error = bb - aa
        mse += np.square(error, dtype=np.float32).mean(axis=(0, 2), dtype=np.float64)
        if prev is not None:
            flicker += np.abs(error - prev).mean(axis=(0, 2), dtype=np.float64)
        prev = error
        grain = aa[:, 2].astype(np.float32)
        other = bb[:, 2].astype(np.float32)
        grain -= grain.mean(dtype=np.float64)
        other -= other.mean(dtype=np.float64)
        moments += [np.mean(grain*grain, dtype=np.float64), np.mean(other*other, dtype=np.float64),
                    np.mean(grain*other, dtype=np.float64)]
    if n != FRAMES:
        raise ValueError("Unexpected frame count")
    v1, v2, cov = moments/n
    return dict(bytes=output.stat().st_size, psnr=(10*np.log10(1023**2/(mse/n))).tolist(),
                residual_temporal_mae=(flicker/(n-1)).tolist(), grain_sd_source=float(np.sqrt(v1)),
                grain_sd_output=float(np.sqrt(v2)), grain_correlation=float(cov/np.sqrt(v1*v2)) if v2 else None,
                colour=colour_difference(truth, output, every=4, stride=8))


def input_grain_range(source, downsample=False):
    """Measure the 1080p band before and after encoding at the same normalized margins."""
    width, height = 608, 1048
    filters = [DOWNSAMPLE] if downsample else []
    filters += [f"crop={width}:{height}:1296:16", "extractplanes=y"]
    raw = subprocess.check_output([engine._tool("ffmpeg"), "-nostdin", "-v", "error", "-i", source,
                                   "-vf", ",".join(filters), "-pix_fmt", "gray10le", "-f", "rawvideo", "pipe:1"])
    plane = np.frombuffer(raw, dtype="<u2").reshape(FRAMES, height, width)
    variance = np.var(plane, axis=(1, 2), dtype=np.float64)
    return dict(frames=len(plane), samples=int(plane.size), minimum=int(plane.min()), maximum=int(plane.max()),
                grain_sd=float(np.sqrt(variance.mean())))


def amd_reference(source, output):
    """Use the reference export pipeline with only its spatial scaler replaced."""
    stock = engine._software_command

    def command(*args, **kwargs):
        cmd = stock(*args, **kwargs)
        cmd[1:1] = AMD_DEVICE
        cmd[cmd.index("-vf") + 1] = ("format=p010le,sr_amf=w=3840:h=2160:algorithm=sr1-0:format=p010le,"
                                     "hwdownload,format=p010le,format=yuv420p10le,setsar=1")
        return cmd

    with patch.object(engine, "_software_command", command):
        return engine.render(source, output, "reference", lambda *_: None, threading.Event())


def amd_ramp():
    codes = np.arange(64, 941, dtype="<u2")
    width, height = 877 * 4, 64
    y = np.tile(np.repeat(codes, 4), (height, 1))
    uv = np.full((height // 2, width // 2), 512, dtype="<u2")
    args = [engine._tool("ffmpeg"), "-nostdin", "-v", "error", *AMD_DEVICE, "-f", "rawvideo", "-pix_fmt", "yuv420p10le",
            "-video_size", f"{width}x{height}", *COLOUR, "-i", "pipe:0", "-frames:v", "1", "-vf",
            "format=p010le,sr_amf=w=iw*2:h=ih*2:format=p010le:algorithm=sr1-0,hwdownload,format=p010le,format=yuv420p10le",
            "-pix_fmt", "yuv420p10le", "-f", "rawvideo", "pipe:1"]
    raw = subprocess.check_output(args, input=y.tobytes()+uv.tobytes()*2)
    result = np.frombuffer(raw, dtype="<u2")[:width*height*4].reshape(2*height, 2*width)[height, 4::8]
    return dict(distinct=int(np.unique(result).size), matched=int((result==codes).sum()),
                max_error=int(np.abs(result.astype(int)-codes).max()), first=int(result[0]), last=int(result[-1]))


def streaming_reference(source, output):
    import ai
    stock = ai.nvenc_command

    def command(*args, **kwargs):
        cmd = stock(*args, **kwargs)
        if "--rtx-worker" in cmd:
            return [sys.executable, str(Path(__file__).resolve()), "--streaming-worker", cmd[-1]]
        return cmd

    with patch.object(ai, "nvenc_command", command):
        return engine.render(source, output, "rtx", lambda *_: None, threading.Event())


def streaming_worker(params):
    import rtx_backend
    load_sdk = rtx_backend.load_sdk

    def select(scratch):
        sdk, libs = load_sdk(scratch)
        constructor = sdk.VideoSuperRes

        def create(*args, **kwargs):
            kwargs["quality"] = sdk.QualityLevel.STREAMING_ULTRA
            return constructor(*args, **kwargs)

        sdk.VideoSuperRes = create
        return sdk, libs

    with patch.object(rtx_backend, "load_sdk", select):
        return rtx_backend.run_worker(params)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--app", type=Path, required=True)
    parser.add_argument("--modes", nargs="+", choices=MODES, default=MODES, help="Installed application modes to benchmark")
    parser.add_argument("--amd", action="store_true", help="Also test the optional AMD AMF P010 spatial scaler")
    parser.add_argument("--streaming", action="store_true", help="Also test NVIDIA's pre-transcode Ultra model")
    args = parser.parse_args()
    folder = args.directory.resolve()
    folder.mkdir(parents=True, exist_ok=True)
    if any(folder.iterdir()):
        parser.error("Choose an empty scratch directory")
    truth, source, rpu_hash = fixture(folder)
    input_grain = dict(before_compression=input_grain_range(truth, downsample=True),
                       after_compression=input_grain_range(source))
    (folder / "input-grain.json").write_text(json.dumps(input_grain, indent=2), encoding="utf-8")
    results = {}
    for mode in args.modes:
        output = folder / (mode+".mkv")
        report = folder / (mode+"-render.json")
        subprocess.run([args.app.resolve(), "--export", source, "--mode", mode, "--output", output, "--result", report],
                       check=True, timeout=600)
        result = json.loads(report.read_bytes())
        if not result.get("verified") or result["frame_count"] != FRAMES or result["rpu_sha256"] != rpu_hash:
            raise ValueError(result)
        results[mode] = measure(truth, output)
        (folder/"metrics.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
        print(mode, json.dumps(results[mode]), flush=True)
    if args.amd:
        output = folder / "amf-sr10.mkv"
        report = amd_reference(source, output)
        if not report["verified"] or report["frame_count"] != FRAMES or report["rpu_sha256"] != rpu_hash:
            raise ValueError(report)
        stats = dict(**measure(truth, output), ramp=amd_ramp())
        (folder/"amf-metrics.json").write_text(json.dumps(stats, indent=2), encoding="utf-8")
        print("AMD AMF", json.dumps(stats), flush=True)
    if args.streaming:
        output = folder / "streaming-ultra.mkv"
        report = streaming_reference(source, output)
        if not report["verified"] or report["frame_count"] != FRAMES or report["rpu_sha256"] != rpu_hash:
            raise ValueError(report)
        stats = measure(truth, output)
        (folder/"streaming-metrics.json").write_text(json.dumps(stats, indent=2), encoding="utf-8")
        print("NVIDIA Streaming Ultra", json.dumps(stats), flush=True)


if __name__ == "__main__":
    if sys.argv[1:2] == ["--streaming-worker"] and len(sys.argv) == 3:
        raise SystemExit(streaming_worker(json.loads(sys.argv[2])))
    main()
