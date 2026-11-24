"""Validate Dolby Vision Profile 7 carriage, layer identity and frame correspondence."""

import hashlib
import json
import re
import subprocess
import tempfile
import time
from pathlib import Path

import dovi


def config(stream):
    records = [
        r
        for r in stream.get("side_data_list", ())
        if r.get("side_data_type") == "DOVI configuration record"
    ]
    return records[0] if len(records) == 1 else {}


def validate_base(stream, record, *, split=False):
    if (
        stream.get("codec_name") != "hevc"
        or stream.get("pix_fmt") not in ("p010le", "yuv420p10le")
        or (
            stream.get("color_range"),
            stream.get("color_space"),
            stream.get("color_transfer"),
            stream.get("color_primaries"),
        )
        != ("tv", "bt2020nc", "smpte2084", "bt2020")
    ):
        raise ValueError("Profile 7 基础层必须为有限范围的 BT.2020/PQ HEVC Main 10。")
    expected = dict(
        dv_profile=7, rpu_present_flag=1, el_present_flag=1, bl_present_flag=int(not split)
    )
    if any(record.get(k) != value for k, value in expected.items()):
        raise ValueError("Profile 7 的基础层、增强层或 RPU 配置记录不完整。")
    if record.get("dv_bl_signal_compatibility_id") not in (0, 6):
        raise ValueError("不支持此 Profile 7 基础层兼容类型。")


def _boxes(file, begin, end):
    while begin + 8 <= end:
        file.seek(begin)
        header = file.read(8)
        if len(header) != 8:
            raise ValueError("MP4 box 头不完整。")
        size = int.from_bytes(header[:4], "big")
        body = begin + 8
        if size == 1:
            size = int.from_bytes(file.read(8), "big")
            body += 8
        elif size == 0:
            size = end - begin
        finish = begin + size
        if finish < body or finish > end:
            raise ValueError("MP4 box 长度不合法。")
        yield header[4:], body, finish
        begin = finish


def _vdep(path, owner, base):
    # Dolby Vision Streams Within the ISO Base Media File Format, Annex C.
    # Seeking over media payloads keeps identification independent of file length.
    path = Path(path)
    with path.open("rb") as file:
        for kind, start, end in _boxes(file, 0, path.stat().st_size):
            if kind != b"moov":
                continue
            for kind, a, b in _boxes(file, start, end):
                if kind != b"trak":
                    continue
                track, refs = None, []
                for kind, c, d in _boxes(file, a, b):
                    if kind == b"tkhd":
                        file.seek(c)
                        version = file.read(1)
                        offset = {b"\x00": 12, b"\x01": 20}.get(version)
                        if offset is not None and c + offset + 4 <= d:
                            file.seek(c + offset)
                            track = int.from_bytes(file.read(4), "big")
                    elif kind == b"tref":
                        for tag, e, f in _boxes(file, c, d):
                            if tag == b"vdep":
                                if f - e != 4:
                                    return False
                                file.seek(e)
                                refs.append(int.from_bytes(file.read(4), "big"))
                if track == owner:
                    return refs == [base]
    return False


def track_pair(path, data, videos):
    """Accept two video tracks only when the MP4 explicitly links the DoVi EL to its BL."""
    if len(videos) != 2 or "mov" not in data.get("format", {}).get("format_name", "").split(","):
        return None
    for bl, el in (videos, videos[::-1]):
        record = config(el)
        if not (
            el.get("codec_name") == "hevc"
            and el.get("codec_tag_string") in {"dvhe", "dvh1"}
            and record.get("dv_profile") == 7
            and record.get("bl_present_flag") == 0
            and record.get("el_present_flag") == 1
            and record.get("rpu_present_flag") == 1
            and bl.get("codec_name") == "hevc"
            and not config(bl)
        ):
            continue
        try:
            owner, base = (int(str(s["id"]), 0) for s in (el, bl))
        except (KeyError, ValueError):
            continue
        if owner != base and _vdep(path, owner, base):
            return bl, el
    return None


def classify(rpu, count, run):
    summary = dovi._run(run, "info", "-i", rpu, "-s")
    found = re.search(r"(?m)^\s*Profile: 7 \((FEL|MEL|FEL, MEL)\)\s*$", summary)
    frames = re.search(r"(?m)^\s*Frames:\s*(\d+)\s*$", summary)
    if not found or not frames or (count is not None and int(frames[1]) != count):
        raise ValueError("Profile 7 全部 RPU 的类型或帧数与视频不一致。")
    return found[1], int(frames[1])


def layer_info(layer, ffprobe, run):
    data = json.loads(
        run(
            [
                ffprobe,
                "-v",
                "error",
                "-count_packets",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=codec_name,width,height,pix_fmt,nb_read_packets",
                "-of",
                "json",
                layer,
            ]
        )
    )
    streams = data.get("streams", [])
    if (
        len(streams) != 1
        or streams[0].get("codec_name") != "hevc"
        or streams[0].get("pix_fmt") != "yuv420p10le"
    ):
        raise ValueError("无法验证独立的 10 位 HEVC 基础层或增强层。")
    return streams[0]


def layers(base, enhancement, expected, width, height, ffprobe, run):
    bl, el = (layer_info(path, ffprobe, run) for path in (base, enhancement))
    if (
        (bl["width"], bl["height"]) != (width, height)
        or (el["width"], el["height"]) not in ((width, height), (width // 2, height // 2))
        or any(str(s.get("nb_read_packets")) != str(expected) for s in (bl, el))
    ):
        raise ValueError("基础层与增强层尺寸或逐帧数量不匹配。")


def run_pipe(upstream, downstream, run, work):
    """Pipe the Annex B byte stream without buffering a film in RAM or on disk."""
    cancel = getattr(run, "cancel", None)
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    producer = consumer = None
    with tempfile.TemporaryFile(dir=work) as src_log, tempfile.TemporaryFile(dir=work) as dst_log:
        try:
            if cancel is not None and cancel.is_set():
                raise InterruptedError("任务已取消。")
            producer = subprocess.Popen(
                upstream,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=src_log,
                creationflags=flags,
            )
            try:
                consumer = subprocess.Popen(
                    downstream,
                    stdin=producer.stdout,
                    stdout=dst_log,
                    stderr=subprocess.STDOUT,
                    creationflags=flags,
                )
            finally:
                producer.stdout.close()
            while True:
                if cancel is not None and cancel.is_set():
                    raise InterruptedError("任务已取消。")
                statuses = (producer.poll(), consumer.poll())
                if any(status not in (None, 0) for status in statuses):
                    messages = []
                    stages = zip((upstream, downstream), statuses, (src_log, dst_log))
                    for cmd, status, log in stages:
                        end = log.seek(0, 2)
                        log.seek(max(0, end - 1800))
                        detail = log.read().decode("utf-8", "replace").strip()
                        if status not in (None, 0) or detail:
                            label = Path(cmd[0]).name
                            if status is not None:
                                label += f" ({status})"
                            messages.append(f"{label} {detail}".rstrip())
                    raise RuntimeError("双层 HEVC 流式拆分失败。\n" + "\n".join(messages))
                if statuses == (0, 0):
                    return
                time.sleep(0.05)
        finally:
            for process in (consumer, producer):
                if process is not None and process.poll() is None:
                    process.kill()
            for process in (consumer, producer):
                if process is not None:
                    process.wait()


def prepare(
    source,
    base_index,
    el_index,
    work,
    width,
    height,
    expected,
    extract,
    ffprobe,
    run,
    *,
    limit=None,
):
    """Return elementary BL, EL, original P7 RPU, classified layer type and display-frame count."""
    base, el = work / "BL.hevc", work / "EL.hevc"
    if el_index is None:
        demux = [dovi.find_dovi_tool(), "demux", "-i", "-", "-b", base, "-e", el]
        extract(source, base_index, "pipe:1", run, limit=limit, downstream=demux)
    else:
        extract(source, base_index, base, run, limit=limit)
        extract(source, el_index, el, run, limit=limit)
    rpu = work / "unaltered.rpu"
    dovi._run(run, "extract-rpu", "-i", el, "-o", rpu)
    layer, frames = classify(rpu, expected, run)
    layers(base, el, frames, width, height, ffprobe, run)
    return base, el, rpu, layer, frames


def signature(path, cancel):
    """Fingerprint coded pictures/SEI in order, and unique decoder parameter sets.

    Access unit delimiters and repeated identical VPS/SPS/PPS can legitimately
    differ between Annex B and a container configuration record.
    """

    def units():
        pattern = re.compile(b"\x00\x00\x00?\x01")
        pending = b""
        with Path(path).open("rb") as source:
            while chunk := source.read(2 * 1024 * 1024):
                if cancel is not None and cancel.is_set():
                    raise InterruptedError("任务已取消。")
                pieces = pattern.split(pending + chunk)
                yield from pieces[:-1]
                pending = pieces[-1]
                if len(pending) > 128 * 1024 * 1024:
                    raise ValueError("双层 HEVC 数据缺少有效的 NAL 边界。")
            yield pending

    sets, frames, payload = set(), 0, hashlib.sha256()
    prefix_sei = []
    for raw in units():
        unit = raw.rstrip(b"\0")
        if len(unit) < 2:
            continue
        kind = (unit[0] >> 1) & 0x3F
        if kind in (32, 33, 34):
            sets.add(hashlib.sha256(unit).digest())
        elif kind == 39:
            # hvcC may repeat an identical prefix SEI before each IRAP.
            block = hashlib.sha256(unit).digest()
            if block not in prefix_sei:
                prefix_sei.append(block)
        elif kind not in (35, 36, 37, 38):
            if kind < 32 and len(unit) >= 3 and unit[2] & 0x80:
                for block in sorted(prefix_sei):
                    payload.update(block)
                prefix_sei.clear()
                frames += 1
            payload.update(len(unit).to_bytes(8, "big"))
            payload.update(unit)
    if not sets or not frames:
        raise ValueError("增强层或基础层缺少可验证的视频图像。")
    return frames, hashlib.sha256(b"".join(sorted(sets)) + payload.digest()).hexdigest()
