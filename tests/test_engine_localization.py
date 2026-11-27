import unittest
from string import Formatter

from locale_engine import ENGINE_MESSAGES, engine_localize


class EngineLocalizationTests(unittest.TestCase):
    def test_every_translation_preserves_template_fields_and_formatting(self):
        def fields(template):
            return sorted((name, spec, conversion) for _, name, spec, conversion
                          in Formatter().parse(template) if name is not None)

        for source, translations in ENGINE_MESSAGES.items():
            with self.subTest(source=source):
                self.assertEqual(len(translations), 2)
                for translation in translations:
                    self.assertTrue(translation.strip())
                    self.assertEqual(fields(source), fields(translation))

    def test_dynamic_error_preserves_user_path_and_native_diagnostics(self):
        path = "C:/影片/{测试}/NVEncC64.exe"
        detail = "\r\n[ffmpeg] 字幕轨的起始时间发生了变化。\r\ninput=" + path
        message = "找不到 NVEncC 编码器：" + path + detail
        for lang in ("en", "ja"):
            with self.subTest(lang=lang):
                translated = engine_localize(message, lang)
                self.assertNotEqual(translated, message)
                self.assertTrue(translated.endswith(path + detail))
                self.assertEqual(engine_localize(path, lang), path)
        self.assertEqual(engine_localize(message, "zh-CN"), message)

    def test_legacy_engine_callbacks_localize_counts_with_stage(self):
        status = "逐帧测量 MaxCLL 与 MaxFALL · 1,250 / 2,000 帧 · 25.0 fps"
        self.assertEqual(engine_localize(status, "en"),
                         "Measuring MaxCLL and MaxFALL frame by frame · 1,250 / 2,000 frames · 25.0 fps")
        self.assertIn("1,250 / 2,000 フレーム · 25.0 fps", engine_localize(status, "ja"))
        diagnostic = "ffmpeg.exe 返回 1。\n任务已取消。"
        self.assertEqual(engine_localize(diagnostic, "en"), "ffmpeg.exe exited with code 1.\n任务已取消。")


if __name__ == "__main__":
    unittest.main()
