"""Check the actual FFmpeg conversions surrounding NVIDIA's packed RGB10 model."""

from array import array
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from rtx_backend import pq_to_rgb10, rgb10_to_pq

FFMPEG = ROOT / "bin" / "ffmpeg.exe"


@unittest.skipUnless(FFMPEG.is_file(), "requires the bundled Windows FFmpeg")
class PackedColor(unittest.TestCase):
    def convert(self, data, source, target, graph):
        return subprocess.run(
            [str(FFMPEG), "-nostdin", "-v", "error", "-f", "rawvideo", "-pixel_format", source,
             "-video_size", "1024x8", "-i", "pipe:0", "-vf", graph, "-pix_fmt", target,
             "-frames:v", "1", "-f", "rawvideo", "pipe:1"],
            input=data, capture_output=True, check=True,
        ).stdout

    def test_every_legal_gray_survives_the_rgb10_round_trip(self):
        row = [min(940, max(64, x)) for x in range(1024)]
        yuv = array("H", row * 8 + [512] * (1024 * 4)).tobytes()
        rgb = self.convert(yuv, "yuv420p10le", "x2bgr10le", pq_to_rgb10("limited"))
        words = array("I", rgb)
        for index in range(64, 941):
            code = round((index - 64) * 1023 / 876)
            word = words[index]
            for shift in (0, 10, 20):
                self.assertLessEqual(abs(((word >> shift) & 1023) - code), 1)
        self.assertEqual(self.convert(rgb, "x2bgr10le", "yuv420p10le", rgb10_to_pq()), yuv)

    def test_2020_ncl_color_bars_have_the_expected_chroma_and_channel_order(self):
        bars = [(900, 20, 60), (130, 880, 72), (80, 30, 840), (620, 380, 260)]
        row = [r | g << 10 | b << 20 for r, g, b in bars for _ in range(256)]
        rgb = array("I", row * 8).tobytes()
        yuv = self.convert(rgb, "x2bgr10le", "yuv420p10le", rgb10_to_pq())
        samples = array("H", yuv)
        back = array("I", self.convert(yuv, "yuv420p10le", "x2bgr10le", pq_to_rgb10("limited")))
        for i, (r, g, b) in enumerate(bars):
            x = i * 256 + 128
            luminance = .2627 * r + .6780 * g + .0593 * b
            expected = (64 + 876 * luminance / 1023,
                        512 + 896 * (b - luminance) / (2 * (1 - .0593) * 1023),
                        512 + 896 * (r - luminance) / (2 * (1 - .2627) * 1023))
            actual = (samples[x], samples[8192 + x // 2], samples[10240 + x // 2])
            for measured, reference in zip(actual, expected):
                self.assertAlmostEqual(measured, round(reference), delta=1)
            for shift, reference in zip((0, 10, 20), (r, g, b)):
                self.assertAlmostEqual((back[x] >> shift) & 1023, reference, delta=2)
