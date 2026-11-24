"""Provision the isolated, version-pinned Windows x64 FEL composition runtime."""

import argparse
import io
import json
import os
from pathlib import Path
import shutil
import zipfile

from fetch_tools import Package, digest, fetch, install


PYTHON = Package(
    "https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip",
    "4acbed6dd1c744b0376e3b1cf57ce906f9dc9e95e68824584c8099a63025a3c3",
    {},
)
VS = Package(
    "https://github.com/vapoursynth/vapoursynth/releases/download/R73/VapourSynth64-Portable-R73.zip",
    "3326f10d0fdcdec45649a474cbc9810795ab3da422634d0f134bca6089afbb91",
    {},
)
FFMS = Package(
    "https://github.com/FFMS/ffms2/releases/download/5.0/ffms2-5.0-msvc.7z",
    "e867a3df7262865107df40f230f5b8e1455905eba9b8852e6f35b1227537caeb",
    {
        "ffms2-5.0-msvc/x64/ffms2.dll": "bin/fel/ffms2.dll",
        "ffms2-5.0-msvc/COPYING.GPLv3": "licenses/fel-ffms2-GPLv3.txt",
    },
)
FEL = Package(
    "https://github.com/bbeny123/felbaker/releases/download/1.0.0/felbaker-win-x64-avx2.zip",
    "d98be96bc8f5e68785c0089fc6eab9e8a678f2bff3bb1cd25684d37c2f1adb79",
    {"felbaker.dll": "bin/fel/felbaker.dll"},
)


def provision(dest, cache, sevenzip):
    dest = Path(dest).resolve()
    cache = Path(cache).resolve()
    cache.mkdir(parents=True, exist_ok=True)
    runtime = dest / "bin" / "fel"
    runtime.mkdir(parents=True, exist_ok=True)
    (dest / "licenses").mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(fetch(PYTHON, cache)) as archive:
        for name in (
            "python.exe",
            "python312.dll",
            "python3.dll",
            "python312.zip",
            "python312._pth",
            "_socket.pyd",
            "select.pyd",
            "unicodedata.pyd",
            "_queue.pyd",
            "_overlapped.pyd",
            "_asyncio.pyd",
            "_ssl.pyd",
            "libssl-3.dll",
            "libcrypto-3.dll",
            "_hashlib.pyd",
            "_bz2.pyd",
            "_lzma.pyd",
            "_ctypes.pyd",
            "libffi-8.dll",
            "LICENSE.txt",
        ):
            archive.extract(name, runtime)
    with zipfile.ZipFile(fetch(VS, cache)) as archive:
        for name in (
            "concrt140.dll",
            "msvcp140.dll",
            "msvcp140_1.dll",
            "msvcp140_2.dll",
            "msvcp140_atomic_wait.dll",
            "msvcp140_codecvt_ids.dll",
            "vcruntime140.dll",
            "vcruntime140_1.dll",
            "vcruntime140_threads.dll",
            "portable.vs",
        ):
            archive.extract(name, runtime)
        wheel_name = "wheel/vapoursynth-73-cp312-abi3-win_amd64.whl"
        with zipfile.ZipFile(io.BytesIO(archive.read(wheel_name))) as wheel:
            wheel.extract("vapoursynth.pyd", runtime)
            for member, target in (
                (
                    "vapoursynth-73.data/data/Lib/site-packages/vapoursynth.dll",
                    runtime / "vapoursynth.dll",
                ),
                (
                    "vapoursynth-73.dist-info/licenses/COPYING.LESSER",
                    dest / "licenses/fel-vapoursynth-LGPL21.txt",
                ),
            ):
                target.write_bytes(wheel.read(member))
    for pkg in (FFMS, FEL):
        install(pkg, fetch(pkg, cache), dest, sevenzip)
    for dirname in ("vs-coreplugins", "vs-plugins"):
        folder = runtime / dirname
        folder.mkdir(exist_ok=True)
        (folder / "empty.txt").touch()
    manifest = runtime / "runtime-sha256.json"
    files = [p for p in runtime.iterdir() if p.is_file() and p != manifest]
    manifest.write_text(
        json.dumps({p.name: digest(p) for p in sorted(files)}, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dest", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--cache", type=Path)
    parser.add_argument("--seven-zip")
    args = parser.parse_args()
    sevenzip = (
        args.seven_zip
        or shutil.which("7z")
        or str(Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "7-Zip/7z.exe")
    )
    provision(args.dest, args.cache or args.dest / ".tools-cache", sevenzip)
