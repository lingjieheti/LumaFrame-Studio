"""Exercise export naming and worker-state transitions through real Qt controls."""

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QSettings
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication
import main


class ExportWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        temp = self.enterContext(tempfile.TemporaryDirectory())
        self.directory = Path(temp)
        settings = QSettings(str(self.directory / "preferences.ini"), QSettings.IniFormat)
        with patch.object(main, "QSettings", return_value=settings):
            self.window = main.Studio()
        self.addCleanup(self.window.close)
        source = self.directory / "原片.mkv"
        self.window.got_info(0, {
            "path": str(source), "source_type": "dv", "dv_profile": 7,
            "profile": "Dolby Vision Profile 7 双层 · FEL", "width": 3840, "height": 2160,
            "target_width": 3840, "target_height": 2160, "fps": "24", "can_keep7": True,
            "supported": True, "duration": 6.25, "subtitle_count": 0, "audio_count": 1, "reason": "",
        }, "")

    def wait_for_result(self):
        deadline = time.monotonic() + 10
        while self.window.busy and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.01)
        self.assertFalse(self.window.busy)

    def test_native_hdr10_offers_picture_preserving_authoring(self):
        info = dict(self.window.info, dv_profile=None, source_type="hdr10", can_keep7=False, profile="HDR10")
        self.window.got_info(0, info, "")
        self.assertEqual(self.window.targets.currentData(), "newdv")
        self.assertEqual(self.window.modes.count(), 1)
        self.assertEqual(self.window.modes.currentData(), "copy")
        self.assertTrue(self.window.mapping_row.isHidden())

    def test_compression_repair_is_optional_for_smaller_profile8_sources(self):
        info = dict(self.window.info, dv_profile=8, width=1920, height=1080, can_keep7=False)
        self.window.got_info(0, info, "")
        self.assertEqual(self.window.modes.currentData(), "rtx")
        self.assertGreater(self.window.modes.findData("rtx_compressed"), 0)
        self.window.got_info(0, dict(info, width=3840, height=2160, can_copy8=True), "")
        self.assertEqual(self.window.modes.findData("rtx_compressed"), -1)

    def test_existing_names_and_preview_keep_custom_destination(self):
        chosen = self.directory / "自选名称.mkv"
        chosen.write_bytes(b"original")
        with patch.object(main.QFileDialog, "getSaveFileName", return_value=(str(chosen), "")) as dialog:
            self.window.set_destination()
        self.assertTrue(dialog.call_args.kwargs["options"] & main.QFileDialog.Option.DontConfirmOverwrite)
        available = self.directory / "自选名称-2.mkv"
        self.assertEqual(Path(self.window.path.text()), available)
        sample = self.directory / "自选名称-2-试片.mkv"
        sample.write_bytes(b"old preview")

        def complete(source, dest, *args, target, **kwargs):
            with Path(dest).open("xb") as out:
                out.write(b"new output")
            return dict(path=dest, frame_count=1, width=3840, height=2160, target=target)

        with patch("preview.render_sample", side_effect=complete):
            self.window.previewbtn.click()
            self.wait_for_result()
        self.assertEqual(Path(self.window.last_output).name, "自选名称-2-试片-2.mkv")
        self.assertEqual(Path(self.window.path.text()), available)
        self.window.targets.setCurrentIndex(self.window.targets.findData("preserve"))
        self.assertEqual(Path(self.window.path.text()), available)
        available.write_bytes(b"another job")
        with patch("engine.render", side_effect=complete):
            self.window.renderbtn.click()
            self.wait_for_result()
        self.assertEqual(Path(self.window.last_output).name, "自选名称-3.mkv")
        self.assertEqual(chosen.read_bytes(), b"original")
        self.assertEqual(sample.read_bytes(), b"old preview")
        self.assertEqual(available.read_bytes(), b"another job")

    def test_progress_does_not_regress_or_replace_cancellation(self):
        window = self.window
        window.busy = True
        window.started_at = time.monotonic() - 125
        window.progress_value = 0
        window.update_progress(70, "编码")
        window.update_progress(12, "封装")
        self.assertIn("70%", window.status.text())
        self.assertIn("00:02:05", window.status.text())
        window.stop()
        window.update_progress(80, "迟到的状态")
        self.assertIn("正在取消", window.status.text())
        with patch.object(main.QMessageBox, "question", side_effect=AssertionError):
            window.closeEvent(QCloseEvent())
        window.finished(None, "已取消")
        self.assertFalse(window.busy)

    def test_failed_job_can_be_retried_without_a_dialog(self):
        with patch("engine.render", side_effect=ValueError("空间不足\n具体诊断")):
            self.window.renderbtn.click()
            self.wait_for_result()
        self.assertIn("空间不足", self.window.status.text())
        self.assertFalse(self.window.detailbtn.isHidden())
        self.assertTrue(self.window.renderbtn.isEnabled())
        self.assertIsNone(QApplication.activeModalWidget())

    def test_native_p81_defaults_to_copy_and_sdr_mapping_reaches_worker(self):
        window = self.window
        p81 = dict(window.info, dv_profile=8, can_keep7=False, can_copy8=True, profile="Dolby Vision Profile 8.1")
        window.got_info(0, p81, "")
        self.assertEqual((window.targets.currentData(), window.modes.currentData()), ("preserve", "copy"))
        self.assertTrue(window.mapping_row.isHidden())
        window.got_info(0, dict(p81, source_type="sdr", can_copy8=False, dv_profile=None, profile="SDR BT.709"), "")
        self.assertFalse(window.mapping_row.isHidden())
        window.sdr_mapping.setCurrentIndex(window.sdr_mapping.findData("reference"))
        with patch("engine.render", side_effect=ValueError("test finished")) as render:
            window.renderbtn.click()
            self.wait_for_result()
        self.assertEqual(render.call_args.kwargs["sdr_mapping"], "reference")


if __name__ == "__main__":
    unittest.main()
