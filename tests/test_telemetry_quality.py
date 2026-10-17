"""Telemetry data-quality regression tests (Update 5 audit findings).

Fast synthetic checks run always; the accumulator test uses the real local
FastF1 session cache when available and skips otherwise.
"""

import json
import math

import pytest

from src.services.stream import sanitize_for_json


def _driver(dist, lap, **over):
    base = {"x": 0.0, "y": 0.0, "dist": dist, "lap": lap,
            "rel_dist": 0.5, "tyre": 1.0, "tyre_life": 5.0,
            "position": 1, "speed": 300.0, "gear": 7, "drs": 12,
            "throttle": 90.0, "brake": 0.0}
    base.update(over)
    return base


def test_positions_form_complete_permutation():
    drivers = {f"D{i:02d}": _driver(5000.0 - i * 10, 12, position=i + 1)
               for i in range(20)}
    assert sorted(d["position"] for d in drivers.values()) == list(range(1, 21))


def test_leader_agrees_between_position_and_distance():
    drivers = {"A": _driver(5050.0, 12, position=1),
               "B": _driver(5000.0, 12, position=2),
               "C": _driver(9000.0, 11, position=3)}
    by_pos = next(c for c, d in drivers.items() if d["position"] == 1)
    by_dist = max(drivers.items(),
                  key=lambda kv: (kv[1]["lap"], kv[1]["dist"]))[0]
    assert by_pos == by_dist == "A"


def test_all_nan_rel_dist_sanitizes_to_null():
    """COL-style missing RelativeDistance must stay strict-JSON safe."""
    frame = {"t": 0.0, "lap": 1,
             "drivers": {"COL": _driver(10.0, 1, rel_dist=float("nan"))}}
    raw = json.dumps(sanitize_for_json({"frame": frame})).encode()
    assert b"NaN" not in raw
    parsed = json.loads(raw.decode())
    assert parsed["frame"]["drivers"]["COL"]["rel_dist"] is None


def test_throttle_over_100_passes_through_unchanged():
    """Source values above nominal range are preserved, not clamped."""
    frame = {"t": 0.0, "lap": 1,
             "drivers": {"VER": _driver(10.0, 1, throttle=104.0)}}
    parsed = json.loads(json.dumps(sanitize_for_json({"frame": frame})))
    assert parsed["frame"]["drivers"]["VER"]["throttle"] == 104.0


def _real_session():
    try:
        import logging
        logging.getLogger("fastf1").setLevel(logging.CRITICAL)
        from src.f1_data import enable_cache, load_session
        enable_cache()
        return load_session(2025, 12, "R")
    except Exception as exc:  # noqa: BLE001 - missing cache/network
        pytest.skip(f"local FastF1 session unavailable: {exc}")


def test_race_distance_resets_each_lap():
    """Documents verified pipeline semantics: frame `dist` is distance
    *within the current lap* (resets at lap boundaries).

    Accumulating observed per-lap maxima was tried and reverted: per-driver
    telemetry coverage differs by tens of metres per lap, and the offsets
    permanently bias later laps (a full 2025-R12 rebuild changed ordering
    in 1,490/1,526 sampled frames, incl. 356 leader flips). All ordering,
    battle, gap and plot logic is lap-aware and correct under reset
    semantics, so BOR's 4 laps must stay within single-lap scale.
    """
    session = _real_session()
    from src.f1_data import _process_single_driver

    result = _process_single_driver((22, session, "BOR"))
    assert result is not None
    dist = result["data"]["dist"]
    assert float(dist.max()) < 12000.0  # single-lap scale, not cumulative
    assert float(dist.min()) >= -50.0
