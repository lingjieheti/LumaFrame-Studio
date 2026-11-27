"""Switch languages through the actual preferences widget, including during an export."""
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication
import main
from export_progress import Work
from localization import Translator, resolve_language


class Languages(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.settings = QSettings(str(folder / "prefs.ini"), QSettings.IniFormat)
        self.settings.setValue("uiLanguage", "zh-CN")
        self.source = folder / "导入影片 {1}.mkv"
        self.window = self.open_window()

    def open_window(self):
        with patch.object(main, "QSettings", return_value=self.settings):
            window = main.Studio()
        self.addCleanup(window.close)
        return window

    def select(self, code):
        index = self.window.languages.findData(code)
        self.assertGreaterEqual(index, 0)
        self.window.languages.setCurrentIndex(index)
        self.app.processEvents()

    def test_language_negotiation_and_migration(self):
        self.assertEqual(resolve_language("auto", "ja_JP"), "ja")
        self.assertEqual(resolve_language("auto", "de-DE"), "en")
        self.assertEqual(resolve_language("auto", "zh_Hans_CN"), "zh-CN")
        self.assertEqual(resolve_language("ja", "en_US"), "ja")
        self.settings.remove("uiLanguage")
        self.settings.setValue("dark", True)
        self.assertEqual(self.open_window().words.code, "zh-CN")

    def test_live_localization_preserves_ongoing_work_and_preferences(self):
        w = self.window
        w.caption(w.film, lambda: self.source.name)
        w.got_info(0, {"path": str(self.source), "source_type": "sdr", "dv_profile": None,
                        "profile": "SDR BT.709", "width": 1920, "height": 1080, "fps": "24",
                        "target_width": 3840, "target_height": 2160, "supported": True,
                        "duration": 12, "audio_count": 2, "subtitle_count": 3, "reason": ""}, "")
        w.targets.setCurrentIndex(w.targets.findData("newdv"))
        w.speeds.setCurrentIndex(w.speeds.findData("fast"))
        w.area_choice.setCurrentIndex(w.area_choice.findData("custom"))
        w.margin_inputs[2].setValue(40)
        w.slider.setValue(68)
        dest = w.path.text()
        self.select("en")
        self.assertEqual(w.film.text(), self.source.name)
        self.assertNotRegex(w.status.text() + w.hint.text() + w.modes.currentText(), r"[\u3400-\u9fff]")
        self.assertIn("3840", w.status.text())
        self.assertEqual(w.path.text(), dest)
        self.assertEqual(w.speeds.currentData(), "fast")
        self.assertEqual(w.margin_inputs[2].value(), 40)
        self.assertEqual(w.density_value.text(), "68%")
        w.busy = True
        self.addCleanup(setattr, w, "busy", False)
        w.started_at = time.monotonic() - 5
        w.update_progress(40, "读取原片逐帧时间戳", Work("scan", 9, 12, "frames"), time.monotonic())
        self.assertNotRegex(w.status.text(), r"[\u3400-\u9fff]")
        self.select("ja")
        self.assertIn("9 / 12", w.status.text())
        self.assertEqual("40%", w.percent_value.text())
        self.assertFalse(w.cancel_event.is_set())
        self.assertEqual(w.targets.currentData(), "newdv")
        w.finished(None, "字幕轨的起始时间发生了变化。")
        self.assertNotIn("导出未完成", w.status.text())
        self.assertEqual(self.open_window().words.code, "ja")
        self.select("zh-CN")
        self.assertIn("字幕轨", w.status.text())

    def test_cli_translates_only_human_fields(self):
        result = self.source.parent / "result.json"
        progress = result.with_name("progress.json")
        error = "字幕轨的起始时间发生了变化。"
        with patch("engine.render", side_effect=ValueError(error)):
            code = main.export_command([str(self.source), "--output", str(self.source.with_suffix(".out.mkv")),
                "--result", str(result), "--progress-file", str(progress), "--language", "en",
                "--target", "newdv", "--mode", "rtx"])
        self.assertEqual(code, 1)
        expected = Translator("en").message(error)
        self.assertNotEqual(expected, error)
        self.assertEqual(json.loads(result.read_bytes()), {"verified": False, "error": expected})
        snapshot = json.loads(progress.read_bytes())
        self.assertEqual((snapshot["state"], snapshot["stage"]), ("failed", expected))
