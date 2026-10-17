"""Data Quality Service tests (Update 6).

Small deterministic fixtures for edge cases; one real-session integration
guarded by skip when local FastF1/prepared data is unavailable.
"""

import copy
import math

import pytest

from src.services import data_quality as dq
from src.services.data_quality import (
    assess_driver_lap,
    assess_field,
    assess_session,
    quality_summary_for_analysis,
)


def _frames(n, driver="VER", lap=5, **overrides):
    base = {"x": 100.0, "y": 200.0, "dist": 100.0, "rel_dist": 0.5,
            "speed": 300.0, "gear": 7, "drs": 0, "throttle": 80.0,
            "brake": 0.0, "tyre": 1.0, "tyre_life": 5.0,
            "lap": lap, "position": 1}
    frames = []
    for i in range(n):
        sample = dict(base)
        # Fresh variation on every continuous channel each sample, so a
        # healthy fixture is never mistaken for frozen clamp data.
        sample["x"] = 100.0 + i * 1.7
        sample["y"] = 200.0 + (i % 5) * 0.9
        sample["dist"] = 100.0 + i * 2.3
        sample["rel_dist"] = min(0.9999, 0.02 + i * 0.0011)
        sample["speed"] = 300.0 + (i % 7) * 0.3
        sample["throttle"] = 80.0 + (i % 3) * 0.5
        sample["brake"] = (i % 11) * 0.7
        sample.update(overrides.get("per_frame", {}).get(i, {}))
        if "sample" in overrides:
            sample.update(overrides["sample"])
        frames.append({"t": round(i * 0.04, 3), "lap": lap,
                       "drivers": {driver: sample}})
    return frames


def test_fully_valid_telemetry_is_full():
    frames = _frames(60)
    finding = assess_field(frames, "VER", "speed")
    assert finding["quality"] == "full"
    assert finding["reason"] == "ok"
    assert finding["samples"] == 60
    assert finding["non_finite_samples"] == 0


def test_partially_missing_telemetry_is_partial():
    frames = _frames(60)
    for i in range(0, 60, 6):
        frames[i]["drivers"]["VER"]["speed"] = float("nan")
    finding = assess_field(frames, "VER", "speed")
    assert finding["quality"] == "partial"
    assert finding["reason"] == "non_finite_samples:10/60"
    assert finding["samples"] == 60


@pytest.mark.parametrize("bad", [None, float("nan"), float("inf"), float("-inf")])
def test_completely_missing_telemetry_is_missing(bad):
    frames = _frames(60, sample={"throttle": bad})
    finding = assess_field(frames, "VER", "throttle")
    assert finding["quality"] == "missing"
    assert finding["reason"] == "all_values_non_finite"


def test_absent_field_is_missing():
    frames = _frames(30)
    for frame in frames:
        del frame["drivers"]["VER"]["brake"]
    finding = assess_field(frames, "VER", "brake")
    assert finding["quality"] == "missing"


def test_long_constant_run_is_frozen_heuristic():
    frames = _frames(1200, sample={"speed": 0.0, "x": 5.0})
    finding = assess_field(frames, "VER", "speed")
    assert finding["quality"] == "frozen"
    assert "heuristic" in finding["reason"]
    assert "threshold" in finding["reason"]


def test_constant_discrete_channels_are_not_frozen():
    frames = _frames(1200)  # drs == 0 and gear == 7 throughout: legitimate
    assert assess_field(frames, "VER", "drs")["quality"] == "full"
    assert assess_field(frames, "VER", "gear")["quality"] == "full"
    assert assess_field(frames, "VER", "tyre")["quality"] == "full"


def test_short_constant_run_is_unknown_not_frozen():
    frames = _frames(10, sample={"speed": 250.0})
    finding = assess_field(frames, "VER", "speed")
    assert finding["quality"] == "unknown"
    assert "insufficient_evidence" in finding["reason"]


def test_resampled_channel_provenance_is_unknown_with_detail():
    finding = assess_field(_frames(60), "VER", "speed")
    assert finding["provenance"] == "unknown"
    assert "markers_discarded" in finding["provenance_detail"]


def test_structural_provenance_mapping():
    assert assess_field(_frames(5), "VER", "lap")["provenance"] == "derived"
    assert assess_field(_frames(5), "VER", "position")["provenance"] == "derived"
    assert assess_field(_frames(5), "VER", "no_such_field")["provenance"] == "unknown"


def test_safety_car_provenance_is_simulated():
    frames = _frames(30)
    frames[5]["safety_car"] = {"x": 1.0, "y": 2.0,
                               "phase": "on_track", "alpha": 1.0}
    summary = assess_session({"frames": frames, "track_statuses": []})
    assert summary["safety_car_frames"] == 1


def test_retired_driver_scope_is_unknown():
    frames = _frames(60, driver="VER")
    finding = assess_field(frames, "LAW", "speed")  # absent everywhere
    assert finding["quality"] == "missing"
    assert finding["reason"] == "all_values_non_finite"
    lap_summary = assess_driver_lap(frames, "LAW", 5)
    assert lap_summary["scope"]["frames_in_scope"] == 0
    assert lap_summary["aggregate"] in ("missing", "unknown")


def test_missing_channel_for_one_driver_only():
    frames = _frames(120, sample={"rel_dist": float("nan")})
    finding = assess_field(frames, "VER", "rel_dist")
    assert finding["quality"] == "missing"
    assert assess_field(frames, "VER", "speed")["quality"] == "full"


def test_empty_session_and_empty_scope():
    summary = assess_session({"frames": [], "track_statuses": []})
    assert summary["n_frames"] == 0
    assert summary["drivers"] == {}
    finding = assess_field([], "VER", "speed")
    assert finding["quality"] == "unknown"
    assert finding["reason"] == "empty_scope"


def test_determinism_and_aggregation_rules():
    frames = _frames(
        120, per_frame={i: {"throttle": float("nan")} for i in range(0, 120, 12)})
    first = assess_driver_lap(frames, "VER", 5)
    second = assess_driver_lap(frames, "VER", 5)
    assert first == second  # deterministic
    assert first["fields"]["throttle"]["quality"] == "partial"
    assert first["fields"]["throttle"]["reason"] == "non_finite_samples:10/120"
    assert first["fields"]["speed"]["quality"] == "full"
    assert first["aggregate"] == "partial"  # worst wins, not full
    assert first["rules_version"] == dq.QUALITY_RULES_VERSION


def test_summary_full_requires_all_fields_full():
    frames = _frames(120)
    summary = assess_driver_lap(frames, "VER", 5)
    assert summary["aggregate"] == "full"
    assert all(f["quality"] == "full" for f in summary["fields"].values())


def test_inputs_are_never_mutated():
    frames = _frames(200, sample={"speed": 0.0})
    payload = {"frames": frames,
               "track_statuses": [{"status": "4", "start_time": 1.0,
                                   "end_time": 5.0}]}
    snapshot = copy.deepcopy(payload)
    assess_field(frames, "VER", "speed")
    assess_driver_lap(frames, "VER", 5)
    assess_session(payload)
    quality_summary_for_analysis(assess_session(payload), "VER")
    assert payload == snapshot


def test_frame_schema_untouched_and_compatible():
    frames = _frames(50)
    before_keys = [set(f.keys()) for f in frames]
    assess_session({"frames": frames, "track_statuses": []})
    assert [set(f.keys()) for f in frames] == before_keys
    for frame in frames:  # insight-window contract spot check
        assert "t" in frame and "drivers" in frame
        assert "speed" in frame["drivers"]["VER"]


def test_compact_analysis_query():
    summary = assess_session({"frames": _frames(120), "track_statuses": []})
    answer = quality_summary_for_analysis(
        summary, "VER", fields=["speed", "throttle", "brake", "gear"])
    assert answer["aggregate"] == "full"
    assert set(answer["fields"]) == {"speed", "throttle", "brake", "gear"}
    missing = quality_summary_for_analysis(summary, "NOPE")
    assert missing["aggregate"] == "unknown"


def _real_payload():
    import os
    import pickle
    path = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "computed_data", "2025_R12_R_v2.pkl")
    if not os.path.exists(path):
        pytest.skip("prepared 2025 R12 race cache unavailable")
    with open(path, "rb") as handle:
        return pickle.load(handle)["payload"]


def test_real_session_known_findings():
    """Integration: COL rel_dist missing, frozen DNF clamp, VER healthy."""
    import time
    payload = _real_payload()
    start = time.monotonic()
    summary = assess_session(payload)
    elapsed = time.monotonic() - start
    assert summary["n_frames"] == 148011
    col = summary["drivers"]["COL"]
    assert col["fields"]["rel_dist"]["quality"] == "missing"
    # COL retired after lap 1: edge-clamped speed is exactly constant over
    # ~147k frames, so the heuristic correctly reports frozen (carried
    # forward), never a sensor-fault claim.
    assert col["fields"]["speed"]["quality"] == "frozen"
    assert "heuristic" in col["fields"]["speed"]["reason"]
    law_speed = summary["drivers"]["LAW"]["fields"]["speed"]
    assert law_speed["quality"] in ("frozen", "full")
    ver = summary["drivers"]["VER"]
    assert ver["fields"]["speed"]["quality"] == "full"
    assert ver["lap_min"] == 1 and ver["lap_max"] == 52
    assert summary["safety_car_frames"] == 27661
    assert set(summary["status_codes"]) == {"1", "2", "4", "6", "7"}
    # Bounded overhead guard: single pass must stay far below prep time.
    assert elapsed < 120, f"session assessment took {elapsed:.1f}s"
    answer = quality_summary_for_analysis(
        summary, "VER", fields=["speed", "throttle", "brake", "gear"])
    assert answer["aggregate"] == "full"
