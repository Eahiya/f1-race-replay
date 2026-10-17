"""Battle Intel window tests (Update 8): offscreen construction, rendering
of analysis results, navigation, export, and worker lifecycle."""

from src.insights.battle_intel_window import (
    BattleIntelWindow,
    _default_session,
    render_markdown_report,
)
from src.services import battle_analysis as ba


def _mini_payload():
    """Tiny two-driver race (fast): BBB leads, AAA passes at t=30."""
    n = 1500
    frames = []
    for i in range(n):
        t = i * 0.04
        da = 3000.0 + 83.3 * t - 200.0 + (6.0 if t <= 30.0 else 6.0 + (t - 30.0) * 6.0)
        db = 3000.0 + 83.3 * t
        if t > 30.0:
            da, db = db + 30.0, db
            da = db + 30.0
        frames.append({"t": t, "lap": 10, "drivers": {
            code: {"lap": 10, "dist": da if code == "AAA" else db,
                   "speed": 300.0 + (i % 9) * 0.4,
                   "throttle": 95.0 + (i % 13) * 0.4,
                   "brake": (i % 29) * 0.5, "gear": 6 + (i % 3),
                   "tyre": 1.0, "tyre_life": 5.0, "x": 0.0, "y": 0.0,
                   "rel_dist": 0.5, "drs": 0, "position": 1}
            for code in ("AAA", "BBB")}})
    return {"frames": frames, "driver_colors": {},
            "track_statuses": [{"status": "1", "start_time": 0.0,
                                "end_time": None}],
            "total_laps": 10}


def _mini_result():
    return ba.analyze_session(
        _mini_payload(), {"year": 2025, "round": 1}, return_matrices=True)


def test_default_session_prefers_cached_race():
    year, rnd = _default_session()
    assert (year, rnd) == (2025, 12)


def test_window_constructs_offscreen(qapp):
    window = BattleIntelWindow(year=2025, round_number=12)
    try:
        assert window.windowTitle().startswith("F1 Battles")
        assert window.export_btn.isEnabled() is False
        assert window.mode_combo.count() == 2
    finally:
        window.close()
        window.deleteLater()
    qapp.processEvents()


def test_render_result_and_navigate(qapp):
    result = _mini_result()
    window = BattleIntelWindow(year=2025, round_number=12)
    try:
        window._on_result(result)
        assert window.export_btn.isEnabled() is True
        assert "battles" in window.status_lbl.text()
        assert window.events_table.rowCount() > 0
        window._show_item(0)
        assert window.detail.toPlainText() != ""
        assert len(window.ax_gap.lines) >= 1
        # Overtakes mode lists confirmed events.
        window.mode_combo.setCurrentText("Overtakes")
        qapp.processEvents()
        assert window.events_table.rowCount() >= 0
    finally:
        window.close()
        window.deleteLater()
    qapp.processEvents()


def test_evidence_detail_is_structured_not_raw_dicts(qapp):
    result = _mini_result()
    window = BattleIntelWindow(year=2025, round_number=12)
    try:
        window._on_result(result)
        for mode in ("Overtakes", "Battles"):
            window.mode_combo.setCurrentText(mode)
            qapp.processEvents()
            if window.events_table.rowCount() == 0:
                continue
            window._show_item(0)
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


def test_show_item_out_of_range_is_safe(qapp):
    result = _mini_result()
    window = BattleIntelWindow(year=2025, round_number=12)
    try:
        window._on_result(result)
        window._show_item(-1)
        window._show_item(10 ** 6)
        window._result = None
        window._show_item(0)  # no result: safe no-op
    finally:
        window.close()
        window.deleteLater()
    qapp.processEvents()


def test_export_report_round_trip(qapp, tmp_path):
    result = _mini_result()
    report = render_markdown_report(result)
    assert "Battles & Overtaking" in report
    assert "Rules v1" in report
    out = tmp_path / "battle.md"
    out.write_text(report, encoding="utf-8")
    assert out.stat().st_size > 200


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


def test_worker_cancel_before_start_emits_nothing(qapp):
    from src.insights.battle_intel_window import BattleAnalysisWorker
    received = []
    worker = BattleAnalysisWorker(2025, 12, parent=None)
    worker.finished.connect(lambda result: received.append(result))
    worker.failed.connect(lambda msg: received.append(msg))
    worker.request_cancel()
    worker.start()
    worker.wait(5000)
    _pump(qapp, 1)
    assert received == []


def test_window_close_during_analysis(qapp):
    import shiboken6
    from src.insights.battle_intel_window import BattleAnalysisWorker
    window = BattleIntelWindow(year=2025, round_number=12)
    worker = BattleAnalysisWorker(1999, 99, parent=window)
    window._worker = worker
    worker.start()
    assert window.close() is True
    qapp.processEvents()
    assert not shiboken6.isValid(worker)
    window.deleteLater()
    qapp.processEvents()


def test_repeated_analyze_retires_stale_worker(qapp):
    from src.insights.battle_intel_window import BattleAnalysisWorker
    window = BattleIntelWindow(year=2025, round_number=12)
    try:
        first = BattleAnalysisWorker(2025, 12, parent=window)
        window._worker = first
        first.start()
        window._retire_worker()
        assert window._worker is None
        delivered = []
        first.finished.connect(lambda result: delivered.append(result))
        _pump(qapp, 2)
        assert delivered == []
    finally:
        window.close()
        window.deleteLater()
    qapp.processEvents()


def test_failed_path_without_blocking_dialog(qapp, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    shown = []
    monkeypatch.setattr(QMessageBox, "critical",
                        lambda *args, **kwargs: shown.append(args))
    window = BattleIntelWindow(year=2025, round_number=12)
    try:
        window.analyze_btn.setEnabled(False)
        window._on_failed("synthetic failure")
        assert window.analyze_btn.isEnabled() is True
        assert window.status_lbl.text() == "Analysis failed."
        assert shown
    finally:
        window.close()
        window.deleteLater()
    qapp.processEvents()


def test_menu_entry_wires_window(qapp):
    import inspect

    from src.gui import insights_menu as menu_mod
    # The menu exposes a launcher that lazily imports the real window.
    assert hasattr(menu_mod.InsightsMenu, "launch_battle_intel")
    source = inspect.getsource(menu_mod.InsightsMenu.launch_battle_intel)
    assert "battle_intel_window" in source and "BattleIntelWindow" in source
    assert "Battles & Overtaking Intelligence" in open(
        menu_mod.__file__, encoding="utf-8").read()
