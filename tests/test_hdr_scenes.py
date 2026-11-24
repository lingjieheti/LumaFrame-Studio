"""Validate authored shot boundaries in the RPU read back from the HEVC."""
import json
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest

import dovi
import engine
import hdr


@unittest.skipUnless(os.environ.get("LUMAFRAME_TEST_VULKAN") == "1",
                     "requires Vulkan, bundled FFmpeg and dovi_tool")
class HDRShotBoundaries(unittest.TestCase):
    def test_cuts_flash_dissolve_and_last_short_shot(self):
        gray = (541, 512, 512)
        red = (541, 470, 635)
        light = (660, 512, 512)
        ramp = [(541, round(512 - 42 * n / 12), round(512 + 123 * n / 12))
                for n in range(1, 13)]
        # Two equal-Y cuts, a single-frame flash, a 12-frame dissolve, then
        # a luma cut into a two-frame last shot. RPU indices are display order.
        sequence = [gray] * 4 + [red] * 4 + [gray] * 4 + [red] + [gray] * 3 + ramp + [red] * 4 + [light] * 2
        width, height = 192, 112
        pixels = width * height
        raw = b"".join(struct.pack("<H", code) * samples for frame in sequence
                       for code, samples in zip(frame, (pixels, pixels // 4, pixels // 4)))
        tags = ["-color_range", "tv", "-colorspace", "bt2020nc", "-color_trc", "smpte2084",
                "-color_primaries", "bt2020"]
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "shots.hevc"
            subprocess.run([engine._tool("ffmpeg"), "-v", "error", "-f", "rawvideo",
                            "-pixel_format", "yuv420p10le", "-video_size", f"{width}x{height}",
                            *tags, "-i", "pipe:0", *tags, "-c:v", "libx265", "-x265-params",
                            "lossless=1:bframes=3:keyint=50:log-level=error", source],
                           input=raw, check=True)
            run = engine._Runner(None)
            measured = hdr.measure_output(source, root, run)
            self.assertEqual([(s["start"], s["length"]) for s in measured],
                             [(0, 4), (4, 4), (8, 24), (32, 2)])
            planned, encoded, actual = (root / name for name in ("planned.rpu", "out.hevc", "actual.rpu"))
            dovi.generate_rpu_from_measurements(measured, planned, root, run)
            dovi.inject_rpu(source, planned, encoded, run)
            dv = dovi.find_dovi_tool()
            run([dv, "extract-rpu", "-i", encoded, "-o", actual])
            self.assertEqual(planned.read_bytes(), actual.read_bytes())
            shots, levels = root / "scenes.txt", root / "levels.json"
            run([dv, "export", "-i", actual, "--levels-format", "json", "--levels",
                 f"level1={levels}", "-d", f"scenes={shots}"])
            self.assertEqual(shots.read_text().splitlines(), ["0", "4", "8", "32"])
            frames = json.loads(levels.read_bytes())
            self.assertEqual(len(frames), len(sequence))
            # Independent BT.2020 NCL matrix for the two first uniform shots.
            for index, (y, _, cr) in enumerate(sequence[:8]):
                red_pq = (y - 64) / 876 + (1 - 0.2627) * (cr - 512) / 448
                self.assertAlmostEqual(frames[index]["max_pq"], round(4095 * red_pq), delta=3)
                self.assertAlmostEqual(frames[index]["avg_pq"], round(4095 * red_pq), delta=3)
