"""Locally painted glass surfaces for the desktop workspace."""

import sys
from pathlib import Path

from PySide6.QtCore import Qt, QRectF, QPointF, QEasingCurve, QVariantAnimation, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap, QRadialGradient
from PySide6.QtWidgets import QWidget, QPushButton, QComboBox, QStyle, QLabel

ASSETS = Path(__file__).resolve().parent / "assets"


def system_effects():
    """Return the desktop transparency and animation preferences without changing them."""
    if sys.platform != "win32":
        return True, True
    import ctypes
    from ctypes import wintypes
    import winreg

    transparent = animated = True
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
            transparent = bool(winreg.QueryValueEx(key, "EnableTransparency")[0])
    except OSError:
        pass

    class HighContrast(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.UINT), ("dwFlags", wintypes.DWORD), ("lpszDefaultScheme", wintypes.LPWSTR)]

    # SPI_GETCLIENTAREAANIMATION and SPI_GETHIGHCONTRAST.
    spi = ctypes.windll.user32.SystemParametersInfoW
    enabled = wintypes.BOOL()
    if spi(0x1042, 0, ctypes.byref(enabled), 0):
        animated = bool(enabled.value)
    contrast = HighContrast()
    contrast.cbSize = ctypes.sizeof(contrast)
    if spi(0x42, contrast.cbSize, ctypes.byref(contrast), 0) and contrast.dwFlags & 1:
        transparent = animated = False
    return transparent, animated


def opaque(win):
    return getattr(win, "solid", False) or not getattr(win, "allow_transparency", True)


def gradient(rect, stops):
    g = QLinearGradient(rect.topLeft(), rect.bottomRight())
    for at, color in stops:
        g.setColorAt(at, QColor(color))
    return g


def rounded(rect, radius):
    path = QPainterPath()
    path.addRoundedRect(rect, radius, radius)
    return path


def rim(p, path, rect, strength=1.0):
    # Uneven reflections at opposing tangents give the edge volume without a white outline.
    g = QLinearGradient(rect.topLeft(), rect.bottomRight())
    for at, rgb, alpha in (
        (0, (255, 255, 255), 224), (.30, (238, 245, 255), 142),
        (.53, (118, 138, 184), 24), (.79, (212, 222, 249), 66), (1, (248, 251, 255), 160),
    ):
        g.setColorAt(at, QColor(*rgb, int(alpha * strength)))
    p.setBrush(Qt.NoBrush)
    p.setPen(QPen(QBrush(g), 1))
    p.drawPath(path)


def bloom(p, rect, cx, cy, radius, color):
    glow = QRadialGradient(rect.left() + cx * rect.width(), rect.top() + cy * rect.height(), radius)
    glow.setColorAt(0, color)
    glow.setColorAt(1, QColor(color.red(), color.green(), color.blue(), 0))
    p.fillRect(rect, glow)


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
    elif name == "gpu":
        p.drawRoundedRect(QRectF(4, 4, 11, 11), 2, 2)
        p.drawRoundedRect(QRectF(7.2, 7.2, 4.6, 4.6), 1, 1)
        for n in (6, 10, 14):
            line((n, 1.5), (n, 3.5))
            line((n, 15.5), (n, 17.5))
            line((1.5, n), (3.5, n))
            line((15.5, n), (17.5, n))
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
        self._hover = 0.0
        self._light = QPointF()
        self._keyboard_focus = False
        self._transition = QVariantAnimation(self)
        self._transition.setEasingCurve(QEasingCurve.OutCubic)
        self._transition.valueChanged.connect(self._set_hover)

    def _set_hover(self, value):
        self._hover = value
        self.update()

    def _animate_hover(self, target):
        self._transition.stop()
        win = self.window()
        if (opaque(win) or not getattr(win, "allow_motion", True)
                or not self.style().styleHint(QStyle.SH_Widget_Animate, None, self)):
            self._set_hover(target)
            return
        self._transition.setStartValue(self._hover)
        self._transition.setEndValue(target)
        self._transition.setDuration(125 if target else 160)
        self._transition.start()

    def enterEvent(self, event):
        super().enterEvent(event)
        self._light = event.position()
        if self.isEnabled():
            self._animate_hover(1.0)

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self._animate_hover(0.0)

    def mouseMoveEvent(self, event):
        if self.isEnabled() and not opaque(self.window()) and getattr(self.window(), "allow_motion", True):
            self._light = event.position()
            self.update()
        super().mouseMoveEvent(event)

    def focusInEvent(self, event):
        self._keyboard_focus = event.reason() in (Qt.TabFocusReason, Qt.BacktabFocusReason, Qt.ShortcutFocusReason)
        super().focusInEvent(event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Space, Qt.Key_Return, Qt.Key_Enter):
            self._keyboard_focus = True
            self.update()
        super().keyPressEvent(event)

    def mousePressEvent(self, event):
        self._keyboard_focus = False
        super().mousePressEvent(event)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        win = self.window()
        dark, solid = getattr(win, "dark", False), opaque(win)
        enabled, down = self.isEnabled(), self.isDown()
        hover = self._hover if enabled else 0
        nav, hero, primary = self.role == "nav", self.role == "hero", self.role == "primary"
        rect = QRectF(self.rect()).adjusted(2.5, 2.5 + int(down), -2.5, -3.5)
        radius = 12 if nav else rect.height() / 2
        path = rounded(rect, radius)
        text = "#f0f2ff" if dark else "#262a40"
        if primary or hero:
            text = "#ffffff"
        if self.selected:
            text = "#e5e4ff" if dark else "#39368c"
        if not enabled:
            text = "#aaadbc" if dark else "#666b80"
        density = max(0, min(100, getattr(win, "density", 45)))
        if not nav or self.selected or hover:
            if solid:
                if primary and enabled:
                    fill = "#5457aa"
                elif hero:
                    fill = "#30394b"
                elif nav and self.selected:
                    fill = "#3d4054" if dark else "#e6e6f8"
                else:
                    fill = "#333646" if dark else "#e9ebf2"
                p.fillPath(path, QColor(fill))
                p.setPen(QPen(QColor("#666b82" if dark else "#a4a9bc"), 1))
                p.drawPath(path)
            elif nav:
                # Rows sit inside the glass sidebar. Selection is a thin ink wash.
                if self.selected:
                    p.fillPath(path, gradient(rect, [(0, "#704e538a" if dark else "#c5ffffff"),
                                                     (1, "#5c6767a8" if dark else "#92d5dafa")]))
                    rim(p, path, rect, .46)
                elif hover:
                    p.fillPath(path, QColor(198, 199, 232, round(30 * hover)))
            else:
                self._paint_lens(p, rect, path, radius, dark, density, enabled, hero, primary)
            if hover and enabled:
                p.save()
                p.setClipPath(path)
                if solid or not getattr(win, "allow_motion", True):
                    p.fillPath(path, QColor(120, 128, 168, round(28 * hover)))
                else:
                    light = QRadialGradient(self._light, max(58, rect.width() * .70))
                    light.setColorAt(0, QColor(255, 255, 255, round((24 if hero else 48) * hover)))
                    light.setColorAt(1, QColor(255, 255, 255, 0))
                    p.fillRect(rect, light)
                p.restore()
        if down:
            p.fillPath(path, QColor(14, 17, 44, 37))
        if self.hasFocus() and self._keyboard_focus:
            outer = rect.adjusted(-1.5, -1.5, 1.5, 1.5)
            p.setPen(QPen(QColor("#aebfff" if dark or hero else "#4e5eb0"), 2))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(outer, radius + 1.5, radius + 1.5)

        font = QFont(self.font())
        font.setPixelSize(14)
        font.setWeight(QFont.DemiBold if primary or self.selected or hero else QFont.Medium)
        p.setFont(font)
        p.setPen(QColor(text))
        icon_space = 24 if self.icon_name else 0
        pad = 14 if nav else 9
        available = self.width() - 2 * pad - icon_space
        label = p.fontMetrics().elidedText(self.text(), Qt.ElideRight, max(1, available))
        width = p.fontMetrics().horizontalAdvance(label)
        start = pad if nav and label else (self.width() - width - icon_space) / 2
        if not label and self.icon_name:
            start = (self.width() - 19) / 2
        icon_at_end = primary and self.icon_name == "go"
        p.translate(0, int(down))
        if self.icon_name:
            ink(p, self.icon_name, start + width + 5 if icon_at_end else start,
                self.height() / 2 - 9.5,
                text if not nav or self.selected else ("#abaed1" if dark else "#656987"))
            if not icon_at_end:
                start += icon_space
        p.drawText(QRectF(start, 0, width + 2, self.height()), Qt.AlignLeft | Qt.AlignVCenter, label)

    def _paint_lens(self, p, rect, path, radius, dark, density, enabled, hero, primary):
        if not self.isDown():
            p.fillPath(path.translated(0, 1.5), QColor(12, 16, 46, 29 if enabled else 13))
        brightness = 1.0
        if hero and isinstance(self.parentWidget(), Stage):
            p.save()
            p.setClipPath(path)
            brightness = self.parentWidget().sample_glass(p, self)
            p.restore()
        if not enabled:
            stops = [(0, "#d43b3e50"), (1, "#c5373a49")] if dark else [(0, "#def0f1f7"), (1, "#dddfe2ec")]
        elif hero:
            # Neutral smoke keeps the label legible over snow, subtitles and white video frames.
            p.fillPath(path, QColor(16, 23, 49, int(114 + brightness * 91) + density // 7))
            stops = [(0, "#3cffffff"), (.46, "#1ac2d9ff"), (1, "#307b94d0")]
        elif primary:
            stops = [(0, "#e15167b8"), (.5, "#ee5a56b2"), (1, "#f049499e")]
        else:
            stops = ([(0, "#d046495c"), (.5, "#b2393d50"), (1, "#c42a2d40")] if dark else
                     [(0, "#eaffffff"), (.56, "#c8f1f3fa"), (1, "#bcdde3f1")])
        p.fillPath(path, gradient(rect, stops))
        if not hero and not primary and enabled:
            p.fillPath(path, QColor(45, 48, 65, density // 2) if dark else QColor(250, 251, 255, density))
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(16, 20, 51, 60 if dark else 27), 1))
        p.drawPath(path)
        edge = rect.adjusted(.65, .65, -.65, -.65)
        rim(p, rounded(edge, radius - .65), edge, .82 if enabled else .45)


class Material(QWidget):
    def paintEvent(self, event):
        p = QPainter(self)
        r = QRectF(self.rect())
        win = self.window()
        dark = getattr(win, "dark", False)
        if opaque(win):
            p.fillRect(r, QColor("#20222c" if dark else "#f3f4f8"))
            return
        stops = ([(0, "#182234"), (.52, "#171b29"), (1, "#242234")] if dark else
                 [(0, "#e3eafa"), (.5, "#f3f4fa"), (1, "#eeebf5")])
        p.fillRect(r, gradient(r, stops))
        bloom(p, r, .03, .06, r.width() * .46, QColor(113, 161, 233, 23 if dark else 46))
        bloom(p, r, .06, .88, r.height() * .58, QColor(161, 132, 215, 21 if dark else 34))


class Rail(QWidget):
    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        win = self.window()
        dark, solid = getattr(win, "dark", False), opaque(win)
        r = QRectF(self.rect()).adjusted(3.5, 2.5, -3.5, -4.5)
        path = rounded(r, 23)
        if solid:
            p.fillPath(path, QColor("#282b37" if dark else "#f8f9fd"))
            p.setPen(QPen(QColor("#505365" if dark else "#c9cedc"), 1))
            p.drawPath(path)
            return
        # Sample a diffuse strip of the current artwork. A vertical falloff keeps
        # the navigation calm when a saturated video replaces the preview.
        p.fillPath(path.translated(0, 2.5), QColor(15, 24, 54, 32 if dark else 17))
        p.save()
        p.setClipPath(path)
        stage = getattr(win, "stage", None)
        if stage is not None and not stage.frosted.isNull():
            tex = stage.frosted
            p.setOpacity(.46 if dark else .50)
            p.drawPixmap(r, tex, QRectF(tex.width() * .39, 0, tex.width() * .42, tex.height()))
            p.setOpacity(1)
        fill = ([(0, "#90414a67"), (.48, "#9c2c3044"), (1, "#b332334b")] if dark else
                [(0, "#aaf6faff"), (.55, "#b8f6f6fc"), (1, "#a6e1e6fb")])
        p.fillRect(r, gradient(r, fill))
        density = max(0, min(100, getattr(win, "density", 45)))
        p.fillPath(path, QColor(43, 46, 63, int(density * 1.9)) if dark else
                   QColor(246, 248, 255, int(density * 1.9)))
        bloom(p, r, 0, .02, r.width() * 1.25, QColor(212, 234, 255, 20 if dark else 52))
        p.restore()
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(25, 29, 61, 72 if dark else 32), 1))
        p.drawPath(path)
        edge = r.adjusted(.65, .65, -.65, -.65)
        rim(p, rounded(edge, 22.4), edge, .67 if dark else .95)


class Stage(QWidget):
    dropFile = Signal(str)

    def __init__(self):
        super().__init__()
        self.background = QPixmap(str(ASSETS / "optical-glass.jpg"))
        self.poster = QPixmap()
        self._dragging = False
        self._update_frost()
        self.setAcceptDrops(True)
        self.setMinimumHeight(211)
        self.setMaximumHeight(260)

    def _update_frost(self):
        self.frosted = self.texture().scaled(64, 40, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self._frost_image = self.frosted.toImage()

    def texture(self):
        return self.background if self.poster.isNull() else self.poster

    def set_poster(self, pix):
        self.poster = pix
        self._update_frost()
        self.update()
        for w in self.window().findChildren(Rail):
            w.update()
        for w in self.findChildren(GlassButton):
            w.update()

    def _crop(self):
        # Used by both preview and lens. Map the same center crop, even for a portrait poster.
        pix = self.texture()
        if pix.isNull() or not self.width() or not self.height():
            return QRectF()
        scale = max(self.width() / pix.width(), self.height() / pix.height())
        w, h = self.width() / scale, self.height() / scale
        return QRectF((pix.width() - w) / 2, (pix.height() - h) / 2, w, h)

    def sample_glass(self, p, button):
        crop = self._crop()
        if crop.isEmpty() or self.frosted.isNull():
            return 1.0
        tex = self.texture()
        sx, sy = self.frosted.width() / tex.width(), self.frosted.height() / tex.height()
        pos = button.mapTo(self, button.rect().topLeft())
        u, v = crop.width() / self.width(), crop.height() / self.height()
        sample = QRectF((crop.left() + pos.x() * u) * sx, (crop.top() + pos.y() * v) * sy,
                        button.width() * u * sx, button.height() * v * sy)
        target = QRectF(button.rect())
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.drawPixmap(target, self.frosted, sample)
        # The perimeter magnifies a slightly more distant sample than the center.
        # Qt's existing button clip makes this a narrow curved refraction band.
        p.save()
        band = rounded(target, target.height() / 2).subtracted(
            rounded(target.adjusted(5, 5, -5, -5), target.height() / 2 - 5))
        p.setClipPath(band, Qt.IntersectClip)
        dx, dy = sample.width() * .055, sample.height() * .12
        p.drawPixmap(target, self.frosted, sample.adjusted(dx, dy, -dx, -dy))
        p.restore()
        # Use the brightest local sample to choose an appropriate neutral backing.
        brightness = 0
        for x in (.15, .5, .85):
            for y in (.28, .72):
                color = self._frost_image.pixelColor(
                    min(self.frosted.width() - 1, max(0, int(sample.left() + x * sample.width()))),
                    min(self.frosted.height() - 1, max(0, int(sample.top() + y * sample.height()))),
                )
                brightness = max(brightness, .213 * color.redF() + .715 * color.greenF() + .072 * color.blueF())
        return brightness

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        r = QRectF(self.rect()).adjusted(.5, .5, -.5, -.5)
        path = rounded(r, 22)
        p.setClipPath(path)
        p.fillPath(path, QColor("#151b32"))
        if not self.texture().isNull():
            p.drawPixmap(QRectF(self.rect()), self.texture(), self._crop())
        # A white frame needs enough neutral backing to keep even the last subtitle glyph legible.
        shade = QLinearGradient(r.topLeft(), r.topRight())
        if self.poster.isNull():
            stops = [(0, 110), (.34, 80), (.68, 15), (1, 4)]
        else:
            text_edge = 0
            for kind in ("heroTitle", "heroDetail"):
                label = self.findChild(QLabel, kind)
                if label is not None:
                    visible = min(label.width(), label.fontMetrics().horizontalAdvance(label.text()))
                    text_edge = max(text_edge, label.x() + visible)
            end = min(.99, (text_edge + 8) / max(1, self.width()))
            falloff = min(.36, 1 - end)
            stops = [(0, 220), (end * .60, 189), (end, 168),
                     (end + falloff * .24, 146), (end + falloff * .70, 38), (end + falloff, 0)]
        for at, alpha in stops:
            shade.setColorAt(at, QColor(11, 16, 36, alpha))
        p.fillRect(r, shade)
        if self._dragging:
            p.fillPath(path, QColor(95, 120, 214, 40))
            p.setPen(QPen(QColor("#d9edff"), 3))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(r.adjusted(2, 2, -2, -2), 20, 20)
        else:
            rim(p, path, r, .68)

    def dragEnterEvent(self, e):
        urls = e.mimeData().urls()
        if len(urls) == 1 and urls[0].isLocalFile():
            self._dragging = True
            self.update()
            e.acceptProposedAction()

    def dragLeaveEvent(self, e):
        self._dragging = False
        self.update()
        super().dragLeaveEvent(e)

    def dropEvent(self, e):
        self._dragging = False
        self.update()
        urls = e.mimeData().urls()
        if len(urls) == 1 and urls[0].isLocalFile():
            e.acceptProposedAction()
            self.dropFile.emit(urls[0].toLocalFile())


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
