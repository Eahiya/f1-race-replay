"""Quali Intel window tests (Update 7): offscreen construction, rendering
of real analysis results, finding navigation, and report export."""

import os
import pickle

import pytest

from src.insights.quali_intel_window import (
    QualiIntelWindow,
    _default_session,
    render_markdown_report,
)
from src.services import quali_analysis as qa


def _real_payload():
    path = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "computed_data", "2025_R12_Q_v2.pkl")
    if not os.path.exists(path):
        pytest.skip("prepared 2025 R12 qualifying cache unavailable")
    with open(path, "rb") as handle:
        return pickle.load(handle)["payload"]


def _real_result():
    payload = _real_payload()
    return qa.analyze(payload, {"year": 2025, "round": 12,
                                "event": "British Grand Prix"},
                      "Q3", "VER", "NOR", corners=None)


def test_default_session_prefers_cached_quali():
    year, rnd = _default_session()
    assert (year, rnd) == (2025, 12)


def test_window_constructs_offscreen(qapp):
    window = QualiIntelWindow(year=2025, round_number=12, segment="Q3")
    try:
        assert window.windowTitle().startswith("F1 Qualifying")
        assert window.session_combo_segment.currentText() == "Q3"
        assert window.export_btn.isEnabled() is False
    finally:
        window.close()
        window.deleteLater()
    qapp.processEvents()


def test_render_real_result_and_navigate(qapp):
    result = _real_result()
    window = QualiIntelWindow(year=2025, round_number=12, segment="Q3")
    try:
        window._on_result(result)
        assert window.export_btn.isEnabled() is True
        assert window.findings_list.count() == len(result["findings"])
        assert window.sections_table.rowCount() == len(result["section_rows"])
        # Click the first finding: detail shown and charts zoom to interval.
        item = window.findings_list.item(0)
        xlim_before = window.ax_delta.get_xlim()
        window._on_finding_clicked(item)
        assert window.detail.toPlainText() != ""
        assert window.ax_delta.get_xlim() != xlim_before
        # Charts contain the actual selected telemetry (no placeholders).
        assert len(window.ax_thr.lines) == 2
        assert len(window.ax_spd.lines) == 2
        assert len(window.ax_delta.lines) >= 1
    finally:
        window.close()
        window.deleteLater()
    qapp.processEvents()


def test_finding_detail_is_structured_not_raw_dicts(qapp):
    result = _real_result()
    window = QualiIntelWindow(year=2025, round_number=12, segment="Q3")
    try:
        window._on_result(result)
        if window.findings_list.count() == 0:
            pytest.skip("no findings to render")
        window._on_finding_clicked(window.findings_list.item(0))
        text = window.detail.toPlainText()
        assert "MEASURED" in text
        assert "DATA QUALITY" in text
        assert "PROVENANCE" in text
        # Raw Python container reprs must never leak into the panel.
        assert "{'" not in text
        assert "': " not in text
        assert "None" not in text
    finally:
        window.close()
        window.deleteLater()
    qapp.processEvents()


def test_export_report_round_trip(qapp, tmp_path):
    result = _real_result()
    report = render_markdown_report(result)
    assert "VER" in report and "NOR" in report
    assert "84.892" in report
    assert "causal confidence" in report.lower() or "Causal confidence" in report
    out = tmp_path / "report.md"
    out.write_text(report, encoding="utf-8")
    assert out.stat().st_size > 1000


def test_report_contains_warnings_and_provenance():
    result = _real_result()
    report = render_markdown_report(result)
    assert "lap_validity_unverified_not_retained" in report
    assert "markers_discarded" in report


def test_worker_cancel_before_start(qapp):
    from src.insights.quali_intel_window import QualiAnalysisWorker
    worker = QualiAnalysisWorker(2025, 12, "Q3", "VER", "NOR", parent=None)
    worker.request_cancel()
    assert worker._cancel is True
    worker.wait(1000)


def _pump(qapp, seconds, predicate=None):
    import time
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        qapp.processEvents()
        if predicate is not None and predicate():
            return True
        time.sleep(0.05)
    qapp.processEvents()
    return predicate() if predicate is not None else False


def test_worker_full_run_off_thread(qapp):
    """Worker completes without touching widgets; result via signal only."""
    from src.insights.quali_intel_window import QualiAnalysisWorker
    received = {}
    worker = QualiAnalysisWorker(2025, 12, "Q3", "VER", "NOR",
                                 want_corners=False, parent=None)
    worker.finished.connect(lambda result: received.update(result=result))
    worker.failed.connect(lambda msg: received.update(error=msg))
    worker.start()
    assert _pump(qapp, 60, lambda: "result" in received or "error" in received)
    worker.wait(5000)
    assert "error" not in received, received.get("error")
    assert len(received["result"]["findings"]) > 0
    # Worker holds no widget references.
    assert not any("QWidget" in type(value).__name__
                   for value in vars(worker).values()
                   if not isinstance(value, (str, int, bool)))


def test_worker_cancel_before_thread_start_emits_nothing(qapp):
    from src.insights.quali_intel_window import QualiAnalysisWorker
    received = []
    worker = QualiAnalysisWorker(2025, 12, "Q3", "VER", "NOR",
                                 want_corners=False, parent=None)
    worker.finished.connect(lambda result: received.append(result))
    worker.failed.connect(lambda msg: received.append(msg))
    worker.request_cancel()
    worker.start()
    worker.wait(5000)
    _pump(qapp, 1)
    assert received == []


def test_window_close_during_analysis(qapp):
    """Closing mid-run stops the worker instead of destroying it live."""
    window = QualiIntelWindow(year=2025, round_number=12, segment="Q3")
    from src.insights.quali_intel_window import QualiAnalysisWorker
    worker = QualiAnalysisWorker(2025, 12, "Q3", "VER", "NOR",
                                 want_corners=True, parent=window)
    window._worker = worker
    worker.start()
    assert window.close() is True
    qapp.processEvents()
    import shiboken6
    assert not shiboken6.isValid(worker)  # stopped and deleted: no live thread
    window.deleteLater()
    qapp.processEvents()


def test_repeated_analyze_retires_stale_worker(qapp):
    """A second run disconnects the first worker: no stale delivery."""
    window = QualiIntelWindow(year=2025, round_number=12, segment="Q3")
    try:
        from src.insights.quali_intel_window import QualiAnalysisWorker
        first = QualiAnalysisWorker(2025, 12, "Q3", "VER", "NOR",
                                    want_corners=False, parent=window)
        window._worker = first
        first.start()
        window._retire_worker()
        assert window._worker is None
        assert not first.isRunning()
        delivered = []
        first.finished.connect(lambda result: delivered.append(result))
        _pump(qapp, 2)  # any late signal would arrive here
        assert delivered == []
    finally:
        window.close()
        window.deleteLater()
    qapp.processEvents()


def test_full_gui_analyze_twice(qapp):
    """End-to-end: Analyze button -> worker -> rendered result, twice."""
    window = QualiIntelWindow(year=2025, round_number=12, segment="Q3")
    try:
        for _ in range(2):
            window._on_analyze()
            assert _pump(
                qapp, 90,
                lambda: window.export_btn.isEnabled()
                and "findings" in window.status_lbl.text())
            assert window.findings_list.count() > 0
    finally:
        window.close()
        window.deleteLater()
    qapp.processEvents()


def test_failed_path_without_blocking_dialog(qapp, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    shown = []
    monkeypatch.setattr(QMessageBox, "critical",
                        lambda *args, **kwargs: shown.append(args))
    window = QualiIntelWindow(year=2025, round_number=12, segment="Q3")
    try:
        window.analyze_btn.setEnabled(False)
        window._on_failed("synthetic failure")
        assert window.analyze_btn.isEnabled() is True
        assert window.status_lbl.text() == "Analysis failed."
        assert shown, "error must surface to the user"
    finally:
        window.close()
        window.deleteLater()
    qapp.processEvents()


def test_finding_navigation_with_incomplete_section(qapp):
    result = _real_result()
    window = QualiIntelWindow(year=2025, round_number=12, segment="Q3")
    try:
        window._on_result(result)
        from PySide6.QtWidgets import QListWidgetItem
        from PySide6.QtCore import Qt
        ghost = QListWidgetItem("ghost")
        ghost.setData(Qt.UserRole, "no_such_finding")
        window._on_finding_clicked(ghost)  # unknown id: safe no-op
        # Finding without an interval zooms nothing but still shows detail.
        flat = {"id": "x", "kind": "coverage", "section": {"label": "y"},
                "measured": {}, "evidence": {}, "interpretation": "nothing here",
                "measurement_confidence": "low", "causal_confidence": "withheld",
                "missing_evidence": [], "next_steps": [],
                "quality": {}, "provenance": {}}
        window._show_finding(flat)
        assert "nothing here" in window.detail.toPlainText()
    finally:
        window.close()
        window.deleteLater()
    qapp.processEvents()
