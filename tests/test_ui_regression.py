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
        self.enterContext(patch("encode_policy.nvidia_gpu_name", return_value="NVIDIA GeForce RTX 5080"))
        self.window.events.gpu.disconnect(self.window.gpu_name)
        self.window.gpu_name("NVIDIA GeForce RTX 5080")
        source = self.directory / "原片.mkv"
        self.window.got_info(0, {
            "path": str(source), "source_type": "dv", "dv_profile": 7,
            "profile": "Dolby Vision Profile 7 双层 · FEL", "width": 3840, "height": 2160,
            "target_width": 3840, "target_height": 2160, "fps": "24", "can_keep7": True,
            "supported": True, "duration": 6.25, "subtitle_count": 0, "audio_count": 1, "reason": "",
        }, "")

    def wait_for_result(self, window=None):
        window = window or self.window
        deadline = time.monotonic() + 10
        while window.busy and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.01)
        self.assertFalse(window.busy)

    def test_native_hdr10_offers_picture_preserving_authoring(self):
        info = dict(self.window.info, dv_profile=None, source_type="hdr10", can_keep7=False, profile="HDR10")
        self.window.got_info(0, info, "")
        self.assertEqual(self.window.targets.currentData(), "newdv")
        self.assertIn("保留原始 HDR10 画面", self.window.plan.text())

    def test_short_viewport_keeps_format_and_export_in_view(self):
        window = self.window
        self.app.processEvents()
        window.gpu_name("")
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
                self.assertEqual(window.gpu_label.toolTip(), window.words.text("未检测到 NVIDIA GPU"))
                for widget, enclosing in ((window.targets, window.workspace_viewport),
                                          (window.pick, window.stage),
                                          (window.film_detail, window.stage),
                                          (window.renderbtn, window)):
                    rect = QRect(widget.mapTo(enclosing, QPoint()), widget.size())
                    self.assertTrue(
                        widget.isVisibleTo(enclosing) and enclosing.rect().contains(rect),
                        f"{widget.accessibleName() or widget.objectName()} bounds {rect} in {enclosing.rect()}, "
                        f"viewport {window.workspace_viewport.size()}, sheet {window.workspace.parentWidget().size()}, "
                        f"stage {window.stage.size()}, GPU {window.gpu_label.text()!r}, font {widget.font().toString()}",
                    )
        self.assertLess(window.stage.height(), large_preview)
        window.languages.setCurrentIndex(window.languages.findData("zh-CN"))
        window.resize(1140, 800)
        self.app.processEvents()
        self.assertEqual(window.stage.height(), large_preview)

    def test_default_target_and_explicit_hdr10_use_full_canvas(self):
        window = self.window
        source = dict(window.info, width=1920, height=1080, can_keep7=False, dv_profile=None, source_type="hdr10")
        window.got_info(0, source, "")
        self.assertEqual(window.targets.currentData(), "newdv")
        self.assertIn("RTX", window.plan.text())
        window.targets.setCurrentIndex(window.targets.findData("hdr10"))
        with patch("preview.render_sample", side_effect=ValueError("test finished")) as preview:
            window.start(sample=True)
            self.wait_for_result()
        self.assertEqual(preview.call_args.args[2], "rtx")
        self.assertEqual(preview.call_args.kwargs["target"], "hdr10")
        self.assertIsNone(preview.call_args.kwargs.get("active_area"))

    def test_old_manual_settings_do_not_override_automatic_quality(self):
        source = dict(self.window.info, source_type="sdr", width=1920, height=1080, can_keep7=False)
        self.window.settings.setValue("rtxSpeed", "fast")
        self.window.settings.setValue("sdrMapping", "reference")
        with patch.object(main, "QSettings", return_value=self.window.settings):
            window = main.Studio()
        self.addCleanup(window.close)
        window.events.gpu.disconnect(window.gpu_name)
        window.gpu_name("NVIDIA GeForce RTX 5080")
        window.got_info(0, source, "")
        for sample in (True, False):
            with self.subTest(sample=sample), patch("preview.render_sample" if sample else "engine.render",
                    side_effect=ValueError("test finished")) as worker:
                window.start(sample=sample)
                self.wait_for_result(window)
                self.assertEqual(worker.call_args.args[2], "rtx")
                self.assertEqual(worker.call_args.kwargs["sdr_mapping"], "auto")
                self.assertEqual(worker.call_args.kwargs["speed"], "quality")

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

    def test_automatic_grading_starts_without_a_model_dialog(self):
        window = self.window
        window.got_info(0, dict(window.info, source_type="sdr", dv_profile=0, width=1920,
                                height=1080, can_keep7=False), "")
        self.assertIn("自动逐镜调色", window.plan.text())
        with patch.object(main.QFileDialog, "getOpenFileName") as picker, \
                patch("engine.render", side_effect=ValueError("test finished")) as render:
            window.start()
            self.wait_for_result()
        picker.assert_not_called()
        self.assertEqual(render.call_args.kwargs["sdr_mapping"], "auto")
        self.assertTrue(window.renderbtn.isEnabled())

    def test_cli_cinema_pack_is_imported_before_render(self):
        for mapping in ("auto", "cinema"):
            with self.subTest(mapping=mapping), patch("cinema_lut.install_model") as install:
                report = self.directory / f"{mapping}.json"
                args = [self.window.info["path"], "--output", str(self.directory / "out.mkv"),
                        "--result", str(report), "--mode", "reference", "--target", "newdv",
                        "--cinema-model", "pack.zip"]
                if mapping == "cinema":
                    args.extend(["--sdr-mapping", mapping])

                def render(*args, **kwargs):
                    install.assert_called_once_with("pack.zip")
                    self.assertEqual(kwargs["sdr_mapping"], mapping)
                    return {"verified": True}

                with patch("engine.render", side_effect=render):
                    self.assertEqual(main.export_command(args), 0)
                self.assertTrue(json.loads(report.read_text("utf-8"))["verified"])

    def test_processing_summary_reflows_as_the_window_narrows(self):
        window = self.window
        window.show()
        grid = window.setting_fields
        for width, position in ((1140,(0,1)), (920,(0,1)), (720,(1,0))):
            with self.subTest(width=width):
                window.resize(width, 480)
                self.app.processEvents()
                self.assertEqual(grid.getItemPosition(grid.indexOf(window.plan_field))[:2], position)
                self.assertTrue(window.plan.isVisible())
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

    def test_native_p81_defaults_to_copy_and_switching_source_resets_processing(self):
        window = self.window
        p81 = dict(window.info, dv_profile=8, can_keep7=False, can_copy8=True, profile="Dolby Vision Profile 8.1")
        window.got_info(0, p81, "")
        self.assertEqual(window.targets.currentData(), "preserve")
        self.assertIn("无损封装", window.plan.text())
        window.got_info(0, dict(p81, source_type="sdr", can_copy8=False, dv_profile=None, profile="SDR BT.709"), "")
        self.assertIn("自动逐镜调色", window.plan.text())
        with patch("preview.render_sample", side_effect=ValueError("test preview")) as preview:
            window.previewbtn.click()
            self.wait_for_result()
        self.assertEqual(preview.call_args.kwargs["sdr_mapping"], "auto")
        window.got_info(0, p81, "")
        with patch("engine.render", side_effect=ValueError("test HDR source")) as native:
            window.renderbtn.click()
            self.wait_for_result()
        self.assertEqual(native.call_args.args[2], "copy")
        self.assertEqual(native.call_args.kwargs["sdr_mapping"], "expand")
        self.assertNotIn("逐镜调色", window.plan.text())

    def test_per_shot_grading_fits_the_scrolled_workspace(self):
        window = self.window
        window.setAttribute(Qt.WA_DontShowOnScreen)
        window.got_info(0, dict(window.info, source_type="sdr", dv_profile=None, can_keep7=False,
                                width=1920, height=1080, profile="SDR BT.709"), "")
        summary = window.plan
        window.show()
        viewport = window.workspace_viewport
        for size in ((720, 480), (1140, 800)):
            window.resize(*size)
            for code in ("en", "ja", "zh-CN"):
                with self.subTest(size=size, language=code):
                    window.languages.setCurrentIndex(window.languages.findData(code))
                    self.app.processEvents()
                    viewport.parentWidget().ensureWidgetVisible(summary)
                    bounds = QRect(summary.mapTo(viewport, QPoint()), summary.size())
                    self.assertTrue(viewport.rect().contains(bounds))
                    self.assertGreaterEqual(summary.height(), summary.heightForWidth(summary.width()))


if __name__ == "__main__":
    unittest.main()
