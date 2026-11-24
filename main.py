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
from PySide6.QtCore import Qt, QSettings, Signal, QUrl, QTimer, QObject
from PySide6.QtGui import (
    QColor,
    QDesktopServices,
    QFont,
    QIcon,
    QPixmap,
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
    QComboBox,
    QCheckBox,
    QLineEdit,
    QProgressBar,
    QFileDialog,
    QMessageBox,
    QStackedWidget,
    QScrollArea,
    QSlider,
)
from glass import ASSETS, GlassButton, Material, Rail, Stage, Select

VERSION = "1.1.1"
REPO = "https://github.com/lingjieheti/LumaFrame-Studio"
SPONSOR = "https://github.com/lingjieheti/suzerain-save-studio/blob/main/SPONSOR.md"
TARGETS = {
    "preserve": "保留杜比视界",
    "keep7": "原样保留双层 · Profile 7",
    "newdv": "生成杜比视界",
    "hdr10": "生成 HDR10",
}
MODES = {
    "rtx": "NVIDIA RTX AI · 高码率 Ultra",
    "neural": "FSRCNNX · 神经网络",
    "reference": "Spline36 · 保真缩放",
    "quality": "NVIDIA NIS · 快速缩放",
}
ROOT = Path(__file__).resolve().parent
HIDE = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def label(text, kind=""):
    w = QLabel(text)
    w.setObjectName(kind)
    w.setTextFormat(Qt.PlainText)
    return w


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


class Events(QObject):
    inspected = Signal(int, object, str)
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
        self.density = self.settings.value("glassDensity", 45, type=int)
        self.setWindowTitle("LumaFrame Studio")
        self.setWindowIcon(QIcon(str(ASSETS / "icon.ico")))
        self.resize(1050, 685)
        self.setMinimumSize(820, 590)
        self.info = None
        self.busy = False
        self.custom_destination = None
        self.failure_detail = ""
        self.seq = 0
        self.cancel_event = threading.Event()
        self.events = Events(self)
        self.events.inspected.connect(self.got_info)
        self.events.poster.connect(self.got_poster)
        self.events.state.connect(self.update_progress)
        self.events.complete.connect(self.finished)
        self.clock = QTimer(self)
        self.clock.setInterval(1000)
        self.clock.timeout.connect(self.show_progress)
        try:
            self.history = json.loads(str(self.settings.value("history", "[]")))[:20]
        except (ValueError, TypeError):
            self.history = []
        self.root = Material()
        self.setCentralWidget(self.root)
        outer = layout(QHBoxLayout, self.root)
        rail = Rail()
        rail.setFixedWidth(182)
        outer.addWidget(rail)
        rb = layout(QVBoxLayout, rail, 12, 6)
        rb.setContentsMargins(13, 29, 13, 17)
        brand = QWidget()
        bb = layout(QHBoxLayout, brand, 0, 8)
        logo = QLabel()
        logo.setPixmap(
            QPixmap(str(ASSETS / "icon.png")).scaled(
                36, 36, Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
        )
        bb.addWidget(logo)
        brandtext = QWidget()
        bt = layout(QVBoxLayout, brandtext, 0, 0)
        bt.addWidget(label("LumaFrame", "brand"))
        bt.addWidget(label("STUDIO", "brandSmall"))
        bb.addWidget(brandtext)
        bb.addStretch()
        rb.addWidget(brand)
        rb.addSpacing(31)
        self.nav = []
        for i, (title, glyph) in enumerate(
            [("影片工作台", "studio"), ("导出记录", "history"), ("外观与指南", "design")]
        ):
            b = GlassButton(title, glyph, "nav")
            b.clicked.connect(lambda _, j=i: self.show_page(j))
            self.nav.append(b)
            rb.addWidget(b)
        rb.addStretch()
        for txt, glyph, url in [("赞助作者", "heart", SPONSOR), ("GitHub", "link", REPO)]:
            b = GlassButton(txt, glyph, "nav")
            b.clicked.connect(lambda _, u=url: external(u))
            rb.addWidget(b)
        self.stack = QStackedWidget()
        outer.addWidget(self.stack, 1)
        self.workspace = self.page("影片工作台")
        box = self.workspace
        self.stage = Stage()
        self.stage.dropFile.connect(self.load)
        box.addWidget(self.stage)
        sl = layout(QVBoxLayout, self.stage, 24, 5)
        sl.addStretch()
        self.film = label("导入影片", "heroTitle")
        sl.addWidget(self.film)
        self.film_detail = label("将一个本地视频拖到这里，或选择文件。", "heroDetail")
        sl.addWidget(self.film_detail)
        sl.addSpacing(7)
        self.pick = GlassButton("选择影片", "add", "hero")
        self.pick.setFixedWidth(122)
        self.pick.clicked.connect(self.choose)
        sl.addWidget(self.pick)
        sl.addStretch()
        section = QWidget()
        sh = layout(QHBoxLayout, section)
        sh.addWidget(label("导出设置", "section"))
        sh.addStretch()
        self.gpu_label = label("检测图形设备…", "muted")
        sh.addWidget(self.gpu_label)
        box.addWidget(section)
        self.events.gpu.connect(self.gpu_label.setText)
        card = QFrame()
        card.setObjectName("card")
        box.addWidget(card)
        cg = layout(QVBoxLayout, card, 16, 11)
        self.targets = Select()
        self.modes = Select()

        def row(title, widget, buddy=True):
            line = QWidget()
            h = layout(QHBoxLayout, line, 0, 12)
            cap = label(title, "field")
            cap.setMinimumWidth(85)
            h.addWidget(cap)
            if buddy:
                cap.setBuddy(widget)
            h.addWidget(widget, 1)
            return line

        cg.addWidget(row("输出格式", self.targets))
        cg.addWidget(row("画质模式", self.modes))
        pathfield = QWidget()
        pb = layout(QHBoxLayout, pathfield, 0, 8)
        self.path = QLineEdit()
        self.path.setReadOnly(True)
        self.path.setPlaceholderText("选择影片后自动生成")
        pb.addWidget(self.path)
        self.browse = GlassButton("更改", "folder")
        self.browse.setFixedSize(84, 36)
        self.browse.clicked.connect(self.set_destination)
        pb.addWidget(self.browse)
        cg.addWidget(row("保存位置", pathfield, False))
        self.hint = label("", "muted")
        self.hint.setWordWrap(True)
        cg.addWidget(self.hint)
        self.targets.currentIndexChanged.connect(self.target_change)
        self.modes.currentIndexChanged.connect(self.mode_change)
        self.status = label("等待导入影片", "status")
        self.status.setWordWrap(True)
        box.addWidget(self.status)
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(5)
        self.bar.hide()
        box.addWidget(self.bar)
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
        box.addWidget(foot)
        box.addStretch()
        h = self.page("导出记录")
        self.history_host = QWidget()
        self.history_list = layout(QVBoxLayout, self.history_host, 0, 9)
        h.addWidget(self.history_host)
        h.addStretch()
        pref = self.page("外观与指南")
        surface = QFrame()
        surface.setObjectName("card")
        plo = layout(QVBoxLayout, surface, 19, 12)
        plo.addWidget(label("外观", "section"))
        self.dark_box = QCheckBox("深色外观")
        self.dark_box.setChecked(self.dark)
        self.dark_box.toggled.connect(self.appearance)
        plo.addWidget(self.dark_box)
        self.solid_box = QCheckBox("减少透明度")
        self.solid_box.setChecked(self.solid)
        self.solid_box.toggled.connect(self.appearance)
        plo.addWidget(self.solid_box)
        pref.addWidget(surface)
        density_row = QWidget()
        dr = layout(QHBoxLayout, density_row, 0, 12)
        dr.addWidget(label("玻璃浓度", "field"))
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setAccessibleName("玻璃浓度")
        self.slider.setRange(0, 100)
        self.slider.setValue(self.density)
        self.slider.setEnabled(not self.solid)
        self.slider.valueChanged.connect(self.density_change)
        dr.addWidget(self.slider, 1)
        plo.addWidget(density_row)
        guide = QFrame()
        guide.setObjectName("card")
        gl = layout(QVBoxLayout, guide, 19, 11)
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
            gl.addWidget(label(title, "field"))
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
            self.events.gpu.emit(name)

        threading.Thread(target=probe_gpu, daemon=True).start()

    def page(self, title):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        wrap = QWidget()
        wrap.setObjectName("sheet")
        v = layout(QVBoxLayout, wrap, 25, 13)
        v.setContentsMargins(27, 23, 27, 19)
        v.addWidget(label(title, "title"))
        scroll.setWidget(wrap)
        self.stack.addWidget(scroll)
        return v

    def show_page(self, i):
        self.stack.setCurrentIndex(i)
        for j, b in enumerate(self.nav):
            b.selected = i == j
            b.update()

    def appearance(self):
        self.dark = self.dark_box.isChecked()
        self.solid = self.solid_box.isChecked()
        self.slider.setEnabled(not self.solid)
        self.settings.setValue("dark", self.dark)
        self.settings.setValue("reduceTransparency", self.solid)
        self.apply_style()

    def density_change(self, n):
        self.density = n
        self.settings.setValue("glassDensity", n)
        for w in self.findChildren(Rail):
            w.update()

    def apply_style(self):
        fg, mut, card, edge, inp = (
            ("#edf0fa", "#b3b8ca", "#292b39", "#46495c", "#323544")
            if self.dark
            else ("#232536", "#555c72", "#fafafd", "#d9dce7", "#f2f3f8")
        )
        palette = self.palette()
        for role, c in [
            (QPalette.Window, card),
            (QPalette.Base, card),
            (QPalette.WindowText, fg),
            (QPalette.Text, fg),
            (QPalette.Button, inp),
            (QPalette.ButtonText, fg),
            (QPalette.Highlight, "#6662c5"),
        ]:
            palette.setColor(role, QColor(c))
        self.setPalette(palette)
        self.setStyleSheet(f"""
        QWidget {{font-family:'Microsoft YaHei UI','Segoe UI';font-size:14px;color:{fg};}}
        QScrollArea,QScrollArea>QWidget>QWidget,#sheet, QLabel {{background:transparent;}}
        QLabel#title {{font-size:30px;font-weight:700;}} QLabel#section {{font-size:18px;font-weight:650;}}
        QLabel#brand {{font-family:'Segoe UI';font-size:18px;font-weight:700;}} QLabel#brandSmall {{font-family:'Segoe UI';font-size:9px;letter-spacing:2px;color:{mut};}}
        QLabel#heroTitle {{color:white;font-size:27px;font-weight:650;}} QLabel#heroDetail {{font-size:13px;color:#e3e5f2;}}
        QLabel#muted {{font-size:12px;color:{mut};}} QLabel#field,QLabel#status {{font-weight:600;}}
        QFrame#card {{background:{card};border:1px solid {edge};border-radius:16px;}}
        QLineEdit,QComboBox {{background:{inp};padding:6px 11px;border-radius:8px;border:1px solid {edge};min-height:21px;}}
        QComboBox:focus,QLineEdit:focus {{border:1px solid #8382ca;}}
        QComboBox::drop-down {{border:0;width:28px;background:transparent;}}
        QComboBox::down-arrow {{width:0;height:0;}}
        QComboBox QAbstractItemView {{background:{card};color:{fg};selection-background-color:#5958ba;selection-color:white;padding:4px;}}
        QProgressBar {{background:{edge};border:none;border-radius:2px;}} QProgressBar::chunk {{background:#6d65d8;border-radius:2px;}}
        QCheckBox {{spacing:9px;}} QScrollBar:vertical {{width:9px;background:transparent;}} QScrollBar::handle:vertical {{background:{edge};border-radius:4px;min-height:20px;}}
        QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical {{height:0px;}}
        """)
        self.root.update()
        for w in self.findChildren(QWidget):
            QWidget.update(w)

    def set_controls(self, enabled):
        for w in (self.renderbtn, self.previewbtn, self.browse, self.targets, self.modes):
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
        if self.busy:
            return
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
        self.film.setText(self.film.fontMetrics().elidedText(Path(path).name, Qt.ElideMiddle, 500))
        self.film.setToolTip(Path(path).name)
        self.film_detail.setText("正在读取视频轨道…")
        self.pick.setText("更换影片")
        self.status.setText("正在检测片源格式")
        self.settings.setValue("inputDir", str(Path(path).parent))

        def read():
            try:
                import engine

                result = engine.inspect_source(path)
                self.events.inspected.emit(seq, result, "")
                if not result["supported"]:
                    return
                exe = ROOT / "bin" / "ffmpeg.exe"
                t = min(max(result["duration"] * 0.10, 0), 25)
                vf = "scale=640:-2"
                if result["source_type"] == "dv":
                    vf = "zscale=t=linear:npl=100,format=gbrpf32le,tonemap=mobius,zscale=p=bt709:t=bt709:m=bt709:r=limited,format=yuv420p,scale=640:-2"
                    if result.get("dv_profile") in (5, 7):
                        return
                p = subprocess.run(
                    [
                        str(exe),
                        "-nostdin",
                        "-v",
                        "error",
                        "-ss",
                        str(t),
                        "-i",
                        path,
                        "-map",
                        "0:V:0",
                        "-frames:v",
                        "1",
                        "-vf",
                        vf,
                        "-c:v",
                        "mjpeg",
                        "-f",
                        "image2pipe",
                        "-",
                    ],
                    capture_output=True,
                    timeout=12,
                    creationflags=HIDE,
                )
                if p.returncode == 0 and p.stdout:
                    self.events.poster.emit(seq, p.stdout)
            except Exception as exc:
                if "result" not in locals():
                    self.events.inspected.emit(seq, None, str(exc))

        threading.Thread(target=read, daemon=True).start()

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
        keys = ["hdr10", "newdv"] if info.get("source_type") == "sdr" else ["preserve"]
        if info.get("can_keep7"):
            keys.insert(0, "keep7")
        for key in keys:
            caption = TARGETS[key]
            if key == "preserve" and info.get("dv_profile") in (5, 7):
                caption = f"杜比视界 Profile {info['dv_profile']} → Profile 8.1"
            self.targets.addItem(caption, key)
        self.targets.blockSignals(False)
        self.target_change()

    def target_change(self, *_):
        k = self.targets.currentData()
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
        for key in keys:
            name = MODES[key]
            if k == "keep7":
                name = "无损封装 · 原始双层码流"
            elif key == "reference" and k in ("hdr10", "newdv"):
                name = "Spline36 · 高位深扩展"
            self.modes.addItem(name, key)
        self.modes.blockSignals(False)
        self.mode_change()
        self.suggest_dest()

    def mode_change(self, *_):
        key = self.modes.currentData()
        tips = {
            "rtx": "10 位 AI 超分与高码率编码，适合优质电影片源。首次使用需下载约 416 MiB 的 NVIDIA 运行库。",
            "neural": "使用 NVIDIA GPU 增强亮度细节，以 x265 慢速 CRF 14 编码。",
            "reference": "使用高位深 Spline36 与 x265 慢速编码，适合保留胶片纹理。",
            "quality": "采用 NVIDIA Image Scaling 与硬件编码，适合快速导出。",
        }
        text = tips.get(key, "")
        if self.targets.currentData() == "keep7":
            text = "直接保留原始画质和逐帧杜比数据。此格式需要支持 Profile 7 的播放器。"
        elif self.info and self.info.get("dv_profile") == 7 and self.targets.currentData() == "preserve":
            text += " 导出前会读取增强层，生成 HDR10 兼容的单层 Profile 8.1。"
            if "FEL" in self.info.get("profile", ""):
                text += " 重建的 12 位画面逐帧送入后续处理，请为分层码流和成片封装预留磁盘空间。"
        elif self.targets.currentData() == "newdv":
            text += " 按新画面测量生成杜比动态元数据。"
        self.hint.setText(text)

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
        if self.busy or not self.info or not self.info["supported"]:
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
                    )
                else:
                    res = engine.render(
                        src, dest, mode, self.events.state.emit, self.cancel_event, target=target
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
            self.history_list.addWidget(label("完成导出后，影片会显示在这里。", "muted"))
            return
        for r in self.history:
            frame = QFrame()
            frame.setObjectName("card")
            h = layout(QHBoxLayout, frame, 12, 10)
            desc = QWidget()
            v = layout(QVBoxLayout, desc, 0, 4)
            name = label(Path(r["path"]).name, "field")
            name.setWordWrap(True)
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
    parser.add_argument("--sample", action="store_true")
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
                    options.target = "keep7" if data.get("can_keep7") else (
                        "hdr10" if data["source_type"] == "sdr" else "preserve"
                    )
                if not options.mode:
                    native = (data["width"], data["height"]) == (data["target_width"], data["target_height"])
                    options.mode = "reference" if native else "rtx"
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
                )
            else:
                result = engine.render(
                    options.input,
                    options.output,
                    options.mode,
                    lambda *_: None,
                    threading.Event(),
                    target=options.target,
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
