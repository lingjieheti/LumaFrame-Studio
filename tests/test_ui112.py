"""Cover replacement/close of live probes, settings recovery and dual-track preview precision."""

import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication
import engine
import main
import preview


class SourceLifecycle(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def make_window(self, history=None):
        where = Path(self.enterContext(tempfile.TemporaryDirectory()))
        settings = QSettings(str(where / "settings.ini"), QSettings.IniFormat)
        if history is not None:
            settings.setValue("history", json.dumps(history))
        with patch.object(main, "QSettings", return_value=settings):
            window = main.Studio()
        self.addCleanup(window.close)
        return window

    def wait_for(self, predicate):
        deadline = time.monotonic() + 5
        while not predicate() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.01)
        self.assertTrue(predicate())

    def test_replacing_source_and_closing_stop_active_probes(self):
        window = self.make_window()
        window.show()
        started = {name: threading.Event() for name in ("first", "second")}
        stopped = {name: threading.Event() for name in started}

        def inspect(name, *, cancel):
            started[name].set()
            try:
                engine._Runner(cancel)([sys.executable, "-c", "import time; time.sleep(10)"])
            finally:
                stopped[name].set()

        with patch.object(engine, "inspect_source", side_effect=inspect):
            window.load("first")
            self.wait_for(started["first"].is_set)
            window.load("second")
            self.wait_for(started["second"].is_set)
            self.wait_for(stopped["first"].is_set)
            self.assertEqual(window.film.text(), "second")
            window.close()
            self.wait_for(lambda: not window.isVisible())
            self.assertTrue(stopped["second"].is_set())
            self.assertFalse(window.inspections)

    def test_partly_corrupt_history_preserves_readable_records(self):
        record = dict(path="done.mkv", time="2026-11-24", target="keep7", n=12)
        window = self.make_window([None, {"path": 7}, record, []])
        self.assertEqual(window.history, [record])


class SampleTimebase(unittest.TestCase):
    def test_compare_split_timestamps_before_matroska_rounding(self):
        info = {"streams": [{"index": 0, "codec_type": "video"},
                            {"index": 1, "codec_type": "video"}], "_p7_bl": 0, "_p7_el": 1,
                "format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2"}}
        compared = []

        def timestamps(path, track, run, *, coded_order, with_end=False):
            compared.append((path.suffix, track))
            pts = [0, 41667 + (62 if path.suffix == ".mp4" and track == 1 else 0)]
            return (pts, 83333) if with_end else pts

        def copy_video(args):
            Path(args[-1]).touch()

        with tempfile.TemporaryDirectory() as folder, \
                patch.object(engine, "_Runner", return_value=copy_video), \
                patch.object(engine, "_probe", return_value=info), \
                patch.object(engine, "_tool", return_value="ffmpeg"), \
                patch.object(engine, "_times", side_effect=timestamps):
            with self.assertRaisesRegex(ValueError, "时间不匹配"):
                preview.render_sample("input-renamed.mkv", Path(folder) / "sample.mkv",
                                      "reference", lambda *_: None, None, target="keep7", duration=6)
        self.assertEqual(compared, [(".mp4", 0), (".mp4", 1)])


if __name__ == "__main__":
    unittest.main()
