"""Exercise actual Qt navigation, preferences and file detection on Windows."""

import argparse
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QPoint, QSettings, Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication
import main


def run(source=None, pictures=None):
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    app.setFont(QFont("Microsoft YaHei UI", 10))
    with tempfile.TemporaryDirectory() as temp:
        settings = QSettings(str(Path(temp) / "preferences.ini"), QSettings.IniFormat)
        with patch.object(main, "QSettings", return_value=settings):
            window = main.Studio()
        window.setAttribute(Qt.WA_DontShowOnScreen)
        window.show()

        def spin(seconds, until=None):
            end = time.monotonic() + seconds
            while time.monotonic() < end:
                app.processEvents()
                time.sleep(0.015)
                if until and until():
                    break

        def screenshot(name):
            if pictures:
                Path(pictures).mkdir(parents=True, exist_ok=True)
                window.grab().scaledToWidth(1100, Qt.SmoothTransformation).save(
                    str(Path(pictures) / name)
                )

        spin(0.6)
        screenshot("workspace-light.png")
        assert not window.renderbtn.isEnabled()
        window.nav[2].click()
        assert window.stack.currentIndex() == 2
        window.dark_box.click()
        assert window.dark
        window.nav[0].click()
        spin(0.1)
        screenshot("workspace-dark.png")
        window.solid_box.click()
        assert not window.slider.isEnabled()
        window.solid_box.click()
        window.dark_box.click()
        window.slider.setValue(70)
        assert window.density == 70
        window.nav[1].click()
        assert window.stack.currentIndex() == 1
        p7 = {
            "dv_profile": 7, "source_type": "dv", "width": 3840, "height": 2160,
            "target_width": 3840, "target_height": 2160, "supported": True,
            "path": str(Path(temp) / "dual.mkv"), "can_keep7": True,
        }
        window.info = p7
        window.refill_targets()
        assert window.targets.currentData() == "keep7" and window.modes.count() == 1
        assert "无损" in window.modes.currentText() and "-P7" in window.path.text()
        window.targets.setCurrentIndex(window.targets.findData("preserve"))
        assert window.modes.currentData() == "reference" and "Profile 8.1" in window.hint.text()
        window.info = dict(p7, width=1920, height=1080, can_keep7=False)
        window.refill_targets()
        assert window.targets.count() == 1 and window.modes.currentData() == "rtx"
        window.info = None
        if source:
            window.load(str(Path(source).resolve()))
            spin(12, lambda: window.info is not None)
            assert window.info and window.info["supported"], window.status.text()
            assert window.renderbtn.isEnabled() and window.previewbtn.isEnabled()
            assert window.path.text().endswith(".mkv")
            assert window.targets.count() and window.modes.count()
            print(window.film_detail.text(), window.modes.currentData())
            if pictures and window.info.get("can_keep7"):
                window.nav[0].click()
                window.path.setText("D:/影片/双层杜比测试片-4K-DV-P7.mkv")
                window.path.setCursorPosition(0)
                spin(0.15)
                screenshot("workspace-dual.png")
        # At 125% scaling a small laptop can have fewer than 600 logical
        # pixels available. Both actions must stay outside the scrolling form.
        window.nav[0].click()
        window.resize(720, 480)
        spin(0.1)
        for button in (window.previewbtn, window.renderbtn):
            first = button.mapTo(window, QPoint(0, 0))
            last = button.mapTo(window, button.rect().bottomRight())
            assert button.isVisible() and window.rect().contains(first) and window.rect().contains(last)
        for button in window.nav:
            assert button.text() == "" and button.accessibleName()
        window.nav[2].click()
        # Higher density changes the live specimen, while opaque rendering is
        # independent of density. Compare the actual paint result.
        sample = window.material_sample
        if window.allow_transparency:
            window.slider.setValue(0)
            clear = sample.grab().toImage()
            window.slider.setValue(100)
            assert clear != sample.grab().toImage()
            window.solid_box.setChecked(True)
            solid = sample.grab().toImage()
            window.slider.setValue(0)
            assert solid == sample.grab().toImage() and not window.slider.isEnabled()
        window.close()
        app.processEvents()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--input")
    p.add_argument("--screenshots")
    a = p.parse_args()
    run(a.input, a.screenshots)
