"""Qualifying Performance Intelligence analysis core (Update 7).

Pure, deterministic, read-only telemetry analysis over prepared qualifying
payloads (see ``get_quali_telemetry`` cache shape). No GUI, no network, no
ML. All inputs are read but never mutated; all outputs are fresh NumPy
arrays / plain dicts.

Units (explicit everywhere):
- distance: metres, within-lap coordinate (0 ~= start/finish line).
- speed: km/h (source contract).
- throttle: percent 0..100; values outside are invalid (incl. the known
  104 sentinel observed in race telemetry) and masked, never clamped.
- brake (qualifying payload): percent 0..100 (source contract).
- time: seconds of elapsed lap time; delta_t(d) = t_A(d) - t_B(d), so
  positive means the reference driver A is behind B at distance d.

Pipeline: select laps -> comparability -> distance alignment -> throttle
events -> cumulative/section deltas -> corner-validated sections ->
evidence-backed findings -> presentation-ready AnalysisResult.

Data-quality/provenance reuse: quali lap frames are adapted to the
race-frame view expected by Update 6's ``data_quality`` service
(``quali_frames_to_race_view``); resampled channels therefore carry
provenance ``unknown`` with the documented pipeline detail, while
structurally known values (sector aggregates, ordering) are labelled.
"""

import math

import numpy as np

ANALYSIS_RULES_VERSION = 1

# Common distance grid spacing. Source frames are 25 Hz resamples: at
# 50-90 m/s that is one sample per ~2-3.6 m, so 2 m neither invents
# precision nor discards real resolution. Event locations inherit ±2 m.
GRID_STEP_M = 2.0

# At least this fraction of the shorter lap must be covered by the common
# grid, otherwise the laps are declared not reliably comparable.
MIN_COVERAGE_FRACTION = 0.95

# Residual tolerance between cumulative-delta-at-finish and the official
# lap-time difference (2.5 samples at 25 Hz).
MAX_END_RESIDUAL_S = 0.10

# Throttle channel contract.
THROTTLE_VALID_MIN = 0.0
THROTTLE_VALID_MAX = 100.0
HIGH_THROTTLE_DEFAULT = 95.0
SUSTAIN_SAMPLES_DEFAULT = 5      # 200 ms at the 25 Hz source rate
LIFT_THRESHOLD_DEFAULT = 50.0
# Gaps larger than this are not interpolated across (reported as missing).
MAX_INTERP_GAP_M = 10.0

# Corner alignment check geometry (heuristic, documented).
CORNER_WINDOW_M = 150.0
CORNER_REF_SPAN_M = 400.0
CORNER_DIP_FRACTION = 0.92

# Causal vocabulary guard: these stems must never appear in a generated
# causal interpretation (case-insensitive substring match).
BANNED_CAUSAL_STEMS = (
    "traction", "grip", "downforce", "power", "engine", "talent", "skill",
    "better driver", "superior",
)

SEGMENTS = ("Q1", "Q2", "Q3")


def session_type_for_segment(segment):
    """Cache session type holding a segment (Q1/Q2/Q3 live under "Q")."""
    if segment in ("Q1", "Q2", "Q3"):
        return "Q"
    return segment


# --------------------------------------------------------------------------
# Lap extraction / selection
# --------------------------------------------------------------------------

def extract_lap_series(segment_payload):
    """Extract per-channel arrays from one cached qualifying-lap payload.

    Returns dict of float64 arrays (t, dist, speed, throttle, brake, gear,
    drs, x, y) plus ``lap_time_s`` (official, = last frame t), ``n`` and
    ``coverage`` metadata. Raises ValueError on empty/unusable laps.
    """
    frames = (segment_payload or {}).get("frames", [])
    if not frames:
        raise ValueError("incomplete lap: no telemetry frames cached")
    cols = {"t": [], "dist": [], "speed": [], "throttle": [], "brake": [],
            "gear": [], "drs": [], "x": [], "y": []}
    for frame in frames:
        tel = frame.get("telemetry", {})
        cols["t"].append(frame.get("t", math.nan))
        for key in ("dist", "speed", "throttle", "brake", "gear", "drs",
                    "x", "y"):
            value = tel.get(key, math.nan)
            try:
                cols[key].append(float(value))
            except (TypeError, ValueError):
                cols[key].append(math.nan)
    series = {key: np.asarray(values, dtype=np.float64)
              for key, values in cols.items()}
    finite_t = series["t"][np.isfinite(series["t"])]
    finite_d = series["dist"][np.isfinite(series["dist"])]
    if len(finite_t) < 25 or len(finite_d) < 25:
        raise ValueError("incomplete lap: fewer than 25 usable samples")
    return {"series": series,
            "lap_time_s": float(finite_t[-1]),
            "n": len(frames)}


def select_lap(payload, segment, driver_code, lap_index=0):
    """Select a driver's lap for analysis.

    The prepared cache retains exactly one (fastest) lap per driver per
    segment, so ``lap_index`` must be 0; anything else yields an explicit
    unsupported warning rather than a fabricated alternative. Returns
    (selection_dict, series_dict). ``selection_dict`` carries lap time,
    sample count, compound, sector times and warnings. Lap validity flags
    and the true lap number are NOT retained in cache: reported as
    ``unverified``/``unknown``, never guessed.
    """
    warnings = []
    if segment not in SEGMENTS:
        raise ValueError(f"segment must be one of {SEGMENTS}")
    telemetry = (payload or {}).get("telemetry", {})
    if driver_code not in telemetry:
        raise ValueError(f"driver {driver_code} absent from prepared data")
    seg = telemetry[driver_code].get(segment, {})
    if lap_index != 0:
        warnings.append("only_fastest_lap_cached:lap_index_unsupported")
    series_info = extract_lap_series(seg)
    results = {row.get("code"): row for row in payload.get("results", [])}
    row = results.get(driver_code, {})
    compound = seg.get("compound", "unknown")
    return ({"driver": driver_code,
             "full_name": telemetry[driver_code].get("full_name", driver_code),
             "segment": segment,
             "lap_time_s": series_info["lap_time_s"],
             "official_time_s": _safe_float(row.get(segment)),
             "n_samples": series_info["n"],
             "compound": compound,
             "sector_times": seg.get("sector_times", {}),
             "validity": "unverified_not_retained",
             "lap_number": "unknown_not_retained",
             "warnings": warnings},
            series_info["series"])


def _safe_float(value):
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------
# Comparability (Update 6 quality service reuse)
# --------------------------------------------------------------------------

def quali_frames_to_race_view(frames, driver_code):
    """Adapt quali lap frames to the race-frame view of data_quality.

    The Update 6 service assesses ``frame["drivers"][code]`` dicts; this
    adapter rebuilds that shape (with a lap-scope sentinel of 1, documented
    as scoping-only since the true lap number is not retained) without
    touching the input frames.
    """
    view = []
    for frame in frames:
        tel = frame.get("telemetry", {})
        view.append({"t": frame.get("t", math.nan),
                     "lap": 1,  # scope sentinel only, not the true lap number
                     "drivers": {driver_code: {
                         "lap": 1,
                         "position": 1,
                         "dist": tel.get("dist", math.nan),
                         "rel_dist": tel.get("rel_dist", math.nan),
                         "speed": tel.get("speed", math.nan),
                         "gear": tel.get("gear", math.nan),
                         "drs": tel.get("drs", math.nan),
                         "throttle": tel.get("throttle", math.nan),
                         "brake": tel.get("brake", math.nan),
                         "tyre": math.nan,
                         "tyre_life": math.nan,
                         "x": tel.get("x", math.nan),
                         "y": tel.get("y", math.nan),
                     }}})
    return view


def assess_lap_quality(frames, driver_code):
    """Field quality/provenance for one quali lap via the Update 6 service."""
    from src.services import data_quality as dq

    view = quali_frames_to_race_view(frames, driver_code)
    fields = {}
    for field in ("speed", "throttle", "brake", "gear", "drs", "dist",
                  "rel_dist", "x", "y"):
        fields[field] = dq.assess_field(view, driver_code, field)
    rank = {"missing": 0, "partial": 1, "frozen": 2, "unknown": 3, "full": 4}
    aggregate = min((finding["quality"] for finding in fields.values()),
                    key=lambda q: rank[q])
    return {"fields": fields, "aggregate": aggregate,
            "rules_version": dq.QUALITY_RULES_VERSION,
            "scope_note": "lap_scope_sentinel: Adapter assigns lap=1 to every "
                          "frame for scoping; the true lap number is not "
                          "retained in cache."}


def check_comparability(sel_a, sel_b, quality_a, quality_b, session_flags=None):
    """Decide whether two selected laps support comparison.

    Never blocks on compound or weather differences (reported as warnings).
    Blocks (comparable=False) only on: different segments, incomplete laps,
    or missing/unknown aggregate quality on a primary channel set.
    """
    warnings = list(sel_a.get("warnings", [])) + list(sel_b.get("warnings", []))
    flags = session_flags or {}
    if sel_a["segment"] != sel_b["segment"]:
        return {"comparable": False,
                "warnings": warnings + ["different_segments"],
                "details": {}}
    details = {}
    for tag, sel in (("A", sel_a), ("B", sel_b)):
        coverage_ok = sel["n_samples"] >= 25
        details[f"lap_{tag}_complete"] = coverage_ok
        if not coverage_ok:
            warnings.append(f"incomplete_lap_{tag}")
    if sel_a.get("compound") != sel_b.get("compound"):
        warnings.append("different_compounds:"
                        f"{sel_a.get('compound')}_vs_{sel_b.get('compound')}")
    for tag, quality in (("A", quality_a), ("B", quality_b)):
        agg = quality.get("aggregate")
        details[f"quality_{tag}"] = agg
        if agg in ("missing", "unknown"):
            warnings.append(f"insufficient_channel_quality_{tag}:{agg}")
        elif agg in ("partial", "frozen"):
            warnings.append(f"reduced_channel_quality_{tag}:{agg}")
    if flags.get("had_red_flag"):
        warnings.append("session_red_flag:per_lap_exposure_unknown")
    if flags.get("had_yellow"):
        warnings.append("session_yellow_flag:per_lap_exposure_unknown")
    comparable = all(details[f"lap_{t}_complete"] for t in ("A", "B"))
    comparable = comparable and all(
        quality.get("aggregate") not in ("missing", "unknown")
        for quality in (quality_a, quality_b))
    if sel_a.get("validity") == "unverified_not_retained":
        warnings.append("lap_validity_unverified_not_retained")
    return {"comparable": comparable, "warnings": sorted(set(warnings)),
            "details": details}


# --------------------------------------------------------------------------
# Distance alignment
# --------------------------------------------------------------------------

def _validate_distance(dist, label):
    finite = dist[np.isfinite(dist)]
    if len(finite) < 25:
        raise ValueError(f"{label}: fewer than 25 finite distance samples")
    steps = np.diff(finite)
    if np.any(steps <= 0):
        raise ValueError(f"{label}: distance not strictly increasing; "
                         "alignment refused")
    return float(finite[0]), float(finite[-1])


def interp_masked(x_grid, xp, fp):
    """Linear interpolation that refuses to fabricate across gaps.

    Values at grid points bracketed only by samples more than
    MAX_INTERP_GAP_M apart (or outside valid coverage) come back NaN.
    """
    xp = np.asarray(xp, dtype=np.float64)
    fp = np.asarray(fp, dtype=np.float64)
    valid = np.isfinite(xp) & np.isfinite(fp)
    order = np.argsort(xp[valid], kind="mergesort")
    xv = xp[valid][order]
    fv = fp[valid][order]
    if len(xv) < 2:
        return np.full_like(x_grid, math.nan, dtype=np.float64)
    # Deduplicate equal positions (keep first) so np.interp is well defined.
    keep = np.ones(len(xv), dtype=bool)
    keep[1:] = np.diff(xv) > 0
    xv, fv = xv[keep], fv[keep]
    if len(xv) < 2:
        return np.full_like(x_grid, math.nan, dtype=np.float64)
    out = np.interp(x_grid, xv, fv, left=math.nan, right=math.nan)
    # Mask grid points inside gaps wider than the allowance.
    gaps = np.diff(xv)
    for i, gap in enumerate(gaps):
        if gap > MAX_INTERP_GAP_M:
            out[(x_grid > xv[i]) & (x_grid < xv[i + 1])] = math.nan
    return out


def nearest_masked(x_grid, xp, fp):
    """Nearest-sample mapping for discrete channels (gear, DRS)."""
    xp = np.asarray(xp, dtype=np.float64)
    fp = np.asarray(fp, dtype=np.float64)
    valid = np.isfinite(xp) & np.isfinite(fp)
    xv = xp[valid]
    fv = fp[valid]
    if len(xv) == 0:
        return np.full_like(x_grid, math.nan, dtype=np.float64)
    idx = np.searchsorted(xv, x_grid)
    idx = np.clip(idx, 0, len(xv) - 1)
    left = np.clip(idx - 1, 0, len(xv) - 1)
    use_left = (idx > 0) & (np.abs(x_grid - xv[left]) <= np.abs(x_grid - xv[idx]))
    pick = np.where(use_left, left, idx)
    gap = np.abs(x_grid - xv[pick])
    out = fv[pick].astype(np.float64)
    out[gap > MAX_INTERP_GAP_M] = math.nan
    return out


def align_laps(series_a, series_b, grid_step=GRID_STEP_M):
    """Align two laps on a common within-lap distance grid.

    Returns dict with grid, per-driver traces, per-channel valid fractions
    and warnings. Throttle values outside 0..100 (incl. the 104 sentinel)
    are masked invalid BEFORE interpolation. Raises ValueError when either
    distance axis is unusable.
    """
    start_a, end_a = _validate_distance(series_a["dist"], "driver_A")
    start_b, end_b = _validate_distance(series_b["dist"], "driver_B")
    grid_start = max(start_a, start_b, 0.0)
    grid_end = min(end_a, end_b)
    shorter = min(end_a - start_a, end_b - start_b)
    if grid_end - grid_start < MIN_COVERAGE_FRACTION * shorter:
        raise ValueError("insufficient common distance overlap; "
                         "alignment refused")
    grid = np.arange(grid_start, grid_end + grid_step / 2.0, grid_step)
    warnings = []
    if grid_start > 0.0:
        warnings.append(f"grid_starts_at_{grid_start:.1f}m:pre_line_samples_excluded")
    aligned = {"grid": grid, "grid_start": grid_start, "grid_end": grid_end,
               "warnings": warnings, "drivers": {}}
    for tag, series in (("A", series_a), ("B", series_b)):
        dist = series["dist"]
        throttle = series["throttle"].copy()
        bad_throttle = ~np.isfinite(throttle) | (throttle < THROTTLE_VALID_MIN) | (
            throttle > THROTTLE_VALID_MAX)
        n_bad = int(np.count_nonzero(bad_throttle))
        if n_bad:
            warnings.append(f"throttle_invalid_masked_{tag}:{n_bad}_samples")
            throttle[bad_throttle] = math.nan
        traces = {
            "t": interp_masked(grid, dist, series["t"]),
            "speed": interp_masked(grid, dist, series["speed"]),
            "throttle": interp_masked(grid, dist, throttle),
            "brake": interp_masked(grid, dist, series["brake"]),
            "x": interp_masked(grid, dist, series["x"]),
            "y": interp_masked(grid, dist, series["y"]),
            "gear": nearest_masked(grid, dist, series["gear"]),
            "drs": nearest_masked(grid, dist, series["drs"]),
        }
        valid = {channel: float(np.count_nonzero(np.isfinite(values))) / len(grid)
                 for channel, values in traces.items()}
        for channel, fraction in valid.items():
            if fraction < 0.99:
                warnings.append(f"channel_coverage_{tag}_{channel}:{fraction:.3f}")
        aligned["drivers"][tag] = {"traces": traces, "valid_fraction": valid}
    aligned["warnings"] = sorted(set(warnings))
    return aligned


# --------------------------------------------------------------------------
# Throttle analysis
# --------------------------------------------------------------------------

def _valid_runs(mask):
    """Yield (start, end) index spans of True runs in a boolean mask."""
    runs = []
    start = None
    for i, flag in enumerate(mask):
        if flag and start is None:
            start = i
        elif not flag and start is not None:
            runs.append((start, i))
            start = None
    if start is not None:
        runs.append((start, len(mask)))
    return runs


def first_sustained_crossing(values, threshold=HIGH_THROTTLE_DEFAULT,
                             min_samples=SUSTAIN_SAMPLES_DEFAULT):
    """First index where values reach `threshold` and stay there.

    NaN breaks sustenance (no claim across missing data). Returns None when
    the threshold is never reached-and-held.
    """
    values = np.asarray(values, dtype=np.float64)
    above = np.isfinite(values) & (values >= threshold)
    for start, end in _valid_runs(above):
        if end - start >= min_samples:
            return int(start)
    return None


def find_lifts(values, high=HIGH_THROTTLE_DEFAULT,
               low=LIFT_THRESHOLD_DEFAULT,
               min_samples=SUSTAIN_SAMPLES_DEFAULT):
    """Throttle lifts: sustained drops below `low` after high running.

    A lift starts at the first sample below `low` that begins a below-low
    run of >= min_samples, considering only regions that previously
    reached `high`. Returns list of (start_index, length) in grid samples.
    """
    values = np.asarray(values, dtype=np.float64)
    reached_high = False
    lifts = []
    i, n = 0, len(values)
    while i < n:
        sample = values[i]
        if not math.isfinite(sample):
            i += 1
            continue
        if sample >= high:
            reached_high = True
            i += 1
            continue
        if reached_high and sample < low:
            j = i
            while j < n and np.isfinite(values[j]) and values[j] < low:
                j += 1
            if j - i >= min_samples:
                lifts.append((int(i), int(j - i)))
            i = j
            continue
        i += 1
    return lifts


def throttle_events(trace, grid, threshold=HIGH_THROTTLE_DEFAULT,
                    min_samples=SUSTAIN_SAMPLES_DEFAULT):
    """Deterministic throttle event set for one aligned trace."""
    pickup = first_sustained_crossing(trace, threshold, min_samples)
    lifts = find_lifts(trace, threshold, LIFT_THRESHOLD_DEFAULT, min_samples)
    finite = trace[np.isfinite(trace)]
    return {
        "threshold": float(threshold),
        "min_sustain_samples": int(min_samples),
        "pickup_index": pickup,
        "pickup_distance_m": float(grid[pickup]) if pickup is not None else None,
        "n_lifts": len(lifts),
        "lifts": [{"start_index": s, "length": ln,
                   "start_distance_m": float(grid[s]),
                   "end_distance_m": float(grid[min(s + ln, len(grid) - 1)])}
                  for s, ln in lifts],
        "mean_throttle_pct": float(np.mean(finite)) if len(finite) else None,
        "valid_fraction": float(len(finite)) / len(trace) if len(trace) else 0.0,
    }


def throttle_at(grid, trace, distance_m):
    """Throttle percentage at a reference distance (nearest grid sample)."""
    index = int(np.clip(np.searchsorted(grid, distance_m), 0, len(grid) - 1))
    value = trace[index]
    return None if not math.isfinite(value) else float(value)


def sustained_high_runs(values, threshold=HIGH_THROTTLE_DEFAULT,
                        min_samples=SUSTAIN_SAMPLES_DEFAULT):
    """All sustained high-throttle runs as (start, end) grid-index spans."""
    values = np.asarray(values, dtype=np.float64)
    above = np.isfinite(values) & (values >= threshold)
    return [(int(start), int(end)) for start, end in _valid_runs(above)
            if end - start >= min_samples]


def section_pickup(grid, trace, d_start, d_end,
                   threshold=HIGH_THROTTLE_DEFAULT,
                   min_samples=SUSTAIN_SAMPLES_DEFAULT,
                   dip_min_samples=2, lookback_m=200.0):
    """First sustained high-throttle pickup after a dip, inside a section.

    Returns (pickup_index, genuine_exit). A pickup counts as a corner exit
    only when a below-threshold dip run (>= ``dip_min_samples``) precedes
    it — either in the ``lookback_m`` metres before the window or inside
    the window itself. A window that opens on sustained full throttle with
    no preceding dip yields a carry-over pickup (genuine_exit False): both
    drivers were already flat, so there is no corner exit to compare. The
    first in-window run never shadows a later genuine exit: the search
    starts after the first qualifying dip.
    """
    grid = np.asarray(grid, dtype=np.float64)
    trace = np.asarray(trace, dtype=np.float64)
    idx0 = int(np.searchsorted(grid, d_start, side="left"))
    idx1 = int(np.searchsorted(grid, d_end, side="right"))
    idx0 = max(0, min(idx0, len(grid)))
    idx1 = max(idx0, min(idx1, len(grid)))
    look_start = max(0, idx0 - int(lookback_m / GRID_STEP_M))
    prior = trace[look_start:idx0]
    sub = trace[idx0:idx1]

    def _dip_runs(values):
        below = np.isfinite(values) & (values < threshold)
        return [(s, e) for s, e in _valid_runs(below) if e - s >= dip_min_samples]

    prior_dip = len(_dip_runs(prior)) > 0
    dips = _dip_runs(sub)
    if dips:
        # Search from the first in-window dip: an opening carry-over run
        # must not shadow the genuine exit that follows it.
        search_from = dips[0][0]
        rel = first_sustained_crossing(sub[search_from:], threshold, min_samples)
        if rel is None:
            return None, False
        return int(idx0 + search_from + rel), True
    if prior_dip:
        rel = first_sustained_crossing(sub, threshold, min_samples)
        if rel is None:
            return None, False
        return int(idx0 + rel), True
    rel = first_sustained_crossing(sub, threshold, min_samples)
    if rel is None:
        return None, False
    return int(idx0 + rel), False


# --------------------------------------------------------------------------
# Time-loss attribution
# --------------------------------------------------------------------------

def cumulative_delta(t_a, t_b):
    """delta_t(d) = elapsed_A(d) - elapsed_B(d); positive => A behind."""
    return np.asarray(t_a, dtype=np.float64) - np.asarray(t_b, dtype=np.float64)


def section_deltas(grid, t_a, t_b, sections):
    """Per-section time differences with develop-vs-persist split.

    section_delta = [tA(end)-tA(start)] - [tB(end)-tB(start)]; positive
    means A took longer through the section. Also reports the cumulative
    delta at each boundary so persisted gaps are not mistaken for new ones.
    Sections fully outside finite timing coverage yield delta None.
    """
    results = []
    delta = cumulative_delta(t_a, t_b)
    for section in sections:
        start, end = section["d_start"], section["d_end"]
        mask = (grid >= start) & (grid <= end)
        idx = np.nonzero(mask)[0]
        row = {"id": section["id"], "label": section["label"],
               "kind": section.get("kind", "distance"),
               "d_start": float(start), "d_end": float(end),
               "section_delta_s": None, "delta_start_s": None,
               "delta_end_s": None}
        if len(idx) >= 2:
            ta = t_a[idx]
            tb = t_b[idx]
            if (np.all(np.isfinite(ta)) and np.all(np.isfinite(tb))):
                row["section_delta_s"] = float(
                    (ta[-1] - ta[0]) - (tb[-1] - tb[0]))
        if len(idx) >= 1:
            first, last = delta[idx[0]], delta[idx[-1]]
            row["delta_start_s"] = float(first) if math.isfinite(first) else None
            row["delta_end_s"] = float(last) if math.isfinite(last) else None
        results.append(row)
    return results


def check_end_residual(delta, lap_time_a, lap_time_b):
    """Compare cumulative delta at finish vs official lap-time difference."""
    finite = delta[np.isfinite(delta)]
    if len(finite) == 0:
        return {"residual_s": None, "within_tolerance": False,
                "note": "no_finite_delta_coverage"}
    measured = float(finite[-1])
    official = float(lap_time_a - lap_time_b)
    residual = measured - official
    return {"residual_s": float(residual),
            "within_tolerance": abs(residual) <= MAX_END_RESIDUAL_S,
            "note": "timing_definition_residual_explained"}


# --------------------------------------------------------------------------
# Corner metadata and section resolution
# --------------------------------------------------------------------------

def normalize_corners(corners):
    """Normalize official corner metadata to [{number, letter, distance_m}].

    Accepts a DataFrame-like (Number/Letter/Distance) or a list of dicts.
    Raises ValueError when required fields are absent. Never invents turns.
    """
    if corners is None:
        raise ValueError("no_corner_metadata")
    rows = corners.to_dict("records") if hasattr(corners, "to_dict") else list(corners)
    out = []
    for row in rows:
        try:
            number = int(row.get("Number"))
            distance = float(row.get("Distance"))
        except (TypeError, ValueError):
            raise ValueError("corner_metadata_missing_number_or_distance")
        if not math.isfinite(distance):
            raise ValueError("corner_metadata_non_finite_distance")
        out.append({"number": number, "letter": row.get("Letter") or "",
                    "distance_m": distance})
    if not out:
        raise ValueError("corner_metadata_empty")
    return out


def _corner_speed(trace_speed, grid, distance_m):
    idx = int(np.clip(np.searchsorted(grid, distance_m), 0, len(grid) - 1))
    value = trace_speed[idx]
    return None if not math.isfinite(value) else float(value)


def validate_corners(corner_list, aligned, lap_length_m):
    """Validate official corners against both aligned laps.

    Checks: strictly increasing distances inside the lap extent; empirical
    speed-dip alignment (corner speed < 92% of nearby straight speed for at
    least one driver). Weak corners are excluded with reasons, never
    renumbered or replaced.
    """
    grid = aligned["grid"]
    validated, excluded = [], []
    distances = [corner["distance_m"] for corner in corner_list]
    if any(later <= earlier for earlier, later in zip(distances, distances[1:])):
        return {"validated": [], "excluded": [
            {"corner": corner, "reason": "non_increasing_distances"}
            for corner in corner_list]}
    for corner in corner_list:
        dist = corner["distance_m"]
        if dist < grid[0] - 50.0 or dist > grid[-1] + 50.0:
            excluded.append({"corner": corner, "reason": "outside_lap_extent"})
            continue
        dip = False
        for tag in ("A", "B"):
            speed = aligned["drivers"][tag]["traces"]["speed"]
            corner_v = _corner_speed(speed, grid, dist)
            if corner_v is None:
                continue
            lo = max(grid[0], dist - CORNER_WINDOW_M - CORNER_REF_SPAN_M)
            hi = min(grid[-1], dist + CORNER_WINDOW_M + CORNER_REF_SPAN_M)
            band = (grid >= lo) & (grid <= hi)
            near = (grid >= dist - CORNER_WINDOW_M) & (grid <= dist + CORNER_WINDOW_M)
            ref_vals = speed[band & ~near]
            ref_vals = ref_vals[np.isfinite(ref_vals)]
            if len(ref_vals) == 0:
                continue
            if corner_v < CORNER_DIP_FRACTION * float(np.median(ref_vals)):
                dip = True
        if dip:
            validated.append({**corner, "alignment": "strong"})
        else:
            excluded.append({"corner": corner,
                             "reason": "no_speed_dip_alignment"})
    _ = lap_length_m
    return {"validated": validated, "excluded": excluded}


def _corner_label(corner):
    letter = f"{corner['letter']}" if corner.get("letter") else ""
    return f"T{corner['number']}{letter}"


def resolve_sections(validated_corners, grid_start, grid_end, drs_zones=None):
    """Build attribution sections from validated corners (or fallback).

    Corner sections run boundary-to-boundary (start -> C1 -> ... -> Cn ->
    end) labelled with official turn numbers. Without corners, sections
    fall back to explicitly labelled equal distance thirds; DRS zones are
    reported as overlays, never as attribution sections.
    """
    sections = []
    if validated_corners:
        bounds = [grid_start] + [corner["distance_m"] for corner in validated_corners] + [grid_end]
        labels = []
        prev = "START"
        for corner in validated_corners:
            labels.append(f"{prev}->{_corner_label(corner)}")
            prev = _corner_label(corner)
        labels.append(f"{prev}->FINISH")
        for i, (start, end) in enumerate(zip(bounds, bounds[1:])):
            if end - start < GRID_STEP_M:
                continue
            sections.append({"id": f"corner_seg_{i}", "label": labels[i],
                             "d_start": float(max(start, grid_start)),
                             "d_end": float(min(end, grid_end)),
                             "kind": "corner"})
    else:
        span = grid_end - grid_start
        for i, name in enumerate(("first", "second", "final")):
            sections.append({
                "id": f"fallback_seg_{i}", "label": f"{name}_third_fallback",
                "d_start": float(grid_start + span * i / 3.0),
                "d_end": float(grid_start + span * (i + 1) / 3.0),
                "kind": "distance_fallback"})
    overlays = []
    for zone in drs_zones or []:
        try:
            overlays.append({"d_start": float(zone["zone_start"]),
                             "d_end": float(zone.get("zone_end") or zone["zone_start"])})
        except (TypeError, ValueError, KeyError):
            continue
    return {"sections": sections, "drs_overlays": overlays,
            "corner_based": bool(validated_corners)}


# --------------------------------------------------------------------------
# Findings engine (deterministic, causal restraint enforced)
# --------------------------------------------------------------------------

def _finding(finding_id, kind, section, measured, evidence, interpretation,
             measurement_confidence, causal_confidence, missing_evidence,
             next_steps, quality, provenance):
    text = " ".join([interpretation] + [str(value) for value in measured.values()])
    lowered = text.lower()
    banned = [stem for stem in BANNED_CAUSAL_STEMS if stem in lowered]
    if banned:
        raise ValueError(f"banned_causal_language:{banned}")
    return {"id": finding_id, "kind": kind, "section": section,
            "measured": measured, "evidence": evidence,
            "interpretation": interpretation,
            "measurement_confidence": measurement_confidence,
            "causal_confidence": causal_confidence,
            "missing_evidence": list(missing_evidence),
            "next_steps": list(next_steps), "quality": quality,
            "provenance": provenance}


def _quality_note(quality_a, quality_b):
    worst_rank = {"missing": 0, "partial": 1, "frozen": 2, "unknown": 3, "full": 4}
    worst = min((quality_a.get("aggregate", "unknown"),
                 quality_b.get("aggregate", "unknown")),
                key=lambda q: worst_rank.get(q, 3))
    return worst


def generate_findings(analysis):
    """Deterministic evidence-backed findings for a completed analysis.

    Causal restraint: interpretations use only measured-difference phrasing
    ("consistent with ..."); any banned causal stem raises instead of
    shipping an overclaim. Causal confidence is 'withheld' unless at least
    two independent channels agree in direction on full-quality data.
    """
    sel_a = analysis["selection"]["A"]
    sel_b = analysis["selection"]["B"]
    code_a = sel_a["driver"]
    code_b = sel_b["driver"]
    segment = sel_a["segment"]
    direction = f"{code_a}_vs_{code_b}"
    quality_worst = _quality_note(analysis["quality"]["A"], analysis["quality"]["B"])
    full_quality = quality_worst == "full"
    provenance = {"channels": "unknown_linear_25hz_resample_markers_discarded",
                  "sectors": "derived_timing_feed_aggregation",
                  "corners": ("observed_official_metadata"
                              if analysis["sections"]["corner_based"]
                              else "fallback_distance_splits")}
    findings = []
    grid = analysis["aligned"]["grid"]
    traces_a = analysis["aligned"]["drivers"]["A"]["traces"]
    traces_b = analysis["aligned"]["drivers"]["B"]["traces"]
    delta = analysis["delta"]

    # 1. Overall lap delta (official timing).
    lap_delta = sel_a["lap_time_s"] - sel_b["lap_time_s"]
    leader = code_a if lap_delta < 0 else code_b
    lap_interp = (f"{leader} set the faster lap by {abs(lap_delta):.3f} s "
                  f"({sel_a['lap_time_s']:.3f} s vs {sel_b['lap_time_s']:.3f} s).")
    residual = analysis["residual"]
    if (residual["residual_s"] is not None
            and abs(residual["residual_s"]) > 0.02):
        lap_interp += (f" Telemetry/official residual "
                       f"{residual['residual_s']:+.3f} s comes from edge "
                       f"coverage outside the common grid.")
    findings.append(_finding(
        f"lap_delta:{direction}:{segment}", "lap_delta",
        {"label": "full_lap", "d_start": float(grid[0]), "d_end": float(grid[-1])},
        {"delta_s": round(lap_delta, 3), "unit": "s",
         "lap_a_s": sel_a["lap_time_s"], "lap_b_s": sel_b["lap_time_s"]},
        {"channel": "lap_timing", "residual": analysis["residual"]},
        lap_interp,
        "high" if full_quality else "medium", "withheld",
        ["on-track causation requires corner-level evidence below"],
        ["Inspect section deltas to locate where the gap developed."],
        {"aggregate": quality_worst}, provenance))

    # 2. Per-section deltas.
    for row in analysis["section_rows"]:
        if row["section_delta_s"] is None:
            findings.append(_finding(
                f"section_unresolved:{row['id']}:{direction}", "coverage",
                row, {"section_delta_s": None},
                {"channel": "lap_timing"},
                f"No time attribution for {row['label']}: insufficient aligned "
                "timing coverage.",
                "low", "withheld", ["aligned timing across the section"],
                ["Check channel coverage warnings for gaps."],
                {"aggregate": quality_worst}, provenance))
            continue
        shift = row["section_delta_s"]
        if abs(shift) < 0.005:
            continue  # below reporting significance; not a finding
        favours = code_b if shift > 0 else code_a
        start_gap = row["delta_start_s"] or 0.0
        if abs(start_gap) < 0.05:
            verb = "developed"  # gap opened up inside this section
            persisted = False
        elif (shift > 0) != (start_gap > 0):
            verb = "reversed"
            persisted = False
        elif abs(row["delta_end_s"] or 0.0) > abs(start_gap):
            verb = "extended"
            persisted = False
        else:
            verb = "developed"
            persisted = abs(shift) < 0.3 * abs(start_gap)
        extra = (" The gap largely persisted through this section."
                 if persisted else "")
        findings.append(_finding(
            f"section_delta:{row['id']}:{direction}", "section_delta", row,
            {"section_delta_s": round(shift, 3), "unit": "s",
             "delta_start_s": row["delta_start_s"], "delta_end_s": row["delta_end_s"]},
            {"channel": "lap_timing"},
            f"The time delta shifted by {abs(shift):.3f} s in {favours}'s "
            f"favour across {row['label']} ({verb}).{extra}",
            "high" if full_quality else "medium", "withheld",
            ["causal attribution needs throttle/speed agreement"],
            [f"Compare throttle pickup and exit speed in {row['label']}."],
            {"aggregate": quality_worst}, provenance))

    # 3. Per-section throttle pickup comparison (genuine corner exits).
    thr_cfg = analysis["config"]["high_throttle_pct"]
    sustain_cfg = analysis["config"]["sustain_samples"]
    pickup_compared = 0
    for row in analysis["section_rows"]:
        pick_a, genuine_a = section_pickup(
            grid, traces_a["throttle"], row["d_start"], row["d_end"],
            thr_cfg, sustain_cfg)
        pick_b, genuine_b = section_pickup(
            grid, traces_b["throttle"], row["d_start"], row["d_end"],
            thr_cfg, sustain_cfg)
        if pick_a is None or pick_b is None:
            continue  # no sustained pickup in this section: nothing to compare
        if not (genuine_a or genuine_b):
            continue  # both carried full throttle in: no corner exit here
        d_a, d_b = float(grid[pick_a]), float(grid[pick_b])
        gap_m = d_b - d_a
        earlier = code_a if gap_m > 0 else code_b
        ref_d = max(d_a, d_b) + 40.0
        exit_a = throttle_at(grid, traces_a["speed"], ref_d)
        exit_b = throttle_at(grid, traces_b["speed"], ref_d)
        agree = (exit_a is not None and exit_b is not None
                 and ((exit_b - exit_a) > 0) == (gap_m < 0))
        causal = ("medium" if (agree and full_quality) else
                  ("low" if agree else "withheld"))
        interp = (f"{earlier} reached {thr_cfg:.0f}% throttle "
                  f"{abs(gap_m):.0f} m earlier in {row['label']}.")
        if agree:
            faster = code_b if (exit_b - exit_a) > 0 else code_a
            interp += (f" {faster} was {abs(exit_b - exit_a):.1f} km/h faster "
                       f"40 m after the later pickup, consistent with more "
                       f"effective acceleration through the section.")
        findings.append(_finding(
            f"throttle_pickup:{row['id']}:{direction}", "throttle_pickup",
            {"label": row["label"], "d_start": float(min(d_a, d_b)),
             "d_end": float(ref_d)},
            {"pickup_a_m": round(d_a, 1), "pickup_b_m": round(d_b, 1),
             "gap_m": round(gap_m, 1), "threshold_pct": thr_cfg,
             "exit_speed_a_kmh": exit_a, "exit_speed_b_kmh": exit_b},
            {"channels": ["throttle", "speed", "distance"]}, interp,
            "high" if full_quality else "medium", causal,
            ([] if agree else ["exit-speed corroboration"]),
            ["Check gear traces through the same corner exit.",
             "Check wind/weather differences between the two runs."],
            {"aggregate": quality_worst}, provenance))
        pickup_compared += 1
    if pickup_compared == 0:
        findings.append(_finding(
            f"throttle_pickup_unresolved:{direction}", "coverage",
            {"label": "pickup_comparison"},
            {"sections_compared": 0},
            {"channels": ["throttle"]},
            "No genuine corner-exit pickup found in any section for at "
            "least one driver: pickup comparison withheld.",
            "low", "withheld", ["sustained high-throttle runs after a dip"],
            ["Inspect throttle coverage warnings for masked intervals."],
            {"aggregate": quality_worst}, provenance))

    # 4. Lifts / interruptions.
    for tag, code in (("A", code_a), ("B", code_b)):
        lifts = analysis["throttle"][tag]["lifts"]
        if not lifts:
            continue
        biggest = max(lifts, key=lambda lift: lift["length"])
        span_m = biggest["end_distance_m"] - biggest["start_distance_m"]
        findings.append(_finding(
            f"throttle_lift:{code}:{direction}", "throttle_lift",
            {"label": "lift", "d_start": biggest["start_distance_m"],
             "d_end": biggest["end_distance_m"]},
            {"length_m": round(span_m, 1),
             "n_lifts_total": len(lifts), "unit": "m"},
            {"channels": ["throttle"]},
            f"{code} showed {len(lifts)} throttle lift(s); the longest "
            f"covers {span_m:.0f} m. Lifts cost time only if the alternative "
            "line permits earlier sustained throttle — not established here.",
            "high" if full_quality else "medium", "withheld",
            ["whether the lift was forced (traffic, error, tactic)"],
            ["Cross-check speed/gear traces and sector timing around the lift."],
            {"aggregate": quality_worst}, provenance))

    # 5. Residual / timing-definition finding.
    residual = analysis["residual"]
    if residual["residual_s"] is not None and not residual["within_tolerance"]:
        findings.append(_finding(
            f"timing_residual:{direction}", "coverage",
            {"label": "full_lap"}, {"residual_s": round(residual["residual_s"], 3)},
            {"channel": "lap_timing"},
            f"Cumulative telemetry delta and official lap times differ by "
            f"{residual['residual_s']:.3f} s: section attribution below this "
            f"precision is not reported.",
            "low", "withheld", ["higher-rate timing"],
            ["Treat sub-0.1 s section claims as indicative only."],
            {"aggregate": quality_worst}, provenance))
    return findings


# --------------------------------------------------------------------------
# Top-level analysis entry point
# --------------------------------------------------------------------------

def analyze(payload, identity, segment, code_a, code_b, corners=None,
            config=None):
    """Run the full qualifying comparison. Read-only over all inputs.

    ``payload``: prepared quali cache payload. ``identity``: dict with
    year/round/event for reporting. ``corners``: official corner metadata
    (DataFrame-like or list) or None for distance-fallback sections.
    Returns a presentation-ready AnalysisResult dict.
    """
    cfg = dict(config or {})
    grid_step = float(cfg.get("grid_step_m", GRID_STEP_M))
    threshold = float(cfg.get("high_throttle_pct", HIGH_THROTTLE_DEFAULT))
    sustain = int(cfg.get("sustain_samples", SUSTAIN_SAMPLES_DEFAULT))

    sel_a, series_a = select_lap(payload, segment, code_a)
    sel_b, series_b = select_lap(payload, segment, code_b)
    frames_a = payload["telemetry"][code_a][segment]["frames"]
    frames_b = payload["telemetry"][code_b][segment]["frames"]
    quality_a = assess_lap_quality(frames_a, code_a)
    quality_b = assess_lap_quality(frames_b, code_b)

    session_flags = {}
    seg_statuses = payload["telemetry"][code_a][segment].get("track_statuses", [])
    codes = {str(status.get("status")) for status in seg_statuses}
    session_flags["had_red_flag"] = "5" in codes
    session_flags["had_yellow"] = "2" in codes
    comparability = check_comparability(sel_a, sel_b, quality_a, quality_b,
                                        session_flags)
    result = {"rules_version": ANALYSIS_RULES_VERSION,
              "identity": dict(identity), "segment": segment,
              "selection": {"A": sel_a, "B": sel_b},
              "quality": {"A": quality_a, "B": quality_b},
              "comparability": comparability,
              "aligned": None, "delta": None, "section_rows": [],
              "sections": None, "corners": None, "throttle": None,
              "residual": None, "findings": [],
              "config": {"grid_step_m": grid_step,
                         "high_throttle_pct": threshold,
                         "sustain_samples": sustain}}
    if not comparability["comparable"]:
        result["findings"] = []
        return result

    aligned = align_laps(series_a, series_b, grid_step)
    grid = aligned["grid"]
    traces_a = aligned["drivers"]["A"]["traces"]
    traces_b = aligned["drivers"]["B"]["traces"]
    delta = cumulative_delta(traces_a["t"], traces_b["t"])

    corner_info = {"mode": "fallback", "validated": [], "excluded": [],
                   "warnings": []}
    sections_info = None
    try:
        normalized = normalize_corners(corners)
        validation = validate_corners(
            normalized, aligned,
            lap_length_m=float(max(np.nanmax(series_a["dist"]),
                                   np.nanmax(series_b["dist"]))))
        corner_info = {"mode": "corner" if validation["validated"] else "fallback",
                       "validated": validation["validated"],
                       "excluded": validation["excluded"],
                       "warnings": [f"corner_excluded:{item['corner'].get('number')}:"
                                    f"{item['reason']}" for item in validation["excluded"]]}
        sections_info = resolve_sections(
            validation["validated"], float(grid[0]), float(grid[-1]),
            payload["telemetry"][code_a][segment].get("drs_zones", []))
    except ValueError as exc:
        corner_info["warnings"].append(f"corners_unavailable:{exc}")
        sections_info = resolve_sections(
            [], float(grid[0]), float(grid[-1]),
            payload["telemetry"][code_a][segment].get("drs_zones", []))
    section_rows = section_deltas(grid, traces_a["t"], traces_b["t"],
                                  sections_info["sections"])
    residual = check_end_residual(delta, sel_a["lap_time_s"], sel_b["lap_time_s"])
    throttle = {tag: throttle_events(aligned["drivers"][tag]["traces"]["throttle"],
                                     grid, threshold, sustain)
                for tag in ("A", "B")}

    result.update({"aligned": {"grid": grid,
                               "drivers": aligned["drivers"],
                               "grid_start": aligned["grid_start"],
                               "grid_end": aligned["grid_end"],
                               "warnings": aligned["warnings"]},
                   "delta": delta,
                   "section_rows": section_rows,
                   "sections": sections_info,
                   "corners": corner_info,
                   "throttle": throttle,
                   "residual": residual})
    result["findings"] = generate_findings(result)
    return result
