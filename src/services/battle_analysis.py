"""Battle Reconstruction & Overtaking Intelligence core (Update 8).

Deterministic, read-only analysis over prepared race replay payloads
(see ``get_race_telemetry`` cache shape). No GUI, no network, no ML.
Inputs are read but never mutated; the replay-frame schema, TCP payloads,
cache format and insight contracts are untouched.

Three certainty levels are kept strictly separate:
- battle detected: two drivers ran in sustained proximity (gap + persistence).
- position change detected: their relative order flipped and persisted.
- on-track overtake supported: the flip survives alternative explanations
  (pit cycle, non-running car, neutralized conditions) with corroborating
  gap/telemetry evidence.

Units: time seconds (frame ``t``), distance metres (within-lap ``dist``),
speed km/h (source contract), gaps in metres and seconds
(gap_s = gap_m / trailing-car speed, trailing speed floored at 20 m/s).

Gap semantics (verified, Update 5): ``dist`` resets each lap, so pairwise
gaps are computed only for same-lap pairs as
gap_m = dist_ahead - dist_trailer. Cross-lap pairs are not battles.

Pipeline: build step matrices -> position-change detection -> event
classification -> battle lifecycle reconstruction -> approach analysis ->
evidence-backed findings. Analysis runs on a 5 Hz step grid (SAMPLE_STEP);
event intervals carry ±step uncertainty, documented, never sharpened.
"""

import math

import numpy as np

ANALYSIS_RULES_VERSION = 1

# Detection grid: every 5th frame at 25 FPS => 0.2 s steps.
SAMPLE_STEP = 5
STEP_DT = 0.2

# Battle thresholds (engineering heuristics, documented as such).
BATTLE_ENTER_S = 1.0    # gap below this may open a battle
BATTLE_EXIT_S = 2.0     # gap above this closes battle range (hysteresis)
ATTACK_GAP_S = 0.5      # below this is an attack candidate window
BATTLE_MIN_SUSTAIN_S = 3.0   # seconds inside range to confirm a battle
EXIT_CONFIRM_S = 5.0    # seconds above exit gap to resolve as aborted
PERSIST_S = 10.0        # new order must hold this long to confirm a pass
CLOSE_WINDOW_S = 5.0    # trailing window for closing-rate slope
CLOSE_MIN_POINTS = 8    # valid samples required for a closing-rate fit
TTC_MIN_RATE = 0.5      # m/s; slower closing => no time-to-contact
TTC_MAX_GAP_S = 5.0     # no TTC beyond this gap
PIT_WINDOW_S = 30.0     # tyre-change evidence window around an event
NONRUN_WINDOW_S = 30.0  # window for the non-running heuristic
# Frozen iff dist range < this AND lap frozen over the window. Speed is
# deliberately not required (clamped telemetry freezes mid-race values).
NONRUN_DIST_M = 50.0
STARTUP_IGNORE_S = 5.0  # flips inside session start are telemetry settling
APPROACH_GAP_S = 3.0    # below this an adjacent pair opens an episode
MAX_DATA_GAP_S = 5.0    # tolerated NaN-gap run before interruption
APPROACH_WINDOW_S = 30.0  # approach context before an event
AFTER_WINDOW_S = 10.0     # aftermath context after an event

# Lifecycle states (not every battle visits every state).
ST_APPROACHING = "approaching"
ST_IN_RANGE = "within_battle_range"
ST_ATTACK = "attack_candidate"
ST_POS_CANDIDATE = "position_change_candidate"
ST_CONFIRMED = "confirmed_pass"
ST_DEFENDED = "defended_or_aborted"
ST_RESOLVED = "battle_resolved"
ST_UNRESOLVED = "unresolved"

# Event classifications (stable vocabulary).
CL_PASS = "on_track_pass_supported"
CL_PIT = "pit_cycle_position_change"
CL_RETIRE = "retirement_or_nonrunning_change"
CL_RACE_CONTROL = "race_control_or_classification_change"
CL_UNRESOLVED = "position_change_unresolved"

# Mechanism taxonomy (descriptive patterns, never root causes).
MECH_STRAIGHT = "straight_approach_advantage"
MECH_BRAKING = "braking_phase_pass"
MECH_EXIT = "corner_exit_advantage"
MECH_PIT = "pit_cycle_change"
MECH_OTHER = "other_supported"
MECH_UNRESOLVED = "unresolved"

# Causal vocabulary guard (Update 7 principle: measurement, not intent).
BANNED_CAUSAL_STEMS = (
    "traction", "grip", "downforce", "power unit", "engine advantage",
    "talent", "skill", "better driver", "superior driver",
    "defensive intent", "driver intent",
)


# --------------------------------------------------------------------------
# Adapter: step matrices over the prepared payload (read-only)
# --------------------------------------------------------------------------

def build_step_matrices(payload, sample_step=SAMPLE_STEP, progress=None,
                        cancelled=None):
    """Normalize payload frames into per-driver step series.

    Returns dict with step times, per-driver dict of float64 arrays
    (pos, lap, dist, speed, throttle, brake, gear, tyre, x, y), the order
    list per step, and frame-index mapping. Never mutates the payload.
    """
    frames = payload.get("frames", [])
    if not frames:
        raise ValueError("empty session: no frames to analyse")
    codes = sorted(frames[0].get("drivers", {}).keys())
    if not codes:
        raise ValueError("empty session: no drivers in first frame")
    index = {code: i for i, code in enumerate(codes)}
    step_ids = list(range(0, len(frames), sample_step))
    n_steps = len(step_ids)
    series = {code: {key: np.full(n_steps, math.nan) for key in
                     ("pos", "lap", "dist", "speed", "throttle", "brake",
                      "gear", "tyre", "x", "y")} for code in codes}
    times = np.empty(n_steps)
    orders = []
    for s, fi in enumerate(step_ids):
        if progress is not None and s % 2000 == 0:
            progress("matrices", s, n_steps, f"Reading frames {s}/{n_steps}")
        if cancelled is not None and cancelled():
            raise _Cancelled()
        frame = frames[fi]
        times[s] = frame.get("t", math.nan)
        drivers = frame.get("drivers", {})
        # Drivers with non-finite lap/dist sort deterministically last
        # (missing data trails the field); NaN never compares equal.
        def _rank_key(item):
            info = item[1]
            try:
                lap = float(info.get("lap", math.nan))
            except (TypeError, ValueError):
                lap = math.nan
            try:
                dist = float(info.get("dist", math.nan))
            except (TypeError, ValueError):
                dist = math.nan
            if not math.isfinite(lap):
                lap = -math.inf
            if not math.isfinite(dist):
                dist = -math.inf
            return (lap, dist)
        ranked = sorted(drivers.items(), key=_rank_key, reverse=True)
        orders.append([code for code, _ in ranked])
        for position, (code, info) in enumerate(ranked, start=1):
            entry = series[code]
            entry["pos"][s] = position
            for key in ("lap", "dist", "speed", "throttle", "brake",
                        "gear", "tyre", "x", "y"):
                try:
                    value = float(info.get(key, math.nan))
                except (TypeError, ValueError):
                    value = math.nan
                entry[key][s] = value
    return {"codes": codes, "index": index, "times": times,
            "frame_ids": np.asarray(step_ids, dtype=np.int64),
            "orders": orders, "series": series,
            "step_dt": sample_step / 25.0, "n_frames": len(frames)}


class _Cancelled(Exception):
    """Internal cooperative-cancellation signal for staged analysis."""


def status_at(track_statuses, t):
    """Track-status code active at time t (defensive linear scan)."""
    current = "1"
    for status in track_statuses or []:
        try:
            if float(status.get("start_time", 0.0)) <= t:
                current = str(status.get("status", current))
            else:
                break
        except (TypeError, ValueError):
            continue
    return current


def tyre_changes(series, times, code):
    """Sorted list of (t, old, new) compound changes for a driver."""
    tyre = series[code]["tyre"]
    changes = []
    prev = None
    for s in range(len(times)):
        value = tyre[s]
        if not math.isfinite(value):
            continue
        if prev is not None and value != prev:
            changes.append((float(times[s]), int(prev), int(value)))
        prev = value
    return changes


def is_non_running(series, times, code, t_center,
                   window=NONRUN_WINDOW_S):
    """Heuristic non-running check (documented, never a fault claim).

    Forward-looking window [t_center, t_center + window]: true when
    distance range < 50 m and lap number never advances after the moment
    in question. Forward-looking so a freeze onset AT the event is caught
    even though earlier samples show running (a symmetric window would
    straddle the onset and miss it). Speed is deliberately NOT required:
    clamped carry-forward telemetry freezes mid-race values (observed:
    finished cars frozen at ~250 km/h with static dist). Pit stops also
    freeze briefly, so callers must check pit evidence BEFORE this rule;
    residual risk (e.g. a wing-only stop with a pass, or a freeze onset
    just after an unrelated flip) is listed as an alternative, never
    declared a retirement.
    """
    sel = (times >= t_center) & (times <= t_center + window)
    if not np.any(sel):
        return False
    entry = series[code]
    dist = entry["dist"][sel]
    lap = entry["lap"][sel]
    finite_dist = dist[np.isfinite(dist)]
    finite_lap = lap[np.isfinite(lap)]
    if len(finite_dist) == 0 or len(finite_lap) == 0:
        return False
    return (bool(np.max(finite_dist) - np.min(finite_dist) < NONRUN_DIST_M)
            and bool(np.all(finite_lap == finite_lap[0])))


# --------------------------------------------------------------------------
# Position-change detection
# --------------------------------------------------------------------------

def detect_position_changes(matrices, persist_s=PERSIST_S):
    """Find candidate order flips with persistence and event intervals.

    Vectorized over a (steps × drivers) rank matrix: per-pair sign series
    locate flips, and persistence is checked with slice ops rather than
    per-sample Python scans. Each candidate records last-pre-order /
    first-post-order steps; confirmation requires the new order to hold
    >= persist_s. Non-confirmed flips are transient candidates (possible
    attack or jitter, never confirmed passes). Event interval =
    [t_last_pre, t_first_post] at step resolution (±STEP_DT documented
    uncertainty). Deterministic ids after time/pair sort.
    """
    times = matrices["times"]
    series = matrices["series"]
    codes = matrices["codes"]
    step_dt = matrices["step_dt"]
    n = len(times)
    persist_steps = max(1, int(round(persist_s / step_dt)))
    rank = np.full((n, len(codes)), math.nan)
    for j, code in enumerate(codes):
        pos = series[code]["pos"]
        rank[:, j] = np.where(np.isfinite(pos), pos, math.nan)
    t_startup = float(times[0]) + STARTUP_IGNORE_S if len(times) else 0.0
    events = []
    for a in range(len(codes)):
        for b in range(a + 1, len(codes)):
            valid = np.isfinite(rank[:, a]) & np.isfinite(rank[:, b])
            diff = rank[:, a] - rank[:, b]
            diff = np.where(valid & (diff != 0), np.sign(diff), math.nan)
            prev = diff[:-1]
            cur = diff[1:]
            flips = np.nonzero(np.isfinite(prev) & np.isfinite(cur)
                               & (prev != cur))[0] + 1
            for s in flips:
                s = int(s)
                if float(times[s]) < t_startup:
                    continue  # telemetry-startup settling, not racing
                ahead_before = codes[a] if diff[s - 1] < 0 else codes[b]
                ahead_after = codes[a] if diff[s] < 0 else codes[b]
                want = diff[s]
                window = diff[s:s + persist_steps]
                good = (np.isfinite(window) & (window == want)
                        & (window != 0))
                # Hold length: leading run of the new order.
                run = np.nonzero(~good)[0]
                hold = int(run[0]) if len(run) else len(window)
                # Extend hold beyond the persistence window for reporting.
                s_end = s + hold - 1
                while (s_end + 1 < n and np.isfinite(diff[s_end + 1])
                       and diff[s_end + 1] == want):
                    s_end += 1
                    if s_end - s > 36000:  # bounded paranoia cap
                        break
                hold_total = s_end - s + 1
                confirmed = (hold * step_dt) >= persist_s
                events.append({
                    "pair": (ahead_before, ahead_after),
                    "overtaker": ahead_after,
                    "overtaken": ahead_before,
                    "t_flip": float(times[s]),
                    "t_last_pre": float(times[s - 1]),
                    "t_first_post": float(times[s]),
                    "t_confirm_end": float(times[min(s_end, n - 1)]),
                    "hold_s": hold_total * step_dt,
                    "confirmed": bool(confirmed),
                    "transient": bool(not confirmed),
                    "step": s,
                })
    events.sort(key=lambda e: (e["t_flip"], e["pair"]))
    # Stable event ids after deterministic sort.
    for i, event in enumerate(events):
        event["id"] = f"poschg_{i:04d}"
    return events


# --------------------------------------------------------------------------
# Quality integration (Update 6 reuse)
# --------------------------------------------------------------------------

def assess_window_quality(payload, code_a, code_b, t_start, t_end,
                          channels=("speed", "dist", "throttle", "brake")):
    """Field quality for two drivers over a time window via data_quality.

    Returns {code: {field: finding}} plus worst aggregates. Read-only.
    """
    from src.services import data_quality as dq

    frames = payload.get("frames", [])
    n = len(frames)
    if n == 0:
        return {"A": {}, "B": {}, "aggregate": "unknown"}
    # Frame ids whose t falls in the window (frames are time-ordered).
    start_id = 0
    end_id = n
    for i, frame in enumerate(frames):
        if frame.get("t", 0.0) < t_start:
            start_id = i
        if frame.get("t", 0.0) <= t_end:
            end_id = i + 1
    out = {}
    for tag, code in (("A", code_a), ("B", code_b)):
        fields = {}
        for field in channels:
            fields[field] = dq.assess_field(frames, code, field,
                                            start_id, end_id)
        out[tag] = fields
    rank = {"missing": 0, "partial": 1, "frozen": 2, "unknown": 3, "full": 4}
    worst = "full"
    for tag in ("A", "B"):
        for finding in out[tag].values():
            if rank[finding["quality"]] < rank[worst]:
                worst = finding["quality"]
    out["aggregate"] = worst
    return out


# --------------------------------------------------------------------------
# Overtake classification
# --------------------------------------------------------------------------

def classify_event(event, matrices, payload, tyre_maps, statuses):
    """Classify one detected position change. Read-only.

    Priority (documented): pit evidence -> non-running -> neutralized
    context -> supported pass (green + running + gap evidence) ->
    unresolved with alternatives. Never invents penalties (no penalty
    feed exists in the payload).
    """
    code_new = event["overtaker"]
    code_old = event["overtaken"]
    t_event = event["t_flip"]
    window = PIT_WINDOW_S
    evidence = {"tyre_changes": [], "non_running": [],
                "status_during": None, "gap_before_s": None,
                "persist_s": event["hold_s"]}
    alternatives = []

    for code in (code_new, code_old):
        for (t_change, old, new) in tyre_maps.get(code, []):
            if abs(t_change - t_event) <= window:
                evidence["tyre_changes"].append(
                    {"driver": code, "t": t_change, "old": old, "new": new,
                     "dt_s": t_change - t_event})

    series, times = matrices["series"], matrices["times"]
    for code in (code_new, code_old):
        if is_non_running(series, times, code, t_event):
            evidence["non_running"].append(code)

    if len(evidence["non_running"]) == 2:
        # Whole-pair freeze (parked field, session end, red flag): no
        # single-car retirement can be attributed; fall through to
        # neutralized-context or unresolved handling below.
        alternatives.append("both cars stationary (field freeze?)")
        evidence["non_running"] = []

    status = status_at(statuses, t_event)
    evidence["status_during"] = status
    neutralized = status != "1"
    # Opening-lap context: the whole field runs nose-to-tail, so gap
    # evidence alone is weaker; recorded, never hidden.
    frame_now = payload["frames"][min(event["step"] * SAMPLE_STEP,
                                      len(payload["frames"]) - 1)]
    try:
        leader_lap_now = max(float(v.get("lap", 0))
                             for v in frame_now["drivers"].values())
    except (TypeError, ValueError):
        leader_lap_now = None
    evidence["opening_lap"] = bool(leader_lap_now == 1)
    if evidence["opening_lap"]:
        alternatives.append("opening-lap field sorting (bunched field)")

    # Gap evidence just before the flip (same-lap pairwise gap).
    gap_before = _pair_gap_before(matrices, code_old, code_new, event["step"])
    evidence["gap_before_s"] = gap_before

    quality = assess_window_quality(
        payload, code_new, code_old,
        t_event - APPROACH_WINDOW_S, t_event + AFTER_WINDOW_S)

    if evidence["tyre_changes"]:
        return _verdict(CL_PIT, event, evidence, alternatives, quality,
                        "medium",
                        "tyre compound change within ±30 s for an involved "
                        "driver; order change follows the pit cycle")
    if evidence["non_running"]:
        return _verdict(CL_RETIRE, event, evidence, alternatives, quality,
                        "medium",
                        "an involved driver shows non-running telemetry "
                        "(speed/dist/lap frozen); parked-under-flag vs "
                        "retired cannot be distinguished from frames alone")
    if neutralized:
        alternatives += ["unlapping under neutralisation",
                         "neutralisation reshuffle",
                         "timing-feed reordering"]
        return _verdict(CL_RACE_CONTROL, event, evidence, alternatives,
                        quality, "low",
                        "order changed under non-green track status, where "
                        "racing for position is restricted")
    if (gap_before is not None and gap_before < 2.0
            and quality["aggregate"] in ("full", "partial")):
        return _verdict(CL_PASS, event, evidence, alternatives, quality,
                        "high" if quality["aggregate"] == "full" else "medium",
                        "green-flag running battle with measured pre-pass gap "
                        "and persistent new order")
    alternatives += ["timing-feed discontinuity", "lap-boundary artefact",
                     "insufficient gap evidence"]
    return _verdict(CL_UNRESOLVED, event, evidence, alternatives, quality,
                    "low", "order flipped but no alternative could be ruled "
                    "out with the available evidence")


def _pair_gap_before(matrices, code_old_ahead, code_new_ahead, step,
                     lookback=150):
    """Median same-lap gap (s) over the 30 s before the flip step."""
    series, times = matrices["series"], matrices["times"]
    start = max(0, step - lookback)
    gaps = []
    for s in range(start, step):
        lap_a = series[code_old_ahead]["lap"][s]
        lap_b = series[code_new_ahead]["lap"][s]
        if not (np.isfinite(lap_a) and np.isfinite(lap_b)) or lap_a != lap_b:
            continue
        dist_a = series[code_old_ahead]["dist"][s]
        dist_b = series[code_new_ahead]["dist"][s]
        speed_b = series[code_new_ahead]["speed"][s]
        if not (np.isfinite(dist_a) and np.isfinite(dist_b)
                and np.isfinite(speed_b)):
            continue
        gap_m = dist_a - dist_b
        if gap_m <= 0:
            continue
        gaps.append(gap_m / max(speed_b / 3.6, 20.0))
    if not gaps:
        return None
    return float(np.median(gaps))


def _verdict(classification, event, evidence, alternatives, quality,
             confidence, rationale):
    alternatives = list(alternatives)
    if classification == CL_UNRESOLVED and not alternatives:
        alternatives = ["unlisted alternative: evidence insufficient"]
    return {"event_id": event["id"],
            "classification": classification,
            "overtaker": event["overtaker"],
            "overtaken": event["overtaken"],
            "step": event["step"],
            "t_flip": event["t_flip"],
            "t_last_pre": event["t_last_pre"],
            "t_first_post": event["t_first_post"],
            "hold_s": event["hold_s"],
            "confirmed": event["confirmed"],
            "evidence": evidence,
            "alternatives": alternatives,
            "quality": quality,
            "classification_confidence": confidence,
            "rationale": rationale,
            "provenance": {
                "order": "derived_ordering_by_lap_then_distance",
                "gap": "derived_same_lap_distance_over_speed",
                "status": "observed_feed_record",
                "tyre": "derived_per_lap_timing_broadcast",
                "channels": "unknown_linear_25hz_resample_markers_discarded",
            }}


# --------------------------------------------------------------------------
# Battle lifecycle
# --------------------------------------------------------------------------

def _closing_rate(times, gaps, t_now, window=CLOSE_WINDOW_S,
                  min_points=CLOSE_MIN_POINTS):
    """Robust closing rate c = -d(gap)/dt over the trailing window.

    Least-squares slope on valid points; returns None when too few
    points or the window is degenerate. Positive c means closing.
    """
    sel = (times <= t_now) & (times >= t_now - window) & np.isfinite(gaps)
    if int(np.count_nonzero(sel)) < min_points:
        return None
    x = times[sel]
    y = gaps[sel]
    if x[-1] - x[0] < 1.0:
        return None
    slope = float(np.polyfit(x, y, 1)[0])
    return -slope


def _pair_gap_series(matrices, code_ahead, code_behind):
    """Per-step same-lap gap (s) for an ordered pair; NaN where invalid."""
    series, times = matrices["series"], matrices["times"]
    n = len(times)
    gaps = np.full(n, math.nan)
    lap_a = series[code_ahead]["lap"]
    lap_b = series[code_behind]["lap"]
    dist_a = series[code_ahead]["dist"]
    dist_b = series[code_behind]["dist"]
    speed_b = series[code_behind]["speed"]
    same = np.isfinite(lap_a) & np.isfinite(lap_b) & (lap_a == lap_b)
    ok = (same & np.isfinite(dist_a) & np.isfinite(dist_b)
          & np.isfinite(speed_b))
    gap_m = np.where(ok, dist_a - dist_b, math.nan)
    valid = ok & (gap_m > 0)
    gaps[valid] = gap_m[valid] / np.maximum(speed_b[valid] / 3.6, 20.0)
    return gaps


def _step_gap(series, ahead, behind, s):
    """Scalar same-lap gap (s) at one step; NaN where invalid."""
    lap_a = series[ahead]["lap"][s]
    lap_b = series[behind]["lap"][s]
    if not (math.isfinite(lap_a) and math.isfinite(lap_b)) or lap_a != lap_b:
        return math.nan
    dist_a = series[ahead]["dist"][s]
    dist_b = series[behind]["dist"][s]
    speed_b = series[behind]["speed"][s]
    if not (math.isfinite(dist_a) and math.isfinite(dist_b)
            and math.isfinite(speed_b)):
        return math.nan
    gap_m = dist_a - dist_b
    if gap_m <= 0:
        return math.nan
    floor = max(speed_b / 3.6, 20.0)
    return gap_m / floor


def _slope(xs, ys):
    """Closed-form least-squares slope (fast path for closing rates)."""
    n = len(xs)
    if n < 2:
        return None
    sum_x = sum_y = sum_xx = sum_xy = 0.0
    for x, y in zip(xs, ys):
        sum_x += x
        sum_y += y
        sum_xx += x * x
        sum_xy += x * y
    denom = n * sum_xx - sum_x * sum_x
    if abs(denom) < 1e-9:
        return None
    return (n * sum_xy - sum_x * sum_y) / denom


def _precompute_status_steps(statuses, times):
    """Per-step status code via binary search on segment starts."""
    starts, codes = [], []
    for status in statuses or []:
        try:
            starts.append(float(status.get("start_time", 0.0)))
            codes.append(str(status.get("status", "1")))
        except (TypeError, ValueError):
            continue
    if not starts:
        return ["1"] * len(times)
    order = np.argsort(starts)
    starts = np.asarray(starts)[order]
    codes = [codes[i] for i in order]
    idx = np.clip(np.searchsorted(starts, times, side="right") - 1, 0, len(codes) - 1)
    return [codes[i] for i in idx]


def _precompute_nonrunning(series, times, stride=10):
    """Per-driver boolean mask (2 s resolution) of the non-running heuristic.

    Vectorized with sliding windows: rolling max speed, rolling dist
    range and rolling lap range per driver. Windows containing no finite
    samples never count as non-running (conservative).
    """
    from numpy.lib.stride_tricks import sliding_window_view

    masks = {}
    width = max(1, int(round(NONRUN_WINDOW_S / STEP_DT)))
    for code, entry in series.items():
        dist = np.asarray(entry["dist"], dtype=np.float64)
        lap = np.asarray(entry["lap"], dtype=np.float64)
        n = len(dist)
        if n < width:
            masks[code] = np.zeros(n, dtype=bool)
            continue
        # Forward windows [s, s + width): core index k covers step k.
        view_d = sliding_window_view(dist, width)
        view_l = sliding_window_view(lap, width)
        with np.errstate(all="ignore"):
            min_dst = np.nanmin(view_d, axis=1)
            max_dst = np.nanmax(view_d, axis=1)
            min_lap = np.nanmin(view_l, axis=1)
            max_lap = np.nanmax(view_l, axis=1)
        finite = (np.isfinite(min_dst) & np.isfinite(max_dst)
                  & np.isfinite(min_lap))
        core = (finite & ((max_dst - min_dst) < NONRUN_DIST_M)
                & (max_lap == min_lap))
        probed = np.zeros(n, dtype=bool)
        # Evaluate every stride-th step; nearest-fill the rest below.
        starts = np.arange(0, n - width + 1, stride)
        probed[starts] = core[starts]
        done = np.nonzero(np.arange(n) % stride == 0)[0]
        if len(done):
            nearest = done[np.clip(
                np.searchsorted(done, np.arange(n)), 0, len(done) - 1)]
            # Only propagate True where a probed centre exists.
            full = np.zeros(n, dtype=bool)
            full[done] = probed[done]
            masks[code] = full[nearest]
        else:
            masks[code] = probed
    return masks


def reconstruct_battles(matrices, payload, tyre_maps, statuses,
                        progress=None, cancelled=None):
    """Reconstruct battle episodes for ordered-adjacent pairs per step.

    State machine with hysteresis (enter < 1.0 s, exit > 2.0 s sustained).
    Episodes record transitions with step indices, interruptions (pit /
    neutralized / non-running / data-gap endings), and resolution. Returns
    list of battle dicts; heavyweight per-sample objects are never retained
    (episodes keep index ranges + transition list only).

    Performance: scalar per-step gap math, closed-form slopes over a
    bounded trailing deque, and precomputed status/non-running masks keep
    a full session to a single linear pass.
    """
    times = matrices["times"]
    series = matrices["series"]
    orders = matrices["orders"]
    step_dt = matrices["step_dt"]
    n = len(times)
    status_steps = _precompute_status_steps(statuses, times)
    nonrunning = _precompute_nonrunning(series, times)
    close_span = max(CLOSE_MIN_POINTS, int(CLOSE_WINDOW_S / step_dt))
    battles = []
    active = {}  # unordered pair -> episode state
    nan_run = {}

    def _close_episode(key, end_step, how, detail, drop_empty=False):
        ep = active.pop(key, None)
        if ep is None:
            return
        if drop_empty and not ep["transitions"]:
            return  # contact that never reached battle range: not a battle
        ep["end_step"] = end_step
        ep["end_t"] = float(times[end_step])
        ep["resolution"] = how
        ep["resolution_detail"] = detail
        battles.append(_finalize_battle(ep, matrices, payload))

    for s in range(n):
        if progress is not None and s % 5000 == 0:
            progress("battles", s, n, f"Scanning battles {s}/{n}")
        if cancelled is not None and cancelled():
            raise _Cancelled()
        order = orders[s]
        adjacent = set()
        for i in range(len(order) - 1):
            ahead, behind = order[i], order[i + 1]
            key = frozenset((ahead, behind))
            adjacent.add(key)
            gap = _step_gap(series, ahead, behind, s)
            ep = active.get(key)
            if ep is None:
                # Episodes open only inside approach range; plain adjacency
                # at racing distance is contact, not a battle.
                if not math.isfinite(gap) or gap >= APPROACH_GAP_S:
                    continue
                ep = {"pair": (ahead, behind), "key": key,
                      "start_step": s, "start_t": float(times[s]),
                      "transitions": [], "in_range_since": None,
                      "attack_since": None, "interruptions": [],
                      "max_close": 0.0, "min_gap": math.inf,
                      "recent": []}
                active[key] = ep
                nan_run[key] = 0
            # Direction may have flipped within the same unordered pair.
            if ep["pair"] != (ahead, behind):
                _close_episode(key, s - 1, ST_UNRESOLVED,
                               "pair direction changed", drop_empty=True)
                if not math.isfinite(gap) or gap >= APPROACH_GAP_S:
                    continue
                ep = {"pair": (ahead, behind), "key": key,
                      "start_step": s, "start_t": float(times[s]),
                      "transitions": [], "in_range_since": None,
                      "attack_since": None, "interruptions": [],
                      "max_close": 0.0, "min_gap": math.inf,
                      "recent": []}
                active[key] = ep
                nan_run[key] = 0
            if not math.isfinite(gap):
                nan_run[key] = nan_run.get(key, 0) + 1
                if nan_run[key] * step_dt >= MAX_DATA_GAP_S:
                    _close_episode(key, s, ST_UNRESOLVED,
                                   "data_gap_exceeded", drop_empty=True)
                continue
            nan_run[key] = 0
            ep["min_gap"] = min(ep["min_gap"], gap)
            recent = ep["recent"]
            recent.append((float(times[s]), gap))
            if len(recent) > close_span:
                del recent[:len(recent) - close_span]
            rate = None
            if len(recent) >= CLOSE_MIN_POINTS:
                slope = _slope([t for t, _ in recent],
                               [g for _, g in recent])
                if slope is not None:
                    rate = -slope
            if rate is not None:
                ep["max_close"] = max(ep["max_close"], rate)
            state = _episode_state(ep)
            if state is None:
                if gap < BATTLE_ENTER_S:
                    if ep["in_range_since"] is None:
                        ep["in_range_since"] = s
                    held = (s - ep["in_range_since"]) * step_dt
                    if held >= BATTLE_MIN_SUSTAIN_S:
                        ep["transitions"].append(
                            ("approaching", ST_IN_RANGE, s))
                else:
                    # Intermittent dips must not accumulate wall-clock time
                    # toward a battle that never sustained range.
                    ep["in_range_since"] = None
            elif state == ST_IN_RANGE:
                if gap < ATTACK_GAP_S:
                    if ep["attack_since"] is None:
                        ep["attack_since"] = s
                    ep["transitions"].append((ST_IN_RANGE, ST_ATTACK, s))
                elif gap > BATTLE_EXIT_S:
                    # Hysteresis: only resolve after sustained exit.
                    since = ep.get("exit_since", s)
                    ep["exit_since"] = since
                    if (s - since) * step_dt >= EXIT_CONFIRM_S:
                        _close_episode(key, s, ST_DEFENDED,
                                       "gap exceeded exit threshold sustained")
                        continue
                else:
                    ep.pop("exit_since", None)
            elif state == ST_ATTACK:
                if gap > BATTLE_EXIT_S:
                    _close_episode(key, s, ST_DEFENDED,
                                   "attack faded without order change")
                    continue
            # Interruptions end the episode as unresolved.
            code_pair = ep["pair"]
            interrupted = False
            for code in code_pair:
                for (t_change, _old, _new) in tyre_maps.get(code, []):
                    if abs(t_change - float(times[s])) <= step_dt:
                        ep["interruptions"].append(
                            {"type": "pit", "driver": code,
                             "t": float(t_change)})
                        _close_episode(key, s, ST_UNRESOLVED,
                                       "pit stop during battle",
                                       drop_empty=True)
                        interrupted = True
                        break
                if interrupted:
                    break
            if interrupted or key not in active:
                continue
            if status_steps[s] != "1":
                ep["interruptions"].append(
                    {"type": "neutralized", "t": float(times[s])})
                _close_episode(key, s, ST_UNRESOLVED,
                               "neutralized track status during battle",
                               drop_empty=True)
                continue
            for code in code_pair:
                if nonrunning[code][s]:
                    ep["interruptions"].append(
                        {"type": "non_running", "driver": code,
                         "t": float(times[s])})
                    _close_episode(key, s, ST_UNRESOLVED,
                                   "non-running driver during battle",
                                   drop_empty=True)
                    break
        # Episodes whose pair is no longer adjacent end here.
        for key in [k for k in active if k not in adjacent]:
            _close_episode(key, s - 1 if s > 0 else s, ST_UNRESOLVED,
                           "pair no longer adjacent", drop_empty=True)
    for key in list(active.keys()):
        _close_episode(key, n - 1, ST_UNRESOLVED, "session end",
                       drop_empty=True)
    battles.sort(key=lambda b: (b["start_t"], b["pair"]))
    for i, battle in enumerate(battles):
        battle["id"] = f"battle_{i:04d}"
    return battles


def link_passes_to_battles(battles, verdicts, step_dt=STEP_DT):
    """Upgrade battle episodes that ended in a confirmed order flip.

    A battle closed as "pair direction changed" whose flip matches a
    confirmed event (same unordered pair, |t_flip - end_t| <= 2 steps) is
    re-resolved as confirmed_pass with the event reference. Read-only
    over verdicts; mutates the passed battle dicts' resolution fields.
    """
    by_pair = {}
    for verdict in verdicts:
        if verdict.get("confirmed") and not verdict.get("transient"):
            key = frozenset((verdict["overtaker"], verdict["overtaken"]))
            by_pair.setdefault(key, []).append(verdict)
    for battle in battles:
        if (battle["resolution"] != ST_UNRESOLVED
                or battle["resolution_detail"] != "pair direction changed"):
            continue
        key = frozenset(battle["pair"])
        for verdict in by_pair.get(key, []):
            if abs(verdict["t_flip"] - battle["end_t"]) <= 2 * step_dt + 1e-9:
                battle["resolution"] = ST_CONFIRMED
                battle["resolution_detail"] = (
                    f"order flip confirmed by {verdict['event_id']}")
                battle["pass_event_id"] = verdict["event_id"]
                break
    return battles


def _episode_state(ep):
    if not ep["transitions"]:
        return None
    return ep["transitions"][-1][1]


def _finalize_battle(ep, matrices, payload):
    times = matrices["times"]
    start, end = ep["start_step"], ep["end_step"]
    frontend = {"pair": list(ep["pair"]),
                "start_t": ep["start_t"], "end_t": ep["end_t"],
                "start_step": start, "end_step": end,
                "transitions": [{"from": a, "to": b, "t": float(times[s]),
                                 "step": s} for (a, b, s) in ep["transitions"]],
                "interruptions": ep["interruptions"],
                "min_gap_s": (None if ep["min_gap"] == math.inf
                              else float(ep["min_gap"])),
                "max_closing_mps": float(ep["max_close"]),
                "resolution": ep["resolution"],
                "resolution_detail": ep.get("resolution_detail", "")}
    # Quality assessment only for episodes that actually reached battle
    # range; contact-only episodes keep a light unknown record. This keeps
    # full-session reconstruction linear instead of quadratic.
    if ep["transitions"]:
        quality = assess_window_quality(
            payload, ep["pair"][0], ep["pair"][1],
            ep["start_t"], ep["end_t"],
            channels=("speed", "dist", "throttle", "brake"))
    else:
        quality = {"aggregate": "unknown",
                   "reason": "contact_only_no_battle_range"}
    frontend["quality"] = quality
    frontend["provenance"] = {
        "order": "derived_ordering_by_lap_then_distance",
        "gap": "derived_same_lap_distance_over_speed",
        "channels": "unknown_linear_25hz_resample_markers_discarded",
    }
    return frontend


# --------------------------------------------------------------------------
# Corner validation for race distance axis
# --------------------------------------------------------------------------

def normalize_race_corners(corners):
    """Official corner metadata -> [{number, letter, distance_m}].

    Same contract as qualifying metadata; never invents turn numbers.
    Raises ValueError when required fields are absent.
    Accepts a pandas DataFrame or a list of dicts.
    """
    if corners is None:
        raise ValueError("no_corner_metadata")
    # Convert DataFrame (or any object with to_dict) to a list of dicts so
    # no DataFrame boolean evaluation can occur downstream.
    if hasattr(corners, "to_dict"):
        try:
            rows = corners.to_dict("records")
        except Exception:
            rows = list(corners)
    else:
        rows = list(corners)
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


def validate_race_corners(corner_list, matrices, lap_length_m):
    """Validate official corners against a reference speed profile.

    Reference: the driver completing the most laps; per-5 m distance bins
    take the minimum speed across that driver's laps (within-lap dist axis
    matches the corner Distance frame, verified by Update 5 semantics). A
    corner validates when bin speed at its distance is < 92% of the nearby
    straight reference. Non-increasing lists are rejected wholesale; weak
    corners are excluded with reasons, never renumbered.
    """
    series = matrices["series"]
    ref_code, ref_laps = None, -1
    for code, entry in series.items():
        laps = entry["lap"]
        finite = laps[np.isfinite(laps)]
        done = int(np.max(finite)) if len(finite) else -1
        if done > ref_laps:
            ref_laps, ref_code = done, code
    if ref_code is None:
        return {"validated": [], "excluded": [],
                "note": "no_reference_driver"}
    dist = series[ref_code]["dist"]
    speed = series[ref_code]["speed"]
    ok = np.isfinite(dist) & np.isfinite(speed)
    dist, speed = dist[ok], speed[ok]
    bin_width = 5.0
    n_bins = max(1, int(math.ceil((lap_length_m + bin_width) / bin_width)))
    bin_min = np.full(n_bins, math.inf)
    idx = np.clip((dist / bin_width).astype(int), 0, n_bins - 1)
    for b, v in zip(idx, speed):
        if v < bin_min[b]:
            bin_min[b] = v
    bin_min[~np.isfinite(bin_min)] = math.nan

    def _bin_speed(d):
        b = int(np.clip(d / bin_width, 0, n_bins - 1))
        value = bin_min[b]
        return None if not np.isfinite(value) else float(value)

    distances = [c["distance_m"] for c in corner_list]
    if any(later <= earlier for earlier, later in zip(distances, distances[1:])):
        return {"validated": [],
                "excluded": [{"corner": c, "reason": "non_increasing_distances"}
                             for c in corner_list],
                "note": "rejected wholesale"}
    validated, excluded = [], []
    for corner in corner_list:
        dist_m = corner["distance_m"]
        if dist_m < 0 or dist_m > lap_length_m + 50.0:
            excluded.append({"corner": corner, "reason": "outside_lap_extent"})
            continue
        corner_v = _bin_speed(dist_m)
        ref_vals = []
        d = dist_m - 550.0
        while d <= dist_m + 550.0:
            if abs(d - dist_m) > 150.0 and 0 <= d <= lap_length_m:
                value = _bin_speed(d)
                if value is not None:
                    ref_vals.append(value)
            d += bin_width
        if corner_v is None or not ref_vals:
            excluded.append({"corner": corner,
                             "reason": "insufficient_speed_evidence"})
            continue
        if corner_v < 0.92 * float(np.median(ref_vals)):
            validated.append({**corner, "alignment": "strong"})
        else:
            excluded.append({"corner": corner,
                             "reason": "no_speed_dip_alignment"})
    return {"validated": validated, "excluded": excluded,
            "note": f"reference_driver:{ref_code}"}


# --------------------------------------------------------------------------
# Approach / aftermath analysis (supported passes only)
# --------------------------------------------------------------------------

def _nearest_step(times, t):
    return int(np.clip(np.searchsorted(times, t), 0, len(times) - 1))


def _first_sustained(values, threshold, min_samples):
    """First index reaching threshold and holding for min_samples."""
    run = 0
    for i, value in enumerate(values):
        if isinstance(value, float) and math.isnan(value):
            run = 0
            continue
        if value is not None and value >= threshold:
            run += 1
            if run >= min_samples:
                return i - min_samples + 1
        else:
            run = 0
    return None


def analyze_approach(event, verdict, matrices, payload,
                     validated_corners=None):
    """Approach + aftermath context for a classified event.

    Measures gap trend, closing rate, relative speed, location and lap
    context from validated fields only. No mechanism is asserted here;
    see classify_mechanism. Read-only.
    """
    series, times = matrices["series"], matrices["times"]
    code_new = event["overtaker"]
    code_old = event["overtaken"]
    t_event = event["t_flip"]
    t0 = max(float(times[0]), t_event - APPROACH_WINDOW_S)
    t1 = min(float(times[-1]), t_event + AFTER_WINDOW_S)
    i0, i1 = _nearest_step(times, t0), _nearest_step(times, t1)
    ie = _nearest_step(times, t_event)

    # Same-lap pairwise gap series across the window (NaN where invalid).
    gaps, t_gap = [], []
    for s in range(i0, i1 + 1):
        lap_n = series[code_new]["lap"][s]
        lap_o = series[code_old]["lap"][s]
        if (np.isfinite(lap_n) and np.isfinite(lap_o) and lap_n == lap_o):
            dist_n = series[code_new]["dist"][s]
            dist_o = series[code_old]["dist"][s]
            speed_o = series[code_old]["speed"][s]
            if (np.isfinite(dist_n) and np.isfinite(dist_o)
                    and np.isfinite(speed_o) and dist_n != dist_o):
                ahead = dist_n if s >= ie else dist_o
                behind = dist_o if s >= ie else dist_n
                gap_m = ahead - behind
                if gap_m > 0:
                    gaps.append(gap_m / max(speed_o / 3.6, 20.0))
                    t_gap.append(float(times[s]))
    gaps = np.asarray(gaps)
    approach = {"window_t": [t0, t1], "n_gap_samples": len(gaps)}
    if len(gaps) >= CLOSE_MIN_POINTS:
        slope = float(np.polyfit(np.asarray(t_gap), gaps, 1)[0])
        approach["gap_trend_s_per_s"] = slope
        approach["min_gap_s"] = float(np.min(gaps))
        approach["min_gap_t"] = float(t_gap[int(np.argmin(gaps))])
    else:
        approach["gap_trend_s_per_s"] = None
        approach["min_gap_s"] = None
        approach["min_gap_t"] = None

    speed_n = series[code_new]["speed"][ie]
    speed_o = series[code_old]["speed"][ie]
    approach["rel_speed_kmh"] = (
        float(speed_n - speed_o)
        if np.isfinite(speed_n) and np.isfinite(speed_o) else None)
    approach["speed_new_kmh"] = float(speed_n) if np.isfinite(speed_n) else None
    approach["speed_old_kmh"] = float(speed_o) if np.isfinite(speed_o) else None

    dist_n = series[code_new]["dist"][ie]
    lap_n = series[code_new]["lap"][ie]
    approach["event_dist_m"] = float(dist_n) if np.isfinite(dist_n) else None
    approach["event_lap"] = int(lap_n) if np.isfinite(lap_n) else None

    closing = _closing_rate(times, _pair_gap_series(matrices, code_old, code_new),
                            t_event)
    approach["closing_rate_mps"] = closing
    ming = approach["min_gap_s"]
    if closing is not None and closing > TTC_MIN_RATE and ming is not None:
        # Local linear extrapolation only, labelled as such by callers.
        approach["time_to_contact_s"] = float(
            ming * max(speed_o / 3.6, 20.0) / closing) if np.isfinite(speed_o) else None
    else:
        approach["time_to_contact_s"] = None

    seen_statuses = []
    for status in payload.get("track_statuses", []):
        try:
            start = float(status.get("start_time", 0.0))
        except (TypeError, ValueError):
            continue
        end = status.get("end_time")
        end = float(end) if end is not None else math.inf
        if start <= t1 and end >= t0:
            seen_statuses.append(str(status.get("status")))
    approach["statuses_in_window"] = sorted(set(seen_statuses))
    approach["corner_context"] = _corner_context(
        approach["event_dist_m"], validated_corners)
    return approach


def _corner_context(event_dist_m, validated_corners):
    """Nearest validated corner within 300 m, if corners given.

    ``validated_corners`` is a list of dicts already filtered by
    ``validate_race_corners`` (each has number, letter, distance_m).
    """
    if event_dist_m is None or not validated_corners:
        return {"nearest": None, "note": "no_corner_metadata"}
    best, best_gap = None, 300.0
    for corner in validated_corners:
        gap = abs(corner["distance_m"] - event_dist_m)
        if gap < best_gap:
            best, best_gap = corner, gap
    if best is None:
        return {"nearest": None, "note": "no_corner_within_300m"}
    label = f"T{best['number']}{best['letter']}"
    return {"nearest": {"label": label, "number": best["number"],
                        "distance_m": best["distance_m"],
                        "offset_m": float(event_dist_m - best["distance_m"])},
            "note": "official_geometry_validated"}


def classify_mechanism(event, verdict, matrices, payload, validated_corners=None,
                       approach=None):
    """Conservative mechanism assignment for supported passes.

    Each rule requires corroborating telemetry; otherwise unresolved:
    - braking_phase_pass: validated corner apex within ±150 m of the event
      AND the overtaker's brake-onset distance is later (greater).
    - corner_exit_advantage: first sustained throttle pickup after that
      apex is earlier for the overtaker.
    - straight_approach_advantage: interval contains no validated apex and
      both cars above 250 km/h through it with a closing gap.
    Returns (mechanism, evidence_dict, confidence). Never asserts traction,
    setup, tyres or intent.
    """
    if verdict["classification"] != CL_PASS:
        return MECH_UNRESOLVED, {"reason": "only_supported_passes_classified"}, "withheld"
    series = matrices["series"]
    code_new = event["overtaker"]
    code_old = event["overtaken"]
    t_event = event["t_flip"]
    evidence = {}
    validated = validated_corners or []

    dist_n = series[code_new]["dist"]
    dist_o = series[code_old]["dist"]
    ie = _nearest_step(matrices["times"], t_event)
    near_apex = [c for c in validated
                 if abs(c["distance_m"] - (dist_n[ie] if np.isfinite(dist_n[ie]) else 1e18)) <= 150.0]
    evidence["apex_nearby"] = (
        f"T{near_apex[0]['number']}" if near_apex else None)

    brake_n = series[code_new]["brake"]
    brake_o = series[code_old]["brake"]
    onset_n = _brake_onset_before(dist_n, brake_n, ie)
    onset_o = _brake_onset_before(dist_o, brake_o, ie)
    evidence["brake_onset_new_m"] = onset_n
    evidence["brake_onset_old_m"] = onset_o
    if near_apex and onset_n is not None and onset_o is not None:
        if onset_n > onset_o + 5.0:
            evidence["explanation"] = (
                "overtaker braked later into the corner zone with "
                "corroborating position gain")
            return MECH_BRAKING, evidence, "medium"
        # Fall through to exit check at the same apex.
        pick_n = _pickup_after(dist_n, series[code_new]["throttle"], near_apex[0]["distance_m"])
        pick_o = _pickup_after(dist_o, series[code_old]["throttle"], near_apex[0]["distance_m"])
        evidence["pickup_new_m"] = pick_n
        evidence["pickup_old_m"] = pick_o
        if (pick_n is not None and pick_o is not None and pick_n < pick_o - 5.0):
            evidence["explanation"] = (
                "overtaker reached sustained high throttle earlier after "
                "the corner, consistent with more effective acceleration "
                "through the section")
            return MECH_EXIT, evidence, "medium"
        return MECH_UNRESOLVED, {**evidence, "reason": "no_corroborating_brake_or_throttle_difference"}, "withheld"

    # Straight case: no validated apex in the event interval.
    speed_n = series[code_new]["speed"][ie]
    speed_o = series[code_old]["speed"][ie]
    closing = None
    if approach is not None:
        closing = approach.get("closing_rate_mps")
    if (not near_apex and np.isfinite(speed_n) and np.isfinite(speed_o)
            and speed_n > 250.0 and speed_o > 250.0
            and closing is not None and closing > 0):
        evidence["explanation"] = (
            "gap closed at high speed away from validated corners with "
            "higher overtaker speed")
        evidence["speed_new_kmh"] = float(speed_n)
        evidence["speed_old_kmh"] = float(speed_o)
        return MECH_STRAIGHT, evidence, "low"
    return MECH_UNRESOLVED, {**evidence, "reason": "insufficient_corroboration"}, "withheld"


# --------------------------------------------------------------------------
# Findings + top-level session analysis
# --------------------------------------------------------------------------

def _finding(finding_id, kind, event, verdict, approach, mechanism,
             mechanism_evidence, mechanism_confidence, session_id):
    text = " ".join([str(verdict.get("rationale", "")),
                     str(mechanism_evidence.get("explanation", ""))])
    lowered = text.lower()
    banned = [stem for stem in BANNED_CAUSAL_STEMS if stem in lowered]
    if banned:
        raise ValueError(f"banned_causal_language:{banned}")
    if verdict["classification"] == CL_PASS:
        explanation = (
            f"{verdict['overtaker']} passed {verdict['overtaken']} "
            f"(t {verdict['t_last_pre']:.1f}–{verdict['t_first_post']:.1f} s). "
            f"{verdict['rationale']}.")
    elif verdict["classification"] == CL_PIT:
        explanation = (
            f"Order change {verdict['overtaker']} ahead of "
            f"{verdict['overtaken']} follows the pit cycle, not an on-track "
            f"pass. {verdict['rationale']}.")
    elif verdict["classification"] == CL_RETIRE:
        explanation = (
            f"Order change involving {verdict['overtaker']} / "
            f"{verdict['overtaken']} coincides with non-running telemetry. "
            f"{verdict['rationale']}.")
    elif verdict["classification"] == CL_RACE_CONTROL:
        explanation = (
            f"Order change under {verdict['evidence']['status_during']} "
            f"track status. {verdict['rationale']}; alternatives: "
            f"{'; '.join(verdict['alternatives'])}.")
    else:
        explanation = (
            f"Order between {verdict['overtaker']} and "
            f"{verdict['overtaken']} flipped without a supportable "
            f"explanation. {verdict['rationale']}. Alternatives: "
            f"{'; '.join(verdict['alternatives'])}.")
    if mechanism != MECH_UNRESOLVED:
        explanation += f" Pattern: {mechanism_evidence.get('explanation', mechanism)}."
    unresolved = []
    if verdict["classification"] == CL_UNRESOLVED:
        unresolved.append("decisive classification evidence")
    if mechanism == MECH_UNRESOLVED and verdict["classification"] == CL_PASS:
        unresolved.append("passing mechanism")
    return {
        "id": finding_id,
        "kind": kind,
        "session": session_id,
        "drivers": [verdict["overtaker"], verdict["overtaken"]],
        "event_type": verdict["classification"],
        "classification": verdict["classification"],
        "t_interval": [verdict["t_last_pre"], verdict["t_first_post"]],
        "timing_uncertainty_s": STEP_DT,
        "lap": approach.get("event_lap"),
        "dist_interval_m": ([approach.get("event_dist_m"),
                             approach.get("event_dist_m")]
                            if approach.get("event_dist_m") is not None else None),
        "measured": {
            "hold_s": verdict["hold_s"],
            "gap_before_s": verdict["evidence"].get("gap_before_s"),
            "rel_speed_kmh": approach.get("rel_speed_kmh"),
            "closing_rate_mps": approach.get("closing_rate_mps"),
            "time_to_contact_s": approach.get("time_to_contact_s"),
            "min_gap_s": approach.get("min_gap_s"),
            "units": {"time": "s", "gap": "s", "speed": "km/h",
                      "closing_rate": "m/s"},
        },
        "observations": {
            "t_last_pre": verdict["t_last_pre"],
            "t_first_post": verdict["t_first_post"],
            "event_step": event["step"],
        },
        "quality": verdict["quality"],
        "provenance": verdict["provenance"],
        "classification_confidence": verdict.get("classification_confidence",
                                                 "low"),
        "timing_confidence": ("high" if verdict["hold_s"] >= PERSIST_S
                              else "medium"),
        "mechanism_confidence": mechanism_confidence,
        "mechanism": mechanism,
        "alternatives": verdict["alternatives"],
        "explanation": explanation,
        "unresolved": unresolved,
    }


def analyze_session(payload, identity, corners=None, config=None,
                    progress=None, cancelled=None, return_matrices=False):
    """Full battle + overtake analysis for one prepared race payload.

    Read-only over the payload. Returns dict with battles, events
    (classified), findings, summary counts, warnings, rules version and
    config. Cancellation is cooperative via ``cancelled()``; raises
    _Cancelled (callers translate) when aborted. With
    ``return_matrices=True`` the step matrices are attached under
    ``_matrices`` (same objects, no copy) for chart rendering.
    """
    cfg = dict(config or {})

    # Defensive: ensure corners is never a DataFrame. boolean evaluation of
    # a DataFrame raises "truth value is ambiguous".
    if corners is not None and hasattr(corners, "to_dict"):
        corners = corners.to_dict("records")

    def _report(stage, done, total, message=""):
        if progress is not None:
            try:
                progress(stage, done, total, message)
            except Exception:
                pass

    _report("matrices", 0, 1, "Building step matrices")
    matrices = build_step_matrices(payload, SAMPLE_STEP, _report,
                                   lambda: bool(cancelled and cancelled()))
    _report("matrices", 1, 1, "Step matrices ready")
    statuses = payload.get("track_statuses", [])
    tyre_maps = {}
    for code in matrices["codes"]:
        tyre_maps[code] = tyre_changes(matrices["series"], matrices["times"], code)
    _report("detection", 0, 1, "Detecting position changes")
    events = detect_position_changes(matrices)
    _report("detection", 1, 1, f"{len(events)} candidate order changes")
    # Defensive: ensure corners is never a DataFrame (boolean evaluation
    # of a DataFrame raises "truth value is ambiguous").
    if corners is not None and hasattr(corners, "to_dict"):
        corners = corners.to_dict("records")

    # Pre-compute validated corners ONCE (not per event) to avoid
    # re-normalizing and re-validating for every classified pass event.
    validated_corners = []
    if corners is not None:
        try:
            normalized = normalize_race_corners(corners)
            lap_len = _lap_length_estimate(matrices)
            check = validate_race_corners(normalized, matrices, lap_len)
            validated_corners = check["validated"]
        except ValueError as exc:
            pass  # corners unavailable -> mechanism stays unresolved
    _report("corners", 1, 1, f"{len(validated_corners)} corners validated")

    verdicts, findings = [], []
    confirmed = [e for e in events if e["confirmed"]]
    transient = [e for e in events if not e["confirmed"]]
    total = max(1, len(confirmed))
    for i, event in enumerate(confirmed):
        if cancelled is not None and cancelled():
            raise _Cancelled()
        if i % 25 == 0:
            _report("classification", i, total,
                    f"Classifying event {i}/{total}")
        verdict = classify_event(event, matrices, payload, tyre_maps, statuses)
        approach = analyze_approach(event, verdict, matrices, payload,
                                    validated_corners=validated_corners)
        mechanism, mech_evidence, mech_conf = classify_mechanism(
            event, verdict, matrices, payload,
            validated_corners=validated_corners,
            approach=approach)
        verdicts.append({**verdict, "approach": approach,
                         "mechanism": mechanism,
                         "mechanism_evidence": mech_evidence,
                         "mechanism_confidence": mech_conf})
        findings.append(_finding(f"evt_{event['id']}", "position_change",
                                 event, verdict, approach, mechanism,
                                 mech_evidence, mech_conf, identity))
    _report("classification", total, total, "Classification complete")
    # Transient flips (order held < PERSIST_S) are recorded as unresolved
    # attack/noise candidates with a light record: no expensive quality
    # assessment, no pass claim, explicit transient rationale.
    for event in transient:
        verdicts.append({
            "event_id": event["id"], "classification": CL_UNRESOLVED,
            "overtaker": event["overtaker"], "overtaken": event["overtaken"],
            "t_flip": event["t_flip"], "t_last_pre": event["t_last_pre"],
            "t_first_post": event["t_first_post"],
            "hold_s": event["hold_s"], "confirmed": False,
            "transient": True,
            "evidence": {"persist_s": event["hold_s"], "gap_before_s": None,
                         "tyre_changes": [], "non_running": [],
                         "status_during": status_at(statuses, event["t_flip"])},
            "alternatives": ["transient timing-feed jitter",
                             "brief side-by-side reversal without a pass"],
            "quality": {"aggregate": "unknown",
                        "reason": "transient_not_assessed"},
            "classification_confidence": "low",
            "rationale": (f"order flipped but held only "
                          f"{event['hold_s']:.1f} s (< {PERSIST_S:.0f} s "
                          f"required); treated as transient, not a pass"),
            "provenance": {
                "order": "derived_ordering_by_lap_then_distance",
            },
            "approach": None, "mechanism": MECH_UNRESOLVED,
            "mechanism_evidence": {"reason": "transient_not_analyzed"},
            "mechanism_confidence": "withheld",
        })
        findings.append(_finding(
            f"evt_{event['id']}", "position_change", event,
            verdicts[-1],
            {"event_lap": None, "event_dist_m": None, "rel_speed_kmh": None,
             "closing_rate_mps": None, "time_to_contact_s": None,
             "min_gap_s": None},
            MECH_UNRESOLVED, {"reason": "transient_not_analyzed"},
            "withheld", identity))
    _report("battles", 0, 1, "Reconstructing battles")
    battles = reconstruct_battles(matrices, payload, tyre_maps, statuses,
                                  _report,
                                  lambda: bool(cancelled and cancelled()))
    link_passes_to_battles(battles, verdicts)
    _report("battles", 1, 1, f"{len(battles)} battle episodes")
    for battle in battles:
        findings.append({
            "id": battle["id"], "kind": "battle",
            "session": identity,
            "drivers": battle["pair"],
            "event_type": "battle",
            "classification": battle["resolution"],
            "t_interval": [battle["start_t"], battle["end_t"]],
            "timing_uncertainty_s": STEP_DT,
            "lap": None, "dist_interval_m": None,
            "measured": {"duration_s": battle["end_t"] - battle["start_t"],
                         "min_gap_s": battle["min_gap_s"],
                         "max_closing_mps": battle["max_closing_mps"],
                         "n_transitions": len(battle["transitions"]),
                         "units": {"time": "s", "gap": "s",
                                   "closing_rate": "m/s"}},
            "observations": {"start_step": battle["start_step"],
                             "end_step": battle["end_step"],
                             "transitions": battle["transitions"],
                             "interruptions": battle["interruptions"]},
            "quality": battle["quality"],
            "provenance": battle["provenance"],
            "classification_confidence": "medium",
            "timing_confidence": "high",
            "mechanism_confidence": "withheld",
            "mechanism": MECH_UNRESOLVED,
            "alternatives": [],
            "explanation": _battle_explanation(battle),
            "unresolved": (["resolution"] if battle["resolution"] == ST_UNRESOLVED
                           else []),
        })
    counts = {"battles": len(battles), "events": len(verdicts)}
    for verdict in verdicts:
        key = verdict["classification"]
        counts[key] = counts.get(key, 0) + 1
    output = {"rules_version": ANALYSIS_RULES_VERSION,
              "identity": dict(identity),
              "matrices_meta": {"n_steps": len(matrices["times"]),
                                "step_dt": matrices["step_dt"],
                                "n_frames": matrices["n_frames"],
                                "drivers": matrices["codes"]},
              "battles": battles, "events": verdicts, "findings": findings,
              "counts": counts, "config": cfg,
              "warnings": _session_warnings(payload, matrices)}
    if return_matrices:
        output["_matrices"] = matrices
    return output


def _battle_explanation(battle):
    pair = " vs ".join(battle["pair"])
    duration = battle["end_t"] - battle["start_t"]
    states = " -> ".join([t["to"] for t in battle["transitions"]]) or "contact only"
    base = (f"Battle {pair}: {duration:.1f} s in contact "
            f"(min gap {battle['min_gap_s']} s). Path: {states}.")
    if battle["resolution"] == ST_DEFENDED:
        return base + " Ended without a supported pass."
    if battle["resolution"] == ST_UNRESOLVED:
        return base + f" Unresolved: {battle['resolution_detail']}."
    return base + f" Resolution: {battle['resolution']}."


def _session_warnings(payload, matrices):
    warnings = []
    frames = payload.get("frames", [])
    if frames:
        codes0 = set(frames[0].get("drivers", {}).keys())
        for frame in frames[::2500]:
            if set(frame.get("drivers", {}).keys()) != codes0:
                warnings.append("driver_set_changes_mid_session")
                break
    statuses = {str(s.get("status")) for s in payload.get("track_statuses", [])}
    if "5" in statuses:
        warnings.append("session_red_flag:per_event_exposure_unknown")
    if statuses - {"1"}:
        warnings.append("session_neutralized_periods_present")
    warnings.append("penalty_feed_absent:penalty_causes_unclaimable")
    warnings.append("retirement_flag_absent:non_running_is_heuristic")
    return sorted(set(warnings))


def _lap_length_estimate(matrices):
    dist_max = 0.0
    for entry in matrices["series"].values():
        finite = entry["dist"][np.isfinite(entry["dist"])]
        if len(finite):
            dist_max = max(dist_max, float(np.max(finite)))
    return dist_max if dist_max > 0 else 6000.0


def _brake_onset_before(dist, brake, ie, threshold=0.1, lookback=250):
    """Distance where braking began before step ie (race 0..1 scale).

    Scans back for the last sample below threshold; returns its distance.
    None when not determinable.
    """
    start = max(0, ie - lookback)
    for s in range(ie, start - 1, -1):
        value = brake[s]
        d = dist[s]
        if np.isfinite(value) and np.isfinite(d) and value < threshold:
            return float(d)
    return None


def _pickup_after(dist, throttle, d_from, threshold=95.0, min_samples=5):
    """First distance with sustained high throttle at/after d_from."""
    idx = np.nonzero((dist >= d_from) & np.isfinite(dist) & np.isfinite(throttle))[0]
    run = 0
    for s in idx:
        if throttle[s] >= threshold:
            run += 1
            if run >= min_samples:
                return float(dist[s - min_samples + 1])
        else:
            run = 0
    return None
