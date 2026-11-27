"""Progress follows real measurement records and verified totals."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import hdr
import lightlevels
import profile7


class MeasurementProgress(unittest.TestCase):
    def test_pipe_handles_partial_records_without_touching_binary_stdout(self):
        received = []

        def run(command, downstream, runner, work, *, failure, on_poll):
            self.assertEqual(command[-2:], ["rawvideo", "pipe:1"])
            path = Path(command[command.index("-progress") + 1])
            with path.open("wb", buffering=0) as writer:
                for data in (b"fps=12.3\r\nframe=1", b"2\r\nframe=8\n", b"frame=42\n"):
                    writer.write(data)
                    on_poll()
        with tempfile.TemporaryDirectory() as tmp, patch.object(profile7, "run_pipe", side_effect=run):
            profile7.run_ffmpeg_pipe(["ffmpeg", "rawvideo", "pipe:1"], ["reader"], None, tmp,
                                     on_frame=received.append)
            self.assertEqual(received, [12, 42])
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_static_light_finalizes_only_validated_passes(self):
        stream = dict(width=4, height=4, codec_name="hevc", pix_fmt="yuv420p10le", color_range="tv",
                      color_transfer="smpte2084", color_space="bt2020nc", color_primaries="bt2020")
        run = Mock(return_value=json.dumps(dict(streams=[stream])))
        run.cancel = None
        for valid in (True, False):
            with self.subTest(valid=valid), tempfile.TemporaryDirectory() as tmp:
                phases = []
                calls = []

                def scan(decode, measure, runner, folder, *, on_frame, failure):
                    calls.append(decode)
                    on_frame(1)
                    on_frame(2)
                    # A value within 0.02 of an integer triggers the exact EOTF pass.
                    peak = 50.01 if len(calls) == 1 else 50.005
                    records = 2 if valid or len(calls) == 1 else 1
                    Path(folder, "frames.txt").write_text("".join(
                        f"frame:{i} pts:{i}\nlavfi.astats.1.Max_level={peak}\n"
                        "lavfi.astats.1.DC_offset=5.1\nlavfi.astats.Overall.Number_of_samples=16\n"
                        for i in range(records)), encoding="ascii")

                def measure():
                    return lightlevels.measure_content_light("base.hevc", (4, 4), None, 2, tmp, run,
                                                            progress=lambda *event: phases.append(event))

                with patch.object(profile7, "run_ffmpeg_pipe", side_effect=scan), \
                     patch.object(lightlevels, "_write_pq_table"), patch("engine._tool", side_effect=str):
                    if valid:
                        self.assertEqual(measure(), (51, 6))
                    else:
                        with self.assertRaisesRegex(ValueError, "帧数"):
                            measure()
                expected = [(n, 2, phase) for phase in ("light", "light_refine") for n in (0, 1, 2)]
                self.assertEqual(phases, expected if valid else expected[:-1])

    def test_scene_frames_verified_before_completion(self):
        for expected in (3, 4):
            with self.subTest(expected=expected), tempfile.TemporaryDirectory() as tmp:
                events = []

                def run(command, callback, *, collect):
                    folder, = Path(tmp).glob("scene_cuts_*")
                    Path(folder, "cuts.txt").write_text("".join(
                        f"frame:{i}\nlavfi.scd.mafd=0\n" for i in range(3)), encoding="utf-8")
                    for i in range(3):
                        callback(f"frame:{i} pts:{i}")
                        for name in ("YMIN", "VMAX", "UAVG"):
                            callback(f"lavfi.signalstats.{name}=1024")

                def scenes():
                    return hdr.measure_output("base.hevc", tmp, run, expected_frames=expected,
                                              progress=lambda *event: events.append(event))

                with patch.object(hdr.shutil, "which", return_value="ffmpeg"):
                    if expected == 3:
                        self.assertEqual(scenes()[0]["length"], 3)
                        self.assertEqual(events[-1], (3, 3, "scenes"))
                    else:
                        with self.assertRaisesRegex(ValueError, "帧数"):
                            scenes()
                        self.assertLess(events[-1][0], expected)
                self.assertEqual(events[:2], [(0, expected, "scenes"), (1, expected, "scenes")])

    def test_signature_reports_actual_bytes_and_preserves_digest(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, "video.hevc")
            path.write_bytes(b"\x00\x00\x01\x40\x01\x07\x00\x00\x01\x02\x01\x80"
                             + b"a" * (2 * 1024 * 1024))
            events = []
            expected = profile7.signature(path, None)
            self.assertEqual(profile7.signature(path, None, progress=lambda *e: events.append(e)), expected)
            size = path.stat().st_size
            self.assertEqual(events, [(2 * 1024 * 1024, size), (size, size)])
