"""Create a video-only sample, then process it through the usual encoder and verifier."""

from pathlib import Path
import tempfile

import engine


def render_sample(source, destination, mode, progress, cancel, *, target, duration, sdr_mapping="expand"):
    dest = Path(destination)
    if dest.exists():
        raise FileExistsError("试片目标文件已存在。")
    seek = max(0.0, min(duration * 0.35, duration - 6))
    run = engine._Runner(cancel)
    with tempfile.TemporaryDirectory(prefix=".lf-sample-", dir=dest.parent) as work:
        source_data = engine._probe(source, run)
        primary = engine._video(source_data)
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
                "5",
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
        return engine.render(str(excerpt), str(dest), mode, progress, cancel, target=target, sdr_mapping=sdr_mapping)
