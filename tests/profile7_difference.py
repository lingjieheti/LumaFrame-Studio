"""Measure a pair from profile7_vectors.py after export, in 10-bit luma codes."""

import argparse
import json
import subprocess
from array import array
from pathlib import Path
from statistics import mean

TOOLS = Path(__file__).resolve().parents[1] / "bin"


def luma(path):
    dims = subprocess.check_output([
        TOOLS / "ffprobe.exe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height", "-of", "json", path,
    ])
    stream = json.loads(dims)["streams"][0]
    width, height = stream["width"], stream["height"]
    raw = subprocess.check_output([
        TOOLS / "ffmpeg.exe", "-v", "error", "-i", path, "-map", "0:v:0",
        "-vf", r"select=eq(n\,3),extractplanes=y", "-frames:v", "1",
        "-pix_fmt", "gray10le", "-f", "rawvideo", "-",
    ])
    values = array("H")
    values.frombytes(raw)
    if len(values) != width * height:
        raise ValueError("Missing 10-bit frame")
    return width, height, values


def measure(neutral, patch):
    w, h, a = luma(neutral)
    w2, h2, b = luma(patch)
    if (w, h) != (w2, h2):
        raise ValueError("Image sizes differ")
    inner, outer = [], []
    # Leave wide margins around the stimulus boundary to exclude ringing.
    for y in range(16, h - 16, 16):
        for x in range(16, w - 16, 16):
            diff = abs(a[y * w + x] - b[y * w + x])
            if 0.3 * w < x < 0.7 * w and 0.3 * h < y < 0.7 * h:
                inner.append(diff)
            elif x < 0.2 * w or x > 0.8 * w or y < 0.2 * h or y > 0.8 * h:
                outer.append(diff)
    return {
        "width": w, "height": h, "frame_index": 3,
        "inner_mean_abs_10bit": mean(inner), "outside_mean_abs_10bit": mean(outer),
        "inner_changed_fraction": sum(d > 0 for d in inner) / len(inner),
        "inner_samples": len(inner), "outside_samples": len(outer),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("neutral", type=Path)
    parser.add_argument("patch", type=Path)
    parser.add_argument("--expect-base-only", action="store_true")
    args = parser.parse_args()
    result = measure(args.neutral, args.patch)
    print(json.dumps(result, indent=2))
    inside, outside = result["inner_mean_abs_10bit"], result["outside_mean_abs_10bit"]
    if args.expect_base_only:
        assert inside == outside == 0
    else:
        assert inside > max(1, outside * 4), "The EL patch did not survive reconstruction"
