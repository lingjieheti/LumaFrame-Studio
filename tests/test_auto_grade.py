"""Automatic shot routing must preserve ambiguous lighting and source headroom."""
import subprocess
import tempfile
import unittest
from pathlib import Path

import auto_grade
import engine
import shot_grade


class AutoGradeTests(unittest.TestCase):
    def test_decision_requires_several_independent_exposure_clues(self):
        self.assertTrue(auto_grade.compressed_luma(10, 29, 44, 83))
        for stats in ((0, 7, 12, 15), (1, 22, 40, 70), (20, 23, 26, 33),
                      (9, 33, 52, 218), (10, 68, 105, 112)):
            with self.subTest(stats=stats):
                self.assertFalse(auto_grade.compressed_luma(*stats))

    def test_uncertain_flash_fade_and_headroom_use_the_base_look(self):
        flags = [True] * 12
        flat = [30] * 12
        self.assertTrue(auto_grade.use_cinema(flags, flat, 500_000, 0))
        self.assertFalse(auto_grade.use_cinema(flags, flat, 500_000, .1))
        self.assertFalse(auto_grade.use_cinema(flags, flat, 100_000, 0))
        self.assertFalse(auto_grade.use_cinema([True] * 3, [30] * 3, 500_000, 0))
        self.assertFalse(auto_grade.use_cinema([False] + flags[1:], flat, 500_000, 0))
        self.assertFalse(auto_grade.use_cinema(flags, list(range(18, 30)), 500_000, 0))

    def test_real_scan_chooses_once_per_shot_without_changing_existing_grades(self):
        try:
            ff = engine._tool("ffmpeg")
            engine._tool("ffprobe")
        except FileNotFoundError as exc:
            self.skipTest(str(exc))
        with tempfile.TemporaryDirectory() as scratch:
            source = Path(scratch) / "pair.mkv"
            w, h = 80, 64
            frames = bytearray()
            for lo, hi in [(29, 51)] * 15 + [(80, 225)] * 15:
                frames.extend((bytes([lo]) * (w//2) + bytes([hi]) * (w//2)) * h)
                frames.extend(bytes([128]) * (2*w*h))
            subprocess.run([ff, "-nostdin", "-v", "error", "-f", "rawvideo", "-video_size", f"{w}x{h}",
                            "-pixel_format", "yuv444p", "-framerate", "24", "-i", "pipe:0", "-c:v", "ffv1",
                            "-colorspace", "bt709", "-color_range", "tv", "-color_trc", "bt709",
                            "-color_primaries", "bt709", source], input=frames, check=True)
            run = engine._Runner(None)
            video = engine._video(engine._probe(source, run))
            times = sorted(engine._times(source, video["index"], run))
            legacy = shot_grade.analyze_shots(source, video, times, scratch, run)
            automatic = shot_grade.analyze_shots(source, video, times, scratch, run, auto=True)
            self.assertEqual([(s["length"], s.pop("auto_cinema")) for s in automatic], [(15, True), (15, False)])
            self.assertEqual(automatic, legacy)
