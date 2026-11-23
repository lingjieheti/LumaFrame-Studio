"""Create a video-only sample, then process it through the usual encoder and verifier."""

from pathlib import Path
import tempfile

import engine


def render_sample(source, destination, mode, progress, cancel, *, target, duration):
    dest = Path(destination)
    if dest.exists():
        raise FileExistsError("试片目标文件已存在。")
    seek = max(0.0, min(duration * 0.35, duration - 6))
    run = engine._Runner(cancel)
    with tempfile.TemporaryDirectory(prefix=".lf-sample-", dir=dest.parent) as work:
        # MOV-family Dolby streams often carry the dvh1 sample entry. Keep that
        # container during the copy so the P5 configuration survives intact.
        mov = Path(source).suffix.lower() in {".mp4", ".m4v", ".mov"}
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
                "-map",
                "0:V:0",
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
                "-map",
                "0:V:0",
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
        return engine.render(str(excerpt), str(dest), mode, progress, cancel, target=target)
