"""Local Qt material for navigation and controls. All artwork is original."""

from pathlib import Path
from PySide6.QtCore import Qt, QRectF, QPointF, Signal
from PySide6.QtGui import (
    QPainter,
    QColor,
    QPen,
    QBrush,
    QLinearGradient,
    QRadialGradient,
    QPainterPath,
    QPixmap,
    QFont,
    QCursor,
)
from PySide6.QtWidgets import QWidget, QPushButton, QComboBox

ASSETS = Path(__file__).resolve().parent / "assets"


def gradient(rect, stops):
    g = QLinearGradient(rect.topLeft(), rect.bottomRight())
    for at, color in stops:
        g.setColorAt(at, QColor(color))
    return g


def ink(p, name, x, y, color):
    p.save()
    p.translate(x, y)
    p.setBrush(Qt.NoBrush)
    p.setPen(QPen(QColor(color), 1.65, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))

    def line(*pts):
        path = QPainterPath(QPointF(*pts[0]))
        for pt in pts[1:]:
            path.lineTo(QPointF(*pt))
        p.drawPath(path)

    if name == "studio":
        p.drawRoundedRect(QRectF(1, 3, 17, 14), 3, 3)
        line((5, 8), (8, 8))
        line((5, 12), (10, 12))
        line((13, 5), (13, 15))
    elif name == "history":
        p.drawArc(QRectF(2, 2, 15, 15), -136 * 16, 292 * 16)
        line((1, 4), (2, 8), (6, 7))
        line((10, 6), (10, 10), (13, 12))
    elif name == "design":
        for a, b in [(5, 7), (10, 13), (15, 6)]:
            line((2, a), (17, a))
            p.setBrush(QColor(color))
            p.drawEllipse(QPointF(b, a), 2, 2)
    elif name == "heart":
        q = QPainterPath(QPointF(10, 16))
        q.cubicTo(-5, 7, 5, -1, 10, 6)
        q.cubicTo(15, -1, 25, 7, 10, 16)
        p.drawPath(q)
    elif name == "add":
        line((3, 9), (16, 9))
        line((9.5, 2.5), (9.5, 15.5))
    elif name == "link":
        p.drawRoundedRect(QRectF(2, 6, 12, 11), 2, 2)
        line((9, 2), (17, 2), (17, 10))
        line((8, 11), (17, 2))
    elif name == "go":
        line((3, 9), (16, 9))
        line((10, 3), (16, 9), (10, 15))
    elif name == "folder":
        line((2, 6), (2, 4), (8, 4), (10, 6), (17, 6), (17, 15), (2, 15), (2, 6))
    elif name == "help":
        p.drawEllipse(QRectF(2, 2, 15, 15))
        p.drawText(QRectF(2, 0, 15, 18), Qt.AlignCenter, "?")
    p.restore()


class GlassButton(QPushButton):
    def __init__(self, text, icon="", role="plain"):
        super().__init__(text)
        self.icon_name, self.role, self.selected = icon, role, False
        self.setCursor(Qt.PointingHandCursor)
        self.setMouseTracking(True)
        self.setFixedHeight(41)
        self.setFocusPolicy(Qt.StrongFocus)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(1.5, 1.5, -1.5, -1.5)
        win = self.window()
        dark, solid = getattr(win, "dark", False), getattr(win, "solid", False)
        hover, enabled = self.underMouse(), self.isEnabled()
        nav, hero, primary = self.role == "nav", self.role == "hero", self.role == "primary"
        text_color = "#edf1ff" if dark else "#252940"
        shape = QPainterPath()
        rad = 12 if nav else min(20, rect.height() / 2)
        shape.addRoundedRect(rect, rad, rad)
        painted = not nav or self.selected or hover
        if painted:
            if hero:
                stops = [
                    (0, "#c8ffffff" if solid else "#73ffffff"),
                    (0.6, "#3cacc4ff"),
                    (1, "#557191ab"),
                ]
                text_color = "#ffffff"
            elif primary:
                stops = (
                    [(0, "#4852b6"), (0.62, "#5953c6"), (1, "#554ba8")]
                    if enabled
                    else [(0, "#928fbe"), (1, "#8985ac")]
                )
                text_color = "#ffffff"
            elif self.selected:
                stops = (
                    [(0, "#ecf1ff"), (1, "#d4d9fb")] if not dark else [(0, "#454666"), (1, "#373654")]
                )
                text_color = "#4545a0" if not dark else "#d5d4ff"
            else:
                stops = (
                    [(0, "#fbfcff"), (1, "#e2e6f4")] if not dark else [(0, "#35384d"), (1, "#282a3a")]
                )
            if solid and not primary:
                fill = QColor("#33384b" if dark or hero else "#f0f2f8")
                p.fillPath(shape, fill)
            else:
                if hero and hasattr(self.parentWidget(), "sample_glass"):
                    p.save()
                    p.setClipPath(shape)
                    self.parentWidget().sample_glass(p, self)
                    p.restore()
                p.fillPath(shape, gradient(rect, stops))
            outer = QColor(18, 25, 67, 60 if not dark else 170)
            p.setPen(QPen(outer, 1))
            p.drawPath(shape)
            inner = rect.adjusted(1, 1, -1, -1)
            rim = gradient(inner, [(0, "#c3ffffff"), (0.45, "#40ffffff"), (1, "#14ffffff")])
            p.setPen(QPen(QBrush(rim), 1.0))
            p.drawRoundedRect(inner, rad - 1, rad - 1)
            if hover and enabled and not solid:
                p.save()
                p.setClipPath(shape)
                q = QRadialGradient(QPointF(self.mapFromGlobal(QCursor.pos())), 90)
                q.setColorAt(0, QColor(255, 255, 255, 44))
                q.setColorAt(1, QColor(255, 255, 255, 0))
                p.fillRect(rect, q)
                p.restore()
        if self.isDown():
            p.fillPath(shape, QColor(17, 18, 54, 24))
        if self.hasFocus() and self.focusPolicy() != Qt.NoFocus:
            p.setPen(QPen(QColor("#7b86dd"), 1.5, Qt.DotLine))
            p.drawRoundedRect(rect.adjusted(1, 1, -1, -1), rad, rad)
        f = QFont(self.font())
        f.setPixelSize(14)
        f.setWeight(QFont.DemiBold if primary or self.selected else QFont.Medium)
        p.setFont(f)
        p.setPen(QColor(text_color))
        tw = p.fontMetrics().horizontalAdvance(self.text())
        start = 15 if nav else max(7, (self.width() - tw - (28 if self.icon_name else 0)) / 2)
        if self.icon_name:
            ink(
                p,
                self.icon_name,
                start,
                self.height() / 2 - 9,
                text_color if not nav or self.selected else ("#aaa9ed" if dark else "#6668ac"),
            )
            start += 28
        p.drawText(
            QRectF(start, 0, self.width() - start - 3, self.height()),
            Qt.AlignVCenter | Qt.AlignLeft,
            self.text(),
        )

    def mouseMoveEvent(self, event):
        self.update()
        super().mouseMoveEvent(event)


class Material(QWidget):
    def paintEvent(self, event):
        p = QPainter(self)
        r = QRectF(self.rect())
        dark = self.window().dark
        stops = (
            [(0, "#202333"), (0.45, "#191b27"), (1, "#222131")]
            if dark
            else [(0, "#e6edf6"), (0.49, "#f7f6fa"), (1, "#ede9f4")]
        )
        p.fillRect(r, gradient(r, stops))
        if self.window().solid:
            return
        for x, y, c in [(0, 0.12, "#8a9ccf"), (0.12, 0.83, "#b2a4ce"), (1, 0.03, "#b3bedb")]:
            g = QRadialGradient(r.width() * x, r.height() * y, max(240, r.width() * 0.37))
            col = QColor(c)
            col.setAlpha(23 if dark else 65)
            g.setColorAt(0, col)
            col.setAlpha(0)
            g.setColorAt(1, col)
            p.fillRect(r, g)


class Rail(QWidget):
    def paintEvent(self, event):
        p = QPainter(self)
        r = QRectF(self.rect())
        d, s = self.window().dark, self.window().solid
        if not s and hasattr(self.window(), "stage"):
            stage = self.window().stage
            pix = stage.frosted
            p.save()
            p.setOpacity(0.18 if d else 0.28)
            p.setRenderHint(QPainter.SmoothPixmapTransform)
            p.drawPixmap(r, pix, QRectF(pix.width() * 0.55, 0, pix.width() * 0.30, pix.height()))
            p.restore()
        a = (
            ([("#292c3c"), ("#252637")] if d else ["#f0f3fa", "#e8e9f4"])
            if s
            else (["#7a393e55", "#7a2b2c40"] if d else ["#96ffffff", "#6fdce4f4"])
        )
        p.fillRect(r, gradient(r, [(0, a[0]), (1, a[1])]))
        if not s:
            strength = getattr(self.window(), "density", 45)
            overlay = QColor("#292c3c" if d else "#f0f3fa")
            overlay.setAlpha(int(strength * 1.65))
            p.fillRect(r, overlay)
        p.setPen(QColor(95, 97, 124, 45))
        p.drawLine(self.width() - 1, 0, self.width() - 1, self.height())


class Stage(QWidget):
    dropFile = Signal(str)

    def __init__(self):
        super().__init__()
        self.background = QPixmap(str(ASSETS / "spectrum.jpg"))
        self.poster = QPixmap()
        self.frosted = self.background.scaled(72, 28, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        self.setAcceptDrops(True)
        self.setMinimumHeight(211)
        self.setMaximumHeight(260)

    def set_poster(self, pix):
        self.poster = pix
        tex = self.background if pix.isNull() else pix
        self.frosted = tex.scaled(72, 28, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        self.update()
        for w in self.window().findChildren(Rail):
            w.update()

    def sample_glass(self, p, button):
        # Refract and diffuse the local portion of the preview under the capsule.
        pos = button.mapTo(self, button.rect().topLeft())
        kx = self.frosted.width() / self.width()
        ky = self.frosted.height() / self.height()
        source = QRectF(
            (pos.x() + 4) * kx,
            (pos.y() + 2) * ky,
            (button.width() - 8) * kx,
            (button.height() - 4) * ky,
        )
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.drawPixmap(QRectF(button.rect()), self.frosted, source)
        p.fillRect(button.rect(), QColor(6, 11, 29, 100))

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(r, 20, 20)
        p.setClipPath(path)
        pix = self.background if self.poster.isNull() else self.poster
        if not pix.isNull():
            k = max(r.width() / pix.width(), r.height() / pix.height())
            p.drawPixmap(
                r,
                pix,
                QRectF(
                    (pix.width() - r.width() / k) / 2,
                    (pix.height() - r.height() / k) / 2,
                    r.width() / k,
                    r.height() / k,
                ),
            )
        g = QLinearGradient(r.topLeft(), r.topRight())
        g.setColorAt(0, QColor(13, 17, 35, 201))
        g.setColorAt(0.6, QColor(17, 18, 38, 60))
        g.setColorAt(1, QColor(13, 15, 30, 10))
        p.fillRect(r, g)
        p.setPen(QPen(QColor(255, 255, 255, 80), 1))
        p.drawPath(path)

    def dragEnterEvent(self, e):
        if len(e.mimeData().urls()) == 1 and e.mimeData().urls()[0].isLocalFile():
            e.acceptProposedAction()

    def dropEvent(self, e):
        urls = e.mimeData().urls()
        if len(urls) == 1 and urls[0].isLocalFile():
            self.dropFile.emit(urls[0].toLocalFile())
            e.acceptProposedAction()


class Select(QComboBox):
    def paintEvent(self, e):
        super().paintEvent(e)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(
            QPen(
                QColor("#999db8" if self.window().dark else "#686d83"),
                1.5,
                Qt.SolidLine,
                Qt.RoundCap,
                Qt.RoundJoin,
            )
        )
        x, y = self.width() - 18, self.height() / 2
        c = QPainterPath(QPointF(x - 3, y - 1))
        c.lineTo(x, y + 2)
        c.lineTo(x + 3, y - 1)
        p.drawPath(c)
