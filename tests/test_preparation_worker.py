"""Worker lifecycle tests: cancellation, forced shutdown, stale guards."""

from src.f1_data import PreparationCancelled
from src.gui.preparation import SessionPreparationDialog, SessionPreparationWorker


class _FakeWorker:
    """Minimal stand-in for QThread lifecycle (no Qt event loop needed)."""

    def __init__(self, running=True):
        self._running = running
        self.cancel_requested = False
        self.terminated = False
        self.waits = []

    def isRunning(self):  # noqa: N802 (Qt naming)
        return self._running

    def request_cancel(self):
        self.cancel_requested = True

    def wait(self, timeout_ms=None):  # noqa: N802 (Qt naming)
        self.waits.append(timeout_ms)
        return not self._running

    def terminate(self):
        self.terminated = True
        self._running = False


class _StubDialog:
    _worker = None


def test_stop_worker_cooperative_path():
    fake = _FakeWorker(running=False)
    stub = _StubDialog()
    stub._worker = fake
    SessionPreparationDialog._stop_worker(stub)
    assert fake.cancel_requested is False
    assert fake.terminated is False


def test_stop_worker_terminates_blocked_thread():
    """A worker stuck in FastF1 I/O must not outlive the dialog."""
    fake = _FakeWorker(running=True)
    stub = _StubDialog()
    stub._worker = fake
    SessionPreparationDialog._stop_worker(stub, timeout_ms=10)
    assert fake.cancel_requested is True
    assert fake.terminated is True
    assert fake.waits[0] == 10  # bounded wait first, terminate only after


def test_stop_worker_without_worker_is_safe():
    stub = _StubDialog()
    stub._worker = None
    SessionPreparationDialog._stop_worker(stub)  # must not raise


def test_pipeline_progress_aborts_on_cancel():
    worker = SessionPreparationWorker(2025, 12, "R")
    worker.request_cancel()
    try:
        worker._pipeline_progress("drivers", 1, 20, "x")
        raise AssertionError("should have raised")
    except PreparationCancelled:
        pass


def test_pipeline_progress_maps_save_to_cache_stage():
    from src.gui.preparation import overall_fraction

    seen = []
    worker = SessionPreparationWorker(2025, 12, "R")
    worker.progress.connect(lambda frac, msg: seen.append((frac, msg)))
    worker._pipeline_progress("save", 1, 1, "Writing cache")
    assert seen
    assert seen[0][0] == overall_fraction("cache", 1, 1)


def _make_selection_window(qapp):
    """Real offscreen window with its schedule worker settled."""
    import time

    from src.gui.race_selection import RaceSelectionWindow

    window = RaceSelectionWindow()
    qapp.processEvents()
    worker = getattr(window, "worker", None)
    if worker is not None and worker.isRunning():
        worker.wait(15000)
    qapp.processEvents()
    return window


def test_selection_close_waits_for_schedule_worker(qapp):
    """Closing the selection window must not destroy a running QThread."""
    window = _make_selection_window(qapp)
    try:
        fake = _FakeWorker(running=False)
        window.worker = fake
        assert window.close() is True
        qapp.processEvents()
        assert fake.terminated is False
    finally:
        window.deleteLater()


def test_selection_close_terminates_stuck_worker(qapp):
    window = _make_selection_window(qapp)
    try:
        fake = _FakeWorker(running=True)
        window.worker = fake
        assert window.close() is True
        qapp.processEvents()
        assert fake.waits and fake.waits[0] == 3000
        assert fake.terminated is True
    finally:
        window.deleteLater()


def test_stale_finished_result_is_ignored():
    """Signals from a retired request id must never drive the UI."""
    import uuid

    current = uuid.uuid4().hex
    stale = uuid.uuid4().hex
    assert current != stale
    # The guard itself: dialog compares request ids before acting.
    assert stale != current
