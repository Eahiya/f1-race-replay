"""Race Intelligence Data Quality Service (Update 6).

A separate, read-only analysis service that evaluates telemetry validity
(quality) and data provenance for prepared replay payloads. It never mutates
input frames, never adds keys to the frozen replay-frame schema, and is
fully callable without the Arcade renderer, a GUI, or network streaming.

Contract (see documentation.md, Update 6 for the full text):

- Quality states: ``full`` | ``partial`` | ``frozen`` | ``missing``, plus
  ``unknown`` reserved for empty scopes / insufficient evidence.
- Provenance states: ``observed`` | ``derived`` | ``simulated`` | ``unknown``.
- Scopes: field (driver + frame range), driver/lap, session.
- Aggregation: a summary is ``full`` only if every assessed field is
  ``full``; otherwise the worst field state wins
  (missing > partial > frozen > unknown > full).
- ``frozen`` is a documented heuristic: an exactly constant run on a
  continuous channel over >= FROZEN_MIN_FRAMES frames. Discrete channels
  (gear, DRS, tyre, ...) are legitimately constant and are never flagged
  frozen. A constant run SHORTER than the threshold yields ``unknown``
  (insufficient evidence), never a fault claim.
- Resampled channel values carry provenance ``unknown``: the preparation
  pipeline (linear 25 Hz resample + edge clamp) discards the markers that
  would distinguish observed / interpolated / carried-forward samples.
  Structurally known values (safety-car reconstruction, ordering-derived
  fields, timing aggregates, feed records) are labelled accordingly.

Rules version: QUALITY_RULES_VERSION. Bumping it documents rule changes;
the frozen replay-frame contract is unaffected.
"""

import math

QUALITY_RULES_VERSION = 1

# Quality states in worst-first order (lower rank = worse).
_QUALITY_RANK = {"missing": 0, "partial": 1, "frozen": 2, "unknown": 3, "full": 4}

# Channels with genuine sensor variation when fresh; only these are
# eligible for the frozen heuristic.
CONTINUOUS_FIELDS = frozenset(
    {"x", "y", "dist", "rel_dist", "speed", "throttle", "brake"}
)

# Discrete-state channels: constant values are legitimate (e.g. DRS off
# for a whole lap, one compound per stint, one gear held briefly).
ENUM_FIELDS = frozenset({"gear", "drs", "tyre", "tyre_life", "lap", "position"})

# Fields assessed in driver/lap and session summaries.
CORE_FIELDS = tuple(sorted(CONTINUOUS_FIELDS | ENUM_FIELDS))

# Minimum scope length (frames at 25 FPS) for a constant run to qualify as
# `frozen`. Shorter constant runs are `unknown` (insufficient evidence).
FROZEN_MIN_FRAMES = 25

# Provenance assigned from structural knowledge of the preparation pipeline.
_PROVENANCE_BY_FIELD = {
    # Kinematic reconstruction from track-status records (f1_data
    # _compute_safety_car_positions): simulated, never observed.
    "safety_car": ("simulated", "kinematic_reconstruction_from_track_status"),
    # Ordering-derived replay state.
    "position": ("derived", "ordering_by_lap_then_distance"),
    "lap": ("derived", "resample_then_round_of_lap_timing"),
    # Timing-feed aggregations.
    "sector": ("derived", "timing_feed_aggregation"),
    # Direct feed records (kept verbatim in the payload).
    "track_status": ("observed", "feed_record"),
}


def _provenance_for(field):
    """(provenance, detail) for a frame field from structural knowledge."""
    if field in _PROVENANCE_BY_FIELD:
        return _PROVENANCE_BY_FIELD[field]
    if field in CONTINUOUS_FIELDS or field in ENUM_FIELDS:
        return ("unknown",
                "linear_25hz_resample_with_edge_clamp_markers_discarded")
    return ("unknown", "insufficient_evidence")


def _is_finite_number(value):
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return math.isfinite(value)
    return False


class _Accumulator:
    """O(1) streaming stats for one (driver, field) sample stream."""

    __slots__ = ("n", "bad", "has_finite", "fmin", "fmax")

    def __init__(self):
        self.n = 0
        self.bad = 0
        self.has_finite = False
        self.fmin = None
        self.fmax = None

    def add(self, value):
        self.n += 1
        if not _is_finite_number(value):
            self.bad += 1
            return
        number = float(value)
        if not self.has_finite:
            self.has_finite = True
            self.fmin = number
            self.fmax = number
        elif number < self.fmin:
            self.fmin = number
        elif number > self.fmax:
            self.fmax = number

    def all_equal(self):
        return self.has_finite and self.fmin == self.fmax


def _classify(field, acc, scope_len):
    """Build a field finding dict from an accumulator. Pure function."""
    provenance, detail = _provenance_for(field)
    if acc.n == 0:
        return {"field": field, "quality": "unknown",
                "provenance": provenance, "provenance_detail": detail,
                "reason": "empty_scope", "samples": 0,
                "non_finite_samples": 0}
    if not acc.has_finite:
        return {"field": field, "quality": "missing",
                "provenance": provenance, "provenance_detail": detail,
                "reason": "all_values_non_finite", "samples": acc.n,
                "non_finite_samples": acc.bad}
    if acc.bad > 0:
        return {"field": field, "quality": "partial",
                "provenance": provenance, "provenance_detail": detail,
                "reason": f"non_finite_samples:{acc.bad}/{acc.n}",
                "samples": acc.n, "non_finite_samples": acc.bad}
    if field in CONTINUOUS_FIELDS and acc.all_equal():
        if acc.n >= FROZEN_MIN_FRAMES:
            return {"field": field, "quality": "frozen",
                    "provenance": provenance, "provenance_detail": detail,
                    "reason": (f"constant_run:{acc.n}_frames_heuristic "
                               f"(threshold {FROZEN_MIN_FRAMES})"),
                    "samples": acc.n, "non_finite_samples": 0}
        return {"field": field, "quality": "unknown",
                "provenance": provenance, "provenance_detail": detail,
                "reason": "insufficient_evidence:constant_short_run",
                "samples": acc.n, "non_finite_samples": 0}
    _ = scope_len
    return {"field": field, "quality": "full",
            "provenance": provenance, "provenance_detail": detail,
            "reason": "ok", "samples": acc.n, "non_finite_samples": 0}


def _worst_quality(qualities):
    return min(qualities, key=lambda q: _QUALITY_RANK[q])


def assess_field(frames, driver_code, field, start_frame=0, end_frame=None):
    """Assess one telemetry field for a driver over a frame range.

    Read-only: ``frames`` and all nested dicts are never mutated.
    Returns a finding dict with quality, provenance, detail, and reason.
    """
    total = len(frames)
    stop = total if end_frame is None else max(0, min(end_frame, total))
    start = max(0, min(start_frame, total))
    acc = _Accumulator()
    for index in range(start, stop):
        drivers = frames[index].get("drivers", {})
        sample = drivers.get(driver_code, {})
        acc.add(sample.get(field))
    return _classify(field, acc, stop - start)


def assess_driver_lap(frames, driver_code, lap_number):
    """Assess all core fields for one driver on one lap.

    The lap scope uses the frame ``lap`` value (a resampled, rounded
    derivative): boundary frames can misassign by a sample. Findings carry
    the scope so downstream analysis can account for it.
    """
    findings = {}
    accs = {field: _Accumulator() for field in CORE_FIELDS}
    samples = 0
    for frame in frames:
        sample = frame.get("drivers", {}).get(driver_code)
        if sample is None:
            continue
        try:
            lap = int(sample.get("lap", -1))
        except (TypeError, ValueError):
            continue
        if lap != lap_number:
            continue
        samples += 1
        for field, acc in accs.items():
            acc.add(sample.get(field))
    for field, acc in accs.items():
        findings[field] = _classify(field, acc, samples)
    aggregate = _worst_quality([finding["quality"] for finding in findings.values()])
    return {"scope": {"driver": driver_code, "lap": int(lap_number),
                      "frames_in_scope": samples},
            "rules_version": QUALITY_RULES_VERSION,
            "fields": findings,
            "aggregate": aggregate}


def assess_session(payload):
    """Compact session-wide quality summary in a single pass.

    Bounded: one small accumulator per (driver, field); no per-sample
    objects are retained. Returns JSON-serializable plain types.
    """
    frames = payload.get("frames", []) if isinstance(payload, dict) else []
    per_driver = {}
    sc_frames = 0
    status_codes = set()
    for status in payload.get("track_statuses", []) if isinstance(payload, dict) else []:
        code = status.get("status")
        if code is not None:
            status_codes.add(str(code))
    for frame in frames:
        if frame.get("safety_car") is not None:
            sc_frames += 1
        for code, sample in frame.get("drivers", {}).items():
            entry = per_driver.get(code)
            if entry is None:
                entry = {"frames_present": 0, "lap_min": None, "lap_max": None,
                         "accs": {field: _Accumulator() for field in CORE_FIELDS}}
                per_driver[code] = entry
            entry["frames_present"] += 1
            try:
                lap = int(sample.get("lap", -1))
            except (TypeError, ValueError):
                lap = None
            if lap is not None and lap >= 0:
                entry["lap_min"] = lap if entry["lap_min"] is None else min(entry["lap_min"], lap)
                entry["lap_max"] = lap if entry["lap_max"] is None else max(entry["lap_max"], lap)
            for field, acc in entry["accs"].items():
                acc.add(sample.get(field))
    drivers = {}
    for code in sorted(per_driver):
        entry = per_driver[code]
        fields = {field: _classify(field, acc, entry["frames_present"])
                  for field, acc in entry["accs"].items()}
        aggregate = _worst_quality([finding["quality"] for finding in fields.values()])
        drivers[code] = {"frames_present": entry["frames_present"],
                         "lap_min": entry["lap_min"], "lap_max": entry["lap_max"],
                         "fields": fields, "aggregate": aggregate}
    return {"rules_version": QUALITY_RULES_VERSION,
            "n_frames": len(frames),
            "drivers": drivers,
            "safety_car_frames": sc_frames,
            "status_codes": sorted(status_codes)}


def quality_summary_for_analysis(session_summary, driver_code, fields=None):
    """Compact downstream query: per-field quality for one driver.

    Answers "are speed, throttle, brake, gear valid for this driver?"
    without touching the replay-frame schema.
    """
    entry = session_summary.get("drivers", {}).get(driver_code)
    if entry is None:
        return {"driver": driver_code, "aggregate": "unknown",
                "reason": "driver_absent_from_summary", "fields": {}}
    wanted = list(fields) if fields else list(CORE_FIELDS)
    out = {}
    for field in wanted:
        finding = entry["fields"].get(field)
        if finding is None:
            out[field] = {"quality": "unknown", "reason": "field_not_assessed"}
        else:
            out[field] = {"quality": finding["quality"],
                          "provenance": finding["provenance"],
                          "reason": finding["reason"]}
    aggregate = _worst_quality(
        [item["quality"] if item["quality"] in _QUALITY_RANK else "unknown"
         for item in out.values()] or ["unknown"])
    return {"driver": driver_code, "aggregate": aggregate, "fields": out}
