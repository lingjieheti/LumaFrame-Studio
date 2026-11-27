"""Compare sampled H.264 pictures and their display times to the uncut chart."""
from fractions import Fraction
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import engine
import preview


class OpenGopPreview(unittest.TestCase):
    def test_sample_is_a_contiguous_slice_of_the_decoded_source(self):
        try:
            ffmpeg = engine._tool("ffmpeg")
            engine._tool("ffprobe")
        except FileNotFoundError as exc:
            self.skipTest(str(exc))
        run = engine._Runner(None)

        def decoded(path):
            text = run([ffmpeg, "-v", "error", "-nostdin", "-copyts", "-i", path, "-map", "0:v:0",
                        "-fps_mode", "passthrough", "-enc_time_base", "demux", "-threads", "1",
                        "-f", "framehash", "-hash", "sha256", "-"])
            tick = Fraction(next(line.split()[-1] for line in text.splitlines() if line.startswith("#tb ")))
            rows = [line.split(",") for line in text.splitlines() if line.startswith("0,")]
            return [(int(row[2]) * tick, row[-1].strip()) for row in rows]

        with tempfile.TemporaryDirectory() as temp:
            for name, gop, vfr in (("open", 1, False), ("open-vfr-10bit", 1, True), ("closed", 0, False)):
                with self.subTest(case=name):
                    root = Path(temp)
                    extension = ".mp4" if vfr else ".mkv"
                    source = root / (name + extension)
                    short = root / (name + "-sample" + extension)
                    filters = (["-vf", "format=yuv420p10le,settb=1/24000,setpts=(N+floor(N/5))*1001",
                                "-enc_time_base", "1/24000", "-video_track_timescale", "24000"] if vfr else [])
                    run([ffmpeg, "-v", "error", "-nostdin", "-f", "lavfi", "-i", "testsrc2=s=320x180:r=24:d=24",
                         *filters, "-fps_mode", "passthrough", "-c:v", "libx264", "-threads", "2",
                         "-preset", "fast", "-x264-params", f"open-gop={gop}:keyint=48:min-keyint=48:"
                         "scenecut=0:bframes=3:b-adapt=0:b-pyramid=normal:colorprim=bt709:transfer=bt709:colormatrix=bt709",
                         "-an", source])
                    probe = engine._probe(source, run)
                    with patch.object(engine, "render", side_effect=lambda src, dst, *a, **kw: shutil.copyfile(src, dst)):
                        preview.render_sample(source, short, "rtx", lambda *_: None, threading.Event(),
                                              target="newdv", duration=float(probe["format"]["duration"]))
                    src, sample = decoded(source), decoded(short)
                    first = next(i for i,item in enumerate(src) if item[1] == sample[0][1])
                    self.assertGreater(first, 0)
                    same = src[first:first + len(sample)]
                    self.assertEqual([v for _,v in same], [v for _,v in sample])
                    self.assertEqual([pts - same[0][0] for pts,_ in same], [pts - sample[0][0] for pts,_ in sample])
                    self.assertLess(sample[-1][0] - sample[0][0], 5)
                    self.assertGreater(sample[-1][0] - sample[0][0], 4.6)
                    frames = engine._times(short, 0, run)
                    self.assertEqual(len(frames), len(sample))
                    a, b = engine._video(probe), engine._video(engine._probe(short, run))
                    for k in ("codec_name","pix_fmt","width","height","color_range","color_space",
                              "color_transfer","color_primaries","chroma_location"):
                        self.assertEqual(a.get(k), b.get(k), k)


if __name__ == "__main__":
    unittest.main()
