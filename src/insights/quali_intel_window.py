"""Qualifying Performance Intelligence window (Update 7).

Standalone desktop analysis over prepared qualifying payloads: Q1/Q2/Q3
selection, reference/comparison driver selection, full-lap throttle and
speed charts, cumulative time-delta chart, corner/sector section
breakdown, evidence-backed findings, quality warnings, and markdown
report export. All calculations run locally via
``src.services.quali_analysis``; charts render actual selected telemetry
— no placeholder series. Analysis runs in a worker thread so the GUI
stays responsive; closing the window stops the worker safely.
"""

import sys

import matplotlib
matplotlib.use("QtAgg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication, QComboBox, QFileDialog, QFrame, QHBoxLayout, QLabel,
    QListWidget, QListWidgetItem, QMainWindow, QMessageBox, QPushButton,
    QSizePolicy, QSplitter, QTableWidget, QTableWidgetItem, QTextBrowser,
    QVBoxLayout, QWidget,
)

from src.services import quali_analysis as qa
from src.services.evidence_render import _humanize, render_evidence_html
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

_DELTA_COL = "#F1C40F"

_KIND_COLORS = {
    "lap_delta": _ACCENT,
    "section_delta": "#56A8FF",
    "throttle_pickup": "#28C77A",
    "throttle_lift": "#F4B942",
    "coverage": "#888888",
}


def _default_session():
    """Newest locally cached qualifying session, else season default."""
    import glob
    import re

    from src.lib.season import get_season

    best = None
    for path in glob.glob("computed_data/*_Q_v2.pkl"):
        match = re.search(r"(\d{4})_R(\d+)_[A-Z]+_v\d+\.pkl$", path)
        if match:
            key = (int(match.group(1)), int(match.group(2)))
            if best is None or key > best:
                best = key
    if best:
        return best[0], best[1]
    return get_season(), 12


class QualiAnalysisWorker(QThread):
    """Load payload + corners and run analysis off the GUI thread."""

    progress = Signal(str)
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, year, round_number, segment, code_a, code_b,
                 want_corners=True, parent=None):
        super().__init__(parent)
        self.year = year
        self.round_number = round_number
        self.segment = segment
        self.code_a = code_a
        self.code_b = code_b
        self.want_corners = want_corners
        self._cancel = False

    def request_cancel(self):
        self._cancel = True

    def run(self):
        if self._cancel:
            return  # cancelled before the thread started: emit nothing
        try:
            self.progress.emit("Loading prepared qualifying data...")
            from src.services.replay_cache import load_prepared
            payload, meta = load_prepared(
                kind="quali", year=self.year, round_number=self.round_number,
                session_type=qa.session_type_for_segment(self.segment))
            if payload is None:
                raise ValueError(
                    f"No prepared {self.segment} data for {self.year} round "
                    f"{self.round_number} ({meta.get('reason', 'cache miss')}). "
                    "Prepare this qualifying from session selection first.")
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
                    session = load_session(self.year, self.round_number, "Q")
                    corners = session.get_circuit_info().corners
                except Exception as exc:  # noqa: BLE001 - fallback covers it
                    corners = None
                    self.progress.emit(f"Circuit metadata unavailable ({exc}); "
                                       "using distance sections.")
            if self._cancel:
                return
            self.progress.emit("Aligning laps and generating findings...")
            result = qa.analyze(
                payload, {"year": self.year, "round": self.round_number,
                          "event": f"Round {self.round_number}"},
                self.segment, self.code_a, self.code_b, corners=corners)
            if not self._cancel:
                self.finished.emit(result)
        except Exception as exc:  # noqa: BLE001 - surfaced to the dialog
            if not self._cancel:
                self.failed.emit(str(exc))


class QualiIntelWindow(QMainWindow):
    """Q1/Q2/Q3 driver comparison with time-loss attribution."""

    def __init__(self, year=None, round_number=None, segment="Q3",
                 code_a=None, code_b=None):
        super().__init__()
        default_year, default_round = _default_session()
        self.year = year or default_year
        self.round_number = round_number or default_round
        self._pending_a = code_a
        self._pending_b = code_b
        self._worker = None
        self._result = None
        self._span = None

        self.setWindowTitle("F1 Qualifying Performance Intelligence")
        self.resize(1280, 860)
        self._setup_ui()
        self._load_session_options()
        self.session_combo_segment.setCurrentText(segment
                                                  if segment in ("Q1", "Q2", "Q3")
                                                  else "Q3")

    # -- UI construction -------------------------------------------------
    def _setup_ui(self):
        central = QWidget()
        central.setStyleSheet(f"background-color: {_BG}; color: {_TEXT_COLOR};")
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setSpacing(8)
        root.setContentsMargins(12, 12, 12, 12)

        # Session / driver selection card.
        card = QFrame()
        card.setStyleSheet(f"background-color: {_PANEL_BG}; border-radius: 8px;")
        form = QHBoxLayout(card)
        form.setContentsMargins(10, 6, 10, 6)
        title = QLabel("QUALIFYING INTELLIGENCE")
        title.setFont(QFont("Segoe UI", 12, QFont.Bold))
        form.addWidget(title)
        self.year_combo = QComboBox()
        self.round_combo = QComboBox()
        self.session_combo_segment = QComboBox()
        self.session_combo_segment.addItems(["Q1", "Q2", "Q3"])
        for name, combo in (("Year:", self.year_combo),
                            ("Round:", self.round_combo),
                            ("Segment:", self.session_combo_segment)):
            label = QLabel(name)
            form.addWidget(label)
            form.addWidget(combo)
        self.combo_a = QComboBox()
        self.combo_b = QComboBox()
        for name, combo in (("Ref:", self.combo_a), ("Cmp:", self.combo_b)):
            label = QLabel(name)
            form.addWidget(label)
            form.addWidget(combo)
        self.analyze_btn = QPushButton("Analyze")
        self.analyze_btn.setCursor(Qt.PointingHandCursor)
        self.analyze_btn.clicked.connect(self._on_analyze)
        form.addWidget(self.analyze_btn)
        self.export_btn = QPushButton("Export report")
        self.export_btn.setEnabled(False)
        self.export_btn.clicked.connect(self._on_export)
        form.addWidget(self.export_btn)
        form.addStretch()
        root.addWidget(card)

        self.status_lbl = QLabel("Select a session, then Analyze.")
        self.status_lbl.setStyleSheet(f"color: {_SUB_COLOR};")
        root.addWidget(self.status_lbl)

        # Main splitter: charts | sections+findings.
        splitter = QSplitter(Qt.Horizontal)
        splitter.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        self.figure, (self.ax_thr, self.ax_spd,
                      self.ax_delta) = plt.subplots(
            3, 1, figsize=(8.5, 6.5), sharex=True,
            gridspec_kw={"height_ratios": [1, 1, 1]})
        self.figure.patch.set_facecolor(_BG)
        for ax in (self.ax_thr, self.ax_spd, self.ax_delta):
            ax.set_facecolor(_BG)
            ax.tick_params(colors=_TEXT_COLOR, labelsize=8)
            for spine in ax.spines.values():
                spine.set_color("#444444")
        self.ax_thr.set_ylabel("Throttle %", color=_TEXT_COLOR, fontsize=9)
        self.ax_spd.set_ylabel("Speed km/h", color=_TEXT_COLOR, fontsize=9)
        self.ax_delta.set_ylabel("Delta s (A-B)", color=_TEXT_COLOR, fontsize=9)
        self.ax_delta.set_xlabel("Distance (m)", color=_TEXT_COLOR, fontsize=9)
        self.figure.tight_layout()
        self.canvas = FigureCanvas(self.figure)
        splitter.addWidget(self.canvas)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        warn_title = QLabel("SECTIONS")
        warn_title.setFont(QFont("Segoe UI", 10, QFont.Bold))
        right_layout.addWidget(warn_title)
        self.sections_table = QTableWidget(0, 4)
        self.sections_table.setHorizontalHeaderLabels(
            ["Section", "Shift (s)", "Start", "End"])
        self.sections_table.horizontalHeader().setStretchLastSection(True)
        self.sections_table.setMaximumHeight(220)
        right_layout.addWidget(self.sections_table)
        find_title = QLabel("FINDINGS (click to navigate)")
        find_title.setFont(QFont("Segoe UI", 10, QFont.Bold))
        right_layout.addWidget(find_title)
        self.findings_list = QListWidget()
        self.findings_list.itemClicked.connect(self._on_finding_clicked)
        right_layout.addWidget(self.findings_list, 2)
        self.detail = QTextBrowser()
        self.detail.setMaximumHeight(220)
        right_layout.addWidget(self.detail, 1)
        self.warnings_lbl = QLabel("")
        self.warnings_lbl.setWordWrap(True)
        self.warnings_lbl.setStyleSheet(f"color: {_DELTA_COL};")
        right_layout.addWidget(self.warnings_lbl)
        splitter.addWidget(right)
        splitter.setSizes([800, 460])
        root.addWidget(splitter, 1)

    # -- session/driver loading ------------------------------------------
    def _load_session_options(self):
        import logging
        logging.getLogger("fastf1").setLevel(logging.CRITICAL)
        from src.f1_data import enable_cache
        from src.lib.season import get_season

        try:
            enable_cache()
            import fastf1
            year = get_season()
            schedule = fastf1.get_event_schedule(year)
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
        self.year_combo.currentTextChanged.connect(self._on_year_changed)
        self._refresh_rounds()
        self.year_combo.setCurrentText(str(self.year))

    def _refresh_rounds(self):
        self.round_combo.blockSignals(True)
        self.round_combo.clear()
        for number, name in self._rounds:
            self.round_combo.addItem(f"R{number}: {name}", number)
        index = self.round_combo.findData(self.round_number)
        self.round_combo.setCurrentIndex(max(0, index))
        self.round_combo.blockSignals(False)

    def _on_year_changed(self, _text):
        self._refresh_rounds()

    # -- analysis ----------------------------------------------------------
    def _on_analyze(self):
        self._stop_worker()
        try:
            year = int(self.year_combo.currentText())
        except ValueError:
            year = self.year
        round_number = self.round_combo.currentData() or self.round_number
        segment = self.session_combo_segment.currentText()
        code_a = self.combo_a.currentText() or self._pending_a or "VER"
        code_b = self.combo_b.currentText() or self._pending_b or "NOR"
        # Populate driver combos from cache when possible (fast, local).
        self._refresh_drivers(year, round_number, segment, code_a, code_b)
        code_a = self.combo_a.currentText()
        code_b = self.combo_b.currentText()
        if not code_a or not code_b:
            QMessageBox.warning(self, "No drivers",
                                "No prepared qualifying drivers found.")
            return
        self.analyze_btn.setEnabled(False)
        self.export_btn.setEnabled(False)
        self.status_lbl.setText("Analyzing...")
        self._worker = QualiAnalysisWorker(year, round_number, segment,
                                           code_a, code_b, parent=self)
        self._worker.progress.connect(self.status_lbl.setText)
        self._worker.finished.connect(self._on_result)
        self._worker.failed.connect(self._on_failed)
        self._worker._wired = True
        self._worker.start()

    def _refresh_drivers(self, year, round_number, segment, code_a, code_b):
        try:
            from src.services.replay_cache import load_prepared
            payload, _ = load_prepared(
                kind="quali", year=year, round_number=round_number,
                session_type=qa.session_type_for_segment(segment))
            codes = sorted(payload["telemetry"].keys()) if payload else []
        except Exception:  # noqa: BLE001 - combos stay as-is
            codes = []
        if codes:
            for combo, preferred in ((self.combo_a, code_a),
                                     (self.combo_b, code_b)):
                combo.blockSignals(True)
                combo.clear()
                combo.addItems(codes)
                combo.setCurrentText(preferred if preferred in codes else codes[0])
                combo.blockSignals(False)
            if len(codes) >= 2 and code_a == code_b:
                self.combo_b.setCurrentText(codes[1] if codes[0] == code_a else codes[0])

    def _retire_worker(self):
        """Disconnect, stop and schedule deletion of the current worker.

        Disconnecting first guarantees a late signal from the outgoing run
        can never drive the UI after a newer analysis has started.
        """
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
            worker.wait(5000)
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
        self.status_lbl.setText(
            f"{result['selection']['A']['driver']} vs "
            f"{result['selection']['B']['driver']} "
            f"{result['segment']} — "
            f"{len(result['findings'])} findings.")
        self._render(result)
        self.export_btn.setEnabled(True)

    # -- rendering (actual selected telemetry only) -------------------------
    def _render(self, result):
        for ax in (self.ax_thr, self.ax_spd, self.ax_delta):
            ax.clear()
            ax.set_facecolor(_BG)
            ax.tick_params(colors=_TEXT_COLOR, labelsize=8)
        grid = result["aligned"]["grid"]
        traces_a = result["aligned"]["drivers"]["A"]["traces"]
        traces_b = result["aligned"]["drivers"]["B"]["traces"]
        code_a = result["selection"]["A"]["driver"]
        code_b = result["selection"]["B"]["driver"]
        self.ax_thr.plot(grid, traces_a["throttle"], color=_COL_A,
                         linewidth=1.0, label=f"{code_a} throttle %")
        self.ax_thr.plot(grid, traces_b["throttle"], color=_COL_B,
                         linewidth=1.0, label=f"{code_b} throttle %")
        self.ax_thr.set_ylabel("Throttle %", color=_TEXT_COLOR, fontsize=9)
        self.ax_thr.legend(fontsize=8, facecolor=_PANEL_BG, labelcolor=_TEXT_COLOR)
        self.ax_thr.set_ylim(-5, 110)
        self.ax_spd.plot(grid, traces_a["speed"], color=_COL_A,
                         linewidth=1.0, label=f"{code_a} km/h")
        self.ax_spd.plot(grid, traces_b["speed"], color=_COL_B,
                         linewidth=1.0, label=f"{code_b} km/h")
        self.ax_spd.set_ylabel("Speed km/h", color=_TEXT_COLOR, fontsize=9)
        self.ax_spd.legend(fontsize=8, facecolor=_PANEL_BG, labelcolor=_TEXT_COLOR)
        self.ax_delta.plot(grid, result["delta"], color=_DELTA_COL, linewidth=1.2)
        self.ax_delta.axhline(0.0, color="#555555", linewidth=0.8)
        self.ax_delta.set_ylabel("Delta s (A-B)", color=_TEXT_COLOR, fontsize=9)
        self.ax_delta.set_xlabel("Distance (m)", color=_TEXT_COLOR, fontsize=9)
        for overlay in result["sections"]["drs_overlays"]:
            for ax in (self.ax_thr, self.ax_spd):
                ax.axvspan(overlay["d_start"], overlay["d_end"],
                           color="#1E7A34", alpha=0.18)
        for row in result["section_rows"]:
            if row["section_delta_s"] is None:
                continue
            color = "#2ECC71" if row["section_delta_s"] < 0 else "#E74C3C"
            self.ax_delta.axvspan(row["d_start"], row["d_end"], color=color,
                                  alpha=0.10)
        self._span = None
        self.figure.tight_layout()
        self.canvas.draw_idle()
        self._render_sections(result)
        self._render_findings(result)
        warnings = (result["comparability"]["warnings"]
                    + result["aligned"]["warnings"]
                    + result["corners"]["warnings"])
        self.warnings_lbl.setText("Warnings: " + "; ".join(warnings)
                                  if warnings else "No warnings.")

    def _render_sections(self, result):
        rows = result["section_rows"]
        self.sections_table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            shift = row["section_delta_s"]
            self.sections_table.setItem(i, 0, QTableWidgetItem(row["label"]))
            self.sections_table.setItem(
                i, 1, QTableWidgetItem(
                    f"{shift:+.3f} s" if shift is not None else "n/a"))
            for j, key in enumerate(("delta_start_s", "delta_end_s"), start=2):
                value = row[key]
                self.sections_table.setItem(
                    i, j, QTableWidgetItem(
                        f"{value:+.3f} s" if value is not None else "n/a"))

    def _render_findings(self, result):
        self.findings_list.clear()
        for finding in result["findings"]:
            label = f"[{_humanize(finding['kind'])}] {finding['interpretation']}"
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, finding["id"])
            self.findings_list.addItem(item)
        if result["findings"]:
            self.findings_list.setCurrentRow(0)
            self._show_finding(result["findings"][0])

    def _show_finding(self, finding):
        kind = finding.get("kind")
        section = finding.get("section", {})
        section_trimmed = {key: section[key] for key in ("label", "d_start", "d_end")
                           if key in section}
        self.detail.setHtml(render_evidence_html(
            finding.get("id"), kind, finding.get("interpretation", ""),
            finding.get("measured", {}),
            finding.get("quality", {}), finding.get("provenance", {}),
            extras=[
                ("Evidence", finding.get("evidence", {})),
                ("Section", section_trimmed),
                ("Confidence", {
                    "Measurement": finding.get("measurement_confidence"),
                    "Causal": finding.get("causal_confidence"),
                }),
                ("Missing evidence", {
                    "Items": finding.get("missing_evidence", [])}),
                ("Next steps", {"Steps": finding.get("next_steps", [])}),
            ],
            kind_color=_KIND_COLORS.get(kind, _ACCENT),
            kind_label=_humanize(kind)))

    def _on_finding_clicked(self, item):
        if self._result is None:
            return
        finding_id = item.data(Qt.UserRole)
        finding = next((f for f in self._result["findings"]
                        if f["id"] == finding_id), None)
        if finding is None:
            return
        self._show_finding(finding)
        section = finding.get("section", {})
        if "d_start" in section and "d_end" in section:
            if self._span is not None:
                try:
                    self._span.remove()
                except Exception:  # noqa: BLE001 - already gone
                    pass
            span = max(120.0, (section["d_end"] - section["d_start"]) * 1.2)
            center = (section["d_start"] + section["d_end"]) / 2.0
            self._span = self.ax_delta.axvspan(center - span / 2.0,
                                               center + span / 2.0,
                                               color="#FFFFFF", alpha=0.12)
            self.ax_delta.set_xlim(center - span, center + span)
            self.ax_thr.set_xlim(center - span, center + span)
            self.ax_spd.set_xlim(center - span, center + span)
            self.canvas.draw_idle()

    # -- export ---------------------------------------------------------------
    def _on_export(self):
        if self._result is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export analysis report", "quali_intel_report.md",
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
    """Deterministic markdown rendering of an AnalysisResult."""
    sel_a, sel_b = result["selection"]["A"], result["selection"]["B"]
    ident = result["identity"]
    lines = [
        f"# Qualifying Intelligence — {sel_a['driver']} vs {sel_b['driver']}",
        "",
        f"Session: {ident.get('year')} round {ident.get('round')} "
        f"{result['segment']} ({ident.get('event', '')})",
        f"Laps: {sel_a['driver']} {sel_a['lap_time_s']:.3f} s vs "
        f"{sel_b['driver']} {sel_b['lap_time_s']:.3f} s",
        f"Compounds: {sel_a['compound']} vs {sel_b['compound']} | "
        f"Validity: {sel_a['validity']} | Rules v{result['rules_version']}",
        "",
        "## Comparability",
        f"Comparable: {result['comparability']['comparable']}",
        "Warnings: " + ("; ".join(result["comparability"]["warnings"]) or "none"),
        "",
        "## Sections",
    ]
    for row in result["section_rows"]:
        shift = row["section_delta_s"]
        lines.append(
            f"- {row['label']} [{row['d_start']:.0f}–{row['d_end']:.0f} m]: "
            f"{shift:+.3f} s" if shift is not None else
            f"- {row['label']}: unresolved (insufficient coverage)")
    lines += ["", "## Findings", ""]
    for finding in result["findings"]:
        lines += [f"### {finding['id']}",
                  finding["interpretation"],
                  f"- Measured: {finding['measured']}",
                  f"- Evidence: {finding['evidence']}",
                  f"- Measurement confidence: {finding['measurement_confidence']}; "
                  f"Causal confidence: {finding['causal_confidence']}",
                  f"- Quality: {finding['quality']}",
                  f"- Provenance: {finding['provenance']}",
                  f"- Missing evidence: {finding['missing_evidence']}",
                  f"- Next: {finding['next_steps']}", ""]
    lines += ["## Corner validation",
              f"Mode: {result['corners']['mode']}",
              "Validated: " + ", ".join(
                  str(c.get('number')) for c in result["corners"]["validated"]) or "none",
              "Warnings: " + ("; ".join(result["corners"]["warnings"]) or "none")]
    return "\n".join(lines) + "\n"


def main():  # python -m src.insights.quali_intel_window
    import argparse

    parser = argparse.ArgumentParser(description="Qualifying Performance Intelligence")
    parser.add_argument("--year", type=int, default=None)
    parser.add_argument("--round", type=int, default=None)
    parser.add_argument("--segment", default="Q3")
    parser.add_argument("--a", default=None)
    parser.add_argument("--b", default=None)
    parser.add_argument("--headless", action="store_true",
                        help="print findings without opening the window")
    args = parser.parse_args()
    year, rnd = args.year, args.round
    if year is None or rnd is None:
        year, rnd = _default_session()

    if args.headless:
        from src.services.replay_cache import load_prepared
        from src.f1_data import enable_cache
        enable_cache()
        payload, meta = load_prepared(
            kind="quali", year=year, round_number=rnd,
            session_type=qa.session_type_for_segment(args.segment))
        if payload is None:
            raise SystemExit(f"No prepared data: {meta.get('reason')}")
        codes = sorted(payload["telemetry"].keys())
        code_a = args.a or codes[0]
        code_b = args.b or (codes[1] if len(codes) > 1 else codes[0])
        result = qa.analyze(payload, {"year": year, "round": rnd},
                            args.segment, code_a, code_b, corners=None)
        print(render_markdown_report(result))
        return

    app = QApplication.instance() or QApplication(sys.argv)
    window = QualiIntelWindow(year, rnd, args.segment, args.a, args.b)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
