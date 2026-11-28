"""Pinned offline model installation and shot-locked ITM-LUT inference."""
import hashlib
import math
import os
from pathlib import Path
import re
import shutil
import tempfile
import zipfile
import zlib

from neural import filter_path


_FILES = {
    "bases.f32": (884340, "a7201fd25898f22537ea17a34145efe566f92a079ad85fba0f7477cdf20b9060"),
    "cinema-worker.exe": (311296, "2dcb68b1bfc10433228b1fe2dde0f0b9ce36b93d4e95e973eed76ff27f114eb4"),
    "cinema.onnx": (1528453, "5bf4c693fcced2a0e3c4f5b5c5c495f9308f3a9f82c24d45751b3f1b770ce98e"),
    "cinema_worker.cpp": (7424, "7f361b6af93628a27218c2bfd50a229ef4419797a73080612ead182bd9a716f9"),
    "ITM-LUT-MPL-2.0.txt": (16725, "1f256ecad192880510e84ad60474eab7589218784b9a50bc7ceee34c2b91f1d5"),
    "ONNXRuntime-LICENSE.txt": (1094, "c250d6278f0b47a6439fb7592b08b58a55eb9f535aa49a1db63211c3f982b674"),
    "ONNXRuntime-notices.txt": (345046, "fac5bb85f568b38fd8f3a6aea32737985841f23a1a43e9ce952f79474f883aa9"),
    "onnxruntime.dll": (13557792, "c0926f3b9a7f166e13630f600d78c155562d808936ca3c3a9e0449c755ec7e39"),
    "onnxruntime_providers_shared.dll": (22048, "160233b3750ffcdbfff6880002d367b6be6044aa312bafbd8707bbc579db6bc5"),
    "SOURCES.txt": (745, "7119b510ea5e4f18ba9af90d298b436b73d2bf29f87ee33c603e5733c035aa84"),
}
_MODEL_ERROR = "缺少完整的电影调色模型，请导入官方模型包。"
_BAD_PACK = "电影调色模型包校验失败，请选择完整的官方 ZIP 文件。"
_ALIGN_ERROR = "电影调色的代表帧与源画面不一致。"
_BUNDLED_PACK = Path(__file__).resolve().parent / "assets" / "models" / "LumaFrame-Cinema-LUT-v1.zip"


def _folder():
    base = os.environ.get("LOCALAPPDATA")
    if not base:
        raise ValueError(_MODEL_ERROR)
    return Path(base) / "LumaFrame Studio" / "models" / "cinema-lut-v1"


def _verify(path, spec):
    size, expected = spec
    with path.open("rb") as stream:
        return os.fstat(stream.fileno()).st_size == size and hashlib.file_digest(stream, "sha256").hexdigest() == expected


def _ready(folder):
    try:
        return all(_verify(folder / name, spec) for name, spec in _FILES.items())
    except OSError:
        return False


def require_model():
    """Use a verified local model, or unpack the bundled copy on first use."""
    folder = _folder()
    if _ready(folder):
        return folder
    try:
        return install_model(_BUNDLED_PACK)
    except (OSError, ValueError) as exc:
        raise ValueError(_MODEL_ERROR) from exc


def ensure_auto_model():
    """A missing or unusable model leaves automatic grading on its base curve."""
    try:
        require_model()
    except (OSError, ValueError):
        return False
    return True


def verify_pack(path, *, extract_to=None):
    """Check every pinned payload, independently of ZIP compression or timestamps."""
    try:
        with zipfile.ZipFile(path) as package:
            names = package.namelist()
            if len(names) != len(_FILES) or set(names) != set(_FILES):
                raise ValueError(_BAD_PACK)
            for name, spec in _FILES.items():
                if package.getinfo(name).file_size != spec[0]:
                    raise ValueError(_BAD_PACK)
                with package.open(name) as source:
                    if extract_to is None:
                        valid = hashlib.file_digest(source, "sha256").hexdigest() == spec[1]
                    else:
                        target = extract_to / name
                        with target.open("xb") as output:
                            shutil.copyfileobj(source, output, length=1 << 20)
                        valid = _verify(target, spec)
                    if not valid:
                        raise ValueError(_BAD_PACK)
    except (OSError, zipfile.BadZipFile, RuntimeError, NotImplementedError, EOFError, zlib.error) as exc:
        raise ValueError(_BAD_PACK) from exc


def install_model(path):
    """Publish only after every member has been extracted and verified."""
    folder = _folder()
    folder.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=folder.parent, prefix=".cinema-import-") as tmp:
        fresh = Path(tmp) / "model"
        fresh.mkdir()
        verify_pack(path, extract_to=fresh)
        try:
            fresh.rename(folder)
        except OSError:
            if not folder.is_dir() or folder.is_symlink() or folder.is_junction():
                raise
            # A second importer may have completed first, or a previous model
            # file may need repair. Never replace healthy/in-use DLLs.
            for name, spec in _FILES.items():
                dest = folder / name
                if not dest.is_file() or not _verify(dest, spec):
                    os.replace(fresh / name, dest)
    if not _ready(folder):
        raise ValueError(_MODEL_ERROR)
    return folder


def _selection(indices):
    """Balanced (lazily evaluated) FFmpeg expression, stored in a filter file."""
    ranges = []
    for index in indices:
        if ranges and index == ranges[-1][1] + 1:
            ranges[-1][1] = index
        else:
            ranges.append([index, index])

    def branch(lo, hi):
        left, right = ranges[lo]
        if hi == lo + 1:
            return f"eq(n,{left})" if left == right else f"between(n,{left},{right})"
        mid = (lo + hi) // 2
        return f"if(lt(n,{ranges[mid][0]}),{branch(lo,mid)},{branch(mid,hi)})"
    return branch(0, len(ranges))


def _indices(shots, total):
    groups = []
    next_frame = 0
    for shot in shots:
        start, length = shot["start"], shot["length"]
        if start != next_frame or type(length) is not int or length < 1:
            raise ValueError(_ALIGN_ERROR)
        groups.append(sorted({start + length * p // 10 for p in (1, 3, 5, 7, 9)}))
        next_frame += length
    if not groups or next_frame != total:
        raise ValueError(_ALIGN_ERROR)
    return groups


def prepare_grades(source, video, times, shots, scratch, run, progress=None, *, active_area=None,
                   selected_only=False):
    """Infer selected shots in one decode pass, keeping the original timeline for the shader."""
    from engine import _check_cancel, _tool
    from profile7 import run_pipe

    cancel = getattr(run, "cancel", None)
    _check_cancel(cancel)
    groups = _indices(shots, len(times))
    shot_ids = [i for i, shot in enumerate(shots) if not selected_only or shot["auto_cinema"]]
    records = [dict(shot) for shot in shots]
    if not shot_ids:
        if progress is not None:
            progress(len(times), len(times))
        return None, records
    folder = require_model()
    selected = [index for i in shot_ids for index in groups[i]]
    w, h = int(video["width"]), int(video["height"])
    offsets = (0, 0, 0, 0) if active_area is None else active_area
    if (len(offsets) != 4 or any(type(v) is not int or v < 0 for v in offsets)
            or sum(offsets[:2]) >= w or sum(offsets[2:]) >= h):
        raise ValueError("逐镜调色的源画面有效区域无效。")
    left, right, top, bottom = offsets
    width, height = w-left-right, h-top-bottom
    source_range = "full" if video.get("color_range") == "pc" or video["pix_fmt"].startswith("yuvj") else "limited"
    if progress is not None:
        progress(0, len(times))
    with tempfile.TemporaryDirectory(dir=scratch, prefix="cinema_grade_") as tmp:
        root = Path(tmp)
        marks, plan, output, script = (root / n for n in ("timestamps.txt", "plan.txt", "weights.txt", "graph.txt"))
        graph = (f"settb=AVTB,select='{_selection(selected)}',"
                 "metadata=mode=add:key=lf_grade:value=1,"
                 f"metadata=mode=print:key=lf_grade:file={filter_path(marks)},"
                 f"zscale=filter=spline36:matrixin=709:rangein={source_range}:matrix=gbr:range=full,format=gbrp16le")
        if any(offsets):
            graph += f",crop={width}:{height}:{left}:{top}:exact=1"
        script.write_text(graph, encoding="utf-8")
        plan.write_text("".join(f"{len(groups[i])}\n" for i in shot_ids), encoding="ascii")
        upstream = [_tool("ffmpeg"), "-nostdin", "-v", "error", "-copyts", "-noautorotate", "-i", str(source),
                    "-map", f"0:{int(video['index'])}", "-/filter:v", str(script), "-frames:v", str(len(selected)),
                    "-fps_mode", "passthrough",
                    "-an", "-sn", "-dn", "-f", "rawvideo", "-"]
        downstream = [folder/"cinema-worker.exe", folder, plan, output, str(width), str(height)]
        read = done = 0

        def collect():
            nonlocal read, done
            if not output.exists():
                return
            with output.open("rb") as stream:
                stream.seek(read)
                for line in stream:
                    if not line.endswith(b"\n"):
                        break
                    read += len(line)
                    fields = line.split()
                    if done >= len(shot_ids) or len(fields) != 19:
                        raise ValueError(_ALIGN_ERROR)
                    index, *data = fields
                    values = [float(v) for v in data]
                    i = int(index)
                    shot_id = shot_ids[done]
                    if not 0 <= i < len(groups[shot_id]) or not all(math.isfinite(v) and 0 <= v <= 1 for v in values):
                        raise ValueError(_ALIGN_ERROR)
                    shot = records[shot_id]
                    shot.update(representative=groups[shot_id][i], means=values[:3],
                                weights=[values[k:k+5] for k in (3,8,13)])
                    done += 1
                    if progress is not None and done < len(shot_ids):
                        progress(shot["start"] + shot["length"], len(times))
        run_pipe(upstream, downstream, run, root, failure="电影调色模型推理失败。", on_poll=collect)
        collect()
        count = 0
        with marks.open(encoding="utf-8") as stream:
            for line in stream:
                if line.startswith("frame:"):
                    m = re.match(r"frame:(\d+)\s+pts:(-?\d+)\s", line)
                    if (m is None or count >= len(selected) or int(m[1]) != count
                            or abs(int(m[2]) - times[selected[count]]) > 1):
                        raise ValueError(_ALIGN_ERROR)
                    if count % 256 == 0:
                        _check_cancel(cancel)
                    count += 1
        if count != len(selected) or done != len(shot_ids):
            raise ValueError(_ALIGN_ERROR)
    _check_cancel(cancel)
    if progress is not None:
        progress(len(times), len(times))
    return folder / "bases.f32", records
