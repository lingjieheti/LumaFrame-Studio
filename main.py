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
from PySide6.QtCore import Qt, QEvent, QRectF, QSettings, Signal, QUrl, QTimer, QObject
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
    QLineEdit,
    QProgressBar,
    QFileDialog,
    QMessageBox,
    QStackedWidget,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSpinBox,
)
import glass
from glass import ASSETS, GlassButton, Material, Rail, Stage, Select, ink
from material_sample import MaterialSample

VERSION = "1.4.2"
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


def centered_margins(width, height, ratio):
    """Choose symmetric, even pixel insets close to the requested display ratio."""
    if width / height < ratio:
        inset = max(0, 2 * round((height - width / ratio) / 4))
        return 0, 0, inset, inset
    inset = max(0, 2 * round((width - height * ratio) / 4))
    return inset, inset, 0, 0


def output_targets(info):
    if info.get("can_keep7"):
        return ["keep7", "preserve"]
    if info["source_type"] == "dv":
        return ["preserve"]
    if info["source_type"] == "hdr10" and (info["width"], info["height"]) == (
        info["target_width"], info["target_height"]
    ):
        return ["newdv"]
    return ["hdr10", "newdv"]


def label(text, kind=""):
    w = QLabel(text)
    w.setObjectName(kind)
    w.setTextFormat(Qt.PlainText)
    return w


class ElidedLabel(QLabel):
    """Keep the full text available to assistive tools while fitting the current width."""

    def __init__(self, text, kind):
        super().__init__(text)
        self.setObjectName(kind)
        self.setTextFormat(Qt.PlainText)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)

    def paintEvent(self, event):
        area = self.contentsRect()
        painter = QPainter(self)
        painter.setPen(self.palette().color(QPalette.WindowText))
        caption = self.fontMetrics().elidedText(self.text(), Qt.ElideMiddle, area.width())
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
    state = Signal(float, str)
    complete = Signal(object, str)
    gpu = Signal(str)


class Studio(QMainWindow):
    def __init__(self):
        super().__init__()
        self.settings = QSettings("LumaFrame", "Studio")
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
        self.busy = False
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
        box = self.workspace
        self.stage = Stage()
        self.stage.dropFile.connect(self.load)
        box.addWidget(self.stage, 1)
        sl = layout(QVBoxLayout, self.stage, 26, 7)
        sl.setContentsMargins(29, 20, 27, 21)
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
        card = QFrame()
        card.setObjectName("card")
        box.addWidget(card)
        cg = layout(QVBoxLayout, card, 20, 13)
        section = layout(QHBoxLayout, None, 0, 6)
        section.addWidget(label("导出设置", "section"))
        section.addStretch()
        section.addWidget(Glyph("gpu", 15))
        self.gpu_label = label("检测图形设备…", "device")
        section.addWidget(self.gpu_label)
        cg.addLayout(section)
        self.events.gpu.connect(self.gpu_label.setText)
        self.targets = Select()
        self.modes = Select()
        self.speeds = Select()
        self.speeds.addItem("标准", "quality")
        self.speeds.addItem("并行加速", "fast")
        if self.settings.value("rtxSpeed") == "fast":
            self.speeds.setCurrentIndex(1)
        self.speeds.setToolTip("标准模式使用常规缓冲。并行加速增加缓冲，让 RTX 超分与编码并行。试转和整片使用相同设置。")
        self.targets.setPlaceholderText("根据片源匹配")
        self.modes.setPlaceholderText("根据分辨率推荐")
        self.setting_fields = layout(QGridLayout, None, 0, 14)
        self.field_groups = []
        self.field_columns = None
        for title, combo in [
            ("输出格式", self.targets), ("画质模式", self.modes), ("速度模式", self.speeds)
        ]:
            group = QWidget()
            field = layout(QVBoxLayout, group, 0, 6)
            cap = label(title, "field")
            cap.setBuddy(combo)
            combo.setAccessibleName(title)
            combo.setMinimumWidth(0)
            combo.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
            field.addWidget(cap)
            field.addWidget(combo)
            self.field_groups.append(group)
            if combo is self.speeds:
                self.speed_field = group
                group.hide()
        self.arrange_fields()
        cg.addLayout(self.setting_fields)
        self.mapping_row = QWidget()
        mapping_layout = layout(QHBoxLayout, self.mapping_row, 0, 12)
        mapping_label = label("SDR 亮度映射", "field")
        self.sdr_mapping = Select()
        self.sdr_mapping.setAccessibleName(mapping_label.text())
        mapping_label.setBuddy(self.sdr_mapping)
        self.sdr_mapping.addItem("扩展高光 · 最高 1000 尼特", "expand")
        self.sdr_mapping.addItem("保留 SDR 明暗关系 · 参考白 203 尼特", "reference")
        mapping_layout.addWidget(mapping_label)
        mapping_layout.addWidget(self.sdr_mapping, 1)
        cg.addWidget(self.mapping_row)
        self.area_panel = QWidget()
        area_layout = layout(QVBoxLayout, self.area_panel, 0, 6)
        area_row = layout(QHBoxLayout, None, 0, 12)
        area_label = label("亮度分析区域", "field")
        self.area_choice = Select()
        area_label.setBuddy(self.area_choice)
        self.area_choice.setAccessibleName("亮度分析区域")
        self.area_choice.setToolTip("按输出画幅排除固定黑边，成片保留完整画布。可变画幅请使用完整画幅。")
        self.area_choice.addItem("完整画幅", "full")
        for name, ratio in (("1.85∶1", 1.85), ("2.35∶1", 2.35), ("2.39∶1", 2.39), ("4∶3", 4 / 3)):
            self.area_choice.addItem(name + " · 居中有效画面", ratio)
        self.area_choice.addItem("自定四边 · 成片像素", "custom")
        area_row.addWidget(area_label)
        area_row.addWidget(self.area_choice, 1)
        area_layout.addLayout(area_row)
        self.area_custom = QWidget()
        custom_row = layout(QHBoxLayout, self.area_custom, 0, 5)
        self.margin_inputs = []
        for direction in ("左", "右", "上", "下"):
            field = QSpinBox()
            field.setRange(0, 4095)
            field.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
            field.setAccessibleName(direction + "边距，成片像素")
            field.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
            cap = label(direction, "field")
            cap.setBuddy(field)
            custom_row.addWidget(cap)
            custom_row.addWidget(field, 1)
            self.margin_inputs.append(field)
        area_layout.addWidget(self.area_custom)
        self.area_custom.hide()
        self.area_panel.hide()
        self.area_choice.currentIndexChanged.connect(
            lambda: self.area_custom.setVisible(self.area_choice.currentData() == "custom")
        )
        cg.addWidget(self.area_panel)
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
        self.modes.currentIndexChanged.connect(self.mode_change)
        self.speeds.currentIndexChanged.connect(self.speed_change)
        box.addStretch()
        self.status = label("", "status")
        self.status.hide()
        self.status.setWordWrap(True)
        dock = QFrame()
        dock.setObjectName("actions")
        dock_layout = layout(QVBoxLayout, dock, 0, 7)
        dock_layout.setContentsMargins(0, 8, 0, 0)
        self.page_host.addWidget(dock)
        dock_layout.addWidget(self.status)
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(5)
        self.bar.hide()
        dock_layout.addWidget(self.bar)
        foot = QWidget()
        fh = layout(QHBoxLayout, foot, 0, 8)
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
        fh.addStretch()
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
        self.show_page(0)
        self.set_controls(False)
        self.refill_targets()
        self.apply_style()
        self.refresh_history()
        QShortcut(QKeySequence.Open, self, activated=self.choose)

        def probe_gpu():
            try:
                out = subprocess.run(
                    ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
                    capture_output=True,
                    timeout=6,
                    creationflags=HIDE,
                )
                name = out.stdout.decode(errors="replace").strip().splitlines()[0]
            except (OSError, IndexError, subprocess.TimeoutExpired):
                name = "未检测到 NVIDIA GPU"
            # The desktop window can close before the system GPU query returns.
            try:
                self.events.gpu.emit(name)
            except RuntimeError:
                return

        threading.Thread(target=probe_gpu, daemon=True).start()

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
        compact = self.width() < 920
        self.rail.setFixedWidth(68 if compact else 192)
        self.brandtext.setVisible(not compact)
        for button, caption in self.rail_labels:
            button.setText("" if compact else caption)
        self.arrange_fields()

    def arrange_fields(self):
        columns = 1 if self.width() < 775 else 2
        if self.width() >= 1080 and not self.speed_field.isHidden():
            columns = 3
        if columns == self.field_columns:
            return
        self.field_columns = columns
        for group in self.field_groups:
            self.setting_fields.removeWidget(group)
        for col, stretch in enumerate((5, 5 if columns > 1 else 0, 2 if columns == 3 else 0)):
            self.setting_fields.setColumnStretch(col, stretch)
        for i, group in enumerate(self.field_groups):
            row, col = divmod(i, columns)
            span = 2 if columns == 2 and group is self.speed_field else 1
            self.setting_fields.addWidget(group, row, col, 1, span)

    def eventFilter(self, obj, event):
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
        self.density_value.setText(f"{n}%")
        self.material_sample.update()
        for w in self.findChildren(Rail) + self.findChildren(GlassButton):
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
        self.setStyleSheet(f"""
        QWidget {{font-family:'Microsoft YaHei UI','Segoe UI';font-size:14px;color:{fg};}}
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
        QFrame#card {{background:{card};border:1px solid {edge};border-radius:20px;}}
        QFrame#rule {{background:{edge};border:0;}}
        QFrame#actions {{background:transparent;border:0;border-top:1px solid {edge};}}
        QLineEdit,QComboBox,QSpinBox {{background:{field_bg};padding:7px 12px;border-radius:10px;border:1px solid {edge};min-height:20px;}}
        QComboBox {{padding-right:28px;}}
        QComboBox:disabled {{color:{mut};}}
        QComboBox:enabled:hover,QLineEdit:hover,QSpinBox:hover {{border-color:{mut};}}
        QComboBox:focus,QLineEdit:focus,QSpinBox:focus {{border:2px solid {accent};padding:6px 11px;}}
        QComboBox:focus {{padding-right:27px;}}
        QComboBox::drop-down {{border:0;width:26px;background:transparent;}}
        QComboBox::down-arrow {{width:0;height:0;}}
        QComboBox QAbstractItemView {{background:{card};color:{fg};selection-background-color:#5356ba;selection-color:white;padding:5px;border:1px solid {edge};}}
        QProgressBar {{background:{edge};border:none;border-radius:2px;}}
        QProgressBar::chunk {{background:{accent};border-radius:2px;}}
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
        for w in (self.renderbtn, self.previewbtn, self.browse, self.targets, self.modes, self.speeds, self.sdr_mapping,
                  self.area_panel):
            w.setEnabled(enabled)

    def choose(self):
        if self.busy:
            return
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择影片",
            self.settings.value("inputDir", ""),
            "视频文件 (*.mkv *.mp4 *.mov *.m4v *.ts *.m2ts *.avi *.webm);;所有文件 (*)",
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
        self.path.clear()
        self.refill_targets()
        self.film.setText(Path(path).name)
        self.film.setToolTip(Path(path).name)
        self.film_detail.setText("正在读取视频轨道…")
        self.pick.setText("更换影片")
        self.status.setText("正在检测片源格式")
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
        self.area_panel.hide()
        self.area_choice.setCurrentIndex(0)
        for field in self.margin_inputs:
            field.setValue(0)
        self.info = info
        self.status.show()
        if err:
            self.film_detail.setText("无法读取影片")
            self.status.setText(err[:450])
            return
        fps = float(Fraction(info["fps"] or 0))
        self.film_detail.setText(
            f"{info['width']} × {info['height']}   ·   {fps:.3f} fps   ·   {info['profile'] or '未识别格式'}"
        )
        self.set_controls(info["supported"])
        self.refill_targets()
        details = info["reason"]
        if info["supported"]:
            details = (
                f"将导出 {info['target_width']} × {info['target_height']}，含 {info['audio_count']} 条音轨、"
                f"{info['subtitle_count']} 条字幕。{details}"
            )
        self.status.setText(details)

    def refill_targets(self):
        self.targets.blockSignals(True)
        self.targets.clear()
        info = self.info or {}
        self.mapping_row.setVisible(bool(info.get("supported") and info.get("source_type") == "sdr"))
        if not info.get("supported"):
            self.area_panel.hide()
            self.speed_field.hide()
            self.arrange_fields()
            self.targets.blockSignals(False)
            self.modes.clear()
            self.hint.hide()
            return
        keys = output_targets(info)
        for key in keys:
            caption = TARGETS[key]
            if key == "preserve" and info.get("dv_profile") in (5, 7):
                caption = f"杜比视界 Profile {info['dv_profile']} → Profile 8.1"
            self.targets.addItem(caption, key)
        self.targets.setCurrentIndex(0)
        self.targets.blockSignals(False)
        self.target_change()

    def target_change(self, *_):
        k = self.targets.currentData()
        self.area_panel.setVisible(k in {"hdr10", "newdv"})
        self.modes.blockSignals(True)
        self.modes.clear()
        keys = (
            ["rtx", "neural", "reference", "quality"]
            if k == "preserve"
            else ["rtx", "neural", "reference"]
        )
        if k == "preserve" and self.info and self.info.get("dv_profile") in (5, 7):
            keys = ["rtx", "neural", "reference"]
        if self.info and self.info["supported"]:
            ratio = min(
                self.info["target_width"] / self.info["width"],
                self.info["target_height"] / self.info["height"],
            )
            if ratio <= 1.30:
                keys = [key for key in keys if key != "neural"]
            if ratio <= 1.0:
                keys = ["reference"]
            elif (k == "preserve" and self.info.get("dv_profile") == 8) or self.info.get("source_type") == "hdr10":
                keys.insert(1, "rtx_compressed")
            if k == "preserve" and self.info.get("can_copy8"):
                keys.insert(0, "copy")
            if self.info.get("source_type") == "hdr10" and (self.info["width"], self.info["height"]) == (
                self.info["target_width"], self.info["target_height"]
            ):
                keys = ["copy"]
        for key in keys:
            name = MODES[key]
            if k == "keep7":
                name = "无损封装 · 原始双层码流"
            elif k == "newdv" and key == "copy":
                name = "保留 HDR10 原画面 · 生成动态元数据"
            elif (k == "preserve" and key == "reference" and self.info.get("can_keep7")
                  and self.info.get("profile", "").endswith(" · MEL")):
                name = "保留原始基础层 · 转换杜比元数据"
            elif key == "reference" and self.info.get("source_type") == "sdr":
                name = "Spline36 · 高位深扩展"
            self.modes.addItem(name, key)
        self.modes.setCurrentIndex(0)
        self.modes.blockSignals(False)
        self.mode_change()
        self.suggest_dest()

    def mode_change(self, *_):
        key = self.modes.currentData()
        rtx_speed = supports_fast_export(key, self.targets.currentData())
        self.speed_field.setVisible(rtx_speed)
        self.arrange_fields()
        tips = {
            "copy": "片源已达到输出尺寸，直接保留原始 HEVC 画面、HDR 信号和杜比动态元数据。",
            "rtx": "10 位 AI 超分与高码率编码，适合优质电影片源。首次使用需下载约 416 MiB 的 NVIDIA 运行库。",
            "rtx_compressed": "适合有压缩噪声的片源。轻度时空降噪后进行 RTX 超分。颗粒和文件大小可能变化，可先试转。",
            "neural": "使用 NVIDIA GPU 增强亮度细节，以 x265 慢速 CRF 14 编码。",
            "reference": "使用高位深 Spline36 与 x265 慢速编码。画面有移动细纹、织物或胶片纹理时，可先试转比较。",
            "quality": "采用 NVIDIA Image Scaling 与硬件编码，适合快速导出。",
        }
        text = tips.get(key, "")
        if self.targets.currentData() == "keep7":
            text = "直接保留原始画质和逐帧杜比数据。此格式需要支持 Profile 7 的播放器。"
        elif self.info and self.info.get("dv_profile") == 7 and self.targets.currentData() == "preserve":
            text += " 导出前会读取增强层，生成 HDR10 兼容的单层 Profile 8.1。"
            if (key == "reference" and self.info.get("can_keep7")
                    and self.info.get("profile", "").endswith(" · MEL")):
                text = "全片确认为 MEL 时，沿用原始 HEVC 基础层，并将逐帧杜比元数据转换为 Profile 8.1。"
            if "FEL" in self.info.get("profile", ""):
                text += " 重建的 12 位画面逐帧送入后续处理，请为分层码流和成片封装预留磁盘空间。"
        elif self.info and self.info.get("source_type") == "hdr10" and key == "copy":
            text = "沿用原始 HDR10 画面和母版亮度，逐镜测量生成杜比动态元数据。"
        elif self.targets.currentData() == "newdv":
            text += " 按新画面测量生成杜比动态元数据。"
        if rtx_speed:
            if self.speeds.currentData() == "fast":
                text += " 并行加速增加缓冲，让超分与编码并行。"
            text += " 两档使用相同的超分模型和编码参数。"
        self.hint.setText(text)
        self.hint.setVisible(bool(self.info and self.info.get("supported") and text))

    def speed_change(self):
        self.settings.setValue("rtxSpeed", self.speeds.currentData())
        self.mode_change()

    def suggest_dest(self):
        if not self.info or not self.info["supported"]:
            return
        src = Path(self.info["path"])
        dest = Path(self.settings.value("outputDir", str(src.parent)))
        if not dest.is_dir():
            dest = src.parent
        suffix = {"hdr10": "-4K-HDR10", "keep7": "-4K-DV-P7"}.get(self.targets.currentData(), "-4K-DV")
        f = available_destination(self.custom_destination or dest / (src.stem + suffix + ".mkv"))
        self.path.setText(str(f))
        self.path.setToolTip(str(f))
        self.path.setCursorPosition(0)

    def set_destination(self):
        f, _ = QFileDialog.getSaveFileName(
            self, "保存 4K 影片", self.path.text(), "Matroska 影片 (*.mkv)",
            options=QFileDialog.Option.DontConfirmOverwrite,
        )
        if f:
            if not f.lower().endswith(".mkv"):
                f += ".mkv"
            self.custom_destination = Path(f)
            self.settings.setValue("outputDir", str(Path(f).parent))
            self.suggest_dest()

    def start(self, *, sample=False):
        if self.busy or self.close_after or not self.info or not self.info["supported"]:
            return
        # Recheck immediately before export in case another program took this name.
        # The engine also claims the final destination atomically after verification.
        self.suggest_dest()
        dest = Path(self.path.text())
        if sample:
            dest = available_destination(dest.with_name(dest.stem + "-试片.mkv"))
        dest = str(dest)
        src = self.info["path"]
        mode = self.modes.currentData()
        target = self.targets.currentData()
        speed = self.speeds.currentData() if supports_fast_export(mode, target) else "quality"
        mapping = self.sdr_mapping.currentData() if self.info["source_type"] == "sdr" else "expand"
        active_area = None
        choice = self.area_choice.currentData()
        if target in {"hdr10", "newdv"} and choice != "full":
            import dovi
            canvas = self.info["target_width"], self.info["target_height"]
            margins = (tuple(f.value() for f in self.margin_inputs) if choice == "custom"
                       else centered_margins(*canvas, choice))
            try:
                active_area = dovi.validate_active_area(margins, *canvas)
            except ValueError as exc:
                self.status.setText(str(exc))
                self.status.show()
                return
        self.busy = True
        self.cancel_event = threading.Event()
        self.started_at = time.monotonic()
        self.progress_value = 0
        self.stage_message = "正在准备试片" if sample else "正在准备导出"
        self.set_controls(False)
        self.pick.setDisabled(True)
        self.stopbtn.setEnabled(True)
        self.stopbtn.show()
        self.openbtn.hide()
        self.detailbtn.hide()
        self.bar.setValue(0)
        self.bar.show()
        self.show_progress()
        self.clock.start()

        def run():
            try:
                import engine

                if sample:
                    from preview import render_sample

                    res = render_sample(
                        src,
                        dest,
                        mode,
                        self.events.state.emit,
                        self.cancel_event,
                        target=target,
                        duration=self.info["duration"],
                        sdr_mapping=mapping,
                        active_area=active_area,
                        speed=speed,
                    )
                else:
                    res = engine.render(
                        src, dest, mode, self.events.state.emit, self.cancel_event, target=target,
                        sdr_mapping=mapping, active_area=active_area, speed=speed,
                    )
                self.events.complete.emit(res, "")
            except Exception as exc:
                self.events.complete.emit(None, str(exc))

        threading.Thread(target=run, daemon=True).start()

    def update_progress(self, n, msg):
        if not self.busy or self.cancel_event.is_set():
            return
        self.progress_value = max(self.progress_value, min(int(n), 100))
        self.stage_message = msg
        self.bar.setValue(self.progress_value)
        self.show_progress()

    def show_progress(self):
        if self.busy:
            self.status.show()
            elapsed = int(time.monotonic() - self.started_at)
            minutes, seconds = divmod(elapsed, 60)
            hours, minutes = divmod(minutes, 60)
            self.status.setText(
                f"{self.progress_value}%  ·  {self.stage_message}  ·  已用 {hours:02}:{minutes:02}:{seconds:02}"
            )

    def stop(self):
        self.cancel_event.set()
        self.stopbtn.setEnabled(False)
        self.stage_message = "正在取消并清理临时文件…"
        self.show_progress()

    def finished(self, res, err):
        self.busy = False
        self.clock.stop()
        self.pick.setEnabled(True)
        self.set_controls(True)
        self.stopbtn.hide()
        if res:
            self.last_output = res["path"]
            self.bar.setValue(100)
            self.openbtn.show()
            self.status.setText(
                f"已校验 {res['frame_count']:,} 帧   ·   {res['width']} × {res['height']}   ·   "
                + Path(self.last_output).name
            )
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
            self.status.setText("导出已取消")
        else:
            self.bar.hide()
            self.failure_detail = err.strip() or "媒体处理失败"
            self.status.setText("导出未完成 · " + self.failure_detail.splitlines()[0][:250])
            self.detailbtn.show()
        if getattr(self, "close_after", False):
            self.close()

    def show_failure(self):
        box = QMessageBox(self)
        box.setAttribute(Qt.WA_DeleteOnClose)
        box.setWindowTitle("导出未完成")
        box.setTextFormat(Qt.PlainText)
        box.setText(self.failure_detail.splitlines()[0][:250])
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
            v.addWidget(label("暂无导出记录", "section"), 0, Qt.AlignHCenter)
            v.addWidget(label("完成导出后，可在这里打开成片目录。", "muted"), 0, Qt.AlignHCenter)
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
                    + TARGETS.get(r["target"], "")
                    + "   ·   "
                    + str(r["n"])
                    + " 帧",
                    "muted",
                )
            )
            h.addWidget(desc, 1)
            b = GlassButton("目录", "folder")
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
                    "正在导出",
                    "取消当前导出并关闭工作台？",
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
    parser.add_argument("--target", choices=tuple(TARGETS))
    parser.add_argument("--mode", choices=tuple(MODES))
    parser.add_argument("--speed", choices=("quality", "fast"), default="quality",
                        help="RTX pipeline: quality (standard buffers, default) or fast (parallel). Same model and encoding settings.")
    parser.add_argument("--sdr-mapping", choices=("expand", "reference"), default="expand",
                        help="SDR only: expand highlights to 1000 nits or map SDR white to 203 nits")
    parser.add_argument("--sample", action="store_true")
    parser.add_argument("--active-area", type=int, nargs=4, metavar=("LEFT", "RIGHT", "TOP", "BOTTOM"),
                        help="New HDR10 or Dolby Vision: fixed analysis insets in output pixels, keeping the full canvas")
    options = parser.parse_args(argv)
    result_file = Path(options.result)
    try:
        report = result_file.open("x", encoding="utf-8")
    except OSError:
        return 2
    with report:
        try:
            if options.sample or not options.target or not options.mode:
                data = engine.inspect_source(options.input)
                if not data["supported"]:
                    raise ValueError(data["reason"])
                if not options.target:
                    options.target = output_targets(data)[0]
                if not options.mode:
                    native = (data["width"], data["height"]) == (data["target_width"], data["target_height"])
                    if (data["source_type"] == "hdr10" and native) or (options.target == "preserve" and data.get("can_copy8")):
                        options.mode = "copy"
                    else:
                        options.mode = "reference" if native else "rtx"
            if options.speed == "fast" and not supports_fast_export(options.mode, options.target):
                raise ValueError("--speed fast 仅适用于实际放大的 RTX 模式（rtx、rtx_compressed）。")
            if options.sample:
                from preview import render_sample

                result = render_sample(
                    options.input,
                    options.output,
                    options.mode,
                    lambda *_: None,
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
                    lambda *_: None,
                    threading.Event(),
                    target=options.target,
                    sdr_mapping=options.sdr_mapping,
                    active_area=options.active_area,
                    speed=options.speed,
                )
            json.dump(result, report, ensure_ascii=False, indent=2)
            return 0
        except Exception as exc:
            json.dump({"verified": False, "error": str(exc)}, report, ensure_ascii=False, indent=2)
            return 1


if __name__ == "__main__":
    if sys.argv[1:2] == ["--export"]:
        raise SystemExit(export_command(sys.argv[2:]))
    raise SystemExit(main())
