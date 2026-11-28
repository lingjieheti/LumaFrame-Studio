"""Check shot-aware SDR grading against decoded display order on real media."""
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path

import engine
import shot_grade


class ShotGradeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            engine._tool("ffmpeg")
            engine._tool("ffprobe")
        except FileNotFoundError as exc:
            raise unittest.SkipTest(str(exc)) from exc

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.scratch = Path(self.temp.name)
        self.run = engine._Runner(None)
        self.w, self.h = 100, 80

    def frame(self, white_columns, *, matte=0, low=50, high=235):
        row = bytes([high]) * white_columns + bytes([low]) * (self.w - white_columns)
        luma = bytes([16]) * self.w * matte + row * (self.h - 2 * matte) + bytes([16]) * self.w * matte
        return luma + bytes([128]) * self.w * self.h * 2

    def analyze_input(self, frames):
        source = self.scratch / "sample.mkv"
        subprocess.run([
            engine._tool("ffmpeg"), "-nostdin", "-v", "error", "-y", "-f", "rawvideo",
            "-video_size", f"{self.w}x{self.h}", "-pixel_format", "yuv444p", "-framerate", "25",
            "-i", "pipe:0", "-vf", "setpts='if(lt(N,10),N,N+2)+7'", "-fps_mode", "passthrough",
            "-color_range", "tv", "-colorspace", "bt709", "-color_primaries", "bt709",
            "-color_trc", "bt709", "-c:v", "ffv1", source
        ], input=b"".join(frames), check=True)
        video = engine._video(engine._probe(source, self.run))
        times = engine._times(source, video["index"], self.run)
        return source, video, times

    def test_vfr_offset_and_peak_coverage(self):
        dark, light = self.frame(1), self.frame(50, high=216)
        args = self.analyze_input([dark] * 9 + [light] * 8 + [dark] * 7)
        times = args[2]
        self.assertGreater(times[0], 0)
        self.assertGreater(times[10] - times[9], times[9] - times[8])
        events = []
        shots = shot_grade.analyze_shots(*args, self.scratch, self.run, lambda *v: events.append(v))
        self.assertEqual([(s["start"], s["length"]) for s in shots], [(0, 9), (9, 8), (17, 7)])
        self.assertEqual([s["strength"] for s in shots], [1, 0.08, 1])
        self.assertEqual([s["high_key"] for s in shots], [0, 0, 0])
        self.assertEqual((events[0], events[-1]), ((0, 24), (24, 24)))
        with self.assertRaisesRegex(ValueError, "时间戳"):
            shot_grade.analyze_shots(args[0], args[1], [t + 12 for t in times], self.scratch, self.run)
        with self.assertRaisesRegex(ValueError, "帧数"):
            shot_grade.analyze_shots(args[0], args[1], times + [times[-1] + 1], self.scratch, self.run)

    def test_limited_flashes_hold_the_surrounding_look(self):
        dark, flash = self.frame(1), self.frame(100)
        frames = [dark] * 8 + [flash] + [dark] * 8 + [flash] * 2 + [dark] * 8 + [flash] * 3 + [dark] * 8
        args = self.analyze_input(frames)
        self.assertEqual(shot_grade.analyze_shots(*args, self.scratch, self.run),
                         [dict(start=0, length=len(frames), strength=1, high_key=0)])

    def test_active_picture_and_source_raster(self):
        args = self.analyze_input([self.frame(24, matte=20, high=216)] * 8)
        full = shot_grade.analyze_shots(*args, self.scratch, self.run)[0]["strength"]
        cropped = shot_grade.analyze_shots(*args, self.scratch, self.run,
                                           active_area=(0, 0, 20, 20))[0]["strength"]
        self.assertGreater(full, cropped + 0.25)
        with self.assertRaisesRegex(ValueError, "有效区域"):
            shot_grade.analyze_shots(*args, self.scratch, self.run, active_area=(0, 0, 40, 40))
        with self.assertRaises(RuntimeError):
            shot_grade.analyze_shots(args[0], dict(args[1], width=self.w + 2), args[2], self.scratch, self.run)
        self.assertFalse(any(self.scratch.glob("grade_*")))

    def test_single_frame_and_cancellation(self):
        args = self.analyze_input([self.frame(100)])
        self.assertEqual(shot_grade.analyze_shots(*args, self.scratch, self.run),
                         [dict(start=0, length=1, strength=1.0, high_key=1.0)])
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(InterruptedError):
            shot_grade.analyze_shots(*args, self.scratch, engine._Runner(cancel))
        self.assertFalse(any(self.scratch.glob("grade_*")))

    def test_high_key_is_continuous_and_a_short_flash_cannot_create_it(self):
        diffuse, mid, high = (self.frame(50, low=base, high=216) for base in (50, 158, 214))
        looks = []
        for frame in (diffuse, mid, high):
            args = self.analyze_input([frame] * 4)
            looks.append(shot_grade.analyze_shots(*args, self.scratch, self.run)[0])
        self.assertEqual((looks[0]["strength"], looks[0]["high_key"]), (0.08, 0.0))
        self.assertTrue(0.08 < looks[1]["strength"] < 1)
        self.assertTrue(0 < looks[1]["high_key"] < 1)
        self.assertEqual((looks[2]["strength"], looks[2]["high_key"]), (1.0, 1.0))
        args = self.analyze_input([diffuse] * 8 + [high] * 2 + [diffuse] * 8)
        self.assertEqual(shot_grade.analyze_shots(*args, self.scratch, self.run),
                         [dict(start=0, length=18, strength=0.08, high_key=0)])

    def test_equal_area_wall_and_near_clipped_light(self):
        # Limited Y' 216 / 226 / 235 becomes approx. 233 / 245 / 255 in
        # full range. All three cover half the picture with the same surround.
        looks = []
        for high in (216, 226, 235):
            args = self.analyze_input([self.frame(50, high=high)] * 8)
            looks.append(shot_grade.analyze_shots(*args, self.scratch, self.run)[0])
        self.assertEqual((looks[0]["strength"], looks[0]["high_key"]), (0.08, 0))
        self.assertTrue(0.08 < looks[1]["strength"] < 1)
        self.assertTrue(0 < looks[1]["high_key"] < 1)
        self.assertEqual((looks[2]["strength"], looks[2]["high_key"]), (1, 1))
        wall, flare = self.frame(50, high=216), self.frame(50, high=235)
        args = self.analyze_input([wall] * 8 + [flare] * 2 + [wall] * 8)
        self.assertEqual(shot_grade.analyze_shots(*args, self.scratch, self.run),
                         [dict(start=0, length=18, strength=0.08, high_key=0)])

    def test_midstream_resize_rejected_with_continuous_timestamps(self):
        ff = engine._tool("ffmpeg")
        for i, size in enumerate(("100x80", "112x84")):
            self.run([ff, "-nostdin", "-v", "error", "-f", "lavfi", "-i",
                      f"color=white:size={size}:rate=25", "-frames:v", "8", "-c:v", "mpeg2video",
                      "-bf", "0", "-f", "mpegts", self.scratch / f"part{i}.ts"])
        parts = self.scratch / "concat.txt"
        parts.write_text("file 'part0.ts'\nfile 'part1.ts'\n", encoding="ascii")
        source = self.scratch / "resize.ts"
        self.run([ff, "-nostdin", "-v", "error", "-f", "concat", "-i", parts, "-c", "copy", source])
        video = engine._video(engine._probe(source, self.run))
        times = engine._times(source, video["index"], self.run)
        self.assertEqual(len(times), 16)
        self.assertEqual(times[-1] - times[0], 15 * 40_000)
        with self.assertRaises(RuntimeError):
            shot_grade.analyze_shots(source, video, times, self.scratch, self.run)
        self.assertFalse(any(self.scratch.glob("grade_*")))
