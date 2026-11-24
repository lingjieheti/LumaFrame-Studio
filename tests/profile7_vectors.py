"""Build controlled P7 sources whose only independent variable is the EL picture.

Run with the pinned Windows media tools from tools/fetch_tools.py. The resulting
short clips are for format and differential testing, not subjective film grading.
"""

import argparse
import hashlib
import subprocess
from pathlib import Path
from urllib.request import urlopen

APP = Path(__file__).resolve().parents[1]
TOOLS = APP / "bin"
FRAMES = 8
VECTORS = {
    "fel_orig": "b2b27714b7279c4e24d1a795cb6f95d3ad06745db96d360698ee0932184117a0",
    "mel_orig": "cabda945d36cd4c3eca9c2df05cf387562e648e3250be28632c9b2991f2ec60b",
}


def run(*args):
    result = subprocess.run([str(arg) for arg in args], capture_output=True)
    if result.returncode:
        raise RuntimeError((result.stdout + result.stderr).decode("utf-8", "replace")[-2500:])


def picture(path, filtergraph):
    # B-frame-free elementary streams provide the same decode/display order for
    # both layers. Signal HEVC Main10 / limited-range BT.2020 PQ on the base.
    run(
        TOOLS / "ffmpeg.exe", "-nostdin", "-v", "error", "-f", "lavfi", "-i", filtergraph,
        "-frames:v", FRAMES, "-pix_fmt", "yuv420p10le", "-c:v", "libx265",
        "-preset", "ultrafast", "-x265-params",
        "keyint=8:bframes=0:pools=4:frame-threads=2:crf=12:colorprim=bt2020:transfer=smpte2084:colormatrix=bt2020nc",
        "-color_primaries", "bt2020", "-colorspace", "bt2020nc", "-color_trc", "smpte2084",
        "-color_range", "tv", "-f", "hevc", path,
    )


def build(folder):
    folder.mkdir(parents=True, exist_ok=True)
    template = "nullsrc=s=960x540:r=24,format=yuv420p10le,geq=lum='{y}':cb=512:cr=512"
    picture(folder / "base.hevc", "testsrc2=size=1920x1080:rate=24,format=yuv420p10le")
    picture(folder / "neutral.hevc", template.format(y="512"))
    picture(folder / "patch.hevc", template.format(y="if(between(X,W/4,W*3/4)*between(Y,H/4,H*3/4),760,512)"))
    encoder = TOOLS / "dovi_tool.exe"
    for stem, digest in VECTORS.items():
        url = "https://raw.githubusercontent.com/quietvoid/dovi_tool/2.3.4/assets/tests/" + stem + ".bin"
        with urlopen(url, timeout=25) as remote:
            unit = remote.read()
        if hashlib.sha256(unit).hexdigest() != digest:
            raise RuntimeError(f"Upstream RPU mismatch for {stem}")
        rpu = folder / (stem + ".rpu")
        rpu.write_bytes(unit * FRAMES)
        for effect in (("neutral", "patch") if stem == "fel_orig" else ("neutral",)):
            name = stem[:3] + "-" + effect
            el = folder / (name + "-el.hevc")
            stream = folder / (name + ".hevc")
            run(encoder, "inject-rpu", "-i", folder / (effect + ".hevc"), "-r", rpu, "-o", el)
            run(encoder, "mux", "--bl", folder / "base.hevc", "--el", el, "-o", stream)
            run(TOOLS / "mkvtoolnix/mkvmerge.exe", "-q", "-o", folder / (name + ".mkv"),
                "--default-duration", "0:24fps", stream)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("new_directory", type=Path)
    options = parser.parse_args()
    if options.new_directory.exists() and any(options.new_directory.iterdir()):
        parser.error("choose a new or empty directory")
    build(options.new_directory)
