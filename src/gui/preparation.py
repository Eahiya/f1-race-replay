"""Non-blocking session preparation lifecycle for the race-selection GUI.

Replaces the blocking "Loading session data..." dialog with an explicit
staged pipeline that runs in a worker thread and reports real measurements:

1. validate ......... selection sanity (event + session identifiers)
2. fastf1_cache ..... local FastF1 cache state (hit / miss)
3. load ............. ``load_session()`` (network + parse; FastF1 exposes no
                      byte-level progress, so this stage is honestly shown as
                      active with elapsed time, never a fabricated percent)
4. availability ..... telemetry / position-data validation (preserves the
                      documented telemetry-unavailable safeguards)
5. drivers .......... per-driver telemetry extraction (measured: n/N drivers)
6. frames ........... playback-frame generation (measured: frames + frames/s
                      rate with ETA derived from the measured rate)
7. safety_car ....... Safety Car position synthesis
8. sectors .......... sector splits / speed traps
9. cache ............ atomic versioned cache write
10. launch ........... spawn the replay subprocess (warm cache => fast start)

Overall progress is a weighted blend of completed stages plus fractional
sub-progress inside the measured stages.

Cancellation semantics: the worker checks for cancellation between drivers
and frame chunks, so preprocessing aborts promptly. A worker blocked inside
FastF1 network I/O cannot be interrupted cooperatively; closing or retrying
then terminates the thread as a last resort. Either way the dialog retires
the request id, so a late or partial result can never overwrite a newer
request, and no half-written cache entry is ever published (cache writes
are atomic).
"""

import os
import subprocess
import sys
import tempfile
import time
import uuid

from PySide6.QtCore import QObject, QThread, Signal, Qt, QTimer
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QProgressBar,
    QPushButton, QMessageBox,
)

from src.f1_data import PreparationCancelled


# (stage_id, human label, weight for overall progress)
STAGES = (
    ("validate", "Validating selection", 2),
    ("fastf1_cache", "Checking FastF1 cache", 3),
    ("load", "Loading session (FastF1)", 30),
    ("availability", "Validating telemetry availability", 3),
    ("drivers", "Extracting driver telemetry", 27),
    ("frames", "Generating playback frames", 25),
    ("safety_car", "Computing Safety Car positions", 3),
    ("sectors", "Extracting sector splits", 2),
    ("cache", "Writing prepared-data cache", 4),
    ("launch", "Launching replay", 1),
)
_TOTAL_WEIGHT = sum(weight for _, _, weight in STAGES)


def overall_fraction(stage_id, done=0, total=0):
    """Weighted overall progress in [0, 1] from stage sub-progress."""
    completed = 0
    for sid, _, weight in STAGES:
        if sid == stage_id:
            frac = (done / total) if total else 0.0
            frac = max(0.0, min(1.0, frac))
            return (completed + weight * frac) / _TOTAL_WEIGHT
        completed += weight
    return 1.0


class SessionPreparationWorker(QThread):
    stage_changed = Signal(str, str)          # stage_id, label
    progress = Signal(float, str)             # overall 0..1, detail message
    load_elapsed = Signal(float)              # seconds spent in load stage
    finished = Signal(object, object)         # payload, request_id
    failed = Signal(str, object)               # message, request_id
    cancelled = Signal(object)                # request_id

    def __init__(self, year, round_number, session_type, event_name="",
                 request_id=None, parent=None):
        super().__init__(parent)
        self.year = year
        self.round_number = round_number
        self.session_type = session_type
        self.event_name = event_name
        self.request_id = request_id or uuid.uuid4().hex
        self._cancel_requested = False
        self._load_t0 = 0.0

    def request_cancel(self):
        self._cancel_requested = True

    def _check_cancel(self):
        if self._cancel_requested:
            raise PreparationCancelled("cancelled by user")

    def _pipeline_progress(self, stage, done, total, message=""):
        self._check_cancel()
        # Map preprocessing sub-stages onto the matching lifecycle stage.
        mapped = {"drivers": "drivers", "frames": "frames",
                  "safety_car": "safety_car", "sectors": "sectors",
                  "save": "cache", "cache_hit": "cache"}.get(stage, stage)
        frac = overall_fraction(mapped, done, total or 1)
        detail = message or f"{mapped}: {done}/{total}"
        # Measured rate for the frame stage (real ETA, not fabricated).
        if stage == "frames" and done and total:
            elapsed = max(1e-6, time.monotonic() - getattr(self, "_frames_t0", time.monotonic()))
            rate = done / elapsed
            if rate > 0 and done < total:
                eta = (total - done) / rate
                detail = f"{message} - {rate:,.0f} frames/s, ETA {eta:,.0f}s"
        self.progress.emit(frac, detail)

    def run(self):
        try:
            self._run_pipeline()
        except PreparationCancelled:
            self.cancelled.emit(self.request_id)
        except Exception as exc:  # noqa: BLE001 - surfaced to the dialog
            self.failed.emit(str(exc), self.request_id)

    def _run_pipeline(self):
        from src.f1_data import enable_cache, load_session, get_race_telemetry

        self._check_cancel()
        self.stage_changed.emit("validate", "Validating selection")
        self.progress.emit(overall_fraction("validate", 0, 1), "Validating selection")
        if self.year is None or self.round_number is None:
            raise ValueError("Cannot prepare session: missing year or round number.")
        try:
            enable_cache()
        except Exception:
            pass
        self.progress.emit(overall_fraction("validate", 1, 1), "Selection valid")

        self._check_cancel()
        self.stage_changed.emit("fastf1_cache", "Checking FastF1 cache")
        try:
            from src.lib.settings import get_settings
            cache_dir = get_settings().cache_location
            cache_ok = os.path.isdir(cache_dir)
        except Exception:
            cache_ok = False
        self.progress.emit(
            overall_fraction("fastf1_cache", 1, 1),
            f"FastF1 cache {'ready' if cache_ok else 'will be created'}")

        # --- load: FastF1 exposes no byte-level progress, so report the
        # stage as active with real elapsed time (indeterminate bar).
        self._check_cancel()
        self.stage_changed.emit("load", "Loading session (FastF1)")
        self.progress.emit(overall_fraction("load", 0, 1),
                           "Requesting session from FastF1 (network + parse)...")
        self._load_t0 = time.monotonic()
        session = load_session(self.year, self.round_number, self.session_type)
        load_secs = time.monotonic() - self._load_t0
        self.load_elapsed.emit(load_secs)
        self.progress.emit(overall_fraction("load", 1, 1),
                           f"Session loaded in {load_secs:.1f}s")

        # --- availability: never assume the returned session has telemetry.
        self._check_cancel()
        self.stage_changed.emit("availability", "Validating telemetry availability")
        if not hasattr(session, "_pos_data") or session._pos_data is None:
            raise ValueError(
                "Telemetry and position data is not available for this session. "
                "It may not have taken place yet.")
        self.progress.emit(overall_fraction("availability", 1, 1),
                           "Telemetry available")

        # --- heavy preprocessing with real per-unit progress.
        self._check_cancel()
        self.stage_changed.emit("drivers", "Extracting driver telemetry")
        self._frames_t0 = time.monotonic()
        payload = get_race_telemetry(
            session, session_type=self.session_type,
            progress=self._pipeline_progress,
            year=self.year, round_number=self.round_number)
        self._check_cancel()
        n_frames = len(payload.get("frames", []))
        self.progress.emit(1.0, f"Prepared {n_frames:,} playback frames")
        self.finished.emit(payload, self.request_id)


class SessionPreparationDialog(QDialog):
    """Responsive preparation dialog with staged, measured progress."""

    def __init__(self, parent, year, round_number, session_label, event_name,
                 session_code, launch_command):
        super().__init__(parent)
        self.setWindowTitle("Preparing Session")
        self.setModal(True)
        self.setMinimumWidth(460)
        self.request_id = uuid.uuid4().hex
        self._worker = None
        self._launch_command = launch_command
        self._done = False
        self._load_secs = None

        layout = QVBoxLayout(self)

        title = QLabel(f"<b>{event_name}</b> - {session_label} "
                       f"({year} Round {round_number})")
        title.setWordWrap(True)
        layout.addWidget(title)

        self.stage_label = QLabel("Starting...")
        layout.addWidget(self.stage_label)

        self.overall_bar = QProgressBar()
        self.overall_bar.setRange(0, 100)
        self.overall_bar.setValue(0)
        layout.addWidget(self.overall_bar)

        self.detail_label = QLabel("")
        self.detail_label.setWordWrap(True)
        self.detail_label.setObjectName("subtle")
        layout.addWidget(self.detail_label)

        self.cache_label = QLabel("")
        self.cache_label.setWordWrap(True)
        self.cache_label.setObjectName("subtle")
        layout.addWidget(self.cache_label)

        buttons = QHBoxLayout()
        buttons.addStretch()
        self.retry_button = QPushButton("Retry")
        self.retry_button.setEnabled(False)
        self.retry_button.clicked.connect(self._on_retry)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self._on_cancel)
        buttons.addWidget(self.retry_button)
        buttons.addWidget(self.cancel_button)
        layout.addLayout(buttons)

        self._args = (year, round_number, session_code)
        self._start_worker()

    # -- worker lifecycle -------------------------------------------------
    def _start_worker(self):
        self._done = False
        self.retry_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.cancel_button.setText("Cancel")
        self.overall_bar.setRange(0, 100)
        self.overall_bar.setValue(0)
        year, round_number, session_code = self._args
        self._worker = SessionPreparationWorker(
            year, round_number, session_code,
            request_id=self.request_id, parent=self)
        self._worker.stage_changed.connect(self._on_stage)
        self._worker.progress.connect(self._on_progress)
        self._worker.load_elapsed.connect(self._on_load_elapsed)
        self._worker.finished.connect(self._on_finished)
        self._worker.failed.connect(self._on_failed)
        self._worker.cancelled.connect(self._on_cancelled)
        self._worker.start()

    def _stop_worker(self, timeout_ms=5000):
        """Stop the worker without ever destroying a running QThread.

        Cancellation is cooperative between units of work, so a worker
        blocked inside FastF1 network I/O may not exit in time. In that
        case ``terminate()`` is used as a last resort: the abandoned
        thread holds no locks or files the GUI needs, and its request id
        is already retired so any late result is ignored.
        """
        worker = self._worker
        if worker is None or not worker.isRunning():
            return
        worker.request_cancel()
        worker.wait(timeout_ms)
        if worker.isRunning():
            worker.terminate()
            worker.wait()

    def _on_retry(self):
        self._stop_worker()
        # New request id so any stale worker output is ignored.
        self.request_id = uuid.uuid4().hex
        self._start_worker()

    def _on_cancel(self):
        if self._done:
            self.reject()
            return
        self.cancel_button.setEnabled(False)
        self.cancel_button.setText("Cancelling...")
        self.detail_label.setText("Cancelling after the current unit of work...")
        if self._worker is not None:
            self._worker.request_cancel()

    def closeEvent(self, event):  # noqa: N802 (Qt naming)
        self._stop_worker()
        super().closeEvent(event)

    # -- worker signals ----------------------------------------------------
    def _on_stage(self, stage_id, label):
        if stage_id == "load":
            # Honest indeterminate state: FastF1 gives no byte counts.
            self.overall_bar.setRange(0, 0)
        else:
            if self.overall_bar.maximum() == 0:
                self.overall_bar.setRange(0, 100)
        self.stage_label.setText(label)

    def _on_progress(self, fraction, detail):
        if self.overall_bar.maximum() != 0:
            self.overall_bar.setValue(int(fraction * 100))
        self.detail_label.setText(detail)

    def _on_load_elapsed(self, secs):
        self._load_secs = secs
        if self.overall_bar.maximum() == 0:
            self.overall_bar.setRange(0, 100)
        self.cache_label.setText(f"Session download+parse: {secs:.1f}s (measured)")

    def _on_finished(self, payload, request_id):
        if request_id != self.request_id:
            return  # stale worker result - ignore
        self._done = True
        n_frames = len(payload.get("frames", []))
        extra = f" (session fetch took {self._load_secs:.1f}s)" if self._load_secs else ""
        self.cache_label.setText(f"Ready: {n_frames:,} frames{extra}")
        self.overall_bar.setRange(0, 100)
        self.overall_bar.setValue(100)
        self._launch_replay()

    def _on_failed(self, message, request_id):
        if request_id != self.request_id:
            return
        self._done = True
        self.overall_bar.setRange(0, 100)
        self.stage_label.setText("Preparation failed")
        self.detail_label.setText(message)
        self.retry_button.setEnabled(True)
        self.cancel_button.setEnabled(True)
        self.cancel_button.setText("Close")
        QMessageBox.critical(self, "Preparation error",
                             f"Could not prepare the session:\n{message}")

    def _on_cancelled(self, request_id):
        if request_id != self.request_id:
            return
        self._done = True
        self.reject()

    # -- replay launch (warm cache => fast start) ---------------------------
    def _launch_replay(self):
        import os as _os

        ready_path = _os.path.join(tempfile.gettempdir(),
                                   f"f1_ready_{uuid.uuid4().hex}")
        cmd = list(self._launch_command) + ["--ready-file", ready_path]
        try:
            proc = subprocess.Popen(cmd)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Playback error",
                                 f"Failed to start playback:\n{exc}")
            self.reject()
            return

        self.stage_label.setText("Launching replay (warm cache)...")
        self.detail_label.setText("Waiting for the replay window...")
        timer = QTimer(self)
        polls = {"n": 0}

        def _check_ready():
            polls["n"] += 1
            try:
                if _os.path.exists(ready_path):
                    try:
                        _os.remove(ready_path)
                    except OSError:
                        pass
                    timer.stop()
                    self.accept()
                    return
                if proc.poll() is not None:
                    timer.stop()
                    QMessageBox.critical(
                        self, "Playback error",
                        "Playback process exited before signaling readiness")
                    self.reject()
                elif polls["n"] > 600:  # 2-minute backstop
                    timer.stop()
                    QMessageBox.critical(self, "Playback error",
                                         "Timed out waiting for the replay window")
                    self.reject()
            except Exception:  # noqa: BLE001 - ignore transient FS errors
                pass

        timer.timeout.connect(_check_ready)
        timer.start(200)
        self._ready_timer = timer
        self._play_proc = proc


class _NoOpObject(QObject):
    """Placeholder so module import has no GUI side effects."""


__all__ = ["STAGES", "overall_fraction", "SessionPreparationWorker",
           "SessionPreparationDialog"]
