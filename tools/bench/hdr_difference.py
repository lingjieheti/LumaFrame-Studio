"""Sample BT.2124 colour differences between two aligned BT.2020/PQ clips.

Usage: python tools/bench/hdr_difference.py reference.mkv candidate.mkv
Requires the application's FFmpeg tools and NumPy. Measures the decoded base
layer. Dolby display mapping and the viewing device are outside this metric.
"""

import argparse
from contextlib import closing
from itertools import zip_longest
import json
from pathlib import Path
import subprocess
import sys

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import engine


def describe(path):
    raw = subprocess.check_output([engine._tool("ffprobe"), "-v", "error", "-select_streams", "v:0",
                                   "-show_entries", "stream=width,height,color_space,color_primaries,color_transfer:packet=pts_time",
                                   "-of", "json", path])
    data = json.loads(raw)
    stream = data["streams"][0]
    if (stream.get("color_space"), stream.get("color_primaries"), stream.get("color_transfer")) != (
        "bt2020nc", "bt2020", "smpte2084"
    ):
        raise ValueError("Inputs must have explicit BT.2020 nonconstant-luminance / PQ signalling")
    pts = sorted(float(packet["pts_time"]) for packet in data["packets"])
    return (stream["width"], stream["height"]), pts


def ictcp_frames(path, size, every, stride):
    width, height = size
    filters = (f"select='not(mod(n,{every}))',"
               "zscale=matrix=ictcp:transfer=smpte2084:primaries=2020:range=full:dither=none,"
               "format=yuv444p16le")
    command = [engine._tool("ffmpeg"), "-nostdin", "-v", "error", "-i", path, "-map", "0:v:0",
               "-an", "-sn", "-vf", filters, "-fps_mode", "passthrough", "-f", "rawvideo", "pipe:1"]
    with subprocess.Popen(command, stdout=subprocess.PIPE) as proc:
        try:
            block = width * height * 6
            count = 0
            while buf := proc.stdout.read(block):
                if len(buf) != block:
                    raise ValueError("Truncated 16-bit ICtCp frame")
                frame = np.frombuffer(buf, dtype="<u2").reshape(3, height, width)
                y, x = (count * 3) % stride, count % stride
                yield frame[:, y::stride, x::stride].astype(np.float32)
                count += 1
            if proc.wait():
                raise RuntimeError("FFmpeg failed to convert the input to digital ICtCp")
        finally:
            if proc.poll() is None:
                proc.kill()


def compare(reference, candidate, every=5, stride=4):
    size, a_times = describe(reference)
    other, b_times = describe(candidate)
    if size != other or len(a_times) != len(b_times) or not a_times:
        raise ValueError("The rasters and nonzero frame counts must match")
    if max(abs((a-a_times[0]) - (b-b_times[0])) for a, b in zip(a_times, b_times)) > .0012:
        raise ValueError("The display timelines differ")
    if every < 1 or not 1 <= stride <= min(size):
        raise ValueError("Sampling steps must be positive and fit the raster")
    histogram = np.zeros(108002, dtype=np.int64)
    samples = frames = 0
    total = 0.0
    with closing(ictcp_frames(reference, size, every, stride)) as ref, \
            closing(ictcp_frames(candidate, size, every, stride)) as out:
        for a, b in zip_longest(ref, out):
            if a is None or b is None:
                raise ValueError("Different numbers of decoded frames")
            delta = (a - b) / 65535
            delta[1] *= .5
            error = 720 * np.sqrt(np.sum(delta * delta, axis=0))
            samples += error.size
            total += np.sum(error, dtype=np.float64)
            histogram += np.bincount(np.rint(error.ravel() * 100).astype(np.int32), minlength=len(histogram))
            frames += 1
    if frames != (len(a_times) + every - 1) // every:
        raise ValueError("Decoded frame count differs from the packet timeline")
    return dict(frames=frames, pixels=samples, every=every, stride=stride, mean_delta_e_itp=total/samples,
                p95_delta_e_itp=np.searchsorted(np.cumsum(histogram), .95*samples).item()/100)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reference", type=Path)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--every", type=int, default=5, help="Select one frame in this many")
    parser.add_argument("--stride", type=int, default=4, help="Sample pitch on the full-resolution raster")
    args = parser.parse_args()
    print(json.dumps(compare(args.reference, args.candidate, args.every, args.stride), indent=2))
