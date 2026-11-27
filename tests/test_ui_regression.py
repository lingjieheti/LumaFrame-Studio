"""Exercise export naming and worker-state transitions through real Qt controls."""

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QPoint, QRect, QSettings, Qt
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
        settings.setValue("uiLanguage", "zh-CN")
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

    def test_short_viewport_keeps_format_and_export_in_view(self):
        window = self.window
        window.setAttribute(Qt.WA_DontShowOnScreen)
        window.resize(1140, 800)
        window.show()
        self.app.processEvents()
        large_preview = window.stage.height()
        window.resize(720, 480)
        for code in ("zh-CN", "en", "ja"):
            with self.subTest(language=code):
                window.languages.setCurrentIndex(window.languages.findData(code))
                self.app.processEvents()
                for widget, enclosing in ((window.targets, window.workspace_viewport),
                                          (window.pick, window.stage),
                                          (window.film_detail, window.stage),
                                          (window.renderbtn, window)):
                    rect = QRect(widget.mapTo(enclosing, QPoint()), widget.size())
                    self.assertTrue(widget.isVisibleTo(enclosing) and enclosing.rect().contains(rect), rect)
        self.assertLess(window.stage.height(), large_preview)
        window.languages.setCurrentIndex(window.languages.findData("zh-CN"))
        window.resize(1140, 800)
        self.app.processEvents()
        self.assertEqual(window.stage.height(), large_preview)

    def test_static_hdr10_upscale_and_new_dolby_area_apply_to_the_selected_source(self):
        window = self.window
        source = dict(window.info, width=1920, height=1080, can_keep7=False, dv_profile=None, source_type="hdr10")
        window.got_info(0, source, "")
        self.assertEqual(window.targets.currentData(), "hdr10")
        self.assertEqual(window.modes.currentData(), "rtx")
        self.assertGreater(window.modes.findData("rtx_compressed"), 0)
        self.assertFalse(window.area_panel.isHidden())
        self.assertTrue(window.mapping_row.isHidden())
        window.area_choice.setCurrentIndex(window.area_choice.findData(2.35))
        with patch("preview.render_sample", side_effect=ValueError("test finished")) as hdr_sample:
            window.start(sample=True)
            self.wait_for_result()
        self.assertEqual(hdr_sample.call_args.kwargs["active_area"], (0, 0, 262, 262))
        window.targets.setCurrentIndex(window.targets.findData("newdv"))
        self.assertFalse(window.area_panel.isHidden())
        window.area_choice.setCurrentIndex(window.area_choice.findData(2.39))
        with patch("preview.render_sample", side_effect=ValueError("test finished")) as sample:
            window.start(sample=True)
            self.wait_for_result()
        self.assertEqual(sample.call_args.kwargs["active_area"], (0, 0, 276, 276))
        window.area_choice.setCurrentIndex(window.area_choice.findData("custom"))
        for field, value in zip(window.margin_inputs, (9, 20, 25, 30)):
            field.setValue(value)
        with patch("engine.render", side_effect=ValueError("test finished")) as render:
            window.start()
            self.wait_for_result()
        self.assertEqual(render.call_args.kwargs["active_area"], (9, 20, 25, 30))
        window.margin_inputs[1].setValue(3840)
        with patch("engine.render") as render:
            window.start()
            self.assertFalse(window.busy)
            render.assert_not_called()
        window.got_info(0, dict(source, path="other.mkv"), "")
        self.assertEqual(window.area_choice.currentData(), "full")
        self.assertTrue(all(f.value() == 0 for f in window.margin_inputs))

    def test_compression_repair_is_optional_for_smaller_profile8_sources(self):
        info = dict(self.window.info, dv_profile=8, width=1920, height=1080, can_keep7=False)
        self.window.got_info(0, info, "")
        self.assertEqual(self.window.modes.currentData(), "rtx")
        self.assertGreater(self.window.modes.findData("rtx_compressed"), 0)
        self.window.got_info(0, dict(info, width=3840, height=2160, can_copy8=True), "")
        self.assertEqual(self.window.modes.findData("rtx_compressed"), -1)

    def test_rtx_speed_reaches_sample_and_render_only_on_applicable_modes(self):
        window = self.window
        source = dict(window.info, dv_profile=8, width=1920, height=1080, can_keep7=False)
        window.got_info(0, source, "")
        self.assertFalse(window.speed_field.isHidden())
        self.assertEqual(window.speeds.currentData(), "quality")
        self.assertEqual(window.speeds.currentText(), "标准")
        self.assertIn("相同的超分模型和编码参数", window.hint.text())
        with patch("engine.render", side_effect=ValueError("test finished")) as render:
            window.start()
            self.assertFalse(window.speeds.isEnabled())
            self.wait_for_result()
        self.assertEqual(render.call_args.kwargs["speed"], "quality")

        window.speeds.setCurrentIndex(window.speeds.findData("fast"))
        self.assertEqual(window.speeds.currentText(), "并行加速")
        self.assertEqual(window.settings.value("rtxSpeed"), "fast")
        for mode, sample in (("rtx", True), ("rtx_compressed", False), ("reference", True)):
            with self.subTest(mode=mode):
                window.modes.setCurrentIndex(window.modes.findData(mode))
                self.assertEqual(window.speed_field.isHidden(), mode == "reference")
                with patch("preview.render_sample" if sample else "engine.render",
                           side_effect=ValueError("test finished")) as worker:
                    window.start(sample=sample)
                    self.wait_for_result()
                self.assertEqual(worker.call_args.kwargs["speed"], "quality" if mode == "reference" else "fast")

        native = dict(source, width=3840, height=2160, can_copy8=True)
        window.got_info(0, native, "")
        self.assertEqual(window.modes.currentData(), "copy")
        self.assertTrue(window.speed_field.isHidden())
        with patch.object(main, "QSettings", return_value=window.settings):
            reopened = main.Studio()
        self.addCleanup(reopened.close)
        self.assertTrue(reopened.speed_field.isHidden())
        reopened.got_info(0, source, "")
        self.assertEqual(reopened.speeds.currentData(), "fast")

    def test_export_command_validates_speed_after_resolving_mode_and_target(self):
        # Explicit CLI options and the default-mode path must agree with the desktop controls.
        source = dict(self.window.info, dv_profile=8, width=1920, height=1080, can_keep7=False)
        args = [source["path"], "--output", str(self.directory / "out.mkv")]
        cases = (
            ([], "quality", "rtx", False),
            (["--speed", "fast", "--sample"], "fast", "rtx", True),
            (["--target", "preserve", "--mode", "rtx_compressed", "--speed", "fast"], "fast", "rtx_compressed", False),
            (["--target", "preserve", "--mode", "reference", "--speed", "fast"], None, None, False),
            (["--target", "keep7", "--mode", "rtx", "--speed", "fast"], None, None, False),
        )
        for i, (extra, expected, mode, sample) in enumerate(cases):
            report = self.directory / f"report-{i}.json"
            with self.subTest(extra=extra), patch("engine.inspect_source", return_value=source), \
                    patch("preview.render_sample" if sample else "engine.render", return_value={"verified": True}) as worker:
                result = main.export_command(args + ["--result", str(report)] + extra)
                saved = json.loads(report.read_text("utf-8"))
                if expected:
                    self.assertEqual(result, 0)
                    self.assertTrue(saved["verified"])
                    self.assertEqual(worker.call_args.args[2], mode)
                    self.assertEqual(worker.call_args.kwargs["speed"], expected)
                else:
                    self.assertEqual(result, 1)
                    self.assertFalse(saved["verified"])
                    self.assertIn("--speed fast", saved["error"])
                    worker.assert_not_called()

    def test_three_speed_controls_reflow_as_the_window_narrows(self):
        window = self.window
        window.got_info(0, dict(window.info, dv_profile=8, width=1920, height=1080, can_keep7=False), "")
        window.show()
        grid = window.setting_fields
        for width, expected in [(1100, (0, 2, 1, 1)), (920, (1, 0, 1, 2)), (720, (2, 0, 1, 1))]:
            with self.subTest(width=width):
                window.resize(width, 480)
                self.app.processEvents()
                self.assertEqual(grid.getItemPosition(grid.indexOf(window.speed_field)), expected)
                self.assertTrue(window.speed_field.isVisible())
                self.assertTrue(window.renderbtn.isVisible())

    def test_suggested_destination_belongs_to_the_current_movie(self):
        self.window.settings.setValue("outputDir", str(self.directory / "old export folder"))
        folder = self.directory / "second movie"
        folder.mkdir()
        source = folder / "second.mkv"
        self.window.got_info(0, dict(self.window.info, path=str(source)), "")
        self.assertEqual(Path(self.window.path.text()).parent, folder)

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

    def test_stage_counter_speed_and_two_etas_follow_the_active_stage(self):
        from export_progress import Work
        window = self.window
        window.busy = True
        window.started_at = time.monotonic() - 20
        now = time.monotonic()
        window.update_progress(10, "编码", Work("encode", 0, 100, "frames"), now - 5)
        self.assertIn("估算中", window.total_eta.text())
        window.update_progress(15, "编码", Work("encode", 10, 100, "frames"), now)
        self.assertFalse(window.progress_metrics.isHidden())
        self.assertIn("10 / 100 帧", window.status.text())
        self.assertEqual(window.stage_eta.text(), "约 00:00:45")
        self.assertEqual(window.total_eta.text(), "约 00:01:25")
        self.assertEqual(window.speed_value.text(), "2.0 帧/秒")
        window.update_progress(32, "亮度测量", Work("light", 0, 100, "frames"), now)
        self.assertIn("估算中", window.stage_eta.text())
        self.assertEqual(window.speed_value.text(), "—")
        window.stop()
        self.assertTrue(window.progress_metrics.isHidden())
        window.finished(None, "已取消")

    def test_progress_does_not_regress_or_replace_cancellation(self):
        window = self.window
        window.busy = True
        window.started_at = time.monotonic() - 125
        window.progress_value = 0
        window.update_progress(70, "编码")
        window.update_progress(12, "封装")
        self.assertEqual("70%", window.percent_value.text())
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
