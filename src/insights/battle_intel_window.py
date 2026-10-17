"""Battles & Overtaking Intelligence window (Update 8).

Standalone desktop analysis over prepared race payloads: session picker,
worker-thread full-session analysis with cancel, battle list, overtake
event list, gap/closing-rate and speed charts of actual analysis output,
evidence-backed finding detail, and markdown report export. Charts render
analysis results only — no placeholder series. Closing the window stops
the worker safely; repeated analyses retire stale workers first.
"""

import sys

import matplotlib
matplotlib.use("QtAgg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QApplication, QComboBox, QFileDialog, QFrame, QHBoxLayout, QLabel,
    QMainWindow, QMessageBox, QPushButton,
    QSizePolicy, QSplitter, QTableWidget, QTableWidgetItem, QTextBrowser,
    QVBoxLayout, QWidget,
)

from src.services import battle_analysis as ba
from src.services.evidence_render import render_evidence_html
from src.services.ui_theme import THEME

# Shared theme colours from the design system
_BG = THEME.background
_PANEL_BG = THEME.surface
_TEXT_COLOR = THEME.text_primary
_SUB_COLOR = THEME.text_secondary
_ACCENT = THEME.accent

# Driver-specific colours (not UI theme - these identify drivers in charts)
_COL_A = "#E10600"  # Ferrari / Red default
_COL_B = "#3671C6"  # Red Bull / Blue default

_GAP_COL = "#F1C40F"

_CLASS_COLORS = {
    ba.CL_PASS: "#2ECC71",
    ba.CL_PIT: "#3498DB",
    ba.CL_RETIRE: "#E67E22",
    ba.CL_RACE_CONTROL: "#F1C40F",
    ba.CL_UNRESOLVED: "#888888",
}

# Mechanisms first: "unresolved" is shared by ST_UNRESOLVED and
# MECH_UNRESOLVED, and the battle/event states must win the lookup.
_READABLE = {
    ba.MECH_STRAIGHT: "Straight-line approach advantage",
    ba.MECH_BRAKING: "Braking-phase pass",
    ba.MECH_EXIT: "Corner-exit advantage",
    ba.MECH_PIT: "Pit-cycle change",
    ba.MECH_OTHER: "Other supported mechanism",
    ba.MECH_UNRESOLVED: "Unresolved mechanism",
    ba.CL_PASS: "On-track pass (supported)",
    ba.CL_PIT: "Pit-cycle order change",
    ba.CL_RETIRE: "Retirement / non-running",
    ba.CL_RACE_CONTROL: "Race-control order change",
    ba.CL_UNRESOLVED: "Unresolved order change",
    ba.ST_APPROACHING: "Approaching",
    ba.ST_IN_RANGE: "Within battle range",
    ba.ST_ATTACK: "Attack candidate",
    ba.ST_POS_CANDIDATE: "Position-change candidate",
    ba.ST_CONFIRMED: "Confirmed pass",
    ba.ST_DEFENDED: "Defended / aborted",
    ba.ST_RESOLVED: "Battle resolved",
    ba.ST_UNRESOLVED: "Unresolved",
}

_LABELS = {
    "duration_s": "Duration",
    "min_gap_s": "Minimum gap",
    "max_closing_mps": "Peak closing rate",
    "n_transitions": "State changes",
    "transitions": "State path",
    "interruptions": "Interruptions",
    "interval": "Time interval",
    "hold_s": "New order held for",
    "gap_before_s": "Gap at the flip",
    "persist_s": "Order persistence",
    "rel_speed_kmh": "Relative speed",
    "closing_rate_mps": "Closing rate",
    "time_to_contact_s": "Estimated time to contact",
    "min_gap_t": "Closest-approach moment",
    "gap_trend_s_per_s": "Gap trend",
    "event_lap": "Lap",
    "event_dist_m": "Distance into lap",
    "speed_new_kmh": "Overtaker speed",
    "speed_old_kmh": "Defender speed",
    "status_during": "Track status",
    "opening_lap": "Opening lap",
    "n_gap_samples": "Gap samples",
    "window_t": "Analysis window",
    "statuses_in_window": "Track statuses seen",
    "tyre_changes": "Tyre changes nearby",
    "non_running": "Non-running cars",
    "driver": "Driver",
    "old": "From compound",
    "new": "To compound",
    "dt_s": "Offset from flip",
    "t_last_pre": "Last sample (still ahead)",
    "t_first_post": "First sample (now behind)",
    "event_step": "Simulation step",
    "step": "Step",
    "t": "At",
    "from": "From",
    "to": "To",
    "order": "Order",
    "gap": "Gap",
    "status": "Status",
    "tyre": "Tyre",
    "channels": "Channels",
    "type": "Type",
}

_UNITS = {
    "duration_s": "s", "min_gap_s": "s", "gap_before_s": "s", "hold_s": "s",
    "persist_s": "s", "time_to_contact_s": "s", "interval": "s",
    "window_t": "s", "min_gap_t": "s", "t": "s", "t_last_pre": "s",
    "t_first_post": "s", "dt_s": "s", "max_closing_mps": "m/s",
    "closing_rate_mps": "m/s", "rel_speed_kmh": "km/h",
    "speed_new_kmh": "km/h", "speed_old_kmh": "km/h",
    "event_dist_m": "m", "gap_trend_s_per_s": "s/s",
}


def _humanize(token):
    text = str(token).replace("_", " ").strip()
    return text[:1].upper() + text[1:] if text else text


def _readable(token):
    """Human label for a classification / state / mechanism token."""
    if token is None:
        return "—"
    token = str(token)
    return _READABLE.get(token) or _humanize(token)


def _default_session():
    """Newest locally cached race session, else season default."""
    import glob
    import re

    from src.lib.season import get_season

    best = None
    for path in glob.glob("computed_data/*_Q_v2.pkl"):
        match = re.search(r"(\d{4})_R(\d+)_R_v\d+\.pkl$", path)
        if match:
            key = (int(match.group(1)), int(match.group(2)))
            if best is None or key > best:
                best = key
    if best:
        return best[0], best[1]
    return get_season(), 12


class BattleAnalysisWorker(QThread):
    """Full-session battle analysis off the GUI thread."""

    progress = Signal(str)
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, year, round_number, want_corners=True, parent=None):
        super().__init__(parent)
        self.year = year
        self.round_number = round_number
        self.want_corners = want_corners
        self._cancel = False

    def request_cancel(self):
        self._cancel = True

    def _cancelled(self):
        return self._cancel

    def run(self):
        if self._cancel:
            return  # cancelled before the thread started: emit nothing
        try:
            self.progress.emit("Loading prepared race data...")
            from src.services.replay_cache import load_race_replay
            payload, meta = load_race_replay(
                year=self.year, round_number=self.round_number,
                session_type="R")
            if payload is None:
                raise ValueError(
                    f"No prepared race data for {self.year} round "
                    f"{self.round_number} ({meta.get('reason', 'cache miss')}). "
                    "Prepare this race from session selection first.")
            if self._cancel:
                return
            corners = None
            if self.want_corners:
                self.progress.emit("Loading official circuit metadata...")
                try:
                    import logging
                    logging.getLogger("fastf1").setLevel(logging.CRITICAL)
                    from src.f1_data import enable_cache, load_session
                    enable_cache()
                    session = load_session(self.year, self.round_number, "R")
                    corners = session.get_circuit_info().corners
                    # Convert to list of dicts before passing to analysis so
                    # no DataFrame boolean evaluation can occur downstream.
                    if hasattr(corners, "to_dict"):
                        corners = corners.to_dict("records")
                except Exception as exc:  # noqa: BLE001 - fallback covers it
                    corners = None
                    self.progress.emit(f"Circuit metadata unavailable ({exc}); "
                                       "mechanisms fall back to unresolved.")
            if self._cancel:
                return
            self.progress.emit("Reconstructing battles and overtakes...")
            result = ba.analyze_session(
                payload,
                {"year": self.year, "round": self.round_number},
                corners=corners, cancelled=self._cancelled,
                progress=lambda s, d, t, m: self.progress.emit(m),
                return_matrices=True)
            if not self._cancel:
                self.finished.emit(result)
        except ba._Cancelled:  # noqa: SLF001 - cooperative cancellation
            return
        except Exception as exc:  # noqa: BLE001 - surfaced to the window
            import traceback
            tb = traceback.format_exc()
            print(tb)  # terminal output
            if not self._cancel:
                self.failed.emit(f"{exc}\n\n{tb}")


class BattleIntelWindow(QMainWindow):
    """Battle reconstruction and overtake classification explorer."""

    def __init__(self, year=None, round_number=None):
        super().__init__()
        default_year, default_round = _default_session()
        self.year = year or default_year
        self.round_number = round_number or default_round
        self._worker = None
        self._result = None
        self._span = None

        self.setWindowTitle("F1 Battles & Overtaking Intelligence")
        self.resize(1320, 880)
        self._setup_ui()
        self._load_session_options()

    # -- UI construction -------------------------------------------------
    def _setup_ui(self):
        central = QWidget()
        central.setStyleSheet(f"background-color: {_BG}; color: {_TEXT_COLOR};")
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setSpacing(8)
        root.setContentsMargins(12, 12, 12, 12)

        card = QFrame()
        card.setStyleSheet(f"background-color: {_PANEL_BG}; border-radius: 8px;")
        form = QHBoxLayout(card)
        form.setContentsMargins(10, 6, 10, 6)
        title = QLabel("BATTLES & OVERTAKES")
        title.setFont(QFont("Segoe UI", 12, QFont.Bold))
        form.addWidget(title)
        self.year_combo = QComboBox()
        self.round_combo = QComboBox()
        for name, combo in (("Year:", self.year_combo),
                            ("Round:", self.round_combo)):
            form.addWidget(QLabel(name))
            form.addWidget(combo)
        self.analyze_btn = QPushButton("Analyze race")
        self.analyze_btn.setCursor(Qt.PointingHandCursor)
        self.analyze_btn.clicked.connect(self._on_analyze)
        form.addWidget(self.analyze_btn)
        self.export_btn = QPushButton("Export report")
        self.export_btn.setEnabled(False)
        self.export_btn.clicked.connect(self._on_export)
        form.addWidget(self.export_btn)
        form.addStretch()
        root.addWidget(card)

        self.status_lbl = QLabel("Select a session, then Analyze race.")
        self.status_lbl.setStyleSheet(f"color: {_SUB_COLOR};")
        root.addWidget(self.status_lbl)

        splitter = QSplitter(Qt.Horizontal)
        splitter.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        self.figure, (self.ax_gap, self.ax_spd) = plt.subplots(
            2, 1, figsize=(8.5, 6.5), sharex=True)
        self.figure.patch.set_facecolor(_BG)
        for ax in (self.ax_gap, self.ax_spd):
            ax.set_facecolor(_BG)
            ax.tick_params(colors=_TEXT_COLOR, labelsize=8)
        self.ax_gap.set_ylabel("Gap (s)", color=_TEXT_COLOR, fontsize=9)
        self.ax_spd.set_ylabel("Speed km/h", color=_TEXT_COLOR, fontsize=9)
        self.ax_spd.set_xlabel("Race time (s)", color=_TEXT_COLOR, fontsize=9)
        self.figure.tight_layout()
        self.canvas = FigureCanvas(self.figure)
        splitter.addWidget(self.canvas)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        mode_title = QLabel("BATTLES / OVERTAKES")
        mode_title.setFont(QFont("Segoe UI", 10, QFont.Bold))
        right_layout.addWidget(mode_title)
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["Battles", "Overtakes"])
        self.mode_combo.currentTextChanged.connect(self._refresh_list)
        right_layout.addWidget(self.mode_combo)
        legend = QLabel(
            f"<span style='color:{_CLASS_COLORS[ba.CL_PASS]}'>■</span> on-track "
            f"pass &nbsp;<span style='color:{_CLASS_COLORS[ba.CL_PIT]}'>■</span> "
            f"pit cycle &nbsp;<span style='color:{_CLASS_COLORS[ba.CL_RETIRE]}'>"
            f"■</span> retirement &nbsp;<span "
            f"style='color:{_CLASS_COLORS[ba.CL_RACE_CONTROL]}'>■</span> "
            f"race control &nbsp;<span "
            f"style='color:{_CLASS_COLORS[ba.CL_UNRESOLVED]}'>■</span> unresolved")
        legend.setWordWrap(True)
        legend.setStyleSheet(f"color: {_SUB_COLOR}; font-size: 10px;")
        right_layout.addWidget(legend)
        self.events_table = QTableWidget(0, 4)
        self.events_table.setHorizontalHeaderLabels(
            ["Event", "Time (s)", "Classification", "Conf / Quality"])
        self.events_table.horizontalHeader().setStretchLastSection(True)
        self.events_table.setMaximumHeight(260)
        self.events_table.itemClicked.connect(self._on_row_clicked)
        right_layout.addWidget(self.events_table)
        find_title = QLabel("EVIDENCE DETAIL")
        find_title.setFont(QFont("Segoe UI", 10, QFont.Bold))
        right_layout.addWidget(find_title)
        self.detail = QTextBrowser()
        self.detail.setStyleSheet(
            f"background-color: {_PANEL_BG}; color: {_TEXT_COLOR};"
            "border: 1px solid #3A3F44; border-radius: 6px; padding: 6px;")
        self.detail.setOpenExternalLinks(False)
        right_layout.addWidget(self.detail, 1)
        self.warnings_lbl = QLabel("")
        self.warnings_lbl.setWordWrap(True)
        self.warnings_lbl.setStyleSheet(f"color: {_GAP_COL};")
        right_layout.addWidget(self.warnings_lbl)
        splitter.addWidget(right)
        splitter.setSizes([800, 500])
        root.addWidget(splitter, 1)

    # -- session options ---------------------------------------------------
    def _load_session_options(self):
        import logging
        logging.getLogger("fastf1").setLevel(logging.CRITICAL)
        from src.f1_data import enable_cache
        from src.lib.season import get_season

        try:
            enable_cache()
            import fastf1
            schedule = fastf1.get_event_schedule(get_season())
            rounds = []
            for _, event in schedule.iterrows():
                try:
                    if event.is_testing():
                        continue
                    rounds.append((int(event["RoundNumber"]),
                                   str(event["EventName"])))
                except Exception:  # noqa: BLE001 - skip bad rows
                    continue
            self._rounds = sorted(rounds)
        except Exception:  # noqa: BLE001 - offline fallback
            self._rounds = [(self.round_number, f"Round {self.round_number}")]
        years = [str(y) for y in range(2018, get_season() + 1)]
        self.year_combo.addItems(years)
        self.year_combo.setCurrentText(str(self.year))
        self.year_combo.currentTextChanged.connect(self._refresh_rounds)
        self._refresh_rounds()

    def _refresh_rounds(self, _text=None):
        self.round_combo.blockSignals(True)
        self.round_combo.clear()
        for number, name in self._rounds:
            self.round_combo.addItem(f"R{number}: {name}", number)
        index = self.round_combo.findData(self.round_number)
        self.round_combo.setCurrentIndex(max(0, index))
        self.round_combo.blockSignals(False)

    # -- analysis ----------------------------------------------------------
    def _on_analyze(self):
        self._retire_worker()
        try:
            year = int(self.year_combo.currentText())
        except ValueError:
            year = self.year
        round_number = self.round_combo.currentData() or self.round_number
        self.analyze_btn.setEnabled(False)
        self.export_btn.setEnabled(False)
        self.status_lbl.setText("Analyzing...")
        self._worker = BattleAnalysisWorker(year, round_number, parent=self)
        self._worker.progress.connect(self.status_lbl.setText)
        self._worker.finished.connect(self._on_result)
        self._worker.failed.connect(self._on_failed)
        self._worker._wired = True
        self._worker.start()

    def _retire_worker(self):
        worker = self._worker
        self._worker = None
        if worker is None:
            return
        if getattr(worker, "_wired", False):
            for signal, slot in ((worker.progress, self.status_lbl.setText),
                                 (worker.finished, self._on_result),
                                 (worker.failed, self._on_failed)):
                try:
                    signal.disconnect(slot)
                except (TypeError, RuntimeError):
                    pass
            worker._wired = False
        if worker.isRunning():
            worker.request_cancel()
            worker.wait(8000)
            if worker.isRunning():
                worker.terminate()
                worker.wait()
        worker.deleteLater()

    def _stop_worker(self):
        self._retire_worker()

    def closeEvent(self, event):  # noqa: N802 (Qt naming)
        self._stop_worker()
        super().closeEvent(event)

    def _on_failed(self, message):
        self.analyze_btn.setEnabled(True)
        self.status_lbl.setText("Analysis failed.")
        QMessageBox.critical(self, "Analysis error", message)

    def _on_result(self, result):
        self.analyze_btn.setEnabled(True)
        self._result = result
        counts = result["counts"]
        self.status_lbl.setText(
            f"{counts.get('battles', 0)} battles, "
            f"{counts.get(ba.CL_PASS, 0)} supported passes, "
            f"{counts.get(ba.CL_PIT, 0)} pit-cycle, "
            f"{counts.get(ba.CL_RETIRE, 0)} retirement, "
            f"{counts.get(ba.CL_RACE_CONTROL, 0)} race-control, "
            f"{counts.get(ba.CL_UNRESOLVED, 0)} unresolved.")
        self._refresh_list()
        self.export_btn.setEnabled(True)
        warnings = result.get("warnings", [])
        self.warnings_lbl.setText("Warnings: " + "; ".join(warnings)
                                  if warnings else "No warnings.")

    # -- rendering (analysis results only) ----------------------------------
    def _current_items(self):
        if self._result is None:
            return []
        if self.mode_combo.currentText() == "Battles":
            return self._result["battles"]
        return [v for v in self._result["events"] if v.get("confirmed")]

    def _refresh_list(self):
        items = self._current_items()
        table = self.events_table
        table.setRowCount(len(items))
        for i, item in enumerate(items):
            if self.mode_combo.currentText() == "Battles":
                label = f"{item['id']}: {' vs '.join(item['pair'])}"
                t = item["start_t"]
                cls = item["resolution"]
                conf = item["quality"].get("aggregate", "?")
            else:
                label = (f"{item['event_id']}: {item['overtaker']} past "
                         f"{item['overtaken']}")
                t = item["t_flip"]
                cls = item["classification"]
                conf = item.get("classification_confidence", "?")
            table.setItem(i, 0, QTableWidgetItem(label))
            table.setItem(i, 1, QTableWidgetItem(f"{t:.1f}"))
            colored = QTableWidgetItem(_readable(cls))
            colored.setForeground(
                QColor(_CLASS_COLORS.get(cls, "#FFFFFF")))
            table.setItem(i, 2, colored)
            table.setItem(i, 3, QTableWidgetItem(str(conf)))
            table.item(i, 0).setData(Qt.UserRole, i)
        if items:
            table.selectRow(0)
            self._show_item(0)

    def _on_row_clicked(self, table_item):
        self._show_item(table_item.row())

    def _show_item(self, row):
        items = self._current_items()
        if row < 0 or row >= len(items):
            return
        item = items[row]
        if self.mode_combo.currentText() == "Battles":
            self._show_battle(item)
        else:
            self._show_event(item)

    def _battle_series(self, battle):
        """Gap + speed traces for one battle from the result payload."""
        result = self._result
        matrices = result.get("_matrices")
        if matrices is None:
            return None
        times, series = matrices["times"], matrices["series"]
        code_a, code_b = battle["pair"]
        start, end = battle["start_step"], battle["end_step"]
        span = np.arange(max(0, start - 50), min(len(times), end + 51))
        gaps = np.array([ba._step_gap(series, code_a, code_b, int(s))  # noqa: SLF001
                         for s in span])
        return {"t": times[span],
                "gap": gaps,
                "speed_a": series[code_a]["speed"][span],
                "speed_b": series[code_b]["speed"][span],
                "code_a": code_a, "code_b": code_b}

    def _show_battle(self, battle):
        for ax in (self.ax_gap, self.ax_spd):
            ax.clear()
            ax.set_facecolor(_BG)
            ax.tick_params(colors=_TEXT_COLOR, labelsize=8)
        data = self._battle_series(battle)
        if data is not None:
            self.ax_gap.plot(data["t"], data["gap"], color=_GAP_COL,
                             linewidth=1.2, label="gap (s)")
            self.ax_gap.axhline(ba.BATTLE_ENTER_S, color="#555555",
                                linewidth=0.8, linestyle="--")
            self.ax_gap.axhline(ba.BATTLE_EXIT_S, color="#555555",
                                linewidth=0.8, linestyle="--")
            self.ax_gap.set_ylabel("Gap (s)", color=_TEXT_COLOR, fontsize=9)
            self.ax_gap.legend(fontsize=8, facecolor=_PANEL_BG,
                               labelcolor=_TEXT_COLOR)
            self.ax_spd.plot(data["t"], data["speed_a"], color=_COL_A,
                             linewidth=1.0, label=f"{data['code_a']} km/h")
            self.ax_spd.plot(data["t"], data["speed_b"], color=_COL_B,
                             linewidth=1.0, label=f"{data['code_b']} km/h")
            self.ax_spd.set_ylabel("Speed km/h", color=_TEXT_COLOR, fontsize=9)
            self.ax_spd.set_xlabel("Race time (s)", color=_TEXT_COLOR,
                                   fontsize=9)
            self.ax_spd.legend(fontsize=8, facecolor=_PANEL_BG,
                               labelcolor=_TEXT_COLOR)
            for row in battle["transitions"]:
                for ax in (self.ax_gap, self.ax_spd):
                    ax.axvline(row["t"], color="#FFFFFF", linewidth=0.8,
                               alpha=0.5)
        self.figure.tight_layout()
        self.canvas.draw_idle()
        pair = battle["pair"]
        self._show_finding_text(
            battle["id"], battle["resolution"],
            f"{pair[0]} vs {pair[1]} — {_readable(battle['resolution'])}. "
            f"{battle['resolution_detail']}",
            {"duration_s": battle["end_t"] - battle["start_t"],
             "min_gap_s": battle["min_gap_s"],
             "max_closing_mps": battle.get("max_closing_mps"),
             "n_transitions": len(battle["transitions"]),
             "transitions": battle["transitions"],
             "interruptions": battle["interruptions"]},
            battle.get("quality", {}), battle.get("provenance", {}),
            tags={"A": pair[0], "B": pair[1]})

    def _show_event(self, verdict):
        for ax in (self.ax_gap, self.ax_spd):
            ax.clear()
            ax.set_facecolor(_BG)
            ax.tick_params(colors=_TEXT_COLOR, labelsize=8)
        result = self._result
        matrices = result.get("_matrices")
        if matrices is not None:
            times = matrices["times"]
            code_new, code_old = verdict["overtaker"], verdict["overtaken"]
            flip_step = verdict.get("step", 0)
            lo = max(0, int(np.searchsorted(times, verdict["t_flip"] - 40)))
            hi = min(len(times), int(np.searchsorted(times, verdict["t_flip"] + 15)))
            span = np.arange(lo, hi)
            gaps = np.array([ba._step_gap(matrices["series"], code_old, code_new, int(s))  # noqa: SLF001
                             if s < flip_step else
                             ba._step_gap(matrices["series"], code_new, code_old, int(s))  # noqa: SLF001
                             for s in span])
            self.ax_gap.plot(times[span], gaps, color=_GAP_COL, linewidth=1.2)
            self.ax_gap.axvline(verdict["t_flip"], color="#FFFFFF",
                                linewidth=1.0)
            self.ax_gap.set_ylabel("Gap (s)", color=_TEXT_COLOR, fontsize=9)
            for code, color in ((code_new, _COL_A), (code_old, _COL_B)):
                self.ax_spd.plot(times[span],
                                 matrices["series"][code]["speed"][span],
                                 color=color, linewidth=1.0,
                                 label=f"{code} km/h")
            self.ax_spd.set_ylabel("Speed km/h", color=_TEXT_COLOR, fontsize=9)
            self.ax_spd.set_xlabel("Race time (s)", color=_TEXT_COLOR,
                                   fontsize=9)
            self.ax_spd.legend(fontsize=8, facecolor=_PANEL_BG,
                               labelcolor=_TEXT_COLOR)
        self.figure.tight_layout()
        self.canvas.draw_idle()
        approach = verdict.get("approach") or {}
        approach_public = {k: v for k, v in approach.items()
                           if k != "corner_context"}
        self._show_finding_text(
            verdict["event_id"], verdict["classification"],
            f"{verdict['overtaker']} over {verdict['overtaken']}: "
            f"{verdict['rationale']}",
            {"interval": [verdict["t_last_pre"], verdict["t_first_post"]],
             "hold_s": verdict["hold_s"],
             "event_lap": approach.get("event_lap"),
             "event_dist_m": approach.get("event_dist_m"),
             "rel_speed_kmh": approach.get("rel_speed_kmh"),
             "closing_rate_mps": approach.get("closing_rate_mps"),
             "min_gap_s": approach.get("min_gap_s")},
            verdict.get("quality", {}), verdict.get("provenance", {}),
            tags={"A": verdict["overtaker"], "B": verdict["overtaken"]},
            extras=[
                ("Evidence", verdict.get("evidence", {})),
                ("Approach context", approach_public),
                ("Confidence", {
                    "Classification": verdict.get(
                        "classification_confidence")}),
                ("Alternatives considered", {
                    "Options": verdict.get("alternatives", [])}),
            ])

    def _show_finding_text(self, finding_id, kind, explanation, measured,
                           quality, provenance, tags=None, extras=None):
        self.detail.setHtml(render_evidence_html(
            finding_id, kind, explanation, measured, quality, provenance,
            tags=tags, extras=extras,
            kind_color=_CLASS_COLORS.get(kind, _ACCENT),
            kind_label=_readable(kind)))

    # -- export ---------------------------------------------------------------
    def _on_export(self):
        if self._result is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export battle report", "battle_intel_report.md",
            "Markdown (*.md)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(render_markdown_report(self._result))
            self.status_lbl.setText(f"Report exported to {path}")
        except OSError as exc:
            QMessageBox.critical(self, "Export error", str(exc))


def render_markdown_report(result):
    """Deterministic markdown rendering of a battle-analysis result."""
    ident = result["identity"]
    counts = result["counts"]
    lines = [
        f"# Battles & Overtaking — {ident.get('year')} round "
        f"{ident.get('round')} ({ident.get('event', '')})",
        "",
        f"Battles: {counts.get('battles', 0)} | Supported passes: "
        f"{counts.get(ba.CL_PASS, 0)} | Pit-cycle: {counts.get(ba.CL_PIT, 0)} | "
        f"Retirement: {counts.get(ba.CL_RETIRE, 0)} | Race-control: "
        f"{counts.get(ba.CL_RACE_CONTROL, 0)} | Unresolved: "
        f"{counts.get(ba.CL_UNRESOLVED, 0)}",
        f"Rules v{result['rules_version']}",
        "",
        "## Overtake events (confirmed)",
        "",
    ]
    for verdict in result["events"]:
        if verdict.get("transient") or not verdict.get("confirmed"):
            continue
        lines += [f"### {verdict['event_id']}: {verdict['overtaker']} past "
                  f"{verdict['overtaken']} — {verdict['classification']}",
                  f"t {verdict['t_last_pre']:.1f}–{verdict['t_first_post']:.1f} s, "
                  f"hold {verdict['hold_s']:.1f} s, "
                  f"confidence {verdict['classification_confidence']}",
                  f"Evidence: {verdict['evidence']}",
                  f"Alternatives: {verdict['alternatives']}",
                  f"Quality: {verdict['quality'].get('aggregate')}", ""]
    lines += ["## Battles (with range transitions)", ""]
    for battle in result["battles"]:
        if not battle["transitions"]:
            continue
        lines.append(
            f"- {battle['id']}: {' vs '.join(battle['pair'])} "
            f"{battle['start_t']:.0f}–{battle['end_t']:.0f} s, "
            f"min gap {battle['min_gap_s']} s, {battle['resolution']}")
    lines += ["", "## Warnings", ""]
    lines += [f"- {warning}" for warning in result.get("warnings", [])]
    return "\n".join(lines) + "\n"


def main():  # python -m src.insights.battle_intel_window
    import argparse

    parser = argparse.ArgumentParser(description="Battles & Overtaking Intelligence")
    parser.add_argument("--year", type=int, default=None)
    parser.add_argument("--round", type=int, default=None)
    parser.add_argument("--headless", action="store_true",
                        help="print summary without opening the window")
    args = parser.parse_args()
    year, rnd = args.year, args.round
    if year is None or rnd is None:
        year, rnd = _default_session()

    if args.headless:
        from src.services.replay_cache import load_race_replay
        from src.f1_data import enable_cache
        enable_cache()
        payload, meta = load_race_replay(year=year, round_number=rnd,
                                        session_type="R")
        if payload is None:
            raise SystemExit(f"No prepared data: {meta.get('reason')}")
        result = ba.analyze_session(payload, {"year": year, "round": rnd})
        print(render_markdown_report(result))
        return

    app = QApplication.instance() or QApplication(sys.argv)
    window = BattleIntelWindow(year, rnd)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
