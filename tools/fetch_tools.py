"""Fetch the upstream Windows x64 tools used in LumaFrame Studio 1.0.0."""

import argparse
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
import zipfile


@dataclass(frozen=True)
class Package:
    url: str
    sha256: str
    # Empty archive member means copying the authenticated download itself.
    files: dict[str, str]


GH = "https://github.com"
RAW = "https://raw.githubusercontent.com"
GNU3 = "8ceb4b9ee5adedde47b31e975c1d90c73ad27b6b165a1dcd80c7c545eb65b903"
LGNU3 = "da7eabb7bafdf7d3ae5e9f223aa5bdc1eece45ac569dc21b3b037520b4464768"
DOVI_MIT = "e9d76c149b1176de4aa0202cfb6a4a266a0c4cb71959f429d670c3a857f95e52"
MKV_NOTICES = (
    "Boost-1.0",
    "CC-BY-3.0",
    "LGPL-2.1",
    "LGPL-3.0",
    "nlohmann-json-MIT",
    "pugixml-MIT",
    "QtWaitingSpinner-MIT",
)
NV_FILES = (
    "NVEncC64.exe",
    "avcodec-63.dll",
    "avdevice-63.dll",
    "avfilter-12.dll",
    "avformat-63.dll",
    "avutil-61.dll",
    "swresample-7.dll",
)
PACKAGES = (
    Package(
        f"{GH}/GyanD/codexffmpeg/releases/download/8.1/ffmpeg-8.1-full_build.7z",
        "4dcbe10fa72bffa3e976d09cf3335f53a2716a8041af7a047609898fe6019069",
        {
            "ffmpeg-8.1-full_build/bin/ffmpeg.exe": "bin/ffmpeg.exe",
            "ffmpeg-8.1-full_build/bin/ffprobe.exe": "bin/ffprobe.exe",
            "ffmpeg-8.1-full_build/LICENSE": "licenses/FFmpeg.txt",
        },
    ),
    Package(
        f"{GH}/quietvoid/dovi_tool/releases/download/2.3.4/dovi_tool-2.3.4-x86_64-pc-windows-msvc.zip",
        "0d9733d311dfe49f9dca0c350f7e6d978182673d333c6f07f7365daf9e6c81eb",
        {"dovi_tool.exe": "bin/dovi_tool.exe"},
    ),
    Package(
        "https://mkvtoolnix.download/windows/releases/104.0/mkvtoolnix-64-bit-104.0.7z",
        "4d14b615819d2e106893d68114e45f7bbe8092dcb4c7c3599a0c0a52313321a9",
        {
            "mkvtoolnix/mkvmerge.exe": "bin/mkvtoolnix/mkvmerge.exe",
            "mkvtoolnix/doc/COPYING.txt": "bin/mkvtoolnix/doc/COPYING.txt",
            **{
                f"mkvtoolnix/doc/licenses/{n}.txt": f"bin/mkvtoolnix/doc/licenses/{n}.txt"
                for n in MKV_NOTICES
            },
        },
    ),
    Package(
        f"{GH}/rigaya/NVEnc/releases/download/9.41/NVEncC_9.41_x64.7z",
        "718ce2084d45e56706149bb9cf5d96a5ea697c4c5f75a2f256ebf79918259da2",
        {name: f"bin/nvenc/{name}" for name in NV_FILES},
    ),
    Package(
        f"{GH}/igv/FSRCNN-TensorFlow/releases/download/1.1/FSRCNNX_x2_16-0-4-1.glsl",
        "d5a24a271e5d9a3f7f7a053b150c460a44c25b3cf7f770857d57cc3a2e1c9965",
        {"": "shaders/FSRCNNX_x2_16-0-4-1.glsl"},
    ),
    Package(
        f"{RAW}/quietvoid/dovi_tool/2.3.4/LICENSE", DOVI_MIT, {"": "licenses/dovi_tool-LICENSE.txt"}
    ),
    Package(
        f"{RAW}/quietvoid/dovi_tool/2.3.4/dolby_vision/LICENSE",
        DOVI_MIT,
        {"": "licenses/libdovi-LICENSE.txt"},
    ),
    Package(
        f"{RAW}/rigaya/NVEnc/9.41/NVEnc_license.txt",
        "a1af95239087bb32fe522c1ba673906733158346ba2f839e598b38468b4df351",
        {"": "licenses/NVEnc-license.txt"},
    ),
    Package(
        f"{RAW}/NVIDIAGameWorks/NVIDIAImageScaling/35e13ba316c98eeecf16f37eae70ce88019911f6/licence.txt",
        "62207f1c0fae72800ea7d28ee108e7dbeb852dde20b00c70fc7b0d235e19e46d",
        {"": "licenses/NVIDIA-Image-Scaling-LICENSE.txt"},
    ),
    Package(
        f"{RAW}/igv/FSRCNN-TensorFlow/1.1/LICENSE",
        "a3877cc78b2f5819135bc419775a4afa98ce0c70d72360ed78fe3895fa1df2b5",
        {"": "licenses/FSRCNNX-training-license.txt"},
    ),
    Package(f"{RAW}/FFmpeg/FFmpeg/n8.1/COPYING.LGPLv3", LGNU3, {"": "licenses/FSRCNNX-LGPLv3.txt"}),
    Package(
        f"{RAW}/qtproject/pyside-pyside-setup/v6.9.3/LICENSES/LGPL-3.0-only.txt",
        LGNU3,
        {"": "licenses/Qt-LGPL-3.0.txt"},
    ),
    Package(
        f"{RAW}/qtproject/pyside-pyside-setup/v6.9.3/LICENSES/GPL-3.0-only.txt",
        GNU3,
        {"": "licenses/Qt-GPL-3.0.txt"},
    ),
    Package(
        f"{RAW}/python/cpython/v3.12.10/LICENSE",
        "3b2f81fe21d181c499c59a256c8e1968455d6689d269aa85373bfb6af41da3bf",
        {"": "licenses/Python-3.12.10.txt"},
    ),
    Package(
        f"{RAW}/pyinstaller/pyinstaller/v6.16.0/COPYING.txt",
        "dcf75fdb959db1e3b41c0f8505069d2ece781b5ec6b3d0a4d30975cfc6580245",
        {"": "licenses/PyInstaller-6.16.0.txt"},
    ),
)


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def fetch(pkg, cache):
    name = Path(urlsplit(pkg.url).path).name
    target = cache / (pkg.sha256[:12] + "-" + name)
    if target.is_file() and digest(target) == pkg.sha256:
        return target
    for attempt in range(3):
        fd, name = tempfile.mkstemp(prefix="part-", dir=cache)
        temporary = Path(name)
        try:
            print(f"Downloading {pkg.url}", flush=True)
            request = Request(pkg.url, headers={"User-Agent": "LumaFrameStudio/1.0.0"})
            with os.fdopen(fd, "wb") as output, urlopen(request, timeout=60) as response:
                shutil.copyfileobj(response, output, length=1024 * 1024)
            actual = digest(temporary)
            if actual != pkg.sha256:
                raise ValueError(f"SHA-256 mismatch for {pkg.url}: {actual}")
            os.replace(temporary, target)
            return target
        except OSError:
            if attempt == 2:
                raise
            time.sleep(2**attempt)
        finally:
            temporary.unlink(missing_ok=True)


def place(source, dest):
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=dest.parent, delete=False) as writer:
        tmp = Path(writer.name)
    try:
        shutil.copyfile(source, tmp)
        os.replace(tmp, dest)
    finally:
        tmp.unlink(missing_ok=True)


def install(pkg, archive, dest, sevenzip):
    if "" in pkg.files:
        place(archive, dest / pkg.files[""])
        return
    with tempfile.TemporaryDirectory() as tmp:
        stage = Path(tmp)
        if archive.suffix.lower() == ".zip":
            with zipfile.ZipFile(archive) as zipped:
                for member in pkg.files:
                    local = stage / member
                    local.parent.mkdir(parents=True, exist_ok=True)
                    with zipped.open(member) as source, local.open("wb") as target:
                        shutil.copyfileobj(source, target)
        else:
            members = [str(Path(name)) for name in pkg.files]
            subprocess.run(
                [sevenzip, "x", "-y", "-bso0", f"-o{stage}", str(archive), *members], check=True
            )
        for member, target in pkg.files.items():
            place(stage / member, dest / target)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dest",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Project directory receiving bin, shaders and licenses",
    )
    parser.add_argument("--cache", type=Path, help="Verified download cache (default .tools-cache)")
    parser.add_argument("--seven-zip", help="Path to 7z.exe (7-Zip)")
    args = parser.parse_args()
    sevenzip = (
        args.seven_zip
        or shutil.which("7z")
        or str(Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "7-Zip" / "7z.exe")
    )
    if not (shutil.which(sevenzip) or Path(sevenzip).is_file()):
        parser.error("Install 7-Zip or specify --seven-zip.")
    cache = (args.cache or args.dest / ".tools-cache").resolve()
    cache.mkdir(parents=True, exist_ok=True)
    for pkg in PACKAGES:
        install(pkg, fetch(pkg, cache), args.dest, sevenzip)
    print("Installed all pinned tools, shader and licenses.")


if __name__ == "__main__":
    main()
