"""Versioned, atomic binary cache for prepared replay data.

This module wraps the application's existing pickle-based telemetry cache
with:

- deterministic cache keys (season / round / session type + schema version),
- an integrity envelope (schema version, preprocessing algorithm version,
  frame count, structural validation),
- atomic writes (temp file + validate + os.replace) so interrupted writes
  never destroy the previous valid entry,
- graceful handling of legacy / corrupt entries (including the 0-byte
  placeholder files that older runs could leave behind),
- opt-in LRU eviction under a storage budget.

Only trusted local files are ever unpickled. Cache files must never be
loaded from untrusted external sources.
"""

import hashlib
import os
import pickle
import time

# Increment when the on-disk payload layout changes incompatibly.
CACHE_SCHEMA_VERSION = 2

# Increment when the preprocessing algorithm changes such that previously
# prepared frames must be regenerated (even if the layout is unchanged).
# (Update 5 tried v3 for cumulative race distance but reverted it after the
# rebuild measurably corrupted ordering; v2 output is confirmed correct.)
PREPROCESS_ALGO_VERSION = 2

# Payload keys every valid race-replay entry must carry. ``sector_data`` is
# deliberately optional: older cached telemetry predates it and the replay
# hides the sector panel when it is absent.
REQUIRED_RACE_KEYS = ("frames", "driver_colors", "track_statuses", "total_laps")
OPTIONAL_RACE_KEYS = ("sector_data", "max_tyre_life")

# Qualifying payloads share the envelope/atomicity machinery with a
# different shape: {"results", "telemetry", "max_speed", "min_speed"}.
REQUIRED_QUALI_KEYS = ("results", "telemetry")

_KIND_SPECS = {
    "race": {"required": REQUIRED_RACE_KEYS, "defaults": {"sector_data": {}, "max_tyre_life": {}}},
    "quali": {"required": REQUIRED_QUALI_KEYS, "defaults": {}},
}

# Files smaller than this are treated as corrupt without attempting unpickle
# (covers 0-byte placeholder files left by interrupted runs).
_MIN_SANE_SIZE_BYTES = 64


def _computed_dir():
    try:
        from src.lib.settings import get_settings

        location = get_settings().computed_data_location or "computed_data"
    except Exception:
        location = "computed_data"
    os.makedirs(location, exist_ok=True)
    return location


def cache_key(year, round_number, session_type):
    """Deterministic cache key for a prepared session."""
    safe_session = str(session_type or "R").upper()
    return f"{int(year)}_R{int(round_number)}_{safe_session}_v{CACHE_SCHEMA_VERSION}.pkl"


def cache_path(year, round_number, session_type):
    return os.path.join(_computed_dir(), cache_key(year, round_number, session_type))


def legacy_cache_path(event_name, cache_suffix):
    """Path used by the pre-versioning cache layout (kept for reading)."""
    return os.path.join(_computed_dir(), f"{event_name}_{cache_suffix}_telemetry.pkl")


def _payload_fingerprint(payload):
    try:
        frames = payload.get("frames", [])
        head = repr(frames[0])[:512] if frames else "empty"
        tail = repr(frames[-1])[:512] if frames else "empty"
        raw = f"{len(frames)}|{head}|{tail}|{payload.get('total_laps')}".encode(
            "utf-8", "replace"
        )
        return hashlib.sha256(raw).hexdigest()[:16]
    except Exception:
        return "unknown"


def validate_payload(payload, kind="race"):
    """Return (ok, reason) for ``kind`` in {"race", "quali"}. Never raises."""
    spec = _KIND_SPECS.get(kind)
    if spec is None:
        return False, f"unknown cache kind: {kind}"
    if not isinstance(payload, dict):
        return False, "payload is not a dict"
    for key in spec["required"]:
        if key not in payload:
            return False, f"missing required key: {key}"
    if kind == "race":
        frames = payload.get("frames")
        if not isinstance(frames, list) or not frames:
            return False, "frames is empty or not a list"
        first = frames[0]
        if not isinstance(first, dict) or "drivers" not in first or "t" not in first:
            return False, "frame schema mismatch"
    elif kind == "quali":
        if not isinstance(payload.get("results"), list):
            return False, "results is not a list"
        if not isinstance(payload.get("telemetry"), dict):
            return False, "telemetry is not a dict"
    return True, "ok"


def validate_race_payload(payload):
    """Return (ok, reason). Never raises on bad input."""
    return validate_payload(payload, kind="race")


def _read_pickle_file(path):
    """Unpickle a local file, returning (payload, error)."""
    try:
        size = os.path.getsize(path)
    except OSError as exc:
        return None, f"unreadable: {exc}"
    if size < _MIN_SANE_SIZE_BYTES:
        return None, f"file too small ({size} bytes) - likely interrupted write"
    try:
        with open(path, "rb") as handle:
            return pickle.load(handle), None
    except Exception as exc:  # EOFError, UnpicklingError, ...
        return None, f"unpickle failed: {exc}"


def _quarantine(path):
    """Move a corrupt cache file aside so it is retried cleanly next run."""
    try:
        stamp = int(time.time())
        os.replace(path, f"{path}.corrupt-{stamp}")
    except OSError:
        try:
            os.remove(path)
        except OSError:
            pass


def _unwrap_envelope(data):
    """Accept both new envelopes and legacy raw payloads."""
    if isinstance(data, dict) and "payload" in data and "schema_version" in data:
        return data.get("payload"), data
    return data, None


def _count_frames(payload, kind):
    if kind == "race":
        try:
            return len(payload.get("frames", []))
        except TypeError:
            return None
    return None  # quali frames nest per driver/segment; count not applicable


def load_prepared(kind="race", year=None, round_number=None, session_type="R",
                   legacy_paths=()):
    """Load a prepared payload of ``kind`` ("race" or "quali").

    Returns ``(payload, meta)`` where ``meta`` describes the hit
    (``{"hit": bool, "source": ...}``). Misses return ``(None, meta)`` with a
    human-readable ``reason``. Corrupt files are quarantined, never raised.
    """
    spec = _KIND_SPECS.get(kind, _KIND_SPECS["race"])
    tried = []
    candidates = []
    if year is not None and round_number is not None:
        candidates.append(cache_path(year, round_number, session_type))
    candidates.extend(legacy_paths or ())

    for path in candidates:
        if not os.path.exists(path):
            tried.append(path)
            continue
        data, error = _read_pickle_file(path)
        if error is not None:
            _quarantine(path)
            return None, {"hit": False, "reason": f"corrupt cache ({error})",
                          "path": path, "tried": tried}
        payload, envelope = _unwrap_envelope(data)
        if envelope is not None:
            if envelope.get("schema_version") != CACHE_SCHEMA_VERSION:
                return None, {"hit": False,
                              "reason": "schema version mismatch - regenerating",
                              "path": path, "tried": tried}
            if envelope.get("algo_version") != PREPROCESS_ALGO_VERSION:
                return None, {"hit": False,
                              "reason": "preprocessing algorithm changed - regenerating",
                              "path": path, "tried": tried}
            if envelope.get("kind", "race") != kind:
                return None, {"hit": False,
                              "reason": "cache kind mismatch - regenerating",
                              "path": path, "tried": tried}
            expected = envelope.get("frame_count")
            actual = _count_frames(payload, kind)
            if expected is not None and actual is not None and actual != expected:
                _quarantine(path)
                return None, {"hit": False,
                              "reason": "frame-count mismatch - quarantined",
                              "path": path, "tried": tried}
        ok, reason = validate_payload(payload, kind=kind)
        if not ok:
            _quarantine(path)
            return None, {"hit": False, "reason": f"invalid payload ({reason})",
                          "path": path, "tried": tried}
        # Backward compatibility: older payloads lack optional keys.
        for key, default in spec["defaults"].items():
            payload.setdefault(key, default)
        source = "versioned-cache" if envelope is not None else "legacy-cache"
        return payload, {"hit": True, "source": source, "path": path,
                         "fingerprint": _payload_fingerprint(payload)}
    return None, {"hit": False, "reason": "no cached entry", "tried": tried}


def store_prepared(payload, kind="race", year=None, round_number=None,
                    session_type="R", legacy_path=None):
    """Atomically persist a prepared payload of ``kind``.

    Writes to a temp file, validates by re-reading, then atomically replaces
    the destination so interrupted writes never destroy valid data. Returns
    ``(ok, path_or_error)``.
    """
    ok, reason = validate_payload(payload, kind=kind)
    if not ok:
        return False, f"refusing to cache invalid payload: {reason}"

    if year is not None and round_number is not None:
        dest = cache_path(year, round_number, session_type)
    elif legacy_path:
        dest = legacy_path
    else:
        return False, "no cache destination (need year/round or legacy_path)"

    envelope = {
        "schema_version": CACHE_SCHEMA_VERSION,
        "algo_version": PREPROCESS_ALGO_VERSION,
        "kind": kind,
        "identity": {
            "year": year,
            "round": round_number,
            "session_type": str(session_type or "R").upper(),
        },
        "frame_count": _count_frames(payload, kind),
        "fingerprint": _payload_fingerprint(payload),
        "stored_at": time.time(),
        "payload": payload,
    }

    tmp = f"{dest}.tmp-{os.getpid()}-{int(time.time() * 1000)}"
    try:
        with open(tmp, "wb") as handle:
            pickle.dump(envelope, handle, protocol=pickle.HIGHEST_PROTOCOL)
            handle.flush()
            try:
                os.fsync(handle.fileno())
            except OSError:
                pass
        # Validate before publishing.
        reread, error = _read_pickle_file(tmp)
        if error is not None:
            raise IOError(f"validation read failed: {error}")
        ok2, reason2 = validate_payload(_unwrap_envelope(reread)[0], kind=kind)
        if not ok2:
            raise IOError(f"validation payload invalid: {reason2}")
        os.replace(tmp, dest)
        return True, dest
    except OSError as exc:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        return False, f"cache write failed (disk full?): {exc}"
    except Exception as exc:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        return False, f"cache write failed: {exc}"


def load_race_replay(year=None, round_number=None, session_type="R",
                     legacy_paths=()):
    """Load a prepared race replay payload (see :func:`load_prepared`)."""
    return load_prepared(kind="race", year=year, round_number=round_number,
                         session_type=session_type, legacy_paths=legacy_paths)


def store_race_replay(payload, year=None, round_number=None, session_type="R",
                      legacy_path=None):
    """Atomically persist a prepared race replay payload."""
    return store_prepared(payload, kind="race", year=year,
                           round_number=round_number, session_type=session_type,
                           legacy_path=legacy_path)


def cache_info():
    """Summarise cache storage: files, bytes, corrupt leftovers."""
    directory = _computed_dir()
    entries = []
    total = 0
    try:
        names = os.listdir(directory)
    except OSError:
        return {"directory": directory, "files": [], "total_bytes": 0}
    for name in sorted(names):
        path = os.path.join(directory, name)
        try:
            if os.path.isfile(path):
                size = os.path.getsize(path)
                total += size
                entries.append({"name": name, "bytes": size,
                                "suspect": size < _MIN_SANE_SIZE_BYTES
                                or ".corrupt-" in name or name.endswith(".tmp")})
        except OSError:
            continue
    return {"directory": directory, "files": entries, "total_bytes": total}


def enforce_budget(max_bytes=4 * 1024**3):
    """Evict least-recently-used cache files over budget. Returns bytes freed."""
    directory = _computed_dir()
    try:
        files = [(os.path.join(directory, n), os.path.getmtime(os.path.join(directory, n)),
                  os.path.getsize(os.path.join(directory, n)))
                 for n in os.listdir(directory)
                 if os.path.isfile(os.path.join(directory, n))
                 and not n.endswith(".tmp") and ".corrupt-" not in n]
    except OSError:
        return 0
    total = sum(size for _, _, size in files)
    if total <= max_bytes:
        return 0
    files.sort(key=lambda item: item[1])  # oldest first
    freed = 0
    for path, _, size in files:
        try:
            os.remove(path)
            freed += size
        except OSError:
            continue
        if total - freed <= max_bytes:
            break
    return freed


def cleanup_stale_tmp_files(max_age_seconds=24 * 3600):
    """Remove abandoned temp files from interrupted writes."""
    directory = _computed_dir()
    now = time.time()
    removed = 0
    try:
        names = os.listdir(directory)
    except OSError:
        return 0
    for name in names:
        if ".tmp-" not in name:
            continue
        path = os.path.join(directory, name)
        try:
            if now - os.path.getmtime(path) > max_age_seconds:
                os.remove(path)
                removed += 1
        except OSError:
            continue
    return removed
