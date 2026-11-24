"""Check the fixed active area used when authoring new Dolby Vision metadata."""
from array import array
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import dovi
import engine
import hdr


class AuthoredActiveArea(unittest.TestCase):
    def test_validate_boundaries_and_order(self):
        self.assertEqual(dovi.validate_active_area(None, 192, 112), (0, 0, 0, 0))
        self.assertEqual(dovi.validate_active_area([17, 22, 11, 16], 192, 112), (17, 22, 11, 16))
        self.assertEqual(dovi.validate_active_area((0, 191, 111, 0), 192, 112), (0, 191, 111, 0))
        for invalid in [(0, 0, 0), (0, 0, 0, False), (17, -1, 11, 16), (17, 22, 11, 101),
                        (100, 92, 0, 0), (8192, 0, 0, 0), (0, 0, 0, 0.0), "0,0,0,0"]:
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                dovi.validate_active_area(invalid, 192, 112)

    @unittest.skipUnless((Path(dovi.__file__).parent / "bin/dovi_tool.exe").is_file(),
                         "requires bundled dovi_tool")
    def test_explicit_full_frame_and_legacy_default_generate_identical_metadata(self):
        shots = [dict(start=0, length=2, min_pq=0, max_pq=2800, avg_pq=2031),
                 dict(start=2, length=2, min_pq=5, max_pq=2601, avg_pq=1902)]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            run = engine._Runner(None)
            legacy, explicit = root / "old.rpu", root / "new.rpu"
            dovi.generate_rpu_from_measurements(shots, legacy, root, run)
            dovi.generate_rpu_from_measurements(shots, explicit, root, run,
                                                active_area=(0, 0, 0, 0), canvas=(3840, 2160))
            self.assertEqual(legacy.read_bytes(), explicit.read_bytes())

    @unittest.skipUnless(os.environ.get("LUMAFRAME_TEST_VULKAN") == "1",
                         "requires Vulkan, FFmpeg, mkvmerge and dovi_tool")
    def test_odd_insets_analyse_only_active_pixels_and_keep_coded_picture(self):
        width, height = 192, 112
        area = (17, 22, 11, 16)
        left, right, top, bottom = area
        pixels = width * height
        y = array("H", [64]) * pixels
        for row in range(top, height - bottom):
            start = row * width + left
            y[start:start + width - left - right] = array("H", [
                400 + (3 * x + 5 * row) % 270 for x in range(left, width - right)
            ])
        # A bright line outside the mask exercises both ends of the measured range.
        y[:width * 3] = array("H", [940]) * (width * 3)
        u = array("H", (512 + (row % 4 - 2) * 28 for row in range(height // 2) for _ in range(width // 2)))
        v = array("H", (512 + (x % 5 - 2) * 27 for _ in range(height // 2) for x in range(width // 2)))
        raw = y.tobytes() + u.tobytes() + v.tobytes()
        tags = ["-colorspace", "bt2020nc", "-color_primaries", "bt2020", "-color_trc", "smpte2084", "-color_range", "tv"]
        ff = [engine._tool("ffmpeg"), "-nostdin", "-v", "error"]
        run = engine._Runner(None)
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            source, final = folder / "input.hevc", folder / "output.hevc"
            planned, muxed = folder / "generated.rpu", folder / "output.mkv"
            subprocess.run([*ff, "-f", "rawvideo", "-pixel_format", "yuv420p10le", "-s", f"{width}x{height}",
                            *tags, "-i", "pipe:0", *tags, "-c:v", "libx265", "-preset", "ultrafast",
                            "-x265-params", "lossless=1:bframes=2:log-level=error", source],
                           input=raw * 4, check=True)
            reference_rgb = array("H")
            reference_rgb.frombytes(subprocess.check_output([
                *ff, "-i", source, "-frames:v", "1", "-vf",
                "zscale=filter=spline36:matrixin=2020_ncl:rangein=limited:matrix=gbr:range=full,format=gbrp16le",
                "-f", "rawvideo", "-"
            ]))
            max_rgb = [max(reference_rgb[idx + p * pixels] for p in range(3))
                       for y in range(top, height - bottom)
                       for idx in range(y * width + left, (y + 1) * width - right)]
            expected = [round(value * 4095 / 65535)
                        for value in (min(max_rgb), sum(max_rgb) / len(max_rgb), max(max_rgb))]
            full, = hdr.measure_output(source, folder, run)
            shot, = hdr.measure_output(source, folder, run, active_area=area)
            for key, value in zip(("min_pq", "avg_pq", "max_pq"), expected):
                self.assertAlmostEqual(shot[key], value, delta=3)
            self.assertGreater(full["max_pq"], shot["max_pq"] + 200)
            self.assertLess(full["avg_pq"], shot["avg_pq"] - 200)

            dovi.generate_rpu_from_measurements([shot], planned, folder, run, active_area=area, canvas=(width, height))
            dovi.inject_rpu(source, planned, final, run)
            run([engine._tool("mkvmerge"), "-q", "-o", muxed, "--default-duration", "0:25fps", final])
            result = dovi.verify_rpu(planned, muxed, folder, run, active_area=area, canvas=(width, height))
            self.assertEqual(result["frame_count"], 4)
            with self.assertRaisesRegex(ValueError, "L5"):
                dovi.verify_rpu(planned, muxed, folder, run, active_area=(18, 21, 11, 16), canvas=(width, height))
            base_args = ["-map", "0:v:0", "-fps_mode", "passthrough", "-pix_fmt", "yuv420p10le", "-f", "rawvideo", "-"]
            self.assertEqual(subprocess.check_output([*ff, "-i", muxed, *base_args]), raw * 4)
