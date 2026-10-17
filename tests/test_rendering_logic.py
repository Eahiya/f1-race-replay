"""Rendering-logic tests that run headless (no Arcade window needed)."""

import types

import pytest

from src.ui_components import LeaderboardComponent


def test_progress_delta_with_cumulative_floats():
    delta = LeaderboardComponent._progress_delta
    # progress_m is cumulative metres: (lap-1) * track_length + dist
    assert delta(12000.0, 11950.0) == pytest.approx(50.0)
    assert delta(13000.0, 12000.0) == pytest.approx(1000.0)


def test_progress_delta_with_legacy_floats():
    delta = LeaderboardComponent._progress_delta
    assert delta(5000.0, 4950.0) == pytest.approx(50.0)
    assert delta(None, 10.0) == pytest.approx(10.0)


def test_progress_delta_never_raises():
    delta = LeaderboardComponent._progress_delta
    assert delta(object(), object()) == 0.0


def _status_stub(statuses):
    from src.interfaces.race_replay import F1RaceReplayWindow

    stub = types.SimpleNamespace(
        track_statuses=statuses, _status_idx=0, _status_idx_t=-1.0)
    return F1RaceReplayWindow._track_status_at.__get__(stub)


def test_track_status_indexed_lookup():
    statuses = [
        {"status": "1", "start_time": 0.0, "end_time": 100.0},
        {"status": "2", "start_time": 100.0, "end_time": 200.0},
        {"status": "4", "start_time": 200.0, "end_time": None},
    ]
    lookup = _status_stub(statuses)
    assert lookup(10.0) == "1"
    assert lookup(150.0) == "2"
    assert lookup(500.0) == "4"
    # Seek backwards resets the cached index.
    assert lookup(10.0) == "1"
    assert lookup(199.9) == "2"


def test_track_status_empty_is_green():
    assert _status_stub([])(123.0) == "GREEN"


def test_leader_from_stored_positions():
    """Leaderboard ordering from (lap, dist) matches distance ordering."""
    entries = [
        ("VER", (0, 0, 0), {"lap": 12, "dist": 5000.0}, (12, 5000.0)),
        ("NOR", (0, 0, 0), {"lap": 12, "dist": 5050.0}, (12, 5050.0)),
        ("PIA", (0, 0, 0), {"lap": 11, "dist": 9000.0}, (11, 9000.0)),
    ]
    ordered = sorted(entries, key=lambda e: e[3], reverse=True)
    assert [code for code, *_ in ordered] == ["NOR", "VER", "PIA"]
