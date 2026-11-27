"""Create a video-only sample, then process it through the usual encoder and verifier."""

from fractions import Fraction
import json
from pathlib import Path
import tempfile

import engine


def _h264_sample_filter(nearby, run):
    # A stream-copy cut at DTS can keep undecodable leading B pictures from
    # the previous open GOP and a future reference picture past the sample end.
    data = json.loads(run([engine._tool("ffprobe"), "-v", "fatal", "-select_streams", "v:0",
                           "-read_intervals", "%+6", "-show_packets", "-show_frames",
                           "-show_entries", "packet=pts,pos,flags:frame=pkt_pos:stream=time_base",
                           "-of", "json", nearby]))
    events = data.get("packets_and_frames", [])
    packets = [row for row in events if row["type"] == "packet"]
    decoded = {row["pkt_pos"] for row in events if row["type"] == "frame" and "pkt_pos" in row}
    if not packets or "K" not in packets[0].get("flags", "") or packets[0].get("pos") not in decoded:
        return []
    if any("pts" not in row or "pos" not in row for row in packets):
        return []
    start = packets[0]["pts"]
    first = 1
    while first < len(packets) and packets[first]["pts"] < start and packets[first]["pos"] not in decoded:
        first += 1

    # Stop in coded order before a forward reference reaches the endpoint.
    # Never discard an in-range reference just to keep a dependent B picture.
    end = start + 5 / Fraction(data["streams"][0]["time_base"])
    stop = next((n for n in range(first, len(packets)) if packets[n]["pts"] >= end), len(packets))
    drops = []
    if first > 1:
        drops.append(f"between(n,1,{first-1})")
    if stop < len(packets):
        drops.append(f"gte(n,{stop})")
    return ["-bsf:v", "noise=drop='" + "+".join(drops) + "'"] if drops else []


def render_sample(source, destination, mode, progress, cancel, *, target, duration, sdr_mapping="expand",
                  active_area=None, speed="quality"):
    dest = Path(destination)
    if dest.exists():
        raise FileExistsError("试片目标文件已存在。")
    seek = max(0.0, min(duration * 0.35, duration - 6))
    run = engine._Runner(cancel)
    with tempfile.TemporaryDirectory(prefix=".lf-sample-", dir=dest.parent) as work:
        source_data = engine._probe(source, run)
        primary = engine._video(source_data)
        reordered_avc = primary.get("codec_name") == "h264" and primary.get("has_b_frames", 0) > 0
        split = "_p7_el" in source_data
        initial_maps = ["-map", f"0:{primary['index']}"]
        excerpt_maps = ["-map", "0:V:0"]
        if split:
            initial_maps += ["-map", f"0:{source_data['_p7_el']}"]
            excerpt_maps += ["-map", "0:V:1"]
        # Preserve MOV sample entries and per-track timescales until the BL/EL
        # comparison. An interim Matroska copy would round PTS to milliseconds.
        mov = "mov" in source_data.get("format", {}).get("format_name", "").split(",")
        suffix = ".mp4" if mov else ".mkv"
        nearby = Path(work) / ("nearby" + suffix)
        excerpt = Path(work) / ("excerpt" + suffix)
        progress(0, "正在选择影片中段的关键帧")
        run(
            [
                engine._tool("ffmpeg"),
                "-nostdin",
                "-v",
                "error",
                "-ss",
                f"{seek:.3f}",
                "-i",
                source,
                "-t",
                "6" if reordered_avc else "5",
                *initial_maps,
                "-map_metadata",
                "0",
                "-map_chapters",
                "-1",
                "-c:v",
                "copy",
                "-an",
                "-sn",
                "-dn",
                "-strict",
                "unofficial",
                "-avoid_negative_ts",
                "make_zero",
                nearby,
            ]
        )
        # Input seeking can retain an entire preceding GOP. Cap time a second
        # time on the short, rebased timeline starting at its real keyframe.
        sample_filter = _h264_sample_filter(nearby, run) if reordered_avc else []
        run(
            [
                engine._tool("ffmpeg"),
                "-nostdin",
                "-v",
                "error",
                "-i",
                nearby,
                "-t",
                "5",
                *excerpt_maps,
                "-map_chapters",
                "-1",
                "-c:v",
                "copy",
                *sample_filter,
                "-strict",
                "unofficial",
                excerpt,
            ]
        )
        nearby.unlink()
        if split:
            # FFmpeg does not retain vdep when stream-copying an excerpt. The
            # original MP4 explicitly paired these tracks and they were mapped
            # BL first, EL second in both steps. Check the trimmed pair again.
            import profile7
            import dovi

            bl_times, video_end = engine._times(excerpt, 0, run, coded_order=True, with_end=True)
            el_times = engine._times(excerpt, 1, run, coded_order=True)
            if len(bl_times) != len(el_times) or any(
                abs(a - b) > 2 for a, b in zip(bl_times, el_times)
            ):
                raise ValueError("试片基础层与增强层逐帧时间不匹配。")
            dirpath = Path(work)
            bl, el, _, _, _ = profile7.prepare(
                excerpt,
                0,
                1,
                dirpath,
                primary["width"],
                primary["height"],
                len(bl_times),
                engine._extract,
                engine._tool("ffprobe"),
                run,
            )
            joined = dirpath / "clip.hevc"
            dovi._run(run, "mux", "--bl", bl, "--el", el, "-o", joined)
            timing = dirpath / "timecodes.txt"
            ordered = sorted(bl_times)
            if video_end is not None:
                ordered.append(video_end)
            timing.write_text(
                "# timestamp format v2\n"
                + "".join(f"{(pts - ordered[0]) / 1000:.3f}\n" for pts in ordered),
                encoding="ascii",
            )
            excerpt = dirpath / "verified-input.mkv"
            rate = engine._ratio(primary.get("avg_frame_rate")) or engine._ratio(primary.get("r_frame_rate"))
            if rate <= 0:
                raise ValueError("无法读取双轨试片的帧率。")
            run(
                [
                    engine._tool("mkvmerge"),
                    "-q",
                    "--timestamp-scale",
                    "100000",
                    "-o",
                    excerpt,
                    "--timestamps",
                    "0:" + str(timing),
                    "--default-duration",
                    f"0:{rate}fps",
                    joined,
                ]
            )
        return engine.render(str(excerpt), str(dest), mode, progress, cancel, target=target,
                             sdr_mapping=sdr_mapping, active_area=active_area, speed=speed)
