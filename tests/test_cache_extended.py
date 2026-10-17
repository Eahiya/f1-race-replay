"""Extended cache integrity tests: quali kind, truncation, interrupted
writes, kind mismatches, legacy envelopes, eviction and recovery."""

import os
import pickle

from src.services import replay_cache
from src.services.replay_cache import (
    cache_key,
    cleanup_stale_tmp_files,
    enforce_budget,
    load_prepared,
    store_prepared,
    validate_payload,
)


def _race_payload(n_frames=3):
    drivers = {"D00": {"x": 1.0, "position": 1, "lap": 2, "dist": 10.0}}
    return {
        "frames": [{"t": round(i * 0.04, 3), "lap": 2,
                    "drivers": dict(drivers)} for i in range(n_frames)],
        "driver_colors": {"D00": (255, 0, 0)},
        "track_statuses": [{"status": "1", "start_time": 0.0, "end_time": None}],
        "total_laps": 52,
    }


def _quali_payload():
    return {
        "results": [{"code": "VER", "position": 1}],
        "telemetry": {"VER": {"Q1": {"frames": []}}},
        "max_speed": 320.0,
        "min_speed": 100.0,
    }


def test_quali_store_load_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(replay_cache, "_computed_dir", lambda: str(tmp_path))
    ok, path = store_prepared(_quali_payload(), kind="quali",
                              year=2025, round_number=12, session_type="Q")
    assert ok, path
    loaded, meta = load_prepared(kind="quali", year=2025, round_number=12,
                                 session_type="Q")
    assert meta["hit"] is True
    assert loaded["results"][0]["code"] == "VER"


def test_quali_legacy_raw_file_loads(tmp_path, monkeypatch):
    monkeypatch.setattr(replay_cache, "_computed_dir", lambda: str(tmp_path))
    legacy = os.path.join(str(tmp_path), "evt_quali_telemetry.pkl")
    with open(legacy, "wb") as handle:
        pickle.dump(_quali_payload(), handle, protocol=pickle.HIGHEST_PROTOCOL)
    loaded, meta = load_prepared(kind="quali", legacy_paths=(legacy,))
    assert meta["hit"] is True
    assert meta["source"] == "legacy-cache"


def test_quali_zero_byte_file_is_miss_not_crash(tmp_path, monkeypatch):
    """Regression: the old quali path raised EOFError on 0-byte files."""
    monkeypatch.setattr(replay_cache, "_computed_dir", lambda: str(tmp_path))
    bad = os.path.join(str(tmp_path), "evt_quali_telemetry.pkl")
    open(bad, "wb").close()
    loaded, meta = load_prepared(kind="quali", legacy_paths=(bad,))
    assert loaded is None
    assert meta["hit"] is False


def test_truncated_envelope_is_quarantined(tmp_path, monkeypatch):
    monkeypatch.setattr(replay_cache, "_computed_dir", lambda: str(tmp_path))
    ok, path = store_prepared(_race_payload(), year=2025, round_number=12)
    assert ok
    with open(path, "r+b") as handle:
        handle.truncate(os.path.getsize(path) // 2)
    loaded, meta = load_prepared(kind="race", year=2025, round_number=12)
    assert loaded is None
    assert "corrupt" in meta["reason"] or "invalid" in meta["reason"]
    assert os.path.exists(path) is False  # quarantined


def test_kind_mismatch_triggers_regeneration(tmp_path, monkeypatch):
    monkeypatch.setattr(replay_cache, "_computed_dir", lambda: str(tmp_path))
    ok, _ = store_prepared(_race_payload(), kind="race",
                           year=2025, round_number=12, session_type="R")
    assert ok
    # Same key shape would collide only if session types match; force the
    # mismatch check via a hand-written envelope swap instead:
    ok, _ = store_prepared(_quali_payload(), kind="quali",
                           year=2025, round_number=12, session_type="Q")
    assert ok
    loaded, meta = load_prepared(kind="race", year=2025, round_number=12,
                                 session_type="Q")
    assert loaded is None
    assert "kind mismatch" in meta["reason"]


def test_legacy_envelope_without_kind_loads_as_race(tmp_path, monkeypatch):
    """Update-3 envelopes predate the kind field and must keep loading."""
    monkeypatch.setattr(replay_cache, "_computed_dir", lambda: str(tmp_path))
    payload = _race_payload()
    ok, path = store_prepared(payload, year=2025, round_number=12)
    assert ok
    with open(path, "rb") as handle:
        envelope = pickle.load(handle)
    del envelope["kind"]
    with open(path, "wb") as handle:
        pickle.dump(envelope, handle)
    loaded, meta = load_prepared(kind="race", year=2025, round_number=12)
    assert meta["hit"] is True
    assert len(loaded["frames"]) == 3


def test_interrupted_write_tmp_is_not_valid(tmp_path, monkeypatch):
    monkeypatch.setattr(replay_cache, "_computed_dir", lambda: str(tmp_path))
    stale = os.path.join(str(tmp_path), "2025_R12_R_v2.pkl.tmp-1-2")
    with open(stale, "wb") as handle:
        handle.write(b"partial")
    import time as _time
    old = _time.time() - 100000
    os.utime(stale, (old, old))
    # A tmp file is never a candidate; cleanup removes it.
    loaded, meta = load_prepared(kind="race", year=2025, round_number=12)
    assert loaded is None
    assert cleanup_stale_tmp_files(max_age_seconds=60) == 1


def test_repeated_read_write_is_stable(tmp_path, monkeypatch):
    monkeypatch.setattr(replay_cache, "_computed_dir", lambda: str(tmp_path))
    for _ in range(3):
        ok, _ = store_prepared(_race_payload(), year=2025, round_number=12)
        assert ok
        loaded, meta = load_prepared(kind="race", year=2025, round_number=12)
        assert meta["hit"] is True
    # Previous valid entry survives: exactly one versioned file.
    names = os.listdir(str(tmp_path))
    assert len([n for n in names if n.endswith(".pkl")]) == 1


def test_distinct_identities_do_not_collide(tmp_path, monkeypatch):
    monkeypatch.setattr(replay_cache, "_computed_dir", lambda: str(tmp_path))
    assert cache_key(2025, 12, "R") != cache_key(2025, 12, "Q")
    assert cache_key(2025, 12, "R") != cache_key(2024, 12, "R")
    assert cache_key(2025, 12, "S") != cache_key(2025, 12, "SQ")
    for year, rnd, sess in ((2025, 12, "R"), (2025, 12, "Q"), (2024, 1, "S")):
        kind = "quali" if sess in ("Q", "SQ") else "race"
        payload = _quali_payload() if kind == "quali" else _race_payload()
        ok, _ = store_prepared(payload, kind=kind, year=year,
                               round_number=rnd, session_type=sess)
        assert ok
    assert len(os.listdir(str(tmp_path))) == 3


def test_eviction_keeps_newest_and_recovers(tmp_path, monkeypatch):
    monkeypatch.setattr(replay_cache, "_computed_dir", lambda: str(tmp_path))
    import time as _time
    for rnd in (1, 2, 3):
        assert store_prepared(_race_payload(), year=2025, round_number=rnd)[0]
        _time.sleep(0.02)
    total = sum(os.path.getsize(os.path.join(str(tmp_path), n))
                for n in os.listdir(str(tmp_path)))
    assert enforce_budget(max_bytes=total - 1) > 0
    remaining = os.listdir(str(tmp_path))
    assert len(remaining) == 2
    # Newest entry still loads.
    loaded, meta = load_prepared(kind="race", year=2025, round_number=3)
    assert meta["hit"] is True
    # Evicted entry regenerates cleanly.
    assert store_prepared(_race_payload(), year=2025, round_number=1)[0]
    loaded, meta = load_prepared(kind="race", year=2025, round_number=1)
    assert meta["hit"] is True


def test_algo_version_change_forces_regeneration(tmp_path, monkeypatch):
    """Envelopes written by an older preprocessing algorithm must miss."""
    monkeypatch.setattr(replay_cache, "_computed_dir", lambda: str(tmp_path))
    ok, path = store_prepared(_race_payload(), year=2025, round_number=12)
    assert ok
    with open(path, "rb") as handle:
        envelope = pickle.load(handle)
    envelope["algo_version"] = -1
    with open(path, "wb") as handle:
        pickle.dump(envelope, handle)
    loaded, meta = load_prepared(kind="race", year=2025, round_number=12)
    assert loaded is None
    assert "algorithm changed" in meta["reason"]


def test_validate_rejects_wrong_kind_shapes():
    assert validate_payload({"frames": []}, kind="quali")[0] is False
    assert validate_payload({"results": [], "telemetry": {}}, kind="quali")[0] is True
    assert validate_payload({"results": [], "telemetry": {}}, kind="nope")[0] is False
