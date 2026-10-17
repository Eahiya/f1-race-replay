# F1 Race Replay - Progress & Technical Documentation

This document tracks all recent architecture improvements, bug fixes, newly developed features, and roadmap progress made on the **F1 Race Replay** project.

---

## 1. Bug Fixes & Stability Enhancements

### 1.1 Resolution of `DataNotLoadedError` (Future & Incomplete Sessions)
* **Problem:** When attempting to load sessions without recorded telemetry (such as the 2026 season where provisional schedules exist but races have not occurred), FastF1 emitted a warning (`Failed to load telemetry data!`) but silently returned a session object where internal position data (`_pos_data`) was uninitialized (`None`). Downstream calls to `lap.get_telemetry()` subsequently crashed with `fastf1.core.DataNotLoadedError: The data you are trying to access has not been loaded yet. See Session.load`.
* **Root Causes:**
  1. `datetime.now().year` evaluating to 2026 caused `get_season()` and the GUI to default to 2026.
  2. `load_session()` did not validate whether telemetry/car position data was actually loaded.
  3. `_process_single_driver()` called `lap.get_telemetry()` without error guarding.
* **Fixes Applied:**
  * **Telemetry Availability Check:** Updated [`load_session()`](file:///e:/Project/f1-race-replay/src/f1_data.py) to check `hasattr(session, '_pos_data')`. If missing, it immediately raises a descriptive `ValueError` informing the user that telemetry is unavailable for that session.
  * **Per-Lap Error Guard:** Wrapped `lap.get_telemetry()` in [`_process_single_driver()`](file:///e:/Project/f1-race-replay/src/f1_data.py) in a `try...except` block so incomplete lap packets do not crash the replay generation.
  * **Season Default Capping:** Capped [`get_season()`](file:///e:/Project/f1-race-replay/src/lib/season.py) to `min(year, 2025)` to guarantee that default app launches point to seasons with recorded telemetry data.
  * **GUI & CLI Error Handling:** Wrapped session loading in [`main.py`](file:///e:/Project/f1-race-replay/main.py) and PySide6 worker threads to display clean error dialogs (`QMessageBox.critical`) rather than unhandled subprocess crashes.

---

## 2. Interactive Pit Wall & Insights Suite

The application features a decoupled telemetry streaming architecture: the primary Arcade race replay process hosts a `TelemetryStreamServer` (TCP socket broadcasting 25 FPS JSON packets), while secondary PySide6 analytical windows consume the data via `TelemetryStreamClient` (`PitWallWindow` base class).

We designed, implemented, and integrated four broadcast-grade Pit Wall insight windows:

```
                         ┌────────────────────────────────────┐
                         │      Primary Race Replay           │
                         │    (Arcade Engine + FastF1)        │
                         └─────────────────┬──────────────────┘
                                           │  JSON Stream (Port 9999)
                                           ▼
                         ┌────────────────────────────────────┐
                         │        TelemetryStreamServer       │
                         └─────────────────┬──────────────────┘
            ┌──────────────────┬───────────┴──────────┬──────────────────┐
            ▼                  ▼                      ▼                  ▼
┌──────────────────────┐ ┌───────────────┐ ┌────────────────────┐ ┌───────────────┐
│ Head-to-Head Compare │ │ Tyre Strategy │ │ Race Intervals &   │ │ Weather Radar │
│ (Dual Driver Traces) │ │ (Stint Gantt) │ │ Timing Tower       │ │ (Meteo & Wind)│
└──────────────────────┘ └───────────────┘ └────────────────────┘ └───────────────┘
```

### 2.1 Head-to-Head Telemetry Comparison
* **File:** [`src/insights/head_to_head_window.py`](file:///e:/Project/f1-race-replay/src/insights/head_to_head_window.py)
* **Features:**
  * **Dual Driver Selectors:** Allows selecting any two drivers on the grid (Driver 1 vs. Driver 2) with dynamic team color badges.
  * **Digital Stat Cards:** Real-time speed (km/h), current gear, DRS status badge, green throttle input bar (0–100%), and red brake input bar (0–100%).
  * **Live Delta Readout:** Displays speed difference (`Δ +14.2 km/h NOR`) and on-track distance gap in meters (`VER +35.4 m`).
  * **Stacked Comparison Plots:** High-performance Matplotlib charts comparing:
    1. Speed curves overlaid with team colors.
    2. Stepped gear comparison.
    3. Throttle and brake application overlays.
  * **Domain Modes:** Toggleable between **Rolling Last 30 Seconds** (time mode) and **Current Lap** (distance into lap mode).

### 2.2 Live Tyre Strategy & Stints Visualizer
* **File:** [`src/insights/tyre_strategy_window.py`](file:///e:/Project/f1-race-replay/src/insights/tyre_strategy_window.py)
* **Features:**
  * **Grid Stint Gantt Chart:** Custom-painted timeline showing all 20 drivers ordered by live track position.
  * **Official F1 Compound Badging:** Visual color coding for Soft (`#FF1801`), Medium (`#FFD700`), Hard (`#FFFFFF`), Intermediate (`#39B54A`), and Wet (`#00A3E0`).
  * **Live Pit Stop Detection:** Monitors driver compound and tyre life across laps to automatically detect pit stops, record completed stints, and track stint lengths (e.g., `M18`).
  * **Single Driver Tyre Health Inspector:**
    * Detailed tyre compound and age badge.
    * Remaining grip percentage progress bar (color-coded green $\rightarrow$ amber $\rightarrow$ red).
    * Degradation rate estimation (seconds per lap lost).
    * Projected pit stop window calculation.

### 2.3 Live Race Intervals & Timing Tower
* **File:** [`src/insights/race_intervals_window.py`](file:///e:/Project/f1-race-replay/src/insights/race_intervals_window.py)
* **Features:**
  * **Dynamic Leaderboard:** Real-time grid sorting with starting-grid gain/loss indicators (`▲ +3`, `▼ -1`, `- 0`).
  * **Gap to Leader:** Live gap in seconds or `+1 LAP` indicators.
  * **Interval to Car Ahead & DRS Battle Radar:**
    * Live time interval to the preceding car.
    * When the interval drops below 1.000s, the row highlights with an amber badge: `⚡ DRS +0.345s`, enabling users to instantly spot overtakes and DRS battles.
  * **Tyre & Telemetry Status:** Displays active tyre compound, tyre life in laps, current speed, gear, and track status (`TRACK`, `DRS ON`, `PIT LANE`).

### 2.4 Weather & Track Meteorology Station
* **File:** [`src/insights/weather_radar_window.py`](file:///e:/Project/f1-race-replay/src/insights/weather_radar_window.py)
* **Features:**
  * **Digital Environment Gauges:** Real-time readouts for Track Temperature (°C), Ambient Air Temperature (°C), and Humidity (%).
  * **Dynamic 360° Wind Compass:** Custom widget with cardinal direction markers (N, E, S, W) and an animated vector needle indicating wind direction and speed in both m/s and km/h.
  * **Track Surface Status:** Real-time banner indicating `TRACK CONDITION: DRY` or `⚠️ RAIN DETECTED: WET`.
  * **Rolling Session Temperature Evolution Plot:** Live chart tracking track vs. air temperature trends across session elapsed time.

---

## 3. Navigation & Menu Integration

* **File:** [`src/gui/insights_menu.py`](file:///e:/Project/f1-race-replay/src/gui/insights_menu.py)
* **Updates:** Reorganized the floating Insights Menu into clean functional categories and wired up all launch callbacks:
  * **Telemetry & Battles:**
    * *Head-to-Head Comparison*
    * *Driver Live Telemetry*
    * *Telemetry Stream Viewer*
  * **Race Strategy & Pit Wall:**
    * *Live Race Intervals & Timing*
    * *Tyre Strategy & Stints*
  * **Track & Meteorology:**
    * *Track Position Map*
    * *Weather & Meteorology Radar*

---

## 4. UI/UX Overhaul — Race Replay & Application Shell

A full frontend restyle of the application (no backend/data-layer changes), focused first on fixing the blurry track rendering and then on bringing every on-screen surface to a single broadcast-style dark theme.

### 4.1 Track Rendering Rebuild (Blurry Track Fix)
* **File:** [`src/interfaces/race_replay.py`](file:///e:/Project/f1-race-replay/src/interfaces/race_replay.py)
* **Problem:** The track was drawn by filling jagged inner/outer polygons, producing soft, aliased edges that looked blurry at replay zoom levels.
* **Fixes Applied:**
  * **Polyline-Based Surface:** The track is now drawn as thick anti-aliased polyline strips (dark asphalt surface, `rgb(44, 48, 55)`) bounded by crisp white edge lines, staying sharp at any window size or zoom.
  * **Start/Finish Line:** Bolder checkerboard band rendered across the full track width.
  * **DRS & Sector Overlays:** Cleaner green DRS-zone segments and sector accents composited over the new surface.
  * **Car Rendering:** Team-color halos behind each car, sharp 3-letter label chips, and a highlight ring on the selected driver.
  * **Performance:** Safety-car indicator labels were simplified to avoid per-frame text layout costs.
  * **DPI Awareness:** Layout was audited against the app's 125% display scaling — all component geometry is specified in logical units so proportions hold on high-DPI screens.

### 4.2 HUD Component Restyle
* **File:** [`src/ui_components.py`](file:///e:/Project/f1-race-replay/src/ui_components.py)
* **Components updated:**
  * **Lap / Race-Time Header:** Clean two-line card anchored top-left.
  * **Weather Panel:** Dark translucent card with icon rows for track/air temperature, humidity, wind, and rain status.
  * **Leaderboard:** Proper column layout — position, driver abbreviation in team colour, tyre compound badge with age, DRS/pit indicators — plus info/legend toggles.
  * **Playback Controls:** Circular rewind/pause/forward buttons with a speed pill (`− 4.0x +`), bottom-centered.
  * **Help System:** Compact bottom-left "Help (Click or 'H')" chip; the popup now opens repositioned so it no longer covers the playback controls.
  * **Legend, Driver Info Card, Progress Bar:** Polished to match the new theme.
* **Qualifying Interface:** [`src/interfaces/qualifying.py`](file:///e:/Project/f1-race-replay/src/interfaces/qualifying.py) automatically inherits the restyled shared components (legend, controls popup).

### 4.3 Shared Qt Dark Theme
* **New File:** [`src/gui/theme.py`](file:///e:/Project/f1-race-replay/src/gui/theme.py) — central palette, typography, and reusable widget styles (panels, buttons, list items) for all PySide6 windows.
* **Applied To:**
  * [`src/gui/insights_menu.py`](file:///e:/Project/f1-race-replay/src/gui/insights_menu.py) — themed, and the clipped insight-button description text was fixed.
  * [`src/gui/race_selection.py`](file:///e:/Project/f1-race-replay/src/gui/race_selection.py) — themed to match the rest of the application.

### 4.4 Verification
* The application was run repeatedly with an automated capture harness; start-of-race, mid-race (DRS active, safety-car labels), and help-popup frames were captured and inspected.
* Track output was pixel-sampled to confirm the surface colour (`44, 48, 55`) and white edge lines render exactly as designed.
* Qt windows were rendered offscreen with real system fonts to confirm the theme applies without flashing windows on the desktop.
* All modified modules pass `py_compile` and full end-to-end import checks.

---

## 5. Replay Experience & Track Enhancements (Update #2)

Broadcast-style camera, timing and timeline features for the Arcade race replay (frontend only, plus one contained data-layer addition for sector splits).

### 5.1 Dynamic Camera Modes (Driver Chase & Battle Cam)
* **File:** [`src/interfaces/race_replay.py`](file:///e:/Project/f1-race-replay/src/interfaces/race_replay.py)
* **Design:** The track geometry is baked once into rotated numpy world-space arrays, so the camera is a single scale+translate projection evaluated per frame (`_bake_world_geometry` / `_apply_world_transform`). Camera targets lerp smoothly in `on_update` (also while paused), starting from the fitted free-cam baseline saved by `update_scaling`.
* **Chase Cam:** Clicking a driver row in the leaderboard selects them (existing selection) and the camera smoothly zooms to 2.2× centred on that car; clicking the same row again returns to the free overview.
* **Battle Cam:** `C` toggles an automatic camera that frames the closest same-lap battle every frame (`_closest_battle`, raw timing-distance metric, gap converted to seconds via trailing-car speed).
* **Mode Badge:** A top-centre pill reports the active mode, e.g. `BATTLE CAM · RUS vs HAM +0.49s` or `CHASE CAM · NOR` (hidden in free cam).

### 5.2 On-Track Interval Tags
* Broadcast-style `+0.42s` chips drawn between every on-track pair within 1.0s (capped at 6), tinted with the trailing driver's team colour.
* Gaps use the raw F1 timing `dist` field (same metric as the battle cam). Earlier prototypes used KD-projected track progress, which distorts gaps near the lap seam (at Spa the final corner sits beside the pit straight), overstating a 0.49s battle as 3.3s.
* `T` toggles the tags on/off.

### 5.3 Interactive Timeline Scrubber
* **File:** [`src/ui_components.py`](file:///e:/Project/f1-race-replay/src/ui_components.py) (`RaceProgressBarComponent`, `extract_race_events`)
* The progress bar is now **visible by default** and remains click-to-seek with hover tooltips.
* **New event bookmarks:** pit stops (detected from tyre-compound changes, labelled e.g. `PIA → MEDIUM`, blue dots) and lead changes (P1 changing hands, purple triangles). A 30 m margin / lap-gain filter ignores P1 re-order jitter caused by bunched packs under SC/VSC.
* Existing bookmarks: Safety Car & VSC segments, yellow/red flags, DNF markers, lap ticks. Legend now shows all six marker types in two rows.
* **Bug fixed en route:** two `arcade.draw_triangle_filled` call sites (playhead knob, new lead marker) passed tuple arguments instead of six flat coordinates — latent because the bar defaulted to hidden.

### 5.4 Sector Splits & Speed Traps
* **Data layer (contained):** `extract_sector_split_data()` in [`src/f1_data.py`](file:///e:/Project/f1-race-replay/src/f1_data.py) reads `Sector1Time/2/3` and `SpeedTrap`/`SpeedST` per lap from `session.laps`, computes per-driver laps plus session bests, and is stored in the telemetry pickle. Threaded through [`src/run_session.py`](file:///e:/Project/f1-race-replay/src/run_session.py) and [`main.py`](file:///e:/Project/f1-race-replay/main.py) as `sector_data`. **Backward compatible:** older cached telemetry lacks the key, in which case the panel stays hidden.
* **UI:** new `SectorSplitsComponent` panel (left column under Weather) showing the active driver's most recent completed lap S1/S2/S3 with broadcast colours — **purple** = session best, **green** = personal best, **yellow** = ordinary — plus their best speed-trap speed (purple when it is the session best). The panel follows the chased driver, else the race leader. `S` toggles it.

### 5.5 New Controls Summary
| Input | Action |
|---|---|
| Click leaderboard row | Chase cam on that driver (click again = free cam) |
| `C` | Toggle Battle Cam (auto closest fight < 1.0s) |
| `S` | Toggle Sector Splits panel |
| `T` | Toggle on-track interval tags |
| Click timeline | Seek to that moment (with SC/VSC/pit/lead/DNF bookmarks) |

### 5.6 Verification
* Ran the real replay via a scripted capture harness on cached 2025 Round 12 telemetry: free cam + timeline + sectors, chase cam (scale 0.043 → 0.095), battle cam locking onto the RUS/HAM 0.49s fight, and the help popup — all screenshotted and inspected.
* Real sector extraction returned 18 drivers with correct session bests; event extraction found 23 pit stops, 25 lead changes, 2 SC and 4 VSC periods.
* All touched modules pass `py_compile` + end-to-end imports (including qualifying, which shares the components).
* Note: Windows DPI scaling (125%) means framebuffer pixels = logical units × 1.25; layout is authored in logical units throughout.

---

## 6. Verification & Testing

* **Automated Unit & Stream Test:** Created and executed automated test suite verifying that all four new insight windows instantiate, cleanly connect, and parse live telemetry frame packets without errors.
* **Stream Integration:** Confirmed that `TelemetryStreamClient` threads receive data packets and update Qt canvas elements smoothly at 25 FPS.

---

## 7. Upcoming Roadmap

1. **Replay Canvas Enhancements:**
   * Dynamic zoom & driver follow camera mode in the Arcade replay window.
   * Interactive progress bar scrubber with event bookmarks (Safety Cars, pit stops, lead changes).
   * Visual on-track interval tags between battling cars ($< 1.0\text{s}$).
2. **Rendering Performance Optimization:**
   * Batching Arcade track and car drawing to eliminate micro-stutters on lower-spec hardware.
3. **Session Expansion:**
   * Free Practice (FP1/FP2/FP3) stint comparison tools.

---

## 8. Performance & Loading Optimization Pass

Measured bottlenecks first (headless micro-benchmarks on the dev machine):
JSON packet serialization costs only ~0.05 ms/pkt (not a bottleneck), while the
per-frame path paid ~0.38 ms for 20 KD-tree projections in `on_draw` plus
another ~0.38 ms in the telemetry broadcast, and allocated 40+ `arcade.Text`
objects per frame. All six `computed_data/*` cache files were 0-byte
placeholders that crashed `pickle.load` with an uncaught `EOFError`.

### 8.1 Rendering (`src/interfaces/race_replay.py`, `src/ui_components.py`)
* `on_draw` no longer projects every car through the KD-tree: `dist` is already
  cumulative race distance, so leaderboard order uses stored `(lap, dist)`
  tuples (~0.007 ms vs ~0.38 ms, same ordering).
* `_broadcast_telemetry_state` reads the stored `position == 1` leader (with a
  `(lap, dist)` fallback), reuses preformatted hex driver colours, and is
  throttled to the 25 FPS stream cadence (discrete events use `force=True`).
* New `_track_status_at` indexed lookup replaces the per-frame linear track-
  status scan (resets correctly on timeline seek).
* `_apply_world_transform` writes into preallocated NumPy buffers (`out=`);
  centre-line and DRS strips are baked once in `_rebuild_track_render_data`.
* `LeaderboardComponent` caches per-driver code/tyre-life `Text` objects;
  `RaceProgressBarComponent` caches lap-tick, legend, and tooltip texts.
  `LeaderboardComponent._progress_delta` handles tuple and legacy float
  progress values. Broadcast appearance is unchanged.

### 8.2 Versioned binary cache (`src/services/replay_cache.py`, `src/f1_data.py`)
* New envelope: schema version, algorithm version, identity (year/round/
  session), frame-count validation, fingerprint; deterministic keys
  (`{year}_R{round}_{session}_v2.pkl`).
* Atomic writes (temp + validate + `os.replace`); corrupt/0-byte files are
  quarantined, never raised; legacy raw pickles still load with `sector_data`
  defaulting to `{}`; schema/frame-count mismatches trigger regeneration.
* `get_race_telemetry(..., progress=None, year=None, round_number=None)` keeps
  its signature compatible, honors `computed_data_location` from settings,
  reports real per-driver/per-frame progress, and supports cooperative
  cancellation via `PreparationCancelled`.
* LRU budget eviction (`enforce_budget`) and stale-temp cleanup included.
* `main.py` now calls `enable_cache()` before `load_session()` and passes
  year/round through for versioned keys.

### 8.3 Telemetry stream (`src/services/stream.py`, protocol unchanged)
* Server sockets are non-blocking with `TCP_NODELAY`; a slow reader fails a
  packet (snapshots supersede) and is disconnected after 5 consecutive
  failures — verified: 47 stuck-client packets in ~4 ms, replay never blocks.
  Healthy delivery still 50/50 in order; `SO_REUSEADDR` + clean `stop()`.
* Client gains a 256 KB buffer bound (resyncs on newest packet) and an opt-in
  `coalesce` mode emitting only the newest snapshot per read (default off, so
  per-packet delivery is preserved).

### 8.4 Non-blocking preparation (`src/gui/preparation.py`, `race_selection.py`)
* Ten explicit stages with weighted overall progress; measured sub-progress
  for drivers/frames (frames/s rate + derived ETA); the FastF1 load stage is
  honestly indeterminate with measured elapsed time (FastF1 exposes no byte
  counts — no fabricated percentages/speeds).
* Worker pre-warms the versioned cache in-process, then launches the replay
  subprocess onto the warm cache; per-dialog request ids ignore stale results;
  cooperative cancel, retry, and safe shutdown; telemetry-unavailable errors
  keep the documented friendly messages.

### 8.5 Tests & benchmarks
* `tests/test_replay_cache.py` (12), `tests/test_stream.py` (7),
  `tests/test_rendering_logic.py` (6), `tests/test_preparation.py` (8):
  33 passed. `tests/bench_perf.py` prints the before/after table.
* Full `py_compile` + import checks pass for all touched modules, qualifying,
  insights windows, and the new preparation dialog.

---

## 9. Update 4 — Runtime Reliability & End-to-End Validation

### 9.1 Baseline
* Existing suite before changes: **33 passed** (`tests/test_replay_cache.py`,
  `tests/test_stream.py`, `tests/test_rendering_logic.py`,
  `tests/test_preparation.py`). No pre-existing failures; all fixes below
  address defects confirmed by new reproduction tests or real-session runs.
* After Update 4: **60 passed** (4 pre-existing files + `test_cache_extended.py`,
  `test_stream_extended.py`, `test_preparation_worker.py`, `tests/conftest.py`).

### 9.2 Real-session validation (2025 British GP, Round 12)
FastF1 data was served from the local `.fastf1-cache` (no network needed).

| Measurement | Result |
|---|---|
| Race cold prep (`load_session` + full pipeline) | 464.2 s, 148,011 frames, 52 laps, 18 sector drivers, 27,661 SC frames |
| Race warm load (375 MB versioned cache) | 3.8 s (pickle-bound; sub-second not achievable at this frame count without a schema change, which was rejected for consumer-compat risk) |
| Warm-load memory | 19 MB → 2,484 MB RSS (6.6× pickle expansion: ~3M small dicts). 64-bit Python + ≥8 GB RAM recommended |
| Quali cold prep (20 drivers, parallel Pool) | 15.0 s; warm hit 0.19 s (`2025_R12_Q_v2.pkl`) |
| Preparation worker end-to-end (warm, offscreen Qt) | 9.0 s wall; all stages emitted; request-id matched; 148,011 frames, no error |
| Leader fidelity (`position == 1` vs `(lap, dist)` max) | 149/149 sampled frames agree — broadcast optimisation verified on real data |
| Indexed `_track_status_at` vs legacy linear scan | 4,031 samples incl. backward seeks, 0 mismatches, 0.02 s total |
| `_closest_battle` on real mid-race frames | battles found in 80/80 sampled frames |
| Race-selection window (offscreen) | constructs, populates 24 schedule rows, exits cleanly |

### 9.3 Defects discovered and fixed
1. **Quali cache crashed on corrupt files (high).** `get_quali_telemetry`
   still used the pre-Update-3 path: only `FileNotFoundError` caught, so a
   0-byte/truncated quali pickle raised `EOFError`. Root cause: quali never
   migrated to the hardened cache. Fix: `replay_cache` generalized to
   `load_prepared`/`store_prepared(kind="race"|"quali")` sharing envelope,
   atomic-write, quarantine, and version logic; quali now versioned
   (`2025_R12_Q_v2.pkl`), legacy quali files still load. Regression tests in
   `test_cache_extended.py`.
2. **Non-finite floats on the telemetry wire (medium).** Real frames carry
   `NaN` in `rel_dist` (741 occurrences sampled; timing feed gaps). `json`
   emits non-standard `NaN` tokens. Fix: `sanitize_for_json` maps NaN/Inf to
   `None` at broadcast only — cached frames untouched, schema keys intact,
   strict-JSON compliant. All insight windows use `.get()` guards and never
   read `rel_dist`, so no consumer change was needed (verified by inspection).
   Regression test asserts raw bytes contain no `NaN`/`Infinity` and strict-parse.
3. **Dialog close/retry could destroy a running QThread (medium).**
   `SessionPreparationDialog.closeEvent`/`_on_retry` waited 3 s then
   proceeded; a worker blocked in FastF1 I/O would be destroyed live (abort).
   Fix: `_stop_worker()` — cancel, bounded wait, `terminate()` last resort;
   request id retired so late results are ignored. Same hazard fixed in
   `RaceSelectionWindow.closeEvent` for the schedule worker. Cancellation
   semantics documented in `preparation.py`.
4. **Stream robustness gaps (low).** `handle_client` leaked
   `_send_failures` entries; broadcast could resurrect a failure entry for a
   concurrently dropped client; `start()` twice leaked an accept loop; client
   decoded whole chunks as UTF-8 so a split multibyte char forced a
   reconnect. Fixes: failure-map cleanup, membership guard, double-start
   guard, bytes-based receive buffer decoding complete lines only.
5. **Scripting artefact (not a defect).** An offscreen probe constructing
   `RaceSelectionWindow` without pumping the event loop never exited (lingering
   QThread); with proper `processEvents`/`quit` it exits cleanly — no code
   change, noted for test authors (`tests/conftest.py` provides `qapp`).

### 9.4 Stream reliability verification (protocol unchanged)
Healthy ordered delivery 30/30 and 50/50; stuck client: 47 packets in
1.2–4.3 ms then auto-disconnect, replay never blocked; dead client removed;
multi-client independent disconnect; reconnect after server restart;
4-thread concurrent broadcast of 100 packets with zero corruption; coalesce
mode emits newest snapshot only; shutdown clean with `SO_REUSEADDR` rebind.

### 9.5 Cache integrity verification
Zero-byte, garbage, truncated, schema-mismatched, kind-mismatched, and
frame-count-mismatched entries all miss cleanly with quarantine (never
raise); pre-Update-3 envelopes (no `kind`) still load as race; legacy raw
pickles (race + quali, incl. missing `sector_data`) load with defaults;
interrupted-write `.tmp` files are never candidates and are reaped by
`cleanup_stale_tmp_files`; repeated read/write stable (single file);
distinct year/round/session identities never collide; LRU eviction keeps the
newest and evicted entries regenerate.

### 9.6 Known limitations / unverified GUI scenarios
* No display or EGL on this machine (`ImportError: Library "EGL" not
  found`), so Arcade window rendering, measured FPS/p95/p99, seeking,
  camera switching, pause/resume visuals, and insight-window rendering were
  **not** executed — rendering claims rest on logic-level validation against
  148k real frames, not wall-clock FPS. Do not present micro-benchmarks as
  application FPS.
* Warm race load is 3.8 s / 2.5 GB RSS; sub-second warm load would require a
  columnar frame schema, rejected as incompatible with the four insight
  windows' packet contract.
* Cancellation during FastF1 network I/O terminates the worker thread as a
  documented last resort; in-flight downloads cannot be interrupted
  cooperatively.
* `run_replay_check*.py` harnesses reference paths/flags that no longer
  exist (`--event`, foreign workspaces) and were not used.

### 9.7 Reproduction commands
```
QT_QPA_PLATFORM=offscreen python -m pytest tests/ -q
python -m py_compile main.py src/f1_data.py src/services/replay_cache.py src/services/stream.py src/gui/preparation.py src/gui/race_selection.py src/interfaces/race_replay.py src/ui_components.py
python tests/bench_perf.py
python -c "from src.f1_data import enable_cache,load_session,get_race_telemetry; enable_cache(); s=load_session(2025,12,'R'); get_race_telemetry(s,year=2025,round_number=12)"
python main.py --viewer --year 2025 --round 12   # requires a display
python main.py                                   # GUI session selection
```

---

## 10. Update 5 — Repository & Telemetry Audit, Memory Profiling, Safe Optimization

### 10.1 Areas inspected and repository map
* Data path: `fastf1.get_session` → `load_session` (telemetry guard) →
  `_process_single_driver` (per-lap `get_telemetry`, concat, time-sort) →
  resample onto the common 25 Hz timeline → frame dicts (`t, lap, drivers,
  weather, safety_car`) → `_compute_safety_car_positions` →
  `extract_sector_split_data` → atomic versioned pickle.
* Critical interfaces (unchanged): frame dict schema consumed by
  `race_replay.py`, the four insight windows, and the TCP JSON protocol;
  `(lap, dist)` ordering used by leaderboard, battle cam, interval tags,
  `extract_race_events`, and `race_intervals_window`.
* Cache: `replay_cache.load_prepared/store_prepared(kind="race"|"quali")`,
  deterministic keys, atomic writes, quarantine, LRU budget.
* Workers: `SessionPreparationWorker` (staged progress, cooperative cancel,
  retired request ids) and `FetchScheduleWorker`; both dialogs terminate
  rather than destroy running threads.
* Baseline before changes: **60 passed** on Python 3.13.6 / win32, arcade
  3.3.3, fastf1 3.7.0, numpy 2.2.6, pandas 2.3.1, PySide6 6.12.0, scipy
  1.16.1, pytest 9.0.2. After: **66 passed**.

### 10.2 Telemetry audit findings (2025 British GP, 148,011 frames, real data)
* Healthy: timestamps exact 40 ms grid, monotonic; leader lap 1→52 monotonic;
  positions an exact 1..20 permutation every frame; driver set stable (20);
  status codes 1/2/4/6/7 only (no red flag — correct); SC phases present
  (27,661 frames); quali payload clean, no non-finite values.
* Expected missing data (not defects): COL `rel_dist` NaN in all frames
  (single-lap DNF telemetry lacks it); COL/LAW (1 lap), BOR (4), HAD (18),
  ANT (23), TSU (51) retired — interpolation clamps their frozen values
  (speed ≈ 0) for the rest of the race; DNF cars stay rendered.
* Observed source facts (preserved, not clamped): throttle up to 104.0,
  race brake 0..1 (windows scale ×100), gear 0..8, tyre ints 0..3.
* Experiments run, then reverted (see 10.4): per-driver accumulation is
  unsound with this feed — documented in code.

### 10.3 Memory and performance methodology and results
* Method: `psutil` process RSS (tracemalloc cannot see native/peak); watcher
  thread sampled peak during load; repeated loads + `del`/`gc` to separate
  leaks from allocator retention. Warm file 375 MB.
* Results: RSS 19 MB → 2,484 MB after warm load (6.6× expansion ≈ 17.6 KB /
  frame: 148k frame dicts + ~3M driver dicts + ~30M boxed floats — the
  working set itself). Peak during load 2,517 MB (no transient spike).
  Repeated loads plateau; deleting all references returns RSS to 34 MB:
  **no leak, no duplication, high-but-stable working set.**
* Reference reruns: cold prep 464 s (earlier env) / 260 s / 250 s (same code,
  OS-cache warmth varies); warm load 3.8–5.6 s; `bench_perf.py` unchanged
  within noise. Display-dependent checks (Arcade FPS, seeking, cameras)
  unavailable — no EGL on this machine; not claimed.

### 10.4 Decision table
| Finding | Evidence | Proposed fix | Compatibility risk | Decision |
|---|---|---|---|---|
| `dist` resets each lap; comment claimed cumulative | Full-scan audit; accumulator never advanced | Accumulate observed per-lap maxima | Ordering/gap corruption | **Tried, measured harmful, reverted** |
| Cache never enforced `algo_version` | Code read; envelopes carry it unchecked | Enforce on load | Forces one regen on next algo bump | **Implemented** (no regen now: version stayed 2) |
| Misleading "race distance" comments | Same audit | Correct wording only | None | **Implemented** |
| 2.5 GB warm working set | RSS + reuse experiments | Columnar/binary schema | Breaks frames, windows, stream | **Rejected** — no isolated fix exists |
| NaN `rel_dist`, frozen DNFs, throttle 104 | Audit §§10.2 | None (sanitize covers wire) | — | **Documented, unchanged** |

### 10.5 Code changes and why
* `src/f1_data.py`: reverted accumulation experiment; corrected `dist`
  comments to within-lap semantics (the only change that survives).
* `src/services/replay_cache.py`: `load_prepared` now misses (regenerate)
  when `envelope.algo_version != PREPROCESS_ALGO_VERSION`; version stays 2
  so no user regen is triggered by this update.
* `src/interfaces/race_replay.py`, `src/ui_components.py`: comment-only
  corrections to match verified semantics.
* Tests: `tests/test_telemetry_quality.py` (reset-semantics lock with
  measured rationale, NaN/position/leader invariants, one real-session
  integration guarded by skip); algo-enforcement test in
  `test_cache_extended.py`; `closeEvent` worker tests + `tests/conftest.py`.
* Verification of the revert: post-revert rebuild is NaN-aware identical to
  the pre-experiment backup (0/1,526 sampled frames differ; statuses,
  sectors, laps equal); suite 66 passed; bench stable.

### 10.6 Analyst foundations (Driver Performance Analyst readiness)
* Provided: stable 3-letter driver identity + team colours (`get_driver_colors`,
  frame keys); common 25 Hz aligned timeline with per-driver resampling;
  speed/throttle/brake/gear/dist/tyre-life/weather fields with known units;
  sector splits + session bests (`extract_sector_split_data`); events
  (`extract_race_events`, statuses, SC phases); rolling-30 s + current-lap
  buffers in head-to-head/driver-telemetry windows.
* Provenance warning: Safety Car positions are **simulated**, not observed
  (`_compute_safety_car_positions`); gap seconds are derived via trailing
  speed. An analyst must label these as derived.
* Gaps: no per-sample validity flags (NaN = missing by convention; DNF
  interpolation is implicit, not flagged); no cumulative distance (rejected
  above); no uncertainty estimates.
* Recommendation for Update 6: add an explicit, additive validity layer
  (per-driver/per-lap quality flags: full / partial / interpolated-frozen /
  missing) carried alongside — not inside — the frozen frame schema, plus
  provenance labels (observed / derived / simulated) on every metric the
  analyst will consume. No ML model until that ground truth exists.

---

## 11. Update 6 — Race Intelligence Foundation: Data Validity & Provenance

### 11.1 Repository audit findings
Baseline before changes: **66 passed** (Python 3.13.6 / win32, arcade 3.3.3,
fastf1 3.7.0, numpy 2.2.6, pandas 2.3.1, PySide6 6.12.0, scipy 1.16.1).
No pre-existing failures. New files only: `src/services/data_quality.py`,
`tests/test_data_quality.py`; no existing source file was modified.

| Area | Current behavior | Evidence/source | Risk | Action taken |
|---|---|---|---|---|
| Resample markers | Linear 25 Hz `np.interp` + edge clamp; no observed/interpolated/clamped markers recorded | `f1_data.py` frame build | Provenance gap | Resampled channels labelled provenance `unknown` + pipeline detail; no pipeline rewrite |
| DNF clamp | Values frozen at edge beyond a driver's raw range (e.g. speed 0 for ~147k frames) | Update 5 audit (COL/LAW/BOR/HAD) | Mistaking clamp for observation | Heuristic `frozen` with threshold, labelled heuristic, never a fault claim |
| Safety-car positions | Kinematic reconstruction from track status | `f1_data.py` SC docstring | Claiming observed GPS | Provenance `simulated` |
| NaN propagation | All-NaN channels stay NaN; wire sanitize maps to null | Updates 4–5 | None remaining | Quality `missing`; no action |
| Derived events | Lead-change (+30 m/lap-gain filter), pit (tyre-change), DNF disappearance, flag segments | `ui_components.py extract_race_events` | Heuristic overreach | Provenance `derived` + rule detail |
| Sector splits/bests | Timing-feed aggregation | `extract_sector_split_data` | None | Provenance `derived` |
| Gaps/intervals/DRS | Speed-based calculations | Replay + intervals windows | Derived mistaken for measured | Provenance `derived` |
| Enum channels | gear/DRS/tyre legitimately constant | Code read | False frozen claims | Excluded from frozen heuristic |
| `dist` resets per lap | Verified correct under lap-aware consumers | Update 5 experiment | None | Documented; no action |
| `src/_stubs.py` | Untracked arcade stub harness, unused by app/tests | File read | Dead-code confusion | No action (out of scope) |

Classifications: SC simulation = confirmed provenance fact; DNF clamp and
missing channels = expected source-data limitations; event heuristics =
inferred conditions; missing resample markers = architectural gap
(documented, not rebuilt).

### 11.2 Quality and provenance contract
* Quality: `full` | `partial` | `frozen` | `missing`, plus `unknown` only
  for empty scopes / insufficient evidence. Field-level findings aggregate
  to driver/lap summaries by worst-wins
  (missing > partial > frozen > unknown > full); a summary is `full` only
  if every assessed field is `full`.
* A constant run is `frozen` only on continuous channels
  (x, y, dist, rel_dist, speed, throttle, brake) over >= 25 frames (1 s),
  labelled heuristic with threshold and reason; shorter constant runs are
  `unknown`; discrete channels are never `frozen`.
* Provenance: `observed` (verbatim feed records, e.g. track statuses),
  `derived` (ordering, lap rounding, sector aggregates, gaps, colors),
  `simulated` (safety-car reconstruction only), `unknown` (resampled
  channels whose markers the pipeline discarded, with pipeline detail;
  never fabricated).
* Scopes: field (driver + frame range), driver/lap, session; plus the
  compact `quality_summary_for_analysis` query
  ("are speed/throttle/brake/gear valid for this driver?").
* Rules versioned as `QUALITY_RULES_VERSION = 1`, independent of the
  frozen frame contract. No persistence (not proven needed).

### 11.3 Files added or changed
* Added `src/services/data_quality.py` (~360 lines): read-only assessors
  (`assess_field`, `assess_driver_lap`, `assess_session`,
  `quality_summary_for_analysis`), O(1) streaming accumulators (no
  per-sample retention), plain-JSON-serializable outputs.
* Added `tests/test_data_quality.py` (22 tests). No UI, stream, cache, or
  frame-schema changes; no new dependencies.

### 11.4 Test coverage and real-session validation
22/22 pass: full/partial/missing incl. None/NaN/±inf (parametrized),
frozen heuristic, discrete-constant negatives, short-run unknown,
provenance mapping (observed/derived/simulated/unknown), retired drivers,
single-driver missing channel, empty scopes, determinism, aggregation,
immutability (deepcopy), schema-compat, compact query. Real 2025 R12
integration: 148,011 frames — COL `rel_dist` missing, COL/LAW clamp
`frozen` with heuristic reason, VER speed full, laps 1–52, 27,661 SC
frames, status codes {1,2,4,6,7}.

### 11.5 Runtime and memory measurements
* `assess_session` on 148,011 frames × 20 drivers: ~16–17 s wall, repeat
  run 0.93× (single pass, nothing cached), **zero RSS growth**
  (2,484 → 2,484 MB), summary 10.7 KB for 260 field findings.
* Analysis is opt-in (prep/analysis time), never in the render loop.
* Before/after: replay frames bit-identical (read-only service);
  pre-existing 66 tests still pass; TCP shape and cache format untouched.

### 11.6 Limitations and risks
* Per-sample observed-vs-interpolated provenance is unrecoverable without
  a pipeline rewrite (deliberately not undertaken); resampled channels
  stay `unknown` with an explicit detail string.
* Lap-scope boundaries use rounded resampled `lap` (±1 frame misassignment
  possible at crossings).
* Frozen heuristic cannot distinguish a parked car (red flag, pit box)
  from clamp-carried data — both mean "no fresh observations", which is
  all the label claims.
* No display on this machine: analysis service needs none (headless
  tested); Arcade rendering remains explicitly unverified, unchanged.

### 11.7 Analyst query guide (Update 7 entry point)
```python
from src.services.data_quality import assess_session, quality_summary_for_analysis
summary = assess_session(prepared_payload)          # once, ~17 s at full length
answer = quality_summary_for_analysis(summary, "VER",
                                       fields=["speed", "throttle", "brake", "gear"])
# answer -> {"driver", "aggregate", "fields": {field: {quality, provenance, reason}}}
```
Trust rules for Update 7: consume `full` freely; treat `partial` field-wise;
treat `frozen` as carried-forward (usable for position, not for inputs);
exclude `missing`; never present `simulated` safety-car coordinates as
measured GPS. Next milestone: Driver Performance Analyst built strictly on
these gates — no model training in Update 6 per locked decisions.

### 11.8 Reproduction commands
```
QT_QPA_PLATFORM=offscreen python -m pytest tests/ -q
QT_QPA_PLATFORM=offscreen python -m pytest tests/test_data_quality.py -q
python -m py_compile src/services/data_quality.py tests/test_data_quality.py
```

---

## 12. Update 7 — Qualifying Performance Intelligence

### 12.1 Audit and baseline
Baseline before changes: **88 passed** (same env as Update 6). After: **121 passed**.
Traced: quali cache holds one fastest lap per driver per segment with
25 Hz telemetry (x, y, dist, rel_dist, speed, gear, throttle, brake, drs +
weather), `t` ending at the official lap time, plus sector times, compound,
distance-based DRS zones, and session track statuses. NOT retained: lap
numbers, validity flags, non-fastest laps, sector boundary distances.
Verified on 2025 R12: dist strictly monotonic within-lap (0→~5,828 m),
throttle ≤100 with zero NaN in the audited laps (the 104 sentinel seen in
race data is still masked by contract), brake 0–100 (quali scale).
Official corner metadata (`circuit_info.corners`: Number/Letter/Distance)
exists live via FastF1, not in cache. UI patterns reused: dark cards +
combos + Matplotlib QtAgg (`head_to_head_window`), QThread workers
(`preparation.py`), insights-menu entries.

### 12.2 Architecture and calculations
New `src/services/quali_analysis.py` (pure, deterministic, read-only;
`ANALYSIS_RULES_VERSION = 1`): lap selection (fastest-only, explicit
warning otherwise) → comparability via Update 6 adapter
(`quali_frames_to_race_view`; resampled channels stay provenance
`unknown`) → 2 m common-grid alignment (validated strictly-increasing
dist; continuous channels masked-interpolated with 10 m gap refusal;
gear/DRS nearest; throttle outside 0–100 masked incl. 104 sentinel) →
throttle events (configurable 95% default, 5-sample sustain, lifts,
per-section genuine-exit pickups) → cumulative delta
`delta_t(d) = t_A − t_B` + section deltas with develop/persist/reversed
split → official corner sections (speed-dip validated, flat corners
excluded with reasons) or labelled distance-third fallback + DRS
overlays → deterministic findings with measurement/causal confidence
kept separate and a banned-causal-vocabulary guard.
New `src/insights/quali_intel_window.py`: session/driver/segment pickers
(defaulting to newest cached quali), worker-thread analysis with cancel,
throttle/speed/delta charts of actual telemetry, section table, clickable
findings with chart navigation, warnings panel, markdown export, headless
CLI (`--headless`). Worker hardening (acceptance): entry-cancel check,
disconnect-before-retire so stale runs cannot deliver, export disabled
while analyzing. Segment→cache-type mapping fix
(`session_type_for_segment`: Q1/Q2/Q3 live under "Q"). Menu entry under
Telemetry & Battles. Frozen frame schema, TCP payloads, cache format, and
insight contracts unchanged (only additive menu lines elsewhere).
Partial/frozen aggregates now also raise explicit comparability warnings
(acceptance gap closure), in addition to the confidence downgrade.

### 12.3 Real-data examples (2025 British GP Q3, cached offline)
VER (84.892 s) vs NOR (85.010 s): residual +0.056 s within tolerance;
sections show VER +0.211 s T4→T5 and +0.211 s T7→T13, NOR fightback late;
11/18 corners validated (T1/T2/T8–T12 correctly excluded as flat in
qualifying after per-corner speed-profile verification), 12 corner
sections, per-section pickup findings with exit-speed corroboration.
`--headless` report verified end to end.

### 12.4 Tests and performance
40 new tests (27 analysis + 13 UI + lifecycle), all passing; 128/128 total
after acceptance (was 121 at feature freeze), no existing test touched. Coverage: pickup/sustain/lifts/spikes/NaN/sentinel,
alignment exactness/gaps/non-monotonic/density/start-offset/discrete
handling, hand-calculated section deltas, sign conventions, residuals,
corner mapping + fallback, quality/provenance reuse, invalid laps,
determinism, immutability, causal-withholding (banned stems asserted
absent), empty/missing scopes, real VER/NOR and VER/PIA integrations,
worker full-run/cancel-before-start/close-mid-run/retire-stale/failure
path/navigation/export/end-to-end double analyze.
Benchmarks (2025 R12 Q3, warm cache): payload load 0.19 s, session+corners
load 2.54 s, selection 4 ms, alignment 4 ms, analysis 40–200 ms,
tracemalloc peak 2.1 MB, repeat deterministic. UI responsiveness:
analysis off-thread with cancel; charts render ~2,913 grid points.

### 12.5 Limitations and deferred work
Lap validity/invalidation flags and true lap numbers are not retained —
reported as unverified/unknown, never guessed; only the fastest lap per
segment is cached (manual lap choice limited to what exists);
per-lap flag exposure unknown when the session had red/yellow (warned);
pickup/threshold defaults are configurable, not physical truths; Arcade
rendering and visual chart behaviour unverified (no display; offscreen
construction/render paths exercised). Deferred: multi-lap selection
(requires cache enrichment), sector-boundary attribution (boundaries not
retained), weather-adjusted comparison.

---

## 13. Update 8 — Battle Reconstruction & Overtaking Intelligence

### 13.1 Audit and data-availability matrix (2025 British GP race payload)
Baseline before changes: **128 passed**, no pre-existing failures. New
files only (`battle_analysis.py`, `battle_intel_window.py`, two test
files, menu entry); frozen schema, TCP, cache, replay and insights
untouched. Frames carry per driver: position (derived sort, exact 1..20
permutation every frame), lap, within-lap dist (resets), speed km/h,
throttle 0–100 (104 sentinel present in race data), brake 0–1 race scale,
gear/drs/tyre/tyre_life, x/y; per frame: t (40 ms grid), leader lap,
weather, simulated safety-car coords (excluded from evidence; status
codes used instead). 33 track-status segments (codes 1/2/4/6/7, two SC
periods); 23 tyre-change pit events; sector splits with some None.
Absent: penalties/results/race-control messages (penalty causes
unclaimable), explicit gap fields (derived same-lap only), retirement
flags (frozen-dist+lap heuristic, documented), per-lap flag exposure.

### 13.2 Architecture
`src/services/battle_analysis.py` (deterministic, read-only,
`ANALYSIS_RULES_VERSION = 1`): 5 Hz step matrices → vectorized
position-change detection (±0.2 s event intervals, 10 s persistence,
5 s startup settling) → classification (pit evidence → non-running →
neutralized context → supported pass → unresolved, with opening-lap and
both-frozen guards) → battle lifecycle (1.0 s enter / 2.0 s exit
hysteresis, attack < 0.5 s, closing-rate slopes, pit/neutralized/frozen
interruptions, pass↔battle linkage) → approach analysis (gap trend,
relative speed, valid-only time-to-contact, corner context) →
conservative mechanism rules (braking/exit/straight only with
corroboration) → findings with separate classification/timing/mechanism
confidence and banned-causal-vocabulary enforcement. `src/insights/
battle_intel_window.py`: session picker (newest cached race default),
worker-thread analysis with cancel/retire semantics, battle/overtake
views, gap/speed charts of analysis output, evidence detail, markdown
export, headless CLI. Menu entry under Telemetry & Battles.

### 13.3 Real-session validation (2025 British GP, offline cache)
452 battles (75 linked confirmed passes, 56 defended/aborted), 1,465
order records: 275 supported passes, 81 pit-cycle, 8 retirement (COL×5,
BOR at t≈422 on his final lap, HAD×2 at t≈2227 on hers), 178
race-control (2 SC + VSC periods), 923 unresolved incl. transients.
Spot checks: BEA→BOR retirement at BOR's last lap; STR→OCO supported
with +12.6 km/h and straight mechanism; finish-line frozen-car flips
labelled retirement/unresolved, never passes. No ground-truth overtake
log exists; counts are algorithm output under documented rules, and
thresholds were not tuned to produce them. Early-race shuffles satisfy
the same evidence bar and are counted (opening_lap flagged).

### 13.4 Tests and performance
36 new tests (26 analysis + 10 UI), all passing; **164/164 total**, no
existing test touched. Coverage per mission lists: trends, hysteresis,
noise, gaps/NaN/frozen, TTC validity, interruptions (pit/neutralized/
non-running), all five classifications, transients, startup/finish
artifacts, determinism, immutability, units/signs, banned language,
linkage, empty sessions, worker lifecycle (run/cancel/close/retire/
failure/navigation/export), menu wiring. Benchmarks (warm cache):
matrices ~2 s, detection ~7 s, full analysis ~140–150 s, peak +60 MB
over the 2.5 GB payload (bounded episodes, lazy per-selection charts,
no per-sample objects). UI stays responsive via worker + cancel.

### 13.5 Limitations, ML decision, and next step
Display-dependent rendering unverified (offscreen exercised only);
last-lap/finish-region flips carry irreducible clamp ambiguity
(labelled, never forced); front-wing-only stops are a known residual
misclassification risk; cross-lap pairs are not battles by design;
success prediction explicitly out of scope. Local ML deferred: no
labeled overtake dataset exists, deterministic rules cover the task,
and heuristic scores are reported as categorical confidence, never
probabilities. Recommended next: Driver Performance Analyst gated on
Update 6 quality + Update 7/8 findings (pace vs teammate deltas on
validated laps, battle-adjusted).

## 14. Update 9 - Premium Pit-Wall UI/UX Overhaul

Presentation-only change: every frozen contract (replay-frame schema,
TCP payload, replay timing/seeking, cache format, driver identity,
telemetry units, Update 6/7/8 outputs, exports, menus, shortcuts,
worker lifecycle) is untouched. No new analysis or data is introduced;
existing findings are only rendered differently.

### 14.1 Shared design system (`src/services/ui_theme.py`)
New single source of truth for the visual language, imported by both
renderers. Defines a "Premium F1 Pit Wall" palette (surfaces
`#0B0D10`/`#11151B`/`#1B2029`, F1-red accent `#E10600`, semantic
success/warning/danger/info, track-status and DRS colours, official
compound colours), a `Typography` scale (display 24 → caption 10 with
weights), an 8-step `Spacing` scale, `ComponentSizes`, and
`BorderRadius`. `THEME` (`Theme` dataclass) aggregates all tokens.
Helpers: `hex_to_rgb`, `hex_to_rgba`, `rgba_to_hex`, `lighten`,
`darken`. `apply_dark_theme(widget_or_app)` emits one parameterised QSS
used by all PySide6 windows; `apply_arcade_theme(theme_obj)` maps the
same tokens onto the Arcade `Theme` class (hex → RGBA tuples).

### 14.2 Migration
`src/gui/theme.py` now delegates to `ui_theme` (keeps legacy aliases);
the Arcade `Theme` class in `src/ui_components.py` derives colours from
`THEME`; the eight insight windows (`head_to_head`, `race_intervals`,
`tyre_strategy`, `weather_radar`, `driver_telemetry`,
`track_position`, `battle_intel`, `quali_intel`) and
`settings_dialog.py` consume `THEME`. Driver-identity colours in charts
are intentionally kept separate from UI chrome.

### 14.3 Evidence-panel readability rework (battles + qualifying)
Both analysis windows previously dumped raw Python containers into their
evidence panel (e.g. `Measured: {'gap_before_s': 11.75..., 'transitions': [...]}`).
A shared renderer, `src/services/evidence_render.py`, now turns a finding
into structured, labelled sections — header (id + human-readable kind),
explanation, then **Measured / Evidence / Section / Confidence / Missing
evidence / Next steps / Data quality / Provenance** — with per-key labels
and units, coloured quality badges, and nested values flattened inline.
Classifications, battle states, and mechanisms are shown as prose
(`on_track_pass_supported` → "On-track pass (supported)"); track-status
codes are named ("Green flag (1)"); provenance tokens are de-underscored.
`render_evidence_html` is a pure function over plain data (offscreen-testable);
values are read verbatim from analysis output, so data meaning is unchanged.
The battle window adds a colour legend and a humanised table column
("Classification", "Conf / Quality"); the qualifying window colours each
finding kind and humanises its list labels. The live telemetry windows
(head-to-head, intervals, tyre strategy, weather, driver telemetry, track
map) were reviewed and already present human labels/units, so they needed
no change.

### 14.4 Tests
New offscreen UI tests assert each analysis evidence panel is structured
and never leaks raw container reprs (`{'`, `': `, `None`). Full suite
**166/166 passing**; no existing test modified. Display-dependent
rendering remains offscreen-verified only (no EGL), and the real
2025 British GP cache re-renders with identical counts (452 battles,
1,465 events) under the new presentation.
