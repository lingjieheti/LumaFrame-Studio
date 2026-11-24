"""Live optical sample used beside the desktop's material preferences."""

from PySide6.QtCore import Qt, QRectF, QPointF
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QSizePolicy, QWidget

from glass import opaque, rim, rounded


class MaterialSample(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAccessibleName("玻璃材质预览")
        self.setToolTip("在同一组色带上比较透明度与磨砂浓度。")
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        self.setMinimumSize(115, 76)
        # A fixed optical target gives the eye a sharp edge on both sides of
        # the lens. It stays useful before the user opens their first film.
        self.target = QPixmap(480, 240)
        self.target.fill(QColor("#151938"))
        p = QPainter(self.target)
        p.setRenderHint(QPainter.Antialiasing)
        field = QLinearGradient(0, 240, 440, 0)
        for stop, color in [(0, "#17264d"), (.38, "#343970"), (.72, "#697fb8"), (1, "#b2c7e9")]:
            field.setColorAt(stop, QColor(color))
        p.fillRect(self.target.rect(), field)
        p.translate(230, 122)
        p.rotate(26)
        for x, width, color in [
            (-139, 30, "#4658a9"), (-96, 34, "#7cb6dc"), (-60, 6, "#ccf3f4"),
            (-36, 43, "#9a91d7"), (23, 18, "#c9a6cb"), (53, 7, "#f6cda9"),
            (80, 48, "#667bc3"), (139, 12, "#b7d7ed"),
        ]:
            p.fillRect(QRectF(x, -240, width, 480), QColor(color))
        p.end()
        self.diffused = self.target.scaled(26, 13, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        r = QRectF(self.rect()).adjusted(.5, .5, -.5, -.5)
        frame = rounded(r, 14)
        p.setClipPath(frame)
        scale = max(r.width() / self.target.width(), r.height() / self.target.height())
        crop = QRectF(0, 0, r.width() / scale, r.height() / scale)
        crop.moveCenter(QPointF(self.target.width() / 2, self.target.height() / 2))
        p.drawPixmap(r, self.target, crop)
        window = self.window()
        dark = getattr(window, "dark", False)
        density = max(0, min(100, getattr(window, "density", 45))) / 100
        lens = QRectF(0, 0, min(r.width() * .66, r.height() * 1.36), r.height() * .68)
        lens.moveCenter(r.center())
        shape = rounded(lens, lens.height() / 2)
        p.fillPath(shape.translated(0, 2), QColor(15, 17, 45, 55))
        if opaque(window):
            p.fillPath(shape, QColor("#343849" if dark else "#eef0f8"))
        else:
            p.save()
            p.setClipPath(shape, Qt.IntersectClip)
            # Project the same color bars through the lens with magnification
            # about its center. The soft target and tint track the density dial.
            scene = QRectF(crop.left() + (lens.left() - r.left()) / scale,
                           crop.top() + (lens.top() - r.top()) / scale,
                           lens.width() / scale, lens.height() / scale)
            scene.adjust(scene.width() * .065, scene.height() * .065,
                         -scene.width() * .065, -scene.height() * .065)
            p.drawPixmap(lens, self.target, scene)
            p.setOpacity(.35 + .65 * density)
            sx = self.diffused.width() / self.target.width()
            sy = self.diffused.height() / self.target.height()
            p.drawPixmap(lens, self.diffused, QRectF(scene.x() * sx, scene.y() * sy,
                                                     scene.width() * sx, scene.height() * sy))
            p.setOpacity(1)
            tint = QColor("#262a43" if dark else "#edf1fb")
            tint.setAlpha(round(30 + 193 * density))
            p.fillPath(shape, tint)
            p.restore()
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(20, 24, 51, 100), 1))
        p.drawPath(shape)
        rim(p, shape, lens, .95)
        rim(p, frame, r, .56)
