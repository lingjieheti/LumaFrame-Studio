"""Real-code-value checks for recovering recorded studio-range highlight detail."""
from array import array
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import engine
import hdr
from shot_grade import analyze_shots


class ExtendedWhite(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.ff = engine._tool("ffmpeg")
            engine._tool("ffprobe")
        except FileNotFoundError as exc:
            raise unittest.SkipTest(str(exc)) from exc

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.work = Path(temp.name)

    def clip(self, bars, *, bits=8, tag="tv", ordinary=0):
        width, height = len(bars) * 32, 64
        factor = 1 << (bits - 8)
        def frame(values):
            samples = array("H" if bits == 10 else "B")
            for code in values:
                samples.extend([code * factor] * 32)
            samples *= height
            samples.extend([128 * factor] * (width * height // 2))
            return samples.tobytes()
        bright = frame(bars)
        legal = frame([min(x, 235) for x in bars])
        source = self.work / "sdr.mkv"
        tags = ["-color_range", tag, "-colorspace", "bt709", "-color_trc", "bt709", "-color_primaries", "bt709"]
        subprocess.run([self.ff, "-y", "-v", "error", "-f", "rawvideo", "-video_size", f"{width}x{height}",
                        "-pixel_format", "yuv420p10le" if bits == 10 else "yuv420p", *tags, "-i", "pipe:0",
                        "-c:v", "ffv1", *tags, source], check=True,
                       input=legal * ordinary + bright * (1 if ordinary else 6) + legal * ordinary)
        run = engine._Runner(None)
        video = engine._video(engine._probe(source, run))
        times = sorted(engine._times(source, video["index"], run))
        return source, analyze_shots(source, video, times, self.work, run)

    def test_range_depth_and_flash(self):
        for bits in (8, 10):
            with self.subTest(bits=bits):
                _, shot = self.clip([128, 220, 235, 249], bits=bits)
                self.assertAlmostEqual(shot[0]["headroom"], 0.7, delta=0.06)
        for bars, tag, ordinary in (([128, 232, 235], "tv", 0), ([128, 235, 254], "pc", 0),
                                    ([128, 235, 249], "tv", 5)):
            _, shots = self.clip(bars, tag=tag, ordinary=ordinary)
            self.assertTrue(all("headroom" not in s for s in shots))

    @unittest.skipUnless(os.environ.get("LUMAFRAME_TEST_VULKAN") == "1", "requires NVIDIA Vulkan")
    def test_superwhite_steps_survive_color_transform(self):
        bars = [100, 180, 235, 239, 243, 249, 254]
        w, h = 32 * len(bars), 64
        src, plan = self.clip(bars)
        legacy = [{k: v for k, v in s.items() if k != "headroom"} for s in plan]
        for mode in ("reference", "neural"):
            with self.subTest(mode=mode):
                scale = 2 if mode == "neural" else 1
                tw, th = w * scale, h * scale
                rows = []
                for shots in (legacy, plan):
                    shader = hdr.write_scene_shader(self.work, shots, neural=scale == 2)
                    init, vf = hdr.sdr_filter(w, h, tw, th, mode, mapping="scene", scene_shader=shader, output_rgb=True)
                    cmd = [self.ff, "-v", "error", *init, "-i", src, "-vf", vf, "-frames:v", "1",
                           "-f", "rawvideo", "-"]
                    image = array("H", subprocess.check_output(cmd))
                    rows.append(image[tw*(th//2)+16*scale:tw*(th//2+1):32*scale])
                before, after = rows
                self.assertLessEqual(max(before[2:]) - min(before[2:]), 100)
                self.assertLessEqual(max(abs(a-b) for a,b in zip(before[:2],after[:2])), 1)
                self.assertTrue(all(b > a + 100 for a, b in zip(after[2:], after[3:])), after)
