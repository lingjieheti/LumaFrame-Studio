"""LumaFrame Studio · local Windows desktop application."""

import os
import sys
from pathlib import Path

if __name__ == "__main__" and sys.argv[1:2] == ["--rtx-worker"]:
    from ai import rtx_worker

    for channel, fd in (("stdout", 1), ("stderr", 2)):
        if getattr(sys, channel) is None:
            try:
                setattr(
                    sys,
                    channel,
                    os.fdopen(os.dup(fd), "w", encoding="utf-8", errors="replace", buffering=1),
                )
            except OSError:
                setattr(sys, channel, open(os.devnull, "w"))
    raise SystemExit(rtx_worker(sys.argv[2:]))

import json
import subprocess
import threading
import time
from datetime import datetime
from fractions import Fraction
from PySide6.QtCore import Qt, QEvent, QRectF, QSettings, Signal, QUrl, QTimer, QObject, QLocale, QLibraryInfo, QTranslator
from PySide6.QtGui import (
    QColor,
    QDesktopServices,
    QFont,
    QIcon,
    QPixmap,
    QPainter,
    QPen,
    QPalette,
    QKeySequence,
    QShortcut,
)
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QFrame,
    QLabel,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QComboBox,
    QCheckBox,
    QAbstractButton,
    QLineEdit,
    QProgressBar,
    QFileDialog,
    QMessageBox,
    QStackedWidget,
    QScrollArea,
    QSizePolicy,
    QSlider,
)
import glass
from export_progress import ExportProgress, ProgressFeed, ProgressFile, clock_text, eta_text, rate_text, work_text
from glass import ASSETS, GlassButton, Material, Rail, Stage, Select, ink
from material_sample import MaterialSample
from localization import LANGUAGES, Translator, resolve_language

from encode_policy import nvidia_gpu_name, output_targets, recommended_mode

VERSION = "1.8.1"
REPO = "https://github.com/lingjieheti/LumaFrame-Studio"
SPONSOR = "https://github.com/lingjieheti/suzerain-save-studio/blob/main/SPONSOR.md"
TARGETS = {
    "preserve": "保留杜比视界",
    "keep7": "原样保留双层 · Profile 7",
    "newdv": "生成杜比视界",
    "hdr10": "生成 HDR10",
}
MODES = {
    "copy": "无损封装 · 原始单层码流",
    "rtx": "NVIDIA RTX AI · 高码率 Ultra",
    "rtx_compressed": "NVIDIA RTX · 压缩片源修复",
    "neural": "FSRCNNX · 神经网络",
    "reference": "Spline36 · 保真缩放",
    "quality": "NVIDIA NIS · 快速缩放",
}
ROOT = Path(__file__).resolve().parent
HIDE = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def supports_fast_export(mode, target):
    return mode in {"rtx", "rtx_compressed"} and target in {"preserve", "hdr10", "newdv"}


def label(text, kind=""):
    w = QLabel(text)
    w.setObjectName(kind)
    w.setTextFormat(Qt.PlainText)
    return w


class ElidedLabel(QLabel):
    """Keep the full text available to assistive tools while fitting the current width."""

    def __init__(self, text, kind, elide=Qt.ElideMiddle):
        super().__init__(text)
        self.elide = elide
        self.setObjectName(kind)
        self.setTextFormat(Qt.PlainText)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)

    def paintEvent(self, event):
        area = self.contentsRect()
        painter = QPainter(self)
        painter.setPen(self.palette().color(QPalette.WindowText))
        caption = self.fontMetrics().elidedText(self.text(), self.elide, area.width())
        painter.drawText(area, self.alignment() | Qt.TextSingleLine, caption)


class Glyph(QWidget):
    def __init__(self, name, size=20):
        super().__init__()
        self.name = name
        self.setFixedSize(size, size)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.scale(self.width() / 20, self.height() / 20)
        color = self.palette().color(QPalette.PlaceholderText)
        ink(painter, self.name, 0, 0, color)


class SwitchBox(QCheckBox):
    def __init__(self, text):
        super().__init__(text)
        self.setMinimumHeight(34)
        self.setCursor(Qt.PointingHandCursor)

    def hitButton(self, pos):
        return self.rect().contains(pos)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        dark = self.window().dark
        track = QRectF(self.width() - 48, (self.height() - 23) / 2, 42, 23)
        p.setPen(self.palette().color(QPalette.WindowText))
        p.drawText(self.rect().adjusted(0, 0, -55, 0), Qt.AlignVCenter | Qt.AlignLeft, self.text())
        off = "#60677c" if dark else "#8c94a6"
        on = "#7977ed" if dark else "#5658c7"
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(on if self.isChecked() else off))
        p.drawRoundedRect(track, 11.5, 11.5)
        pos = track.right() - 20.5 if self.isChecked() else track.left() + 2.5
        p.setBrush(QColor("#ffffff"))
        p.drawEllipse(QRectF(pos, track.top() + 2.5, 18, 18))
        if self.hasFocus():
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(QColor("#a9adff" if dark else "#5459c1"), 2))
            p.drawRoundedRect(track.adjusted(-3, -3, 3, 3), 14, 14)


def layout(cls, parent, margin=0, gap=0):
    box = cls(parent)
    box.setContentsMargins(margin, margin, margin, margin)
    box.setSpacing(gap)
    return box


def external(url):
    QDesktopServices.openUrl(QUrl(url))


def shell_path(path):
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))


def available_destination(path):
    path = Path(path)
    candidate = path
    index = 2
    while os.path.lexists(candidate):
        candidate = path.with_name(f"{path.stem}-{index}{path.suffix}")
        index += 1
    return candidate


def poster_frame(path, info, cancel):
    if info.get("dv_profile") in (5, 7) or cancel.is_set():
        return b""
    vf = "scale=640:-2"
    if info["source_type"] in ("dv", "hdr10"):
        vf = "zscale=t=linear:npl=100,format=gbrpf32le,tonemap=mobius,zscale=p=bt709:t=bt709:m=bt709:r=limited,format=yuv420p,scale=640:-2"
    timestamp = min(max(info["duration"] * 0.10, 0), 25)
    args = [
        str(ROOT / "bin" / "ffmpeg.exe"), "-nostdin", "-v", "error", "-ss", str(timestamp),
        "-i", path, "-map", "0:V:0", "-frames:v", "1", "-vf", vf, "-c:v", "mjpeg",
        "-f", "image2pipe", "-",
    ]
    with subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL, creationflags=HIDE) as process:
        deadline = time.monotonic() + 12
        while not cancel.is_set() and time.monotonic() < deadline:
            try:
                data, _ = process.communicate(timeout=0.15)
                return data if process.returncode == 0 else b""
            except subprocess.TimeoutExpired:
                continue
        process.kill()
        process.communicate()
        return b""


class Events(QObject):
    inspected = Signal(int, object, str)
    inspection_done = Signal(object)
    poster = Signal(int, bytes)
    state = Signal(float, str, object, float)
    complete = Signal(object, str)
    gpu = Signal(str)


class Studio(QMainWindow):
    def __init__(self):
        super().__init__()
        self.settings = QSettings("LumaFrame", "Studio")
        default_language = "zh-CN" if self.settings.allKeys() else "auto"
        choice = self.settings.value("uiLanguage", default_language)
        self.language_choice = choice if choice in ("auto", *LANGUAGES) else "auto"
        if self.settings.value("uiLanguage") != self.language_choice:
            self.settings.setValue("uiLanguage", self.language_choice)
        self.words = Translator(resolve_language(self.language_choice, QLocale.system().uiLanguages()))
        self.captions = {}
        self.static_properties = []
        self.dark = self.settings.value("dark", False, type=bool)
        self.solid = self.settings.value("reduceTransparency", False, type=bool)
        self.allow_transparency, self.allow_motion = glass.system_effects()
        self.density = max(0, min(100, self.settings.value("glassDensity", 45, type=int)))
        self.setWindowTitle("LumaFrame Studio")
        self.setWindowIcon(QIcon(str(ASSETS / "icon.ico")))
        available = self.screen().availableGeometry()
        # Leave room for the native Windows title bar and resize frame on scaled displays.
        width, height = available.width() - 16, available.height() - 48
        self.setMinimumSize(min(720, width), min(480, height))
        self.resize(min(1120, width), min(680, height))
        self.info = None
        self.device_name = None
        self.busy = False
        self.progress_tracker = ExportProgress()
        self.started_at = None
        self.custom_destination = None
        self.failure_detail = ""
        self.seq = 0
        self.inspections = set()
        self.close_after = False
        self.cancel_event = threading.Event()
        self.events = Events(self)
        self.events.inspected.connect(self.got_info)
        self.events.inspection_done.connect(self.inspection_done)
        self.events.poster.connect(self.got_poster)
        self.events.state.connect(self.update_progress)
        self.events.complete.connect(self.finished)
        self.clock = QTimer(self)
        self.clock.setInterval(1000)
        self.clock.timeout.connect(self.show_progress)
        try:
            saved = json.loads(str(self.settings.value("history", "[]")))
        except (ValueError, TypeError):
            saved = []
        self.history = [
            item for item in (saved if isinstance(saved, list) else [])
            if isinstance(item, dict)
            and all(isinstance(item.get(key), str) for key in ("path", "time", "target"))
            and isinstance(item.get("n"), int) and item["path"]
        ][:20]
        self.root = Material()
        self.setCentralWidget(self.root)
        outer = layout(QHBoxLayout, self.root, 14, 0)
        rail = Rail()
        self.rail = rail
        rail.setFixedWidth(192)
        outer.addWidget(rail)
        rb = layout(QVBoxLayout, rail, 12, 5)
        rb.setContentsMargins(13, 24, 13, 20)
        brand = QWidget()
        bb = layout(QHBoxLayout, brand, 0, 8)
        bb.setContentsMargins(6, 0, 0, 0)
        logo = QLabel()
        logo.setFixedSize(36, 36)
        logo.setScaledContents(True)
        logo.setPixmap(QPixmap(str(ASSETS / "icon.png")))
        bb.addWidget(logo)
        brandtext = QWidget()
        self.brandtext = brandtext
        bt = layout(QVBoxLayout, brandtext, 0, 0)
        bt.addWidget(label("LumaFrame", "brand"))
        bt.addWidget(label("STUDIO", "brandSmall"))
        bb.addWidget(brandtext)
        bb.addStretch()
        rb.addWidget(brand)
        rb.addSpacing(29)
        self.nav = []
        self.rail_labels = []
        for i, (title, glyph) in enumerate(
            [("影片工作台", "studio"), ("导出记录", "history"), ("外观与指南", "design")]
        ):
            b = GlassButton(title, glyph, "nav")
            b.setCheckable(True)
            b.clicked.connect(lambda _, j=i: self.show_page(j))
            self.nav.append(b)
            self.rail_labels.append((b, title))
            rb.addWidget(b)
        rb.addStretch()
        divisor = QFrame()
        divisor.setObjectName("rule")
        divisor.setFixedHeight(1)
        rb.addWidget(divisor)
        rb.addSpacing(4)
        for txt, glyph, url in [("赞助作者", "heart", SPONSOR), ("GitHub", "link", REPO)]:
            b = GlassButton(txt, glyph, "nav")
            b.clicked.connect(lambda _, u=url: external(u))
            self.rail_labels.append((b, txt))
            rb.addWidget(b)
        for button, caption in self.rail_labels:
            button.setToolTip(caption)
            button.setAccessibleName(caption)
        self.stack = QStackedWidget()
        outer.addWidget(self.stack, 1)
        self.workspace = self.page("影片工作台")
        self.workspace_viewport = self.stack.widget(0).findChild(QScrollArea).viewport()
        box = self.workspace
        self.stage = Stage()
        self.stage.dropFile.connect(self.load)
        box.addWidget(self.stage, 1)
        sl = layout(QVBoxLayout, self.stage)
        sl.addStretch()
        self.film = ElidedLabel("导入影片", "heroTitle")
        sl.addWidget(self.film)
        self.film_detail = label("拖入本地视频，开始 4K 超分与杜比视界转换。", "heroDetail")
        self.film_detail.setWordWrap(True)
        self.stage.installEventFilter(self)
        sl.addWidget(self.film_detail)
        sl.addSpacing(7)
        self.pick = GlassButton("选择影片", "add", "hero")
        self.pick.setFixedWidth(126)
        self.pick.setToolTip("选择本地影片 (Ctrl+O)")
        self.pick.clicked.connect(self.choose)
        sl.addWidget(self.pick)
        sl.addStretch()
        self.size_preview(False)
        self.workspace_viewport.installEventFilter(self)
        card = QFrame()
        card.setObjectName("card")
        box.addWidget(card)
        cg = layout(QVBoxLayout, card, 20, 13)
        section = layout(QHBoxLayout, None, 0, 6)
        section.addWidget(label("导出设置", "section"))
        section.addStretch()
        section.addWidget(Glyph("gpu", 15))
        self.gpu_label = ElidedLabel("检测图形设备…", "device", Qt.ElideRight)
        self.gpu_label.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        self.gpu_label.setMinimumWidth(1)
        section.addWidget(self.gpu_label)
        cg.addLayout(section)
        self.events.gpu.connect(self.gpu_name)
        self.targets = Select()
        self.targets.setPlaceholderText("根据片源匹配")
        self.targets.setAccessibleName("输出格式")
        self.targets.setMinimumWidth(0)
        self.targets.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.setting_fields = layout(QGridLayout, None, 0, 14)
        self.format_field = QWidget()
        field = layout(QVBoxLayout, self.format_field, 0, 6)
        cap = label("输出格式", "field")
        cap.setBuddy(self.targets)
        field.addWidget(cap)
        field.addWidget(self.targets)
        self.plan_field = QWidget()
        field = layout(QVBoxLayout, self.plan_field, 0, 6)
        field.addWidget(label("自动处理", "field"))
        self.plan = label("", "guideTitle")
        self.plan.setWordWrap(True)
        self.plan.setAccessibleName("自动处理")
        field.addWidget(self.plan, 1)
        self.field_columns = None
        self.arrange_fields()
        cg.addLayout(self.setting_fields)
        out = layout(QVBoxLayout, None, 0, 6)
        pb = layout(QHBoxLayout, None, 0, 8)
        self.path = QLineEdit()
        self.path.setAccessibleName("保存位置")
        cap = label("保存位置", "field")
        cap.setBuddy(self.path)
        out.addWidget(cap)
        self.path.setReadOnly(True)
        self.path.setPlaceholderText("选择影片后自动生成")
        pb.addWidget(self.path, 1)
        self.browse = GlassButton("更改", "folder")
        self.browse.setFixedSize(84, 36)
        self.browse.clicked.connect(self.set_destination)
        pb.addWidget(self.browse)
        out.addLayout(pb)
        cg.addLayout(out)
        self.hint = label("", "muted")
        self.hint.setWordWrap(True)
        cg.addWidget(self.hint)
        self.targets.currentIndexChanged.connect(self.target_change)
        box.addStretch()
        self.export_dock = glass.ExportDock()
        self.export_dock.setObjectName("exportDock")
        dock_layout = layout(QVBoxLayout, self.export_dock, 0, 9)
        dock_layout.setContentsMargins(18, 11, 18, 11)
        self.page_host.addWidget(self.export_dock)
        self.status = ElidedLabel("选择影片后即可导出", "status", Qt.ElideRight)
        self.status.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setAccessibleName("导出总进度")
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(7)
        self.bar.hide()
        self.progress_metrics = QWidget()
        metrics = QGridLayout(self.progress_metrics)
        metrics.setContentsMargins(0, 0, 0, 0)
        metrics.setVerticalSpacing(0)
        metrics.setHorizontalSpacing(10)
        self.percent_value = label("0%", "dockPercent")
        self.total_eta = label("估算中…", "dockEta")
        self.stage_eta = label("估算中…", "progressMetric")
        self.speed_value = label("—", "progressMetric")
        for column, (caption, value) in enumerate((
            ("导出总进度", self.percent_value), ("预计总剩余", self.total_eta),
            ("本阶段剩余", self.stage_eta), ("处理速度", self.speed_value)
        )):
            metrics.addWidget(label(caption, "dockCaption"), 0, column)
            metrics.addWidget(value, 1, column, Qt.AlignLeft | Qt.AlignVCenter)
            metrics.setColumnStretch(column, 2 if column == 1 else 1)
            value.setAccessibleName(caption)
        self.total_eta.setToolTip("根据当前阶段的实测速率和后续步骤估算，切换阶段后重新计算。")
        self.progress_metrics.hide()
        dock_layout.addWidget(self.progress_metrics)
        dock_layout.addWidget(self.bar)
        foot = QWidget()
        fh = layout(QHBoxLayout, foot, 0, 8)
        fh.addWidget(self.status, 1)
        self.openbtn = GlassButton("打开目录", "folder")
        self.openbtn.setFixedWidth(115)
        self.openbtn.clicked.connect(self.open_output)
        self.openbtn.hide()
        fh.addWidget(self.openbtn)
        self.detailbtn = GlassButton("错误详情")
        self.detailbtn.setFixedWidth(100)
        self.detailbtn.clicked.connect(self.show_failure)
        self.detailbtn.hide()
        fh.addWidget(self.detailbtn)
        self.stopbtn = GlassButton("取消")
        self.stopbtn.setFixedWidth(82)
        self.stopbtn.clicked.connect(self.stop)
        self.stopbtn.hide()
        fh.addWidget(self.stopbtn)
        self.previewbtn = GlassButton("试转片段")
        self.previewbtn.setFixedWidth(100)
        self.previewbtn.setToolTip("从影片中段的关键帧附近截取约 5 秒，导出一份仅含画面的 4K 试片。")
        self.previewbtn.clicked.connect(lambda: self.start(sample=True))
        fh.addWidget(self.previewbtn)
        self.renderbtn = GlassButton("导出 4K 文件", "go", "primary")
        self.renderbtn.setFixedWidth(163)
        self.renderbtn.clicked.connect(lambda: self.start())
        fh.addWidget(self.renderbtn)
        dock_layout.addWidget(foot)
        h = self.page("导出记录")
        self.history_host = QWidget()
        self.history_list = layout(QVBoxLayout, self.history_host, 0, 9)
        h.addWidget(self.history_host)
        h.addStretch()
        pref = self.page("外观与指南")
        language_row = layout(QHBoxLayout, None, 0, 12)
        language_label = label("界面语言", "field")
        self.languages = Select()
        language_label.setBuddy(self.languages)
        self.languages.setAccessibleName("界面语言")
        self.languages.addItem("跟随系统", "auto")
        for code, autonym in LANGUAGES.items():
            self.languages.addItem(autonym, code)
        self.languages.setCurrentIndex(self.languages.findData(self.language_choice))
        language_row.addWidget(language_label)
        language_row.addWidget(self.languages, 1)
        pref.addLayout(language_row)
        surface = QFrame()
        surface.setObjectName("card")
        swatch_row = layout(QHBoxLayout, surface, 21, 24)
        plo = layout(QVBoxLayout, None, 0, 9)
        swatch_row.addLayout(plo, 1)
        self.material_sample = MaterialSample()
        self.material_sample.setMinimumWidth(135)
        self.material_sample.setMaximumWidth(210)
        self.material_sample.setMinimumHeight(82)
        self.material_sample.setMaximumHeight(125)
        self.material_sample.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        swatch_row.addWidget(self.material_sample, 1)
        plo.addWidget(label("外观", "section"))
        self.dark_box = SwitchBox("深色外观")
        self.dark_box.setChecked(self.dark)
        self.dark_box.toggled.connect(self.appearance)
        plo.addWidget(self.dark_box)
        self.solid_box = SwitchBox("减少透明度")
        self.solid_box.setChecked(self.solid)
        self.solid_box.toggled.connect(self.appearance)
        plo.addWidget(self.solid_box)
        pref.addWidget(surface)
        density_row = QWidget()
        dr = layout(QHBoxLayout, density_row, 0, 12)
        dr.addWidget(label("玻璃浓度", "field"))
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setAccessibleName("玻璃浓度")
        self.slider.setMinimumHeight(27)
        self.slider.setRange(0, 100)
        self.slider.setValue(self.density)
        self.slider.setEnabled(self.allow_transparency and not self.solid)
        self.slider.valueChanged.connect(self.density_change)
        dr.addWidget(self.slider, 1)
        self.density_value = label(f"{self.density}%", "device")
        self.density_value.setMinimumWidth(38)
        self.density_value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        dr.addWidget(self.density_value)
        plo.addWidget(density_row)
        guide = QFrame()
        guide.setObjectName("card")
        gl = layout(QVBoxLayout, guide, 21, 8)
        gl.addWidget(label("转换指南", "section"))
        for title, body in [
            (
                "保留原片",
                "单层片源输出 Profile 8.1。原生 4K 的双层 Profile 7 可原样封装，保留基础层、增强层和动态元数据。",
            ),
            (
                "双层杜比转单层",
                "电视需要 Profile 8.1 时可选择合成输出。FEL 的增强层参与画面重建，合成后编码为 10 位；MEL 走基础层和元数据转换流程。",
            ),
            (
                "普通视频转 HDR",
                "BT.709 SDR 可以生成 HDR10，或根据新 HDR 画面的镜头亮度估算 Profile 8.1 动态元数据。这类结果属于自动制作，创作意图需要在 HDR 设备上判断。",
            ),
            (
                "观看与兼容性",
                "成片为 HEVC Main 10 / MKV，保持原比例、帧时间和受支持的轨道。电视或播放器需支持相应的杜比视界 Profile 与 MKV。工作台缩略图仅用于定位影片。",
            ),
        ]:
            gl.addSpacing(3)
            gl.addWidget(label(title, "guideTitle"))
            l = label(body, "muted")
            l.setWordWrap(True)
            gl.addWidget(l)
        pref.addWidget(guide)
        pref.addWidget(label("LumaFrame Studio   " + VERSION, "muted"))
        pref.addStretch()
        self.capture_interface_text()
        self.languages.currentIndexChanged.connect(self.change_language)
        self.show_page(0)
        self.set_controls(False)
        self.refill_targets()
        self.translate_interface()
        QShortcut(QKeySequence.Open, self, activated=self.choose)

        def probe_gpu():
            name = nvidia_gpu_name()
            # The desktop window can close before the system GPU query returns.
            try:
                self.events.gpu.emit(name)
            except RuntimeError:
                return

        threading.Thread(target=probe_gpu, daemon=True).start()

    def capture_interface_text(self):
        # Capture application-owned labels before loading a film or building history.
        for widget in self.findChildren(QWidget):
            if isinstance(widget, (QLabel, QAbstractButton)) and widget.text():
                self.captions[widget] = widget.text()
            for getter, setter in (("toolTip", "setToolTip"), ("accessibleName", "setAccessibleName"),
                                   ("placeholderText", "setPlaceholderText")):
                read = getattr(widget, getter, None)
                if read and (text := read()):
                    self.static_properties.append((getattr(widget, setter), text))
        for i in range(self.languages.count()):
            self.languages.setItemData(i, self.languages.itemText(i), Qt.UserRole + 1)

    def caption(self, widget, source):
        self.captions[widget] = source
        widget.setText(source() if callable(source) else self.words.text(source))
        if widget in (self.status, self.gpu_label):
            widget.setToolTip(widget.text())

    def gpu_name(self, name):
        self.device_name = name
        self.caption(self.gpu_label, (lambda: name) if name else "未检测到 NVIDIA GPU")
        self.update_plan()

    def option_text(self, source):
        if source.startswith("杜比视界 Profile ") and " → " in source:
            return self.words.text("杜比视界 Profile {profile} → Profile 8.1", profile=source.split()[2])
        return self.words.text(source)

    def add_option(self, combo, source, value):
        combo.addItem(self.option_text(source), value)
        combo.setItemData(combo.count() - 1, source, Qt.UserRole + 1)

    def change_language(self):
        self.language_choice = self.languages.currentData()
        self.settings.setValue("uiLanguage", self.language_choice)
        self.words = Translator(resolve_language(self.language_choice, QLocale.system().uiLanguages()))
        self.translate_interface()

    def translate_interface(self):
        app = QApplication.instance()
        previous = getattr(app, "_studio_qt_translation", None)
        if previous is not None:
            app.removeTranslator(previous)
        qt_translation = QTranslator(app)
        qt_locale = {"zh-CN": "zh_CN", "ja": "ja"}.get(self.words.code)
        if qt_locale and qt_translation.load("qtbase_" + qt_locale, QLibraryInfo.path(QLibraryInfo.TranslationsPath)):
            app.installTranslator(qt_translation)
        app._studio_qt_translation = qt_translation
        if previous is not None:
            previous.deleteLater()
        self.apply_style()
        for widget, source in self.captions.items():
            widget.setText(source() if callable(source) else self.words.text(source))
        for setter, source in self.static_properties:
            setter(self.words.text(source))
        self.status.setToolTip(self.status.text())
        self.gpu_label.setToolTip(self.gpu_label.text())
        for combo in (self.languages, self.targets):
            for i in range(combo.count()):
                source = combo.itemData(i, Qt.UserRole + 1)
                if source:
                    combo.setItemText(i, self.option_text(source))
        self.resize_rail()
        self.update_plan()
        self.refresh_history()
        if self.busy:
            self.show_progress()

    def page(self, title):
        page = QWidget()
        self.page_host = layout(QVBoxLayout, page, 0, 13)
        self.page_host.setContentsMargins(24, 6, 10, 7)
        self.page_host.addWidget(label(title, "title"))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        wrap = QWidget()
        wrap.setObjectName("sheet")
        v = layout(QVBoxLayout, wrap, 0, 15)
        scroll.setWidget(wrap)
        self.page_host.addWidget(scroll, 1)
        self.stack.addWidget(page)
        return v

    def show_page(self, i):
        self.stack.setCurrentIndex(i)
        for j, b in enumerate(self.nav):
            b.selected = i == j
            b.setChecked(b.selected)
            b.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not hasattr(self, "setting_fields"):
            return
        self.resize_rail()
        self.arrange_fields()

    def resize_rail(self):
        compact = self.width() < 920
        self.rail.setFixedWidth(68 if compact else 192)
        self.brandtext.setVisible(not compact)
        for button, caption in self.rail_labels:
            button.setText("" if compact else self.words.text(caption))

    def arrange_fields(self):
        columns = 1 if self.width() < 775 else 2
        if columns == self.field_columns:
            return
        self.field_columns = columns
        self.setting_fields.removeWidget(self.plan_field)
        self.setting_fields.addWidget(self.format_field, 0, 0)
        self.setting_fields.setColumnStretch(0, 1)
        self.setting_fields.setColumnStretch(1, int(columns == 2))
        if columns == 2:
            self.setting_fields.addWidget(self.plan_field, 0, 1)
        else:
            self.setting_fields.addWidget(self.plan_field, 1, 0)

    def size_preview(self, compact):
        self.compact_preview = compact
        self.stage.setMinimumHeight(148 if compact else 211)
        self.stage.setMaximumHeight(166 if compact else 260)
        margins = (21, 8, 21, 8) if compact else (29, 20, 27, 21)
        self.stage.layout().setContentsMargins(*margins)
        self.stage.layout().setSpacing(3 if compact else 7)
        self.film.setStyleSheet("font-size: 27px;" if compact else "")

    def eventFilter(self, obj, event):
        if obj is getattr(self, "workspace_viewport", None) and event.type() == QEvent.Resize:
            compact = event.size().height() < 400
            if compact != self.compact_preview:
                self.size_preview(compact)
        if obj is getattr(self, "stage", None) and event.type() == QEvent.Resize:
            width = self.stage.width()
            self.film.setMaximumWidth(max(230, int(width * 0.57)))
            self.film_detail.setMaximumWidth(max(235, int(width * 0.53)))
        return super().eventFilter(obj, event)

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.ActivationChange and self.isActiveWindow() and hasattr(self, "slider"):
            effects = glass.system_effects()
            if effects != (self.allow_transparency, self.allow_motion):
                self.allow_transparency, self.allow_motion = effects
                self.appearance()

    def appearance(self):
        self.dark = self.dark_box.isChecked()
        self.solid = self.solid_box.isChecked()
        self.slider.setEnabled(self.allow_transparency and not self.solid)
        self.settings.setValue("dark", self.dark)
        self.settings.setValue("reduceTransparency", self.solid)
        self.apply_style()

    def density_change(self, n):
        self.density = n
        self.settings.setValue("glassDensity", n)
        self.caption(self.density_value, f"{n}%")
        self.material_sample.update()
        for w in self.findChildren(Rail) + self.findChildren(GlassButton) + self.findChildren(glass.ExportDock):
            w.update()

    def apply_style(self):
        fg, mut, card, edge, inp, accent = (
            ("#f0f2fb", "#b5bed1", "#252936", "#424859", "#1e222e", "#a5a8ff")
            if self.dark
            else ("#20253a", "#535e75", "#fcfcff", "#d5dbe7", "#f2f4fa", "#5353bf")
        )
        field_bg = inp if self.solid or not self.allow_transparency else (
            "rgba(24, 29, 42, 200)" if self.dark else "rgba(235, 239, 248, 150)"
        )
        palette = self.palette()
        for role, c in [
            (QPalette.Window, card),
            (QPalette.Base, card),
            (QPalette.WindowText, fg),
            (QPalette.Text, fg),
            (QPalette.Button, inp),
            (QPalette.ButtonText, fg),
            (QPalette.PlaceholderText, mut),
            (QPalette.HighlightedText, "#ffffff"),
            (QPalette.Highlight, "#6662c5"),
        ]:
            palette.setColor(role, QColor(c))
        self.setPalette(palette)
        interface_fonts = {"en": "'Segoe UI','Microsoft YaHei UI'", "ja": "'Yu Gothic UI','Meiryo UI','Segoe UI'"}
        fonts = interface_fonts.get(self.words.code, "'Microsoft YaHei UI','Segoe UI'")
        self.setStyleSheet(f"""
        QWidget {{font-family:{fonts};font-size:14px;color:{fg};}}
        QScrollArea,QScrollArea>QWidget>QWidget,#sheet, QLabel {{background:transparent;}}
        QLabel#title {{font-size:29px;font-weight:700;}}
        QLabel#section {{font-size:19px;font-weight:650;}}
        QLabel#brand {{font-family:'Segoe UI';font-size:18px;font-weight:700;}}
        QLabel#brandSmall {{font-family:'Segoe UI';font-size:9px;letter-spacing:2.2px;color:{mut};}}
        QLabel#heroTitle {{color:#ffffff;font-size:35px;font-weight:700;}}
        QLabel#heroDetail {{font-size:14px;color:#f0f3ff;}}
        QLabel#muted,QLabel#device {{font-size:12px;color:{mut};}}
        QLabel#field {{font-size:13px;font-weight:600;color:{mut};}}
        QLabel#guideTitle {{font-size:15px;font-weight:650;}}
        QLabel#status {{font-size:13px;font-weight:500;}}
        QLabel#dockCaption {{font-size:12px;font-weight:500;color:{mut};}}
        QLabel#dockPercent {{font-size:29px;font-weight:700;color:{accent};}}
        QLabel#dockEta {{font-size:25px;font-weight:650;color:{fg};}}
        QLabel#progressMetric {{font-size:15px;font-weight:600;color:{fg};}}
        QFrame#card {{background:{card};border:1px solid {edge};border-radius:20px;}}
        QFrame#rule {{background:{edge};border:0;}}
        QLineEdit,QComboBox {{background:{field_bg};padding:7px 12px;border-radius:10px;border:1px solid {edge};min-height:20px;}}
        QComboBox {{padding-right:28px;}}
        QComboBox:disabled {{color:{mut};}}
        QComboBox:enabled:hover,QLineEdit:hover {{border-color:{mut};}}
        QComboBox:focus,QLineEdit:focus {{border:2px solid {accent};padding:6px 11px;}}
        QComboBox:focus {{padding-right:27px;}}
        QComboBox::drop-down {{border:0;width:26px;background:transparent;}}
        QComboBox::down-arrow {{width:0;height:0;}}
        QComboBox QAbstractItemView {{background:{card};color:{fg};selection-background-color:#5356ba;selection-color:white;padding:5px;border:1px solid {edge};}}
        QProgressBar {{background:{edge};border:none;border-radius:3px;}}
        QProgressBar::chunk {{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 {accent},stop:1 {'#89c6f7' if self.dark else '#4681bd'});border-radius:3px;}}
        QSlider::groove:horizontal {{background:{edge};height:4px;border-radius:2px;}}
        QSlider::sub-page:horizontal {{background:{accent};border-radius:2px;}}
        QSlider::handle:horizontal {{background:{card};border:1px solid {mut};width:16px;margin:-7px 0;border-radius:8px;}}
        QSlider::handle:horizontal:focus {{border:2px solid {accent};}}
        QSlider::sub-page:horizontal:disabled {{background:{edge};}}
        QScrollBar:vertical {{width:9px;background:transparent;}} QScrollBar::handle:vertical {{background:{edge};border-radius:4px;min-height:20px;}}
        QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical {{height:0px;}}
        QScrollBar::add-page:vertical,QScrollBar::sub-page:vertical {{background:transparent;}}
        QMessageBox {{background:{card};}}
        QMessageBox QPushButton {{background:{inp};color:{fg};border:1px solid {edge};border-radius:8px;min-width:70px;padding:6px 12px;}}
        QMessageBox QPushButton:focus {{border:2px solid {accent};padding:5px 11px;}}
        QToolTip {{background:{card};color:{fg};border:1px solid {edge};padding:4px 7px;}}
        """)
        self.root.update()
        for w in self.findChildren(QWidget):
            QWidget.update(w)

    def set_controls(self, enabled):
        for w in (self.renderbtn, self.previewbtn, self.browse, self.targets):
            w.setEnabled(enabled)

    def choose(self):
        if self.busy:
            return
        path, _ = QFileDialog.getOpenFileName(
            self,
            self.words.text("选择影片"),
            self.settings.value("inputDir", ""),
            self.words.text("视频文件 (*.mkv *.mp4 *.mov *.m4v *.ts *.m2ts *.avi *.webm);;所有文件 (*)"),
        )
        if path:
            self.load(path)

    def load(self, path):
        if self.busy or self.close_after:
            return
        for pending in self.inspections:
            pending.set()
        cancel = threading.Event()
        self.inspections.add(cancel)
        self.seq += 1
        seq = self.seq
        self.info = None
        self.custom_destination = None
        self.show_page(0)
        self.set_controls(False)
        self.stage.set_poster(QPixmap())
        self.openbtn.hide()
        self.detailbtn.hide()
        self.bar.hide()
        self.progress_metrics.hide()
        self.path.clear()
        self.path.setToolTip("")
        self.refill_targets()
        self.caption(self.film, lambda: Path(path).name)
        self.film.setToolTip(Path(path).name)
        self.caption(self.film_detail, "正在读取视频轨道…")
        self.caption(self.pick, "更换影片")
        self.caption(self.status, "正在检测片源格式")
        self.status.show()
        self.settings.setValue("inputDir", str(Path(path).parent))

        def read():
            try:
                import engine

                result = engine.inspect_source(path, cancel=cancel)
                if cancel.is_set():
                    return
                self.events.inspected.emit(seq, result, "")
                if result["supported"]:
                    data = poster_frame(path, result, cancel)
                    if data and not cancel.is_set():
                        self.events.poster.emit(seq, data)
            except Exception as exc:
                if "result" not in locals() and not cancel.is_set():
                    self.events.inspected.emit(seq, None, str(exc))
            finally:
                self.events.inspection_done.emit(cancel)

        threading.Thread(target=read, daemon=True).start()

    def inspection_done(self, cancel):
        self.inspections.discard(cancel)
        if self.close_after and not self.inspections and not self.busy:
            self.close()

    def got_poster(self, seq, data):
        if seq != self.seq:
            return
        pix = QPixmap()
        pix.loadFromData(data)
        if not pix.isNull():
            self.stage.set_poster(pix)

    def got_info(self, seq, info, err):
        if seq != self.seq:
            return
        self.info = info
        self.status.show()
        if err:
            self.caption(self.film_detail, "无法读取影片")
            self.caption(self.status, lambda: self.words.message(err)[:450])
            return
        fps = float(Fraction(info["fps"] or 0))
        self.caption(self.film_detail, lambda: f"{info['width']} × {info['height']}   ·   {fps:.3f} fps   ·   "
                     + self.words.message(info["profile"] or "未识别格式"))
        self.set_controls(info["supported"])
        self.refill_targets()
        def description():
            details = self.words.message(info["reason"])
            if not info["supported"]:
                return details
            return self.words.text("将导出 {width} × {height}，含 {audio} 条音轨、{subs} 条字幕。{details}",
                                   width=info["target_width"], height=info["target_height"],
                                   audio=info["audio_count"], subs=info["subtitle_count"], details=details)
        self.caption(self.status, description)

    def refill_targets(self):
        self.targets.blockSignals(True)
        self.targets.clear()
        info = self.info or {}
        if not info.get("supported"):
            self.targets.blockSignals(False)
            self.update_plan()
            return
        keys = output_targets(info)
        for key in keys:
            caption = TARGETS[key]
            if key == "preserve" and info.get("dv_profile") in (5, 7):
                caption = f"杜比视界 Profile {info['dv_profile']} → Profile 8.1"
            self.add_option(self.targets, caption, key)
        self.targets.setCurrentIndex(0)
        self.targets.blockSignals(False)
        self.target_change()

    def target_change(self, *_):
        self.update_plan()
        self.suggest_dest()

    def update_plan(self):
        target = self.targets.currentData()
        ready = bool(target and self.info and self.info.get("supported"))
        self.plan_field.setVisible(ready)
        self.hint.hide()
        if not ready:
            return
        if self.device_name is None:
            self.plan.setText(self.words.text("正在匹配处理方案…"))
            return
        mode = recommended_mode(self.info, target, gpu_name=self.device_name)
        tr = self.words.text
        hint = ""
        if target == "keep7":
            name = tr("原始双层无损封装")
            hint = "直接保留原始画质和逐帧杜比数据。此格式需要支持 Profile 7 的播放器。"
        elif mode == "copy":
            name = tr("保留原始 HDR10 画面") if target == "newdv" else tr("无损封装 · 原始单层码流")
            if target == "newdv":
                hint = "沿用原始 HDR10 画面和母版亮度，逐镜测量生成杜比动态元数据。"
        else:
            native = (self.info["width"], self.info["height"]) == (self.info["target_width"], self.info["target_height"])
            name = tr("高位深转换" if native else MODES[mode])
            if mode == "rtx":
                name = tr("RTX 超分")
                hint = "首次使用 RTX 超分时会自动获取 NVIDIA 运行库（约 416 MiB）。"
            if self.info["source_type"] == "sdr":
                name += " · " + tr("自动逐镜调色")
            if self.info.get("dv_profile") == 7:
                hint = "导出前会读取增强层，生成 HDR10 兼容的单层 Profile 8.1。"
                if self.info.get("can_keep7") and self.info.get("profile", "").endswith(" · MEL"):
                    name = tr("保留原始基础层 · 转换杜比元数据")
                    hint = "全片确认为 MEL 时，沿用原始 HEVC 基础层，并将逐帧杜比元数据转换为 Profile 8.1。"
        self.plan.setText(name)
        self.hint.setText(tr(hint) if hint else "")
        self.hint.setVisible(bool(hint))

    def suggest_dest(self):
        if not self.info or not self.info["supported"]:
            return
        src = Path(self.info["path"])
        suffix = {"hdr10": "-4K-HDR10", "keep7": "-4K-DV-P7"}.get(self.targets.currentData(), "-4K-DV")
        f = available_destination(self.custom_destination or src.with_name(src.stem + suffix + ".mkv"))
        self.path.setText(str(f))
        self.path.setToolTip(str(f))
        self.path.setCursorPosition(0)

    def set_destination(self):
        f, _ = QFileDialog.getSaveFileName(
            self, self.words.text("保存 4K 影片"), self.path.text(), self.words.text("Matroska 影片 (*.mkv)"),
            options=QFileDialog.Option.DontConfirmOverwrite,
        )
        if f:
            if not f.lower().endswith(".mkv"):
                f += ".mkv"
            self.custom_destination = Path(f)
            self.suggest_dest()

    def start(self, *, sample=False):
        if self.busy or self.close_after or not self.info or not self.info["supported"]:
            return
        # Recheck immediately before export in case another program took this name.
        # The engine also claims the final destination atomically after verification.
        self.suggest_dest()
        dest = Path(self.path.text())
        if sample:
            dest = available_destination(dest.with_name(dest.stem + "-" + self.words.text("试片") + ".mkv"))
        dest = str(dest)
        src = self.info["path"]
        target = self.targets.currentData()
        info, device_name = self.info, self.device_name
        mapping = "auto" if info["source_type"] == "sdr" else "expand"
        self.busy = True
        self.cancel_event = threading.Event()
        self.started_at = time.monotonic()
        self.progress_tracker = ExportProgress()
        feed = ProgressFeed(self.events.state.emit)
        self.progress_value = 0
        self.stage_message = "正在准备试片" if sample else "正在准备导出"
        self.set_controls(False)
        self.pick.setDisabled(True)
        self.stopbtn.setEnabled(True)
        self.stopbtn.show()
        self.previewbtn.hide()
        self.renderbtn.hide()
        self.openbtn.hide()
        self.detailbtn.hide()
        self.bar.setValue(0)
        self.bar.show()
        self.show_progress()
        self.clock.start()

        def run():
            try:
                import engine

                mode = recommended_mode(info, target, gpu_name=device_name)
                if sample:
                    from preview import render_sample

                    res = render_sample(
                        src,
                        dest,
                        mode,
                        feed,
                        self.cancel_event,
                        target=target,
                        duration=info["duration"],
                        sdr_mapping=mapping,
                        speed="quality",
                    )
                else:
                    res = engine.render(
                        src, dest, mode, feed, self.cancel_event, target=target,
                        sdr_mapping=mapping, speed="quality",
                    )
                self.events.complete.emit(res, "")
            except Exception as exc:
                self.events.complete.emit(None, str(exc))

        threading.Thread(target=run, daemon=True).start()

    def update_progress(self, n, msg, work=None, now=None):
        if not self.busy or self.cancel_event.is_set():
            return
        self.progress_tracker.update(n, work, time.monotonic() if now is None else now)
        self.progress_value = self.progress_tracker.percent
        self.stage_message = msg
        self.bar.setValue(round(self.progress_value * 10))
        self.show_progress()

    def show_progress(self):
        if not self.busy:
            return
        now = time.monotonic()
        work = self.progress_tracker.work
        percent = f"{self.progress_value:.1f}".removesuffix(".0") + "%"
        tr = self.words.text
        self.percent_value.setText(percent)
        parts = [self.words.message(self.stage_message)]
        cancelling = self.cancel_event.is_set()
        if work and not cancelling:
            parts.append(work_text(work, tr))
        parts.append(tr("已用 {time}", time=clock_text(now - self.started_at)))
        self.status.setText("  ·  ".join(parts))
        self.status.setToolTip(self.status.text())
        self.status.show()
        self.setWindowTitle(f"{percent} · LumaFrame Studio")
        self.progress_metrics.setVisible(not cancelling)
        if not cancelling:
            rate, phase_eta, total_eta = self.progress_tracker.estimates(now)
            self.total_eta.setText(eta_text(total_eta, tr))
            self.stage_eta.setText(tr("收尾中…") if work and work.done >= work.total else eta_text(phase_eta, tr))
            self.speed_value.setText(rate_text(rate, work, tr))

    def stop(self):
        self.cancel_event.set()
        self.stopbtn.setEnabled(False)
        self.stage_message = "正在取消并清理临时文件…"
        self.show_progress()

    def finished(self, res, err):
        self.busy = False
        self.clock.stop()
        self.progress_metrics.hide()
        self.setWindowTitle("LumaFrame Studio")
        self.pick.setEnabled(True)
        self.set_controls(True)
        self.stopbtn.hide()
        self.previewbtn.show()
        self.renderbtn.show()
        if res:
            self.last_output = res["path"]
            self.bar.setValue(self.bar.maximum())
            elapsed = clock_text(time.monotonic() - self.started_at) if self.started_at is not None else None
            self.openbtn.show()
            def summary():
                parts = ["100%", self.words.text("已校验 {frames:,} 帧", frames=res["frame_count"]),
                         f"{res['width']} × {res['height']}"]
                if elapsed is not None:
                    parts.append(self.words.text("用时 {time}", time=elapsed))
                parts.append(Path(res["path"]).name)
                return "   ·   ".join(parts)
            self.caption(self.status, summary)
            self.history.insert(
                0,
                {
                    "path": res["path"],
                    "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
                    "target": res["target"],
                    "n": res["frame_count"],
                },
            )
            self.history = self.history[:20]
            self.settings.setValue("history", json.dumps(self.history, ensure_ascii=False))
            self.refresh_history()
            self.suggest_dest()
        elif self.cancel_event.is_set():
            self.bar.hide()
            self.caption(self.status, "导出已取消")
        else:
            self.bar.hide()
            self.failure_detail = err.strip() or "媒体处理失败"
            self.caption(self.status, lambda: self.words.text("导出未完成 · {error}",
                         error=self.words.message(self.failure_detail).splitlines()[0][:250]))
            self.detailbtn.show()
        if getattr(self, "close_after", False):
            self.close()

    def show_failure(self):
        box = QMessageBox(self)
        box.setAttribute(Qt.WA_DeleteOnClose)
        box.setWindowTitle(self.words.text("导出未完成"))
        box.setTextFormat(Qt.PlainText)
        box.setText(self.words.message(self.failure_detail).splitlines()[0][:250])
        box.setDetailedText(self.failure_detail[-12000:])
        box.open()

    def refresh_history(self):
        while self.history_list.count():
            item = self.history_list.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        if not self.history:
            frame = QFrame()
            frame.setObjectName("card")
            v = layout(QVBoxLayout, frame, 32, 9)
            v.addWidget(Glyph("history", 32), 0, Qt.AlignHCenter)
            v.addWidget(label(self.words.text("暂无导出记录"), "section"), 0, Qt.AlignHCenter)
            v.addWidget(label(self.words.text("完成导出后，可在这里打开成片目录。"), "muted"), 0, Qt.AlignHCenter)
            self.history_list.addWidget(frame)
            return
        for r in self.history:
            frame = QFrame()
            frame.setObjectName("card")
            h = layout(QHBoxLayout, frame, 18, 12)
            desc = QWidget()
            v = layout(QVBoxLayout, desc, 0, 4)
            name = ElidedLabel(Path(r["path"]).name, "guideTitle")
            name.setToolTip(r["path"])
            v.addWidget(name)
            v.addWidget(
                label(
                    r["time"]
                    + "   ·   "
                    + self.words.text(TARGETS.get(r["target"], ""))
                    + "   ·   "
                    + self.words.text("{frames} 帧", frames=r["n"]),
                    "muted",
                )
            )
            h.addWidget(desc, 1)
            b = GlassButton(self.words.text("目录"), "folder")
            b.setFixedWidth(82)
            b.clicked.connect(lambda _, p=r["path"]: shell_path(Path(p).parent))
            h.addWidget(b)
            self.history_list.addWidget(frame)

    def open_output(self):
        shell_path(Path(self.last_output).parent)

    def closeEvent(self, event):
        for pending in self.inspections:
            pending.set()
        if self.busy:
            event.ignore()
            if self.cancel_event.is_set():
                self.close_after = True
                return
            if (
                QMessageBox.question(
                    self,
                    self.words.text("正在导出"),
                    self.words.text("取消当前导出并关闭工作台？"),
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No,
                )
                == QMessageBox.Yes
            ):
                if self.busy:
                    self.close_after = True
                    self.stop()
                else:
                    event.accept()
        elif self.inspections:
            self.close_after = True
            event.ignore()
        else:
            event.accept()


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setApplicationName("LumaFrame Studio")
    app.setFont(QFont("Microsoft YaHei UI", 10))
    window = Studio()
    window.show()
    if len(sys.argv) > 1 and Path(sys.argv[1]).is_file():
        QTimer.singleShot(0, lambda: window.load(sys.argv[1]))
    return app.exec()


def export_command(argv):
    """Headless export uses exactly the same media engine as the desktop window."""
    import argparse
    import engine

    parser = argparse.ArgumentParser()
    parser.add_argument("input")
    parser.add_argument("--output", required=True)
    parser.add_argument("--result", required=True, help="New JSON file for the verification result")
    parser.add_argument("--language", choices=("auto", *LANGUAGES), default="zh-CN",
                        help="Language of human-readable progress and errors; JSON keys stay the same")
    parser.add_argument("--progress-file", help="New JSON file for live progress, processing speed and estimated time remaining")
    parser.add_argument("--target", choices=tuple(TARGETS))
    parser.add_argument("--mode", choices=tuple(MODES))
    parser.add_argument("--speed", choices=("quality", "fast"), default="quality",
                        help="RTX pipeline: quality (standard buffers, default) or fast (parallel). Same model and encoding settings.")
    parser.add_argument("--sdr-mapping", metavar="{auto,expand,reference}", default="auto",
                        help="For SDR, automatically select grading per shot (default), extend highlights to "
                             "1000 nits (expand), or use 203-nit SDR white (reference).")
    parser.add_argument("--cinema-model", metavar="ZIP", help="Import a verified model pack for automatic per-shot grading.")
    parser.add_argument("--sample", action="store_true")
    parser.add_argument("--active-area", type=int, nargs=4, metavar=("LEFT", "RIGHT", "TOP", "BOTTOM"),
                        help="New HDR10 or Dolby Vision: fixed analysis insets in output pixels, keeping the full canvas")
    options = parser.parse_args(argv)
    # Existing headless jobs can still request either legacy grading method.
    if options.sdr_mapping not in {"auto", "expand", "reference", "scene", "cinema"}:
        parser.error("--sdr-mapping must be auto, expand, or reference")
    words = Translator(resolve_language(options.language, QLocale.system().uiLanguages()))
    result_file = Path(options.result)
    media_paths = {Path(options.input).resolve(), Path(options.output).resolve()}
    if result_file.resolve() in media_paths:
        return 2
    try:
        report = result_file.open("x", encoding="utf-8")
    except OSError:
        return 2
    with report:
        live = None
        try:
            if options.cinema_model:
                if options.sdr_mapping not in {"auto", "cinema"}:
                    raise ValueError("当前亮度映射不使用逐镜调色模型。")
                from cinema_lut import install_model
                install_model(options.cinema_model)
            if options.progress_file:
                if Path(options.progress_file).resolve() in media_paths | {result_file.resolve()}:
                    raise ValueError("进度文件必须与片源、成片和验收结果使用不同的路径。")
                live = ProgressFile(options.progress_file, words.message)
            progress = live.feed if live else (lambda *_: None)
            if options.sample or not options.target or not options.mode:
                data = engine.inspect_source(options.input)
                if not data["supported"]:
                    raise ValueError(data["reason"])
                if not options.target:
                    options.target = output_targets(data)[0]
                if not options.mode:
                    options.mode = recommended_mode(data, options.target)
            if options.speed == "fast" and not supports_fast_export(options.mode, options.target):
                raise ValueError("--speed fast 仅适用于实际放大的 RTX 模式（rtx、rtx_compressed）。")
            if options.sample:
                from preview import render_sample

                result = render_sample(
                    options.input,
                    options.output,
                    options.mode,
                    progress,
                    threading.Event(),
                    target=options.target,
                    duration=data["duration"],
                    sdr_mapping=options.sdr_mapping,
                    active_area=options.active_area,
                    speed=options.speed,
                )
            else:
                result = engine.render(
                    options.input,
                    options.output,
                    options.mode,
                    progress,
                    threading.Event(),
                    target=options.target,
                    sdr_mapping=options.sdr_mapping,
                    active_area=options.active_area,
                    speed=options.speed,
                )
            if live:
                telemetry_error = live.finish("completed", "成片已校验")
                if telemetry_error:
                    result = {**result, "progress_file_error": telemetry_error}
            json.dump(result, report, ensure_ascii=False, indent=2)
            return 0
        except Exception as exc:
            failure = {"verified": False, "error": words.message(str(exc))}
            if live:
                telemetry_error = live.finish("failed", str(exc))
                if telemetry_error:
                    failure["progress_file_error"] = telemetry_error
            json.dump(failure, report, ensure_ascii=False, indent=2)
            return 1


if __name__ == "__main__":
    if sys.argv[1:2] == ["--export"]:
        raise SystemExit(export_command(sys.argv[2:]))
    raise SystemExit(main())
