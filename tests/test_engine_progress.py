"""Export counters must reflect completed work and work with both callback APIs."""

from pathlib import Path
import os
import tempfile
import unittest
from unittest.mock import patch

import engine


class Recorder:
    def __init__(self):
        self.events = []
        self.pending = None

    def stage(self, *measurement):
        self.pending = measurement

    def __call__(self, percent, message):
        self.events.append((percent, message, self.pending))
        self.pending = None


class ExportProgress(unittest.TestCase):
    def test_stage_throttles_without_losing_completion_or_displaying_reordered_work(self):
        capture = Recorder()
        report = engine._Stage(capture, "encode", "编码", "frames", 10, 60)
        with patch.object(engine.time, "monotonic", side_effect=[1.0, 1.1, 1.5, 1.6]):
            for value in (0, 1, 6, 4, 10, 10):
                report(value, 10)
        self.assertEqual([event[0] for event in capture.events], [10, 40, 60])
        self.assertTrue(all(event[1] == "编码" for event in capture.events))
        self.assertEqual([event[2][1] for event in capture.events], [0, 6, 10])
        messages = []
        report = engine._Stage(lambda *event: messages.append(event), "verify", "核验", "frames", 95, 99)
        report(30, 30)
        self.assertEqual(messages[0][0], 99)
        self.assertIn("30 / 30", messages[0][1])

    def test_timeline_positions_keep_timestamps_even_with_b_frames_and_side_data(self):
        positions, frames = [], []
        def probe(args, callback, *, collect):
            self.assertFalse(collect)
            self.assertIn("packet=pts_time,duration_time,pos:packet_side_data=", args)
            for row in ("0.0,0.04,120,", "0.08,0.04,342", "0.04,0.04,N/A"):
                callback(row)
        with patch.object(engine, "_tool", return_value="ffprobe"):
            times, end = engine._times("input", 0, probe, with_end=True,
                                       on_position=positions.append, on_frame=frames.append)
        self.assertEqual((times, end), ([0, 40_000, 80_000], 120_000))
        self.assertEqual((positions, frames), ([120, 342], [1, 2, 3]))

    @unittest.skipUnless(os.environ.get("LUMAFRAME_TEST_VULKAN") == "1",
                         "requires Vulkan and bundled FFmpeg, MKVToolNix and dovi_tool")
    def test_authored_dv_and_native_copy_report_monotonic_progress(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, authored, copied = (root / name for name in ("source.mkv", "authored.mkv", "copied.mkv"))
            engine._Runner(None)([
                engine._tool("ffmpeg"), "-nostdin", "-v", "error", "-f", "lavfi", "-i",
                "testsrc2=size=1280x720:rate=24", "-frames:v", "24", "-c:v", "libx264", "-preset", "ultrafast",
                "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", source,
            ])
            with patch.object(engine, "_geometry", return_value=(640, 360)):
                for src, dest, mode, target, keys in (
                    (source, authored, "reference", "newdv",
                     {"scan", "encode", "light", "light_write", "scenes", "mux", "verify", "verify_rpu"}),
                    (authored, copied, "copy", "preserve", {"scan", "copy", "verify", "readback"}),
                ):
                    capture = Recorder()
                    result = engine.render(src, dest, mode, capture, None, target=target)
                    self.assertTrue(result["verified"])
                    percents = [n for n, text, measurement in capture.events]
                    self.assertEqual(percents, sorted(percents))
                    self.assertEqual((percents[0], percents[-1]), (0, 100))
                    measures = [part for n, text, part in capture.events if part]
                    self.assertTrue(keys.issubset({part[0] for part in measures}))
                    for key in keys - {"scan"}:
                        self.assertTrue(any(k == key and done == total > 0 for k, done, total, _ in measures), key)
            self.assertEqual(authored.read_bytes(), copied.read_bytes())
