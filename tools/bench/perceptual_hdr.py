"""Compare 60 consecutive native-resolution UHD HDR crops with ColorVideoVDP 0.5.7."""

import argparse
import json
from pathlib import Path
import subprocess
import sys

import numpy as np
import pycvvdp
import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import engine


FRAMES = 60
RATE = 60000 / 1001
WIDTH, HEIGHT = 384, 256


def read_crop(path, x, y):
    command = [
        engine._tool("ffprobe"), "-v", "error", "-select_streams", "v:0",
        "-read_intervals", "%+2", "-show_frames", "-show_entries",
        "stream=width,height,pix_fmt,color_range,color_space,color_transfer,color_primaries,chroma_location:frame=pts_time",
        "-of", "json", path,
    ]
    details = json.loads(subprocess.check_output(command))
    stream = details["streams"][0]
    keys = ("width", "height", "pix_fmt", "color_range", "color_space", "color_transfer", "color_primaries")
    expected = (3840, 2160, "yuv420p10le", "tv", "bt2020nc", "smpte2084", "bt2020")
    if tuple(stream.get(k) for k in keys) != expected or stream.get("chroma_location") not in (None, "unspecified", "left"):
        raise ValueError(f"{path}: expected 4K limited-range, left-sited 4:2:0 10-bit BT.2020/PQ")
    times = np.array([float(frame["pts_time"]) for frame in details["frames"][:FRAMES]])
    if len(times) != FRAMES or np.max(np.abs(times - np.arange(FRAMES) / RATE)) > .0011:
        raise ValueError(f"{path}: expected 60 frames on a zero-based 60000/1001 fps timeline")

    filters = (
        f"crop={WIDTH}:{HEIGHT}:{x}:{y},"
        "zscale=matrixin=2020_ncl:rangein=limited:range=full:chromalin=left:filter=spline36:dither=none,"
        "format=gbrp16le"
    )
    command = [
        engine._tool("ffmpeg"), "-nostdin", "-v", "error", "-threads", "3", "-i", path,
        "-map", "0:v:0", "-an", "-vf", filters, "-frames:v", str(FRAMES),
        "-fps_mode", "passthrough", "-enc_time_base", "1/60000", "-f", "rawvideo", "pipe:1",
    ]
    planes = np.frombuffer(subprocess.check_output(command), dtype="<u2").reshape(FRAMES, 3, HEIGHT, WIDTH)
    rgb = planes[:, [2, 0, 1]].astype(np.float32) / 65535
    return torch.from_numpy(rgb), times


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reference", type=Path)
    parser.add_argument("outputs", type=Path, nargs="+")
    parser.add_argument("--origin", type=int, nargs=2, metavar=("X", "Y"), default=(1728, 952))
    parser.add_argument("--controls", action="store_true", help="Also score identical and flickering reference crops")
    args = parser.parse_args()
    x, y = args.origin
    if x % 2 or y % 2 or not (0 <= x <= 3840-WIDTH and 0 <= y <= 2160-HEIGHT):
        parser.error("The 384 × 256 crop needs an even origin within the UHD raster")
    torch.set_num_threads(5)
    torch.set_num_interop_threads(1)
    ref, ref_times = read_crop(args.reference, x, y)
    metric = pycvvdp.cvvdp(display_name="standard_hdr_pq", quiet=True, device=torch.device("cpu"))

    def score(candidate):
        with torch.inference_mode():
            prediction, _ = metric.predict(candidate, ref, dim_order="FCHW", frames_per_second=RATE)
        return prediction.item()

    results = {}
    if args.controls:
        gain = torch.ones(FRAMES)
        gain[1::2] = .92
        results["identical"] = score(ref)
        results["alternating_0.92_PQ_signal"] = score(ref * gain.reshape(-1, 1, 1, 1))
    for path in args.outputs:
        frames, times = read_crop(path, x, y)
        if np.max(np.abs(ref_times - times)) > .0011:
            raise ValueError(f"{path}: reference and test frame timestamps differ")
        results[str(path)] = score(frames)
    print(json.dumps({"display": "standard_hdr_pq", "frames": FRAMES, "fps": RATE,
                      "roi": [x, y, WIDTH, HEIGHT], "jod": results}, indent=2))


if __name__ == "__main__":
    main()
