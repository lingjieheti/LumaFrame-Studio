"""Check LumaFrame's composed pixels against FelBaker 1.0.0's published RGB hashes.

Run on Windows x64 after provisioning the app's bin directory. Downloads only the
two pinned upstream HEVC test streams into --cache. The test videos stay local.
"""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
from felbake import _COMPOSE_RGB, _runtime
from fetch_tools import Package, fetch


SOURCE = "https://raw.githubusercontent.com/bbeny123/felbaker/1.0.0/test/samples"
STREAMS = {
    "BL": "c44c7750f6fa97d8ce6a040095366e70ac06b0a072b716b28e36978f1a75c041",
    "EL": "b60aaf504757722bc196dbfd871ac1239badbfc80c47e6f2498e1eb31f526bf5",
}
# https://github.com/bbeny123/felbaker/blob/1.0.0/test/tests.toml
GOLDEN = {
    50: "1006d599bcc07fe97d49c7289f7a3d9a6412f403a8238b73ba84b839d494429e",
    100: "768a9ebe3bd512a834db8f36e426b686ca3164ed11578d5571029122395bda1d",
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, default=ROOT / ".tools-cache/fel-test-streams")
    opts = parser.parse_args()
    runtime = _runtime()
    cache = opts.cache.resolve()
    cache.mkdir(parents=True, exist_ok=True)
    sources = {
        name: fetch(Package(f"{SOURCE}/{name}-150.hevc", sha, {}), cache)
        for name, sha in STREAMS.items()
    }
    with tempfile.TemporaryDirectory(dir=cache) as td:
        temp = Path(td)
        rpu = temp / "original.rpu"
        subprocess.run(
            [ROOT / "bin/dovi_tool.exe", "extract-rpu", "-i", sources["EL"], "-o", rpu], check=True
        )
        config = temp / "inputs.json"
        config.write_text(
            json.dumps(
                {
                    "bl": str(sources["BL"]),
                    "el": str(sources["EL"]),
                    "rpu": str(rpu),
                    "width": 3840,
                    "height": 2160,
                    "frames": 150,
                }
            ),
            encoding="utf-8",
        )
        worker = temp / "golden.py"
        # The external VSPipe harness writes RGB48 as planar G/B/R, while
        # VideoNode.output writes planar R/G/B. Match the upstream byte order.
        worker.write_text(
            _COMPOSE_RGB
            + """
gbr = core.std.ShufflePlanes([rgb, rgb, rgb], planes=[1, 2, 0], colorfamily=vs.RGB)
gbr[:int(sys.argv[2])].output(sys.stdout.buffer, prefetch=4)
""",
            encoding="utf-8",
        )
        for count, expected in GOLDEN.items():
            with subprocess.Popen(
                [runtime, "-I", worker, config, str(count)], stdout=subprocess.PIPE
            ) as child:
                actual = hashlib.file_digest(child.stdout, "sha256").hexdigest()
                if child.wait() or actual != expected:
                    raise RuntimeError(f"{count} frames: expected {expected}, got {actual}")
            print(f"{count} frames, SHA-256 {actual}: matched", flush=True)


if __name__ == "__main__":
    main()
