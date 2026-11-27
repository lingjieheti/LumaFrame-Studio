"""Verify observable progress transitions independently of encoder speed."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from export_progress import ExportProgress, ProgressFeed, ProgressFile, Work, rate_text, work_text


class ProgressTests(unittest.TestCase):
    def test_warmup_eta_stage_change_and_silent_tool(self):
        tracker = ExportProgress()
        tracker.update(10, Work("encode", 0, 100, "frames"), 100)
        tracker.update(11, Work("encode", 2, 100, "frames"), 101)
        self.assertEqual(tracker.estimates(101), (None, None, None))
        tracker.update(15, Work("encode", 10, 100, "frames"), 105)
        rate, phase, overall = tracker.estimates(105)
        self.assertEqual((rate, phase, overall), (2, 45, 85))
        self.assertEqual(rate_text(rate, tracker.work), "2.0 帧/秒")
        self.assertIn("10 / 100", work_text(tracker.work))
        self.assertEqual(tracker.estimates(129), (None, None, None))
        tracker.update(50, Work("mux", 1, 100, "percent"), 130)
        self.assertEqual(tracker.estimates(130), (None, None, None))
        tracker.update(55, None, 131)
        self.assertIsNone(tracker.work)

    def test_feed_keeps_phase_boundaries_and_final_sample(self):
        received = []
        feed = ProgressFeed(lambda *args: received.append(args))
        with patch("export_progress.time.monotonic", return_value=10):
            for key, count in (("encode", 0), ("encode", 1), ("encode", 2), ("light", 0)):
                feed.stage(key, count, 2, "frames")
                feed(30, "处理中")
            feed(100, "完成")
        self.assertEqual([r[2].done if r[2] else None for r in received], [0, 2, 0, None])

    def test_status_file_exclusive_and_verified_only_on_success(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "live.json"
            live = ProgressFile(path)
            def current():
                return json.loads(path.read_text(encoding="utf-8"))
            self.assertIsNone(current()["remaining_seconds"])
            with self.assertRaises(FileExistsError):
                ProgressFile(path)
            live.feed.stage("copy", 16, 32, "bytes")
            live.feed(48, "复制")
            self.assertEqual(current()["completed_units"], 16)
            with patch("export_progress.os.replace", side_effect=PermissionError):
                live.feed(100, "校验")
            self.assertLess(current()["percent"], 100)
            self.assertIsNone(live.finish("completed", "成片已校验"))
            self.assertEqual((current()["percent"], current()["state"]), (100, "completed"))

    def test_cli_failure_visible_and_progress_path_cannot_claim_output(self):
        import main
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder) / "out.mkv"
            result = Path(folder) / "result.json"
            live = Path(folder) / "live.json"
            args = ["input.mkv", "--target", "newdv", "--mode", "rtx", "--output", str(out),
                    "--result", str(result), "--progress-file"]
            with patch("engine.render", side_effect=ValueError("测试错误")) as render:
                self.assertEqual(main.export_command([*args, str(out)]), 1)
                render.assert_not_called()
                self.assertFalse(out.exists())
                result.unlink()
                self.assertEqual(main.export_command([*args, str(live)]), 1)
            data = json.loads(live.read_text(encoding="utf-8"))
            self.assertEqual((data["state"], data["stage"]), ("failed", "测试错误"))
            self.assertLess(data["percent"], 100)
