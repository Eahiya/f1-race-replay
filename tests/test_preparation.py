"""Session-preparation lifecycle tests (no network, no windows)."""

import pandas as pd

from src.f1_data import PreparationCancelled, extract_sector_split_data
from src.gui.preparation import STAGES, SessionPreparationWorker, overall_fraction


def test_stage_weights_cover_full_pipeline():
    ids = [sid for sid, _, _ in STAGES]
    assert ids[0] == "validate"
    assert ids[-1] == "launch"
    assert len(ids) == len(set(ids))  # unique
    assert overall_fraction("validate", 0, 1) == 0.0
    assert overall_fraction("launch", 1, 1) == 1.0


def test_overall_fraction_is_monotonic():
    checkpoints = []
    for sid, _, _ in STAGES:
        checkpoints.append(overall_fraction(sid, 0, 1))
        checkpoints.append(overall_fraction(sid, 1, 1))
    assert checkpoints == sorted(checkpoints)
    assert all(0.0 <= value <= 1.0 for value in checkpoints)


def test_overall_fraction_mid_stage():
    before = overall_fraction("drivers", 0, 20)
    mid = overall_fraction("drivers", 10, 20)
    after = overall_fraction("drivers", 20, 20)
    assert before < mid < after


def test_worker_cancel_flag_aborts_pipeline():
    worker = SessionPreparationWorker(2025, 12, "R")
    assert worker.request_id
    worker.request_cancel()
    try:
        worker._check_cancel()
        raise AssertionError("should have raised")
    except PreparationCancelled:
        pass


def test_distinct_requests_have_distinct_ids():
    first = SessionPreparationWorker(2025, 12, "R")
    second = SessionPreparationWorker(2025, 12, "R")
    assert first.request_id != second.request_id


def _fake_session(rows):
    import datetime

    laps = pd.DataFrame(rows)

    class _Session:
        pass

    session = _Session()
    session.laps = laps
    return session


def test_sector_extraction_with_timing_feed():
    session = _fake_session([
        {"Driver": "VER", "LapNumber": 1,
         "Sector1Time": pd.Timedelta(seconds=28.1),
         "Sector2Time": pd.Timedelta(seconds=30.2),
         "Sector3Time": pd.Timedelta(seconds=29.3),
         "SpeedTrap": 320.5, "SpeedST": pd.NA},
        {"Driver": "NOR", "LapNumber": 1,
         "Sector1Time": pd.Timedelta(seconds=27.9),
         "Sector2Time": pd.Timedelta(seconds=30.5),
         "Sector3Time": pd.Timedelta(seconds=29.9),
         "SpeedTrap": 322.0, "SpeedST": pd.NA},
    ])
    data = extract_sector_split_data(session)
    assert set(data["by_driver"]) == {"VER", "NOR"}
    assert data["session_bests"]["s1"][1] == "NOR"  # fastest S1
    assert data["session_bests"]["speed_trap"][0] == 322.0


def test_sector_extraction_empty_session_returns_empty():
    session = _fake_session([])
    assert extract_sector_split_data(session) == {}


def test_sector_extraction_never_raises_on_garbage():
    class _Broken:
        laps = None

    assert extract_sector_split_data(_Broken()) == {}
