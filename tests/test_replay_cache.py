"""Cache lifecycle tests: identity, versioning, atomicity, compatibility."""

import os
import pickle

import pytest

from src.services import replay_cache
from src.services.replay_cache import (
    CACHE_SCHEMA_VERSION,
    cache_key,
    cache_path,
    cleanup_stale_tmp_files,
    enforce_budget,
    load_race_replay,
    store_race_replay,
    validate_race_payload,
)


def _payload(n_frames=4, n_drivers=3, **overrides):
    drivers = {
        f"D{i:02d}": {
            "x": 1.0, "y": 2.0, "dist": 100.0 * i, "lap": 2,
            "rel_dist": 0.5, "tyre": 1.0, "tyre_life": 3.0,
            "position": i + 1, "speed": 300.0, "gear": 7,
            "drs": 12, "throttle": 90.0, "brake": 0.0,
        }
        for i in range(n_drivers)
    }
    frames = [{"t": round(i * 0.04, 3), "lap": 2, "drivers": dict(drivers)}
              for i in range(n_frames)]
    data = {
        "frames": frames,
        "driver_colors": {"D00": (255, 0, 0)},
        "track_statuses": [{"status": "1", "start_time": 0.0, "end_time": None}],
        "total_laps": 57,
        "max_tyre_life": {1: 20},
        "sector_data": {"by_driver": {}, "session_bests": {}},
    }
    data.update(overrides)
    return data


def test_cache_key_is_deterministic_and_versioned():
    assert cache_key(2025, 12, "R") == cache_key(2025, 12, "R")
    assert f"_v{CACHE_SCHEMA_VERSION}.pkl" in cache_key(2025, 12, "R")
    assert cache_key(2025, 12, "R") != cache_key(2025, 12, "S")
    assert cache_key(2025, 12, "R") != cache_key(2024, 12, "R")


def test_store_load_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(replay_cache, "_computed_dir", lambda: str(tmp_path))
    payload = _payload()
    ok, path = store_race_replay(payload, year=2025, round_number=12, session_type="R")
    assert ok, path
    assert os.path.exists(path)
    # No temp leftovers after a successful write.
    assert [n for n in os.listdir(str(tmp_path)) if ".tmp-" in n] == []
    loaded, meta = load_race_replay(year=2025, round_number=12, session_type="R")
    assert meta["hit"] is True
    assert loaded["total_laps"] == 57
    assert len(loaded["frames"]) == 4
    assert loaded["frames"][0]["drivers"]["D00"]["speed"] == 300.0


def test_legacy_payload_without_envelope_and_sector_data(tmp_path, monkeypatch):
    """Older pickle files (raw dict, no sector_data) must still load."""
    monkeypatch.setattr(replay_cache, "_computed_dir", lambda: str(tmp_path))
    legacy = _payload()
    del legacy["sector_data"]
    legacy_path = os.path.join(str(tmp_path), "legacy_race_telemetry.pkl")
    with open(legacy_path, "wb") as handle:
        pickle.dump(legacy, handle, protocol=pickle.HIGHEST_PROTOCOL)
    loaded, meta = load_race_replay(legacy_paths=(legacy_path,))
    assert meta["hit"] is True
    assert meta["source"] == "legacy-cache"
    assert loaded["sector_data"] == {}
    assert loaded["max_tyre_life"] == {1: 20}


def test_zero_byte_file_is_miss_and_quarantined(tmp_path, monkeypatch):
    monkeypatch.setattr(replay_cache, "_computed_dir", lambda: str(tmp_path))
    bad = os.path.join(str(tmp_path), "empty.pkl")
    open(bad, "wb").close()
    loaded, meta = load_race_replay(legacy_paths=(bad,))
    assert loaded is None
    assert meta["hit"] is False
    assert os.path.exists(bad) is False  # quarantined, never raises
    assert any(".corrupt-" in n for n in os.listdir(str(tmp_path)))


def test_garbage_file_is_miss_and_quarantined(tmp_path, monkeypatch):
    monkeypatch.setattr(replay_cache, "_computed_dir", lambda: str(tmp_path))
    bad = os.path.join(str(tmp_path), "garbage.pkl")
    with open(bad, "wb") as handle:
        handle.write(b"\x00\x01not a pickle" * 10)
    loaded, meta = load_race_replay(legacy_paths=(bad,))
    assert loaded is None
    assert "corrupt" in meta["reason"]


def test_schema_mismatch_triggers_regeneration(tmp_path, monkeypatch):
    monkeypatch.setattr(replay_cache, "_computed_dir", lambda: str(tmp_path))
    payload = _payload()
    ok, path = store_race_replay(payload, year=2025, round_number=12)
    assert ok
    # Tamper the envelope version.
    with open(path, "rb") as handle:
        envelope = pickle.load(handle)
    envelope["schema_version"] = -1
    with open(path, "wb") as handle:
        pickle.dump(envelope, handle)
    loaded, meta = load_race_replay(year=2025, round_number=12, session_type="R")
    assert loaded is None
    assert "version" in meta["reason"]


def test_frame_count_mismatch_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(replay_cache, "_computed_dir", lambda: str(tmp_path))
    payload = _payload(n_frames=4)
    ok, path = store_race_replay(payload, year=2025, round_number=12)
    assert ok
    with open(path, "rb") as handle:
        envelope = pickle.load(handle)
    envelope["payload"]["frames"] = envelope["payload"]["frames"][:2]
    with open(path, "wb") as handle:
        pickle.dump(envelope, handle)
    loaded, meta = load_race_replay(year=2025, round_number=12, session_type="R")
    assert loaded is None


def test_refuses_to_cache_invalid_payload(tmp_path, monkeypatch):
    monkeypatch.setattr(replay_cache, "_computed_dir", lambda: str(tmp_path))
    ok, reason = store_race_replay({"frames": []}, year=2025, round_number=12)
    assert ok is False
    ok, reason = store_race_replay({"nope": True}, year=2025, round_number=12)
    assert ok is False


def test_validate_rejects_bad_frames():
    assert validate_race_payload({})[0] is False
    assert validate_race_payload({"frames": []})[0] is False
    assert validate_race_payload({"frames": [{"t": 1.0}]})[0] is False


def test_lru_budget_evicts_oldest_first(tmp_path, monkeypatch):
    monkeypatch.setattr(replay_cache, "_computed_dir", lambda: str(tmp_path))
    import time as _time

    for rnd in (1, 2, 3):
        ok, _ = store_race_replay(_payload(), year=2025, round_number=rnd)
        assert ok
        _time.sleep(0.02)
    total = sum(os.path.getsize(os.path.join(str(tmp_path), n))
                for n in os.listdir(str(tmp_path)))
    freed = enforce_budget(max_bytes=total - 1)
    assert freed > 0
    assert len(os.listdir(str(tmp_path))) == 2


def test_cleanup_removes_abandoned_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(replay_cache, "_computed_dir", lambda: str(tmp_path))
    stale = os.path.join(str(tmp_path), "x.pkl.tmp-123-456")
    with open(stale, "wb") as handle:
        handle.write(b"partial")
    import time as _time

    old = _time.time() - 100000
    os.utime(stale, (old, old))
    assert cleanup_stale_tmp_files(max_age_seconds=60) == 1
    assert os.path.exists(stale) is False


def test_cache_path_uses_settings_location(tmp_path, monkeypatch):
    from src.lib import settings as settings_mod

    fake = settings_mod.get_settings()
    monkeypatch.setattr(fake, "_settings",
                        dict(fake._settings, computed_data_location=str(tmp_path)))
    monkeypatch.setattr(settings_mod.SettingsManager, "_instance", fake)
    path = cache_path(2025, 12, "R")
    assert path.startswith(str(tmp_path))
