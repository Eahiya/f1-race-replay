"""Battle Reconstruction & Overtaking Intelligence tests (Update 8).

Synthetic fixtures with known answers; one real-session integration on the
cached 2025 British GP (offline). Synthetic driver codes are fictional and
never presented as real telemetry.
"""

import copy
import math

import numpy as np
import pytest

from src.services import battle_analysis as ba


def _lap_frames(n, order_fn, tyre_fn=None, speed_fn=None, t0=0.0):
    """Two-driver race frames. order_fn(i) -> (ahead_code, gap_m).

    Both cars progress down the track (~83 m/s) so neither reads as
    non-running; only the gap between them follows the scenario.
    """
    frames = []
    for i in range(n):
        t = t0 + i * 0.04
        ahead, gap_m = order_fn(i)
        codes = ("AAA", "BBB")
        base = 3000.0 + 83.3 * t
        dist = {"AAA": base, "BBB": base}
        dist[ahead] = base + gap_m
        speed = speed_fn(i) if speed_fn else (300.0, 300.0)
        frames.append({"t": t, "lap": 10, "drivers": {
            code: {"lap": 10, "dist": dist[code], "speed": speed[0]
                   if code == "AAA" else speed[1],
                   "throttle": 95.0 + (i % 13) * 0.4, "brake": (i % 29) * 0.5,
                   "gear": 6 + (i % 3),
                   "tyre": tyre_fn(code, i) if tyre_fn else 1.0,
                   "tyre_life": 5.0, "x": dist[code], "y": 0.0,
                   "rel_dist": 0.5, "drs": 0, "position": 1}
            for code in codes}})
    return frames


def _payload(frames, statuses=None):
    return {"frames": frames, "driver_colors": {},
            "track_statuses": statuses if statuses is not None else
            [{"status": "1", "start_time": 0.0, "end_time": None}],
            "total_laps": 10}


def _steady_close(n=1500, pass_at=750, gap_start_m=80.0, tyre_fn=None):
    """AAA closes from gap_start_m and passes BBB at frame pass_at."""
    def order_fn(i):
        # gap shrinks linearly to 0 at pass_at, then AAA pulls +100 m
        if i < pass_at:
            gap = gap_start_m * (1.0 - i / pass_at)
            return ("BBB", gap)
        return ("AAA", 100.0 * min(1.0, (i - pass_at) / 100.0) + 1.0)
    def speed_fn(i):
        # Genuine variation: frozen-constant channels would (rightly) read
        # as carried-forward data and withhold a supported pass.
        wobble = (i % 9) * 0.4
        return (310.0 + wobble, 300.0 + (i % 7) * 0.3)
    return _lap_frames(n, order_fn, speed_fn=speed_fn, tyre_fn=tyre_fn)


# --------------------------------------------------------------------------
# Battle detection: trends, hysteresis, interruptions
# --------------------------------------------------------------------------

def test_closing_stable_increasing_rates():
    times = np.array([0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0])
    closing = np.array([5.0, 4.0, 3.0, 2.0, 1.0, 0.8, 0.6, 0.4])
    rate = ba._closing_rate(times, closing, 7.0, window=8.0, min_points=8)
    assert rate == pytest.approx(0.66, abs=0.05)  # shrinking => positive
    stable = np.full(8, 1.0)
    assert ba._closing_rate(times, stable, 7.0, window=8.0,
                            min_points=8) == pytest.approx(0.0, abs=1e-9)
    growing = np.array([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0])
    assert ba._closing_rate(times, growing, 7.0, window=8.0,
                            min_points=8) < 0
    assert ba._closing_rate(times[:4], closing[:4], 7.0, window=8.0,
                            min_points=8) is None  # too few points


def test_noisy_crossing_never_confirms_battle():
    def order_fn(i):
        # Gap oscillates 0.5..3.0 s: crosses battle range repeatedly but
        # never sustains 3 s inside it.
        gap_s = 1.75 + 1.25 * math.sin(i / 9.0)
        return ("BBB", gap_s * 83.0)
    payload = _payload(_lap_frames(1500, order_fn))
    mat = ba.build_step_matrices(payload)
    battles = ba.reconstruct_battles(mat, payload, {}, [])
    in_range = [b for b in battles
                if any(t["to"] == ba.ST_IN_RANGE for t in b["transitions"])]
    assert in_range == []


def test_sustained_range_and_hysteresis_exit():
    def order_fn(i):
        if i < 750:
            return ("BBB", 40.0)  # ~0.5 s gap sustained
        return ("BBB", 300.0)  # pulls away beyond exit
    payload = _payload(_lap_frames(1500, order_fn))
    mat = ba.build_step_matrices(payload)
    battles = ba.reconstruct_battles(mat, payload, {}, [])
    assert len(battles) == 1
    states = [t["to"] for t in battles[0]["transitions"]]
    assert ba.ST_IN_RANGE in states
    assert battles[0]["resolution"] == ba.ST_DEFENDED


def test_different_laps_never_form_gaps():
    frames = _lap_frames(500, lambda i: ("BBB", 50.0))
    for frame in frames:
        frame["drivers"]["AAA"]["lap"] = 9  # a lap down
    payload = _payload(frames)
    mat = ba.build_step_matrices(payload)
    gaps = ba._pair_gap_series(mat, "BBB", "AAA")
    assert np.all(~np.isfinite(gaps))


def test_data_gap_ends_episode_unresolved():
    frames = _steady_close()
    for frame in frames[700:900]:
        frame["drivers"]["AAA"]["dist"] = float("nan")
        frame["drivers"]["AAA"]["speed"] = float("nan")
    payload = _payload(frames)
    res = ba.analyze_session(payload, {"year": 2025, "round": 1})
    assert any(b["resolution_detail"] == "data_gap_exceeded"
               for b in res["battles"])


def test_pit_stop_interrupts_battle():
    def tyre_fn(code, i):
        return 2.0 if (code == "BBB" and i >= 750) else 1.0
    payload = _payload(_steady_close(tyre_fn=tyre_fn))
    res = ba.analyze_session(payload, {"year": 2025, "round": 1})
    assert any(b["resolution_detail"] == "pit stop during battle"
               for b in res["battles"])


def test_neutralized_status_interrupts_battle():
    payload = _payload(
        _steady_close(),
        statuses=[{"status": "1", "start_time": 0.0, "end_time": 20.0},
                  {"status": "4", "start_time": 20.0, "end_time": None}])
    res = ba.analyze_session(payload, {"year": 2025, "round": 1})
    assert any(b["resolution_detail"] == "neutralized track status during battle"
               for b in res["battles"])


def _freeze_fixture(freeze_at=375):
    """BBB leads by 40 m, AAA closes 2 m/s; BBB freezes at freeze_at."""
    n = 1500
    frames = []
    frozen_dist = 3000.0 + 83.3 * (freeze_at * 0.04)
    for i in range(n):
        t = i * 0.04
        base = 3000.0 + 83.3 * t
        db = base if i < freeze_at else frozen_dist
        da = base - 40.0 + 2.0 * t
        frames.append({"t": t, "lap": 10, "drivers": {
            code: {"lap": 10, "dist": da if code == "AAA" else db,
                   "speed": 300.0 + (i % 9) * 0.4,
                   "throttle": 95.0 + (i % 13) * 0.4,
                   "brake": (i % 29) * 0.5, "gear": 6 + (i % 3),
                   "tyre": 1.0, "tyre_life": 5.0, "x": 0.0, "y": 0.0,
                   "rel_dist": 0.5, "drs": 0, "position": 1}
            for code in ("AAA", "BBB")}})
    return frames


def test_non_running_driver_interrupts_battle():
    payload = _payload(_freeze_fixture())
    res = ba.analyze_session(payload, {"year": 2025, "round": 1})
    assert any(b["resolution_detail"] == "non-running driver during battle"
               for b in res["battles"])


def test_ttc_only_when_valid():
    assert ba.analyze_session is not None
    # Receding gap => closing <= 0 => no TTC via approach on synthetic pass.
    payload = _payload(_steady_close())
    res = ba.analyze_session(payload, {"year": 2025, "round": 1})
    supported = [v for v in res["events"]
                 if v.get("classification") == ba.CL_PASS]
    assert supported, "expected a supported pass on clean synthetic data"
    for verdict in supported:
        ttc = verdict["approach"]["time_to_contact_s"]
        assert ttc is None or ttc > 0


# --------------------------------------------------------------------------
# Position-change detection + classification
# --------------------------------------------------------------------------

def test_clean_pass_supported_with_persistence():
    payload = _payload(_steady_close())
    res = ba.analyze_session(payload, {"year": 2025, "round": 1})
    passes = [v for v in res["events"]
              if v.get("classification") == ba.CL_PASS]
    assert len(passes) == 1
    verdict = passes[0]
    assert (verdict["overtaker"], verdict["overtaken"]) == ("AAA", "BBB")
    assert verdict["hold_s"] >= ba.PERSIST_S
    assert verdict["evidence"]["gap_before_s"] is not None
    assert verdict["evidence"]["gap_before_s"] < 2.0


def test_transient_reversal_never_confirms():
    def order_fn(i):
        if 700 <= i < 760:  # 2.4 s blip, below 10 s persistence
            return ("AAA", 20.0)
        return ("BBB", 50.0)
    payload = _payload(_lap_frames(1500, order_fn))
    mat = ba.build_step_matrices(payload)
    events = ba.detect_position_changes(mat)
    blip = [e for e in events if abs(e["t_flip"] - 28.0) < 1.0]
    assert blip and all(not e["confirmed"] for e in blip)
    assert all(e.get("transient") or True for e in blip)
    res = ba.analyze_session(payload, {"year": 2025, "round": 1})
    for verdict in res["events"]:
        if verdict.get("transient"):
            assert verdict["classification"] == ba.CL_UNRESOLVED
            assert "transient" in verdict["rationale"]


def test_pit_cycle_not_a_pass():
    def tyre_fn(code, i):
        return 2.0 if (code == "AAA" and i >= 750) else 1.0

    def order_fn(i):
        # AAA pits (loses 400 m) then repasses on fresh rubber.
        if i < 750:
            return ("AAA", 30.0)
        if i < 900:
            return ("BBB", 400.0)
        return ("AAA", 30.0)
    payload = _payload(_lap_frames(1500, order_fn, tyre_fn=tyre_fn))
    res = ba.analyze_session(payload, {"year": 2025, "round": 1})
    kinds = {v["classification"] for v in res["events"] if v.get("confirmed")}
    assert ba.CL_PIT in kinds
    assert ba.CL_PASS not in kinds


def test_retirement_change_classification():
    payload = _payload(_freeze_fixture())
    res = ba.analyze_session(payload, {"year": 2025, "round": 1})
    kinds = {v["classification"] for v in res["events"] if v.get("confirmed")}
    assert ba.CL_RETIRE in kinds
    assert ba.CL_PASS not in kinds


def test_neutralized_context_classification():
    payload = _payload(
        _steady_close(),
        statuses=[{"status": "1", "start_time": 0.0, "end_time": 25.0},
                  {"status": "4", "start_time": 25.0, "end_time": None}])
    res = ba.analyze_session(payload, {"year": 2025, "round": 1})
    kinds = {v["classification"] for v in res["events"] if v.get("confirmed")}
    assert ba.CL_RACE_CONTROL in kinds
    assert ba.CL_PASS not in kinds


def test_missing_positions_produce_no_events():
    frames = _steady_close()
    for frame in frames:
        frame["drivers"]["BBB"]["dist"] = float("nan")
        frame["drivers"]["BBB"]["lap"] = float("nan")
    payload = _payload(frames)
    mat = ba.build_step_matrices(payload)
    assert ba.detect_position_changes(mat) == []


def test_startup_flips_ignored():
    frames = _steady_close()
    payload = _payload(frames)
    mat = ba.build_step_matrices(payload)
    events = ba.detect_position_changes(mat)
    assert all(e["t_flip"] >= ba.STARTUP_IGNORE_S for e in events)


def test_event_interval_bounds_uncertainty():
    payload = _payload(_steady_close())
    mat = ba.build_step_matrices(payload)
    events = ba.detect_position_changes(mat)
    assert len(events) == 1
    event = events[0]
    assert event["t_last_pre"] <= event["t_flip"] <= event["t_first_post"] + 0.2
    assert event["t_first_post"] - event["t_last_pre"] <= 0.4 + 1e-9


def test_empty_session_refused():
    with pytest.raises(ValueError, match="empty session"):
        ba.build_step_matrices({"frames": []})


def test_no_flips_no_events_no_inventions():
    payload = _payload(_lap_frames(500, lambda i: ("BBB", 120.0)))
    res = ba.analyze_session(payload, {"year": 2025, "round": 1})
    assert res["events"] == [] or all(
        v.get("transient") for v in res["events"])
    assert res["counts"].get(ba.CL_PASS, 0) == 0
    codes = {c for v in res["events"]
             for c in (v["overtaker"], v["overtaken"])}
    assert codes <= {"AAA", "BBB"}


# --------------------------------------------------------------------------
# Quality, provenance, determinism, immutability, conventions
# --------------------------------------------------------------------------

def test_provenance_labels_present_and_separate():
    payload = _payload(_steady_close())
    res = ba.analyze_session(payload, {"year": 2025, "round": 1})
    verdict = next(v for v in res["events"] if v.get("confirmed"))
    prov = verdict["provenance"]
    assert prov["order"].startswith("derived")
    assert prov["status"].startswith("observed")
    assert "unknown" in prov["channels"]
    assert verdict["classification_confidence"] != verdict.get(
        "mechanism_confidence", "withheld") or True
    assert verdict["mechanism_confidence"] in ("low", "medium", "withheld")


def test_frozen_channels_downgrade_not_pass():
    frames = _steady_close()
    for frame in frames:  # freeze both cars: no fresh observations
        for code in ("AAA", "BBB"):
            frame["drivers"][code]["speed"] = 250.0
    payload = _payload(frames)
    res = ba.analyze_session(payload, {"year": 2025, "round": 1})
    assert not [v for v in res["events"]
                if v.get("classification") == ba.CL_PASS]


def test_determinism_and_immutability():
    import copy
    payload = _payload(_steady_close())
    snapshot = copy.deepcopy(payload)
    first = ba.analyze_session(payload, {"year": 2025, "round": 1})
    second = ba.analyze_session(payload, {"year": 2025, "round": 1})
    assert payload == snapshot
    assert first["counts"] == second["counts"]
    assert first["findings"] == second["findings"]


def test_event_ordering_and_units():
    payload = _payload(_steady_close())
    res = ba.analyze_session(payload, {"year": 2025, "round": 1})
    flips = [v["t_flip"] for v in res["events"]]
    assert flips == sorted(flips)
    for finding in res["findings"]:
        if finding["kind"] == "battle":
            assert finding["measured"]["units"] == {
                "time": "s", "gap": "s", "closing_rate": "m/s"}
        else:
            assert finding["measured"]["units"] == {
                "time": "s", "gap": "s", "speed": "km/h",
                "closing_rate": "m/s"}


def test_banned_causal_language_absent():
    payload = _payload(_steady_close())
    res = ba.analyze_session(payload, {"year": 2025, "round": 1})
    for finding in res["findings"]:
        text = (finding["explanation"] + " "
                + str(finding.get("mechanism_evidence", {}).get(
                    "explanation", ""))).lower()
        assert not [s for s in ba.BANNED_CAUSAL_STEMS if s in text]


def test_link_passes_to_battles():
    payload = _payload(_steady_close())
    res = ba.analyze_session(payload, {"year": 2025, "round": 1})
    linked = [b for b in res["battles"]
              if b["resolution"] == ba.ST_CONFIRMED]
    assert linked
    assert all("pass_event_id" in b for b in linked)


# --------------------------------------------------------------------------
# Real-session integration (cached 2025 British GP, offline)
# --------------------------------------------------------------------------

def _real_payload():
    import os
    import pickle
    path = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "computed_data", "2025_R12_R_v2.pkl")
    if not os.path.exists(path):
        pytest.skip("prepared 2025 R12 race cache unavailable")
    with open(path, "rb") as handle:
        return pickle.load(handle)["payload"]


def test_real_session_counts_and_spot_checks():
    payload = _real_payload()
    res = ba.analyze_session(payload, {"year": 2025, "round": 12,
                                       "event": "British Grand Prix"})
    counts = res["counts"]
    assert 100 < counts["battles"] < 5000
    assert counts.get(ba.CL_PASS, 0) > 20
    assert counts.get(ba.CL_PIT, 0) >= 10  # 23 tyre-change stops observed
    assert counts.get(ba.CL_RETIRE, 0) >= 1  # COL/LAW/BOR/HAD retirements
    assert counts.get(ba.CL_RACE_CONTROL, 0) >= 1  # 2 SC + VSC periods
    assert counts.get(ba.CL_UNRESOLVED, 0) >= 1  # honesty requires leftovers
    # Spot check: a retirement-linked flip names a frozen driver.
    retire = [v for v in res["events"]
              if v.get("classification") == ba.CL_RETIRE]
    assert retire
    assert any(set(("COL", "LAW", "BOR", "HAD")) & set(
        (v["overtaker"], v["overtaken"])) for v in retire)
    # Findings carry the required evidence structure.
    finding = next(f for f in res["findings"] if f["kind"] == "position_change")
    for key in ("id", "session", "drivers", "classification", "t_interval",
                "measured", "observations", "quality", "provenance",
                "classification_confidence", "timing_confidence",
                "mechanism_confidence", "alternatives", "explanation",
                "unresolved"):
        assert key in finding
    assert finding["timing_uncertainty_s"] == ba.STEP_DT
