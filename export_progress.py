"""Live export telemetry shared by the desktop and unattended exports."""

from collections import deque
from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import tempfile
import time


@dataclass(frozen=True)
class Work:
    key: str
    done: float
    total: float
    unit: str


class ProgressFeed:
    """Pair the engine's optional work counters with their status update."""

    def __init__(self, send):
        self.send = send
        self.pending = None
        self.last_key = None
        self.last_sent = -math.inf

    def stage(self, key, current, total, unit):
        if total > 0 and math.isfinite(total) and math.isfinite(current):
            self.pending = Work(key, min(total, max(0, current)), total, unit)
        else:
            self.pending = None

    def __call__(self, percent, message):
        work, self.pending = self.pending, None
        now = time.monotonic()
        key = (message, work.key, work.unit, work.total) if work else (message,)
        if key == self.last_key and now - self.last_sent < 0.25 and percent < 100:
            if not work or work.done < work.total:
                return
        self.last_sent, self.last_key = now, key
        self.send(percent, message, work, now)


class ExportProgress:
    def __init__(self):
        self.percent = 0.0
        self.work = None
        self.samples = deque()

    def update(self, percent, work, now):
        # The public file is only complete after the worker returns successfully.
        self.percent = max(self.percent, min(99.9, float(percent)))
        old = self.work
        if not work or not old or (work.key, work.total, work.unit) != (old.key, old.total, old.unit):
            self.samples.clear()
        elif work.done < old.done:
            return
        self.work = work
        if not work or (self.samples and self.samples[-1][1] == work.done):
            return
        self.samples.append((now, work.done, self.percent))
        while len(self.samples) > 2 and self.samples[1][0] <= now - 45:
            self.samples.popleft()

    def estimates(self, now):
        """Return rate, stage ETA and route-weighted overall ETA in seconds."""
        if not self.work or len(self.samples) < 2:
            return None, None, None
        start, first, first_percent = self.samples[0]
        last, done, percent = self.samples[-1]
        dt = last - start
        if dt < 2 or now - last > max(20, 2 * (last - self.samples[-2][0])):
            return None, None, None
        rate = (done - first) / dt
        if rate <= 0:
            return None, None, None
        stage_eta = (self.work.total - done) / rate
        # A coarse milestone or a stage switch is never a rate sample.
        overall_eta = None
        if percent > first_percent and done < self.work.total:
            overall_eta = max(stage_eta, (100 - percent) * dt / (percent - first_percent))
        return rate, stage_eta, overall_eta


def clock_text(seconds):
    hours, seconds = divmod(max(0, int(seconds)), 3600)
    minutes, seconds = divmod(seconds, 60)
    return f"{hours:02}:{minutes:02}:{seconds:02}"


def eta_text(seconds, tr=str.format):
    if seconds is None:
        return tr("估算中…")
    return tr("约 {time}", time=clock_text(math.ceil(seconds)))


def byte_text(value):
    for label in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or label == "TiB":
            return f"{value:.1f} {label}" if label != "B" else f"{value:.0f} B"
        value /= 1024


def work_text(work, tr=str.format):
    if not work:
        return ""
    if work.unit == "bytes":
        return f"{byte_text(work.done)} / {byte_text(work.total)}"
    if work.unit == "frames":
        return tr("{done:,.0f} / {total:,.0f} 帧", done=work.done, total=work.total)
    return f"{100 * work.done / work.total:.1f}%"


def rate_text(rate, work, tr=str.format):
    if rate is None or not work:
        return "—"
    if work.unit == "bytes":
        return tr("{rate}/秒", rate=byte_text(rate))
    if work.unit == "frames":
        return tr("{rate:.1f} 帧/秒", rate=rate)
    return tr("{rate:.2f}%/秒", rate=100 * rate / work.total)


class ProgressFile:
    """Reserve a new status file, then replace each complete JSON snapshot atomically."""

    def __init__(self, path, translate=str):
        self.path = Path(path)
        self.translate = translate
        self.started = time.monotonic()
        self.tracker = ExportProgress()
        self.feed = ProgressFeed(self.update)
        self.message = "正在准备导出"
        with self.path.open("x", encoding="utf-8") as stream:
            json.dump(self.snapshot("running", self.started), stream, ensure_ascii=False)

    def snapshot(self, state, now):
        running = state == "running"
        rate, phase_eta, total_eta = self.tracker.estimates(now) if running else (None, None, None)
        work = self.tracker.work if running else None
        return {
            "state": state,
            "stage": self.translate(self.message),
            "percent": 100 if state == "completed" else self.tracker.percent,
            "elapsed_seconds": int(now - self.started),
            "remaining_seconds": math.ceil(total_eta) if total_eta is not None else None,
            "stage_remaining_seconds": math.ceil(phase_eta) if phase_eta is not None else None,
            "stage_key": work.key if work else None,
            "completed_units": work.done if work else None,
            "total_units": work.total if work else None,
            "unit": work.unit if work else None,
            "units_per_second": rate,
        }

    def write(self, state, now):
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=self.path.parent, delete=False) as stream:
            tmp = Path(stream.name)
            try:
                json.dump(self.snapshot(state, now), stream, ensure_ascii=False)
            except BaseException:
                stream.close()
                tmp.unlink(missing_ok=True)
                raise
        try:
            attempts = 1 if state == "running" else 5
            for attempt in range(attempts):
                try:
                    os.replace(tmp, self.path)
                    break
                except PermissionError:
                    # Windows readers may briefly hold the previous snapshot open.
                    if attempt + 1 == attempts:
                        raise
                    time.sleep(0.05 * (attempt + 1))
        finally:
            tmp.unlink(missing_ok=True)

    def update(self, percent, message, work, now):
        self.message = message
        self.tracker.update(percent, work, now)
        try:
            self.write("running", now)
        except OSError:
            # The next sample retries. Monitoring must never interrupt media processing.
            pass

    def finish(self, state, message):
        self.message = message
        try:
            self.write(state, time.monotonic())
        except OSError as exc:
            return str(exc)
        return None
