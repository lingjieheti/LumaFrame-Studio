"""Exercise actual Qt navigation, preferences and file detection on Windows."""

import argparse
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QSettings, Qt
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
        if source:
            window.load(str(Path(source).resolve()))
            spin(12, lambda: window.info is not None)
            assert window.info and window.info["supported"], window.status.text()
            assert window.renderbtn.isEnabled() and window.previewbtn.isEnabled()
            assert window.path.text().endswith(".mkv")
            assert window.targets.count() and window.modes.count()
            print(window.film_detail.text(), window.modes.currentData())
        window.close()
        app.processEvents()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--input")
    p.add_argument("--screenshots")
    a = p.parse_args()
    run(a.input, a.screenshots)
