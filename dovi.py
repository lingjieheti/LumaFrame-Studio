"""Dolby Vision metadata handling for single-layer HEVC Profile 8.1."""

import hashlib
import json
from pathlib import Path
import re
import shutil
import sys
import tempfile


def find_dovi_tool():
    bases = [Path(__file__).resolve().parent]
    if getattr(sys, "frozen", False):
        bases += [Path(sys.executable).resolve().parent, Path(getattr(sys, "_MEIPASS", bases[0]))]
    for root in bases:
        path = root / "bin" / "dovi_tool.exe"
        if path.is_file():
            return path
    raise FileNotFoundError("缺少配套的 bin/dovi_tool.exe，请完整解压软件。")


def validate_profile(stream):
    """Check the decoder configuration and the actual base-layer color description."""
    if stream.get("codec_name") != "hevc":
        raise ValueError("杜比视界保留模式仅支持 HEVC Profile 8.1 片源。")
    records = [
        s
        for s in stream.get("side_data_list", [])
        if s.get("side_data_type", "").lower() == "dovi configuration record"
    ]
    if len(records) != 1:
        raise ValueError("未读到唯一的 Dolby Vision 配置记录，无法确认片源是 Profile 8.1。")
    config = records[0]
    required = {
        "dv_profile": 8,
        "dv_bl_signal_compatibility_id": 1,
        "bl_present_flag": 1,
        "rpu_present_flag": 1,
        "el_present_flag": 0,
    }
    if any(str(config.get(key)) != str(value) for key, value in required.items()):
        raise ValueError("片源不是单层 HEVC Dolby Vision Profile 8.1（HDR10 兼容）。")
    if stream.get("pix_fmt") not in {"yuv420p10le", "p010le"}:
        raise ValueError("杜比视界保留模式要求 10-bit 4:2:0 基础层。")
    expected = {
        "color_range": "tv",
        "color_transfer": "smpte2084",
        "color_primaries": "bt2020",
        "color_space": "bt2020nc",
    }
    for field, value in expected.items():
        if stream.get(field) != value:
            raise ValueError(
                f"Profile 8.1 需要 HDR10 基础层，{field} 应为 {value}，实际为 {stream.get(field)}。"
            )
    return dict(config)


def _run(run, *args):
    return run([str(find_dovi_tool()), *(str(a) for a in args)])


def _count_profile8(path, run):
    summary = _run(run, "info", "-i", path, "-s")
    if not isinstance(summary, str):
        raise TypeError("dovi_tool 的运行器需要返回 stdout 文本。")
    if not re.search(r"(?m)^\s*Profile:\s*8\s*$", summary):
        raise ValueError("RPU 实际内容不是统一的 Profile 8，或解析失败。")
    match = re.search(r"(?m)^\s*Frames:\s*(\d+)\s*$", summary)
    if not match or int(match.group(1)) < 1:
        raise ValueError("没有可验证的杜比视界逐帧元数据。")
    return int(match.group(1))


def _digest(path):
    checksum = hashlib.sha256()
    with Path(path).open("rb") as src:
        for block in iter(lambda: src.read(1024 * 1024), b""):
            checksum.update(block)
    return checksum.hexdigest()


def _export_area(rpu, target, run):
    # --data uses a comma-separated key=value list; run from the chosen directory
    # via a temporary file with no commas in its path when required.
    target = Path(target).resolve()
    if "," in str(target) or "=" in str(target):
        base = Path(tempfile.gettempdir()).resolve()
        if "," in str(base) or "=" in str(base):
            raise ValueError(
                "dovi_tool 的 L5 导出路径与临时目录均含逗号或等号，请指定另一工作目录。"
            )
        with tempfile.TemporaryDirectory(dir=base, prefix="dv_l5_") as scratch:
            return _export_area(rpu, Path(scratch) / "area.json", run)
    _run(run, "export", "-i", rpu, "-d", f"level5={target}")
    return json.loads(target.read_text(encoding="utf-8"))["active_area"]


_SIDES = ("left", "right", "top", "bottom")


def _area_intervals(area, width, height, count):
    presets = {p["id"]: tuple(p[k] for k in _SIDES) for p in area.get("presets", [])}
    for offsets in presets.values():
        if (
            any(type(v) is not int or v < 0 or v > 8191 for v in offsets)
            or offsets[0] + offsets[1] >= width
            or offsets[2] + offsets[3] >= height
        ):
            raise ValueError("L5 有效画面偏移超出实际画布。")
    intervals = []
    for frames, preset in area.get("edits", {}).items():
        a, sep, b = frames.partition("-")
        if not a.isdecimal() or (sep and not b.isdecimal()) or preset not in presets:
            raise ValueError("无法读取 L5 帧范围。")
        intervals.append((int(a), int(b if sep else a), presets[preset]))
    intervals.sort()
    normalized = []
    pos = 0
    for start, end, offsets in intervals:
        if start != pos or end < start or end >= count:
            raise ValueError("杜比视界 L5 帧范围不完整或重叠。")
        if normalized and normalized[-1][2] == offsets:
            normalized[-1] = (normalized[-1][0], end, offsets)
        else:
            normalized.append((start, end, offsets))
        pos = end + 1
    if pos != count:
        raise ValueError("L5 帧数和 RPU 帧数不一致。")
    return normalized


def prepare_rpu(
    elementary_hevc,
    rpu_out,
    source_width,
    source_height,
    out_width,
    out_height,
    work_dir,
    run,
    *,
    _convert_profile5=False,
    profile7_layer=None,
    original_rpu=None,
):
    """Extract RPU in presentation order; rescale active-area offsets on the same uncropped raster."""
    if min(source_width, source_height, out_width, out_height) <= 0:
        raise ValueError("画布尺寸必须为正数。")
    work = Path(work_dir)
    work.mkdir(parents=True, exist_ok=True)
    target = Path(rpu_out)
    with tempfile.TemporaryDirectory(dir=work, prefix="dovi_prepare_") as folder:
        temp = Path(folder)
        source = temp / "source.rpu"
        # Mode 3 verifies every input RPU is profile 5 and removes its reshaping
        # and changes the color matrices after the picture has been reconstructed.
        if profile7_layer not in (None, "MEL", "FEL"):
            raise ValueError("未知的 Profile 7 增强层。")
        # In the pinned dovi_tool, mode 2 removes mapping for FEL only; it
        # preserves MEL mapping. Editor numeric mode 5 is a no-op in 2.3.4.
        mode = 3 if _convert_profile5 else (2 if profile7_layer else None)
        if original_rpu is not None:
            if not profile7_layer or _convert_profile5:
                raise ValueError("复用原始 RPU 仅适用于已校验的 Profile 7 双层片源。")
            # prepare() already extracted and classified this RPU in display order.
            # Apply the same conversion to it without reading the EL video again.
            conversion = temp / "convert.json"
            conversion.write_text(json.dumps({"mode": mode}), encoding="utf-8")
            _run(run, "editor", "-i", original_rpu, "-j", conversion, "-o", source)
        else:
            args = ["-m", str(mode)] if mode else []
            _run(run, *args, "extract-rpu", "-i", elementary_hevc, "-o", source)
        count = _count_profile8(source, run)
        area = _export_area(source, temp / "l5-input.json", run)
        initial = _area_intervals(area, source_width, source_height, count)
        for preset in area["presets"]:
            for axis, original, new in (
                ("left", source_width, out_width),
                ("right", source_width, out_width),
                ("top", source_height, out_height),
                ("bottom", source_height, out_height),
            ):
                preset[axis] = (2 * preset[axis] * new + original) // (2 * original)
        area["crop"] = False
        expected = _area_intervals(area, out_width, out_height, count)
        changed = initial != expected
        if changed:
            config = temp / "edit.json"
            config.write_text(json.dumps({"active_area": area}), encoding="utf-8")
            edited = temp / "edited.rpu"
            _run(run, "editor", "-i", source, "-j", config, "-o", edited)
            actual = _export_area(edited, temp / "l5-output.json", run)
            if _area_intervals(actual, out_width, out_height, count) != expected:
                raise ValueError("L5 分镜有效画面偏移写回后复检未通过。")
            source = edited
        shutil.copyfile(source, target)
    return {
        "frame_count": count,
        "active_area_changed": changed,
        "active_area_segments": len(expected),
        "rpu_path": str(target),
        "sha256": _digest(target),
    }


def inject_rpu(encoded_hevc, rpu, final_hevc, run):
    """When RPU isn't attached by the encoder, attach it before muxing and verify afterward."""
    _run(run, "inject-rpu", "-i", encoded_hevc, "-r", rpu, "-o", final_hevc)


def verify_rpu(source_prepared, encoded_elementary, work_dir, run):
    """Read back the HEVC or single-video Matroska output in display order, including every block."""
    with tempfile.TemporaryDirectory(dir=work_dir, prefix="dovi_verify_") as folder:
        copy = Path(folder) / "readback.rpu"
        _run(run, "extract-rpu", "-i", encoded_elementary, "-o", copy)
        count = _count_profile8(copy, run)
        checksum = _digest(copy)
        if copy.stat().st_size != Path(source_prepared).stat().st_size or checksum != _digest(
            source_prepared
        ):
            raise ValueError("成品中逐帧提取的 Dolby Vision RPU 与计划写入的数据不一致。")
    return {"verified": True, "frame_count": count, "rpu_sha256": checksum}


def generate_rpu_from_measurements(
    measurements,
    rpu_out,
    work_dir,
    run,
    *,
    max_cll=None,
    max_fall=None,
    mastering_max=1000,
    mastering_min=1,
):
    """Create open-source CM 2.9 P8.1 metadata from measurements in 12-bit PQ code values.

    L6 content light levels and mastering_max are nits; mastering_min is in 0.0001 nit units.
    The shot list covers the full output in presentation order without gaps.
    """
    shots = []
    count = 0
    for shot in measurements:
        start, length = shot["start"], shot["length"]
        if type(start) is not int or start != count or type(length) is not int or length < 1:
            raise ValueError("场景帧范围必须从 0 开始连续覆盖所有帧。")
        values = {key: shot[key] for key in ("min_pq", "max_pq", "avg_pq")}
        if (
            any(type(v) is not int or not 0 <= v <= 4095 for v in values.values())
            or not values["min_pq"] <= values["avg_pq"] <= values["max_pq"]
        ):
            raise ValueError("场景亮度必须是有序的 12-bit PQ 整数。")
        shots.append({"start": start, "duration": length, "metadata_blocks": [{"Level1": values}]})
        count += length
    if not shots:
        raise ValueError("画面测量没有得到任何场景。")
    level6 = {
        "max_display_mastering_luminance": mastering_max,
        "min_display_mastering_luminance": mastering_min,
        "max_content_light_level": 0 if max_cll is None else max_cll,
        "max_frame_average_light_level": 0 if max_fall is None else max_fall,
    }
    if any(type(x) is not int or not 0 <= x <= 65535 for x in level6.values()):
        raise ValueError("L6 亮度字段超出 16-bit 合法范围。")
    cfg = {"cm_version": "V29", "profile": "8.1", "length": count, "shots": shots, "level6": level6}
    work = Path(work_dir)
    work.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=work, prefix="dovi_generate_") as folder:
        config = Path(folder) / "analysis.json"
        config.write_text(json.dumps(cfg), encoding="utf-8")
        generated = Path(folder) / "generated.rpu"
        _run(run, "generate", "-j", config, "-o", generated)
        if _count_profile8(generated, run) != count:
            raise ValueError("生成的杜比视界帧数与测量帧数不一致。")
        shutil.copyfile(generated, rpu_out)
    return {
        "rpu_path": str(rpu_out),
        "frame_count": count,
        "scene_count": len(shots),
        "cm_version": "2.9",
        "sha256": _digest(rpu_out),
    }
