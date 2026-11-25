"""Verify that two complete Dolby Vision MKVs contain the same video, frames and metadata.

python tools/bench/verify_transport.py reference.mkv fast.mkv --bin C:/path/to/installed/bin
The JSON report omits input filenames and lists decoded or metadata frame differences by display index.
"""
import argparse
from fractions import Fraction
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import tempfile


def inspect_video(path, tools, scratch):
    ff = tools / "ffmpeg.exe"
    ffprobe = tools / "ffprobe.exe"
    entries = "stream=index,time_base,codec_name,width,height,pix_fmt,color_space,color_transfer,color_primaries,color_range:stream_side_data:packet=pts,duration"
    data = json.loads(subprocess.check_output([ffprobe, "-v", "error", "-select_streams", "V:0",
                                               "-show_entries", entries, "-of", "json", path]))
    stream = data["streams"][0]
    if stream.get("codec_name") != "hevc":
        raise ValueError("Expected an HEVC video stream")
    time_base = Fraction(stream["time_base"])
    timeline = sorted((int(p["pts"]) * time_base,
                       int(p["duration"]) * time_base if "duration" in p else None) for p in data["packets"])
    if not timeline:
        raise ValueError("Missing video packets")
    common = [ff, "-nostdin", "-v", "error", "-threads", "6", "-i", path, "-map", "0:V:0"]
    digest = sha256()
    with subprocess.Popen([*common, "-c", "copy", "-bsf:v", "hevc_mp4toannexb", "-f", "hevc", "pipe:1"],
                          stdout=subprocess.PIPE) as proc:
        for block in iter(lambda: proc.stdout.read(2**20), b""):
            digest.update(block)
        if proc.wait():
            raise RuntimeError("Failed to extract the complete HEVC elementary stream")
    framehash = subprocess.check_output([*common, "-fps_mode", "passthrough", "-pix_fmt", "yuv420p10le",
                                         "-f", "framehash", "-hash", "sha256", "pipe:1"]).decode("ascii")
    pixels = [line.rsplit(",", 1)[-1].strip() for line in framehash.splitlines() if line and not line.startswith("#")]
    rpu = scratch / "metadata.rpu"
    subprocess.run([tools / "dovi_tool.exe", "extract-rpu", "-i", path, "-o", rpu], check=True,
                   stdout=subprocess.DEVNULL)
    content = rpu.read_bytes()
    marker = b"\x00\x00\x00\x01\x19"
    if not content.startswith(marker):
        raise ValueError("Unexpected Dolby Vision RPU framing")
    units = [sha256(b"\x19" + n).digest() for n in content[len(marker):].split(marker)]
    if len(units) != len(timeline) or len(pixels) != len(timeline):
        raise ValueError("Video packets, decoded frames and Dolby Vision RPUs must have the same count")
    info = {k: v for k, v in stream.items() if k not in {"time_base", "index"}}
    return timeline, pixels, units, dict(frames=len(units), hevc_sha256=digest.hexdigest(),
                                        rpu_sha256=sha256(content).hexdigest(), format=info)


def verify(reference, candidate, tools):
    with tempfile.TemporaryDirectory() as left, tempfile.TemporaryDirectory() as right:
        t1, pix1, rpu1, a = inspect_video(reference, tools, Path(left))
        t2, pix2, rpu2, b = inspect_video(candidate, tools, Path(right))
    def differences(first, second):
        return [i for i in range(max(len(first), len(second))) if i >= len(first) or i >= len(second) or first[i] != second[i]]
    fields = dict(display_pts=differences([p[0] for p in t1], [p[0] for p in t2]),
                  packet_durations=differences([p[1] for p in t1], [p[1] for p in t2]),
                  decoded_yuv420p10le=differences(pix1, pix2), rpu=differences(rpu1, rpu2))
    return dict(identical=a == b and not any(fields.values()), reference=a, candidate=b, different_frames=fields)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reference", type=Path)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--bin", type=Path, default=Path(__file__).resolve().parents[2] / "bin")
    args = parser.parse_args()
    result = verify(args.reference, args.candidate, args.bin.resolve())
    print(json.dumps(result, indent=2, ensure_ascii=False))
    raise SystemExit(0 if result["identical"] else 1)
