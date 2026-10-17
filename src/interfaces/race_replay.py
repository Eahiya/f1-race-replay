import os
import math
import time
import arcade
import numpy as np
from scipy.spatial import cKDTree
from src.f1_data import FPS
from src.ui_components import (
    LeaderboardComponent, 
    WeatherComponent, 
    LegendComponent, 
    DriverInfoComponent, 
    RaceProgressBarComponent,
    RaceControlsComponent,
    ControlsPopupComponent,
    SessionInfoComponent,
    RaceHudComponent,
    SectorSplitsComponent,
    Theme,
    draw_label,
    draw_panel,
    draw_pill,
    extract_race_events,
    build_track_from_example_lap,
    draw_finish_line
)
from src.tyre_degradation_integration import TyreDegradationIntegrator
from src.services.stream import TelemetryStreamServer


SCREEN_WIDTH = 1280
SCREEN_HEIGHT = 720
SCREEN_TITLE = "F1 Race Replay"
PLAYBACK_SPEEDS = [0.1, 0.2, 0.5, 1.0, 2.0, 4.0, 8.0, 16.0, 32.0, 64.0, 128.0, 256.0]

class F1RaceReplayWindow(arcade.Window):
    def __init__(self, frames, track_statuses, example_lap, drivers, title,
                 playback_speed=1.0, driver_colors=None, circuit_rotation=0.0,
                 left_ui_margin=340, right_ui_margin=260, total_laps=None, visible_hud=True,
                 session_info=None, session=None, enable_telemetry=False, sector_data=None):
        # Set resizable to True so the user can adjust mid-sim
        super().__init__(SCREEN_WIDTH, SCREEN_HEIGHT, title, resizable=True)
        self.maximize()

        self.telemetry_stream = None
        if enable_telemetry:
            try:
                self.telemetry_stream = TelemetryStreamServer()
                self.telemetry_stream.start()
                print("Telemetry stream server started on localhost:9999")
            except OSError as e:
                print(f"Failed to start telemetry server: {e}")
                print("Continuing without telemetry streaming...")
                self.telemetry_stream = None
            except Exception as e:
                print(f"Error starting telemetry server: {e}")
                self.telemetry_stream = None

        self.frames = frames
        self.track_statuses = track_statuses
        self.n_frames = len(frames)
        self.drivers = list(drivers)
        self.playback_speed = PLAYBACK_SPEEDS[PLAYBACK_SPEEDS.index(playback_speed)] if playback_speed in PLAYBACK_SPEEDS else 1.0
        self.driver_colors = driver_colors or {}
        self.frame_index = 0.0  # use float for fractional-frame accumulation
        self.paused = False
        self.total_laps = total_laps
        self.has_weather = any("weather" in frame for frame in frames) if frames else False
        self.visible_hud = visible_hud # If it displays HUD or not (leaderboard, controls, weather, etc)

        # Rotation (degrees) to apply to the whole circuit around its centre
        self.circuit_rotation = circuit_rotation
        self._rot_rad = float(np.deg2rad(self.circuit_rotation)) if self.circuit_rotation else 0.0
        self._cos_rot = float(np.cos(self._rot_rad))
        self._sin_rot = float(np.sin(self._rot_rad))
        self.finished_drivers = []
        self.left_ui_margin = left_ui_margin
        self.right_ui_margin = right_ui_margin
        self.toggle_drs_zones = True 
        self.show_driver_labels = False
        # UI components
        leaderboard_x = max(20, self.width - self.right_ui_margin + 12)
        self.leaderboard_comp = LeaderboardComponent(x=leaderboard_x, width=240, visible=visible_hud)
        self.hud_comp = RaceHudComponent(left=24, top_offset=24, visible=visible_hud)
        self.weather_comp = WeatherComponent(left=24, top_offset=176, visible=visible_hud)
        self.legend_comp = LegendComponent(x=max(12, self.left_ui_margin - 320), visible=visible_hud)
        self.driver_info_comp = DriverInfoComponent(left=20, width=300)
        self.controls_popup_comp = ControlsPopupComponent(lines=[
            ("SPACE", "Pause/Resume"),
            ("\u2190 / \u2192", "Jump back/forward"),
            ("\u2191 / \u2193", "Speed +/-"),
            ("1-4", "Set speed: 0.5x / 1x / 2x / 4x"),
            ("R", "Restart"),
            ("D", "Toggle DRS Zones"),
            ("L", "Toggle Driver Labels"),
            ("Click", "Chase selected driver (click again = free cam)"),
            ("C", "Toggle Battle Cam (auto closest fight)"),
            ("S", "Toggle Sector Splits"),
            ("T", "Toggle Interval Tags"),
            ("B", "Toggle Progress Bar"),
            ("I", "Toggle Session Banner"),
            ("H", "Toggle Help Popup"),
            ("ESC", "Close Window"),
        ])

        self.controls_popup_comp.set_size(470, 400) # width/height of the popup box
        self.controls_popup_comp.set_font_sizes(header_font_size=16, body_font_size=13) # adjust font sizes
        self.degradation_integrator = None
        if session is not None:
            try:
                print("Initializing tyre degradation model...")
                self.degradation_integrator = TyreDegradationIntegrator(session=session)
                
                # This computes curves once at startup (1-2 seconds)
                init_success = self.degradation_integrator.initialize_from_session()
                
                if init_success:
                    print("✓ Tyre degradation model initialized successfully")
                    # Link integrator to driver info component
                    self.driver_info_comp.degradation_integrator = self.degradation_integrator
                else:
                    print("✗ Tyre degradation model initialization failed")
                    self.degradation_integrator = None
            except Exception as e:
                print(f"✗ Tyre degradation initialization error: {e}")
                self.degradation_integrator = None
        else:
            print("Note: Session not provided, tyre degradation disabled")


        # Progress bar component with race event markers
        self.progress_bar_comp = RaceProgressBarComponent(
            left_margin=left_ui_margin,
            right_margin=right_ui_margin,
            bottom=30,
            height=24,
            marker_height=16
        )
        # The interactive scrubber is a headline control: show it by default
        self.progress_bar_comp.visible = visible_hud

        # --- Update 2: camera modes, interval tags, sector splits ---
        self.battle_cam = False            # C: auto-focus the closest on-track battle
        self.show_interval_tags = True     # T: +X.Xs chips between battling cars
        self.chase_zoom = 2.2              # zoom factor vs. the fitted overview
        self._cam_scale = None             # lerped camera state (None = snap)
        self._cam_tx = None
        self._cam_ty = None
        self._battle_info = None           # (ahead, trailer, gap_s) for the badge

        # Sector split payload (older cached telemetry simply lacks this key)
        self.sector_data = sector_data or {}
        self._sector_by_driver = self.sector_data.get("by_driver", {})
        self._sector_bests = self.sector_data.get("session_bests", {})
        self.sector_splits_comp = SectorSplitsComponent(
            left=20, width=264, top_offset=372,
            visible=visible_hud and bool(self._sector_by_driver)
        )

        # Shared text objects for per-frame overlays (no allocation in draw loops)
        self._interval_text = arcade.Text("", 0, 0, (255, 255, 255, 255), 10, bold=True,
                                          font_name=Theme.FONT,
                                          anchor_x="center", anchor_y="center")
        self._cam_badge_text = arcade.Text("", 0, 0, Theme.TEXT, 11, bold=True,
                                           font_name=Theme.FONT,
                                           anchor_x="center", anchor_y="center")

        # Race control buttons component
        self.race_controls_comp = RaceControlsComponent(
            center_x=self.width // 2,
            center_y=100,
            visible = visible_hud
        )
        self.race_controls_comp.on_resize(self)
        
        # Session info banner component
        self.session_info_comp = SessionInfoComponent(visible=visible_hud)
        self.circuit_length_m = session_info.get('circuit_length_m') if session_info else None
        if session_info:
            self.session_info_comp.set_info(
                event_name=session_info.get('event_name', ''),
                circuit_name=session_info.get('circuit_name', ''),
                country=session_info.get('country', ''),
                year=session_info.get('year'),
                round_num=session_info.get('round'),
                date=session_info.get('date', ''),
                total_laps=total_laps
            )

        self.is_rewinding = False
        self.is_forwarding = False
        self.was_paused_before_hold = False
        
        # Extract race events for the progress bar
        race_events = extract_race_events(frames, track_statuses, total_laps or 0)
        self.progress_bar_comp.set_race_data(
            total_frames=len(frames),
            total_laps=total_laps or 0,
            events=race_events
        )

        # Build track geometry (Raw World Coordinates)
        (self.plot_x_ref, self.plot_y_ref,
         self.x_inner, self.y_inner,
         self.x_outer, self.y_outer,
         self.x_min, self.x_max,
         self.y_min, self.y_max, self.drs_zones) = build_track_from_example_lap(example_lap)

        # Build a dense reference polyline (used for projecting car (x,y) -> along-track distance)
        ref_points = self._interpolate_points(self.plot_x_ref, self.plot_y_ref, interp_points=4000)
        # store as numpy arrays for vectorized ops
        self._ref_xs = np.array([p[0] for p in ref_points])
        self._ref_ys = np.array([p[1] for p in ref_points])

        # Calculate normals for the reference line
        dx = np.gradient(self._ref_xs)
        dy = np.gradient(self._ref_ys)
        norm = np.sqrt(dx**2 + dy**2)
        norm[norm == 0] = 1.0
        self._ref_nx = -dy / norm
        self._ref_ny = dx / norm

        # Build KD-Tree for fast closest-point lookup
        self.track_tree = cKDTree(np.column_stack((self._ref_xs, self._ref_ys)))

        # Determine track winding using the shoelace formula to ensure normals point outwards.
        # A positive area indicates counter-clockwise winding (normals point Left=Inside, so we flip).
        # A negative area indicates clockwise winding (normals point Left=Outside, so we keep).
        signed_area = np.sum(self._ref_xs[:-1] * self._ref_ys[1:] - self._ref_xs[1:] * self._ref_ys[:-1])
        signed_area += (self._ref_xs[-1] * self._ref_ys[0] - self._ref_xs[0] * self._ref_ys[-1])
        if signed_area > 0:
            self._ref_nx = -self._ref_nx
            self._ref_ny = -self._ref_ny

        # cumulative distances along the reference polyline (metres)
        diffs = np.sqrt(np.diff(self._ref_xs)**2 + np.diff(self._ref_ys)**2)
        self._ref_seg_len = diffs
        self._ref_cumdist = np.concatenate(([0.0], np.cumsum(diffs)))
        self._ref_total_length = float(self._ref_cumdist[-1]) if len(self._ref_cumdist) > 0 else 0.0

        # Pre-calculate interpolated world points ONCE (optimization)
        # 1400 samples keeps the rendered ribbon smooth (~2px per segment)
        # while halving the per-frame shape expansion cost.
        self.world_inner_points = self._interpolate_points(self.x_inner, self.y_inner, interp_points=1400)
        self.world_outer_points = self._interpolate_points(self.x_outer, self.y_outer, interp_points=1400)

        # These will hold the actual screen coordinates to draw
        self.screen_inner_points = None
        self.screen_outer_points = None

        # Track geometry baked in world space so the camera is just scale+translate
        self._world_inner_arr = None
        self._world_outer_arr = None
        self.track_center_points = []      # world-space centre-line tuples
        self.track_width_world = 12.0      # ribbon width in world units
        self.track_drs_strips = []         # world-space DRS stripe polylines
        self._centers_screen = np.zeros((0, 2))
        self._track_width_px_now = 12.0
        self._drs_strips_screen = []
        self._bake_world_geometry()
        self._rebuild_track_render_data()
        
        # Scaling parameters (initialized to 0, calculated in update_scaling)
        self.world_scale = 1.0
        self.tx = 0
        self.ty = 0

        # Load Background
        bg_path = os.path.join("resources", "background.png")
        self.bg_texture = arcade.load_texture(bg_path) if os.path.exists(bg_path) else None

        arcade.set_background_color(arcade.color.BLACK)

        # Trigger initial scaling calculation
        self.update_scaling(self.width, self.height)

        # Camera starts at the fitted overview (snaps, no lerp on first frame)
        self._cam_scale = self.world_scale
        self._cam_tx = self.tx
        self._cam_ty = self.ty

        # Selection & hit-testing state for leaderboard
        self.selected_driver = None
        self.leaderboard_rects = []  # list of tuples: (code, left, bottom, right, top)
        # store previous leaderboard order for up/down arrows
        self.last_leaderboard_order = None
        # cached driver-code labels for the on-track chips
        self._code_label_cache = {}
        # cached safety-car labels (avoid per-frame text allocation)
        self._sc_label = arcade.Text("SC", 0, 0, (255, 255, 255, 255), 11, bold=True,
                                     font_name=Theme.FONT, anchor_x="left", anchor_y="center")
        self._sc_deploy = arcade.Text("SC DEPLOYING", 0, 0, (255, 200, 0, 255), 9, bold=True,
                                      font_name=Theme.FONT, anchor_x="center", anchor_y="top")
        self._sc_return = arcade.Text("SC IN", 0, 0, (255, 200, 0, 255), 9, bold=True,
                                      font_name=Theme.FONT, anchor_x="center", anchor_y="top")
        
        # Cached per-session broadcast envelope (driver colours never change
        # mid-session, so pre-format them once instead of per packet).
        self._hex_driver_colors = {
            code: "#{:02X}{:02X}{:02X}".format(*rgb[:3])
            for code, rgb in (self.driver_colors or {}).items()
        }
        # Telemetry broadcast throttle: the stream targets 25 FPS, so never
        # emit more often than one packet per 40 ms from the render loop.
        self._last_broadcast_monotonic = 0.0
        self._broadcast_min_interval = 1.0 / FPS
        # Track-status index cache: statuses are sequential in time, so the
        # per-frame lookup resumes from the last index (resets on seek).
        self._status_idx = 0
        self._status_idx_t = -1.0

        # Broadcast initial telemetry state
        self._broadcast_telemetry_state(force=True)

    def _code_label(self, code, color):
        """Return a cached arcade.Text for an on-track driver code chip."""
        label = self._code_label_cache.get(code)
        if label is None:
            label = arcade.Text(code, 0, 0, color, 10, bold=True,
                                font_name=Theme.FONT)
            self._code_label_cache[code] = label
        return label

    def _track_status_at(self, t):
        """Return the track-status code active at time ``t``.

        Statuses are sequential in time, so this resumes from the cached
        index instead of scanning from zero every frame; the cache resets
        when time moves backwards (timeline seek).
        """
        statuses = self.track_statuses
        if not statuses:
            return "GREEN"
        if t < self._status_idx_t:
            self._status_idx = 0
        idx = self._status_idx
        # Clamp in case the status list changed underneath us.
        idx = max(0, min(idx, len(statuses) - 1))
        while idx + 1 < len(statuses) and statuses[idx + 1]["start_time"] <= t:
            idx += 1
        while idx > 0 and statuses[idx]["start_time"] > t:
            idx -= 1
        self._status_idx = idx
        self._status_idx_t = t
        return statuses[idx].get("status", "GREEN")

    def _broadcast_telemetry_state(self, force=False):
        """Broadcast current telemetry state to connected clients.

        Throttled to the 25 FPS stream cadence so high playback speeds (or
        60 Hz update ticks) cannot flood the socket; ``force=True`` bypasses
        the throttle for discrete events (pause, speed change, seek).
        """
        if not hasattr(self, 'telemetry_stream') or not self.telemetry_stream:
            return
        if not force:
            now = time.monotonic()
            if now - self._last_broadcast_monotonic < self._broadcast_min_interval:
                return
            self._last_broadcast_monotonic = now

        current_frame = self.frames[min(int(self.frame_index), len(self.frames) - 1)] if self.frames else None

        # Get current track status (indexed lookup, no per-frame scan).
        current_track_status = "GREEN"
        if current_frame:
            current_track_status = self._track_status_at(current_frame["t"])

        # Leader info comes straight from the stored per-frame positions
        # (computed once during preprocessing) - no KD-tree projection.
        leader_code = ""
        leader_lap = 1
        if current_frame and "drivers" in current_frame:
            for code, pos in current_frame["drivers"].items():
                if pos.get("position", 99) == 1:
                    leader_code = code
                    leader_lap = pos.get("lap", 1)
                    break
            else:
                # Fallback: largest race distance (same metric as preprocessing).
                best = None
                for code, pos in current_frame["drivers"].items():
                    key = (pos.get("lap", 0), pos.get("dist", 0.0))
                    if best is None or key > best[0]:
                        best = (key, code)
                if best is not None:
                    leader_code = best[1]
                    leader_lap = current_frame["drivers"][leader_code].get("lap", 1)

        # Format time
        t = current_frame["t"] if current_frame else 0
        hours = int(t // 3600)
        minutes = int((t % 3600) // 60)
        seconds = int(t % 60)
        time_str = f"{hours:02}:{minutes:02}:{seconds:02}"

        self.telemetry_stream.broadcast({
            "frame_index": int(self.frame_index),
            "frame": current_frame,
            "track_status": current_track_status,
            "playback_speed": self.playback_speed,
            "is_paused": self.paused,
            "total_frames": self.n_frames,
            "circuit_length_m": self.circuit_length_m,
            "driver_colors": self._hex_driver_colors,
            "session_data": {
                "time": time_str,
                "lap": leader_lap,
                "leader": leader_code,
                "total_laps": self.total_laps
            }
        })

    def _interpolate_points(self, xs, ys, interp_points=2000):
        t_old = np.linspace(0, 1, len(xs))
        t_new = np.linspace(0, 1, interp_points)
        xs_i = np.interp(t_new, t_old, xs)
        ys_i = np.interp(t_new, t_old, ys)
        return list(zip(xs_i, ys_i))

    def _project_to_reference(self, x, y):
        if self._ref_total_length == 0.0:
            return 0.0

        # Vectorized nearest-point lookup using KD-Tree (O(log N))
        _, idx = self.track_tree.query([x, y])
        idx = int(idx)

        # For a slightly better estimate, optionally project onto the adjacent segment
        if idx < len(self._ref_xs) - 1:

            x1, y1 = self._ref_xs[idx], self._ref_ys[idx]
            x2, y2 = self._ref_xs[idx+1], self._ref_ys[idx+1]
            vx, vy = x2 - x1, y2 - y1
            seg_len2 = vx*vx + vy*vy
            if seg_len2 > 0:
                t = ((x - x1) * vx + (y - y1) * vy) / seg_len2
                t_clamped = max(0.0, min(1.0, t))
                proj_x = x1 + t_clamped * vx
                proj_y = y1 + t_clamped * vy
                # distance along segment from x1,y1
                seg_dist = np.sqrt((proj_x - x1)**2 + (proj_y - y1)**2)
                return float(self._ref_cumdist[idx] + seg_dist)

        # Fallback: return the cumulative distance at the closest dense sample
        return float(self._ref_cumdist[idx])

    def update_scaling(self, screen_w, screen_h):
        """
        Recalculates the scale and translation to fit the track 
        perfectly within the new screen dimensions while maintaining aspect ratio.
        """
        padding = 0.05
        # If a rotation is applied, we must compute the rotated bounds
        world_cx = (self.x_min + self.x_max) / 2
        world_cy = (self.y_min + self.y_max) / 2

        def _rotate_about_center(x, y):
            # Translate to centre, rotate, translate back
            tx = x - world_cx
            ty = y - world_cy
            rx = tx * self._cos_rot - ty * self._sin_rot
            ry = tx * self._sin_rot + ty * self._cos_rot
            return rx + world_cx, ry + world_cy

        # Build rotated extents from inner/outer world points
        rotated_points = []
        for x, y in self.world_inner_points:
            rotated_points.append(_rotate_about_center(x, y))
        for x, y in self.world_outer_points:
            rotated_points.append(_rotate_about_center(x, y))

        xs = [p[0] for p in rotated_points]
        ys = [p[1] for p in rotated_points]
        world_x_min = min(xs) if xs else self.x_min
        world_x_max = max(xs) if xs else self.x_max
        world_y_min = min(ys) if ys else self.y_min
        world_y_max = max(ys) if ys else self.y_max

        world_w = max(1.0, world_x_max - world_x_min)
        world_h = max(1.0, world_y_max - world_y_min)
        
        # Reserve left/right UI margins before applying padding so the track
        # never overlaps side UI elements (leaderboard, telemetry, legends).
        inner_w = max(1.0, screen_w - self.left_ui_margin - self.right_ui_margin)
        usable_w = inner_w * (1 - 2 * padding)
        usable_h = screen_h * (1 - 2 * padding)

        # Calculate scale to fit whichever dimension is the limiting factor
        scale_x = usable_w / world_w
        scale_y = usable_h / world_h
        self.world_scale = min(scale_x, scale_y)

        # Center the world in the screen (rotation done about original centre)
        # world_cx/world_cy are unchanged by rotation about centre
        # Center within the available inner area (left_ui_margin .. screen_w - right_ui_margin)
        screen_cx = self.left_ui_margin + inner_w / 2
        screen_cy = screen_h / 2

        self.tx = screen_cx - self.world_scale * world_cx
        self.ty = screen_cy - self.world_scale * world_cy

        # Remember the fitted transform as the free-camera baseline
        self._base_scale = self.world_scale
        self._base_tx = self.tx
        self._base_ty = self.ty

        # Project world geometry through the (possibly camera-modified) transform
        self._apply_world_transform()

    def _rebuild_track_render_data(self):
        """Pre-compute the WORLD-space data the track renderer needs.

        Runs once at start-up (the world geometry never changes), so the
        per-frame cost of drawing the circuit is a single numpy projection:
          * centre line tuples -> white edge band + asphalt surface strips
          * ribbon width in world units (scaled to pixels every frame)
          * DRS stripes, inset from the outer track edge
        """
        inner = self._world_inner_arr
        outer = self._world_outer_arr
        n = min(len(inner), len(outer))

        if n < 2:
            self.track_center_points = []
            self.track_width_world = 12.0
            self.track_drs_strips = []
            self._track_centers_arr = np.zeros((0, 2), dtype=np.float64)
            self._drs_strips_arr = []
            self._drs_strips_screen = []
            return

        centers = []
        across = []
        width_total = 0.0
        for i in range(n):
            ix, iy = inner[i]
            ox, oy = outer[i]
            dx = ox - ix
            dy = oy - iy
            dist = math.hypot(dx, dy)
            centers.append(((ix + ox) * 0.5, (iy + oy) * 0.5))
            width_total += dist
            if dist > 1e-6:
                across.append((dx / dist, dy / dist))
            else:
                across.append((0.0, 0.0))

        width_world = width_total / n
        if width_world <= 0.0:
            width_world = 12.0
        self.track_center_points = centers
        self.track_width_world = width_world
        # Baked float arrays + reusable screen-space buffers so the
        # per-frame transform performs no allocation beyond the draw lists.
        self._track_centers_arr = np.asarray(centers, dtype=np.float64)
        self._centers_screen = np.empty_like(self._track_centers_arr)

        # --- DRS stripes -------------------------------------------------
        # Zone indices refer to the raw example-lap samples while the rendered
        # ribbons use the interpolated arrays, so map one index space onto the other.
        strips = []
        raw_n = len(self.plot_x_ref)
        dense_n = len(self._world_inner_arr)
        if raw_n > 1 and dense_n > 1 and self.drs_zones:
            offset = width_world * 0.21
            for zone in self.drs_zones:
                start = int(zone["start"]["index"])
                end = int(zone["end"]["index"])
                if end <= start:
                    continue
                i0 = int(round(start * (dense_n - 1) / (raw_n - 1)))
                i1 = int(round(end * (dense_n - 1) / (raw_n - 1)))
                i0 = max(0, min(i0, n - 1))
                i1 = max(0, min(i1, n - 1))
                if i1 - i0 < 2:
                    continue
                strip = []
                for i in range(i0, i1 + 1):
                    cx, cy = centers[i]
                    ax, ay = across[i]
                    strip.append((cx + ax * offset, cy + ay * offset))
                strips.append(strip)
        self.track_drs_strips = strips
        # Baked DRS arrays with matching reusable screen buffers.
        self._drs_strips_arr = [np.asarray(strip, dtype=np.float64)
                                for strip in strips if len(strip) > 1]
        self._drs_strips_screen = [np.empty_like(arr)
                                   for arr in self._drs_strips_arr]

    def _bake_world_geometry(self):
        """Pre-rotate the inner/outer boundary polylines into numpy arrays.

        Done once at start-up so every later camera move is just
        scale + translate on top of baked world coordinates.
        """
        def _rotate_arr(pts):
            arr = np.asarray(pts, dtype=np.float64)
            if self._rot_rad:
                cx = (self.x_min + self.x_max) / 2
                cy = (self.y_min + self.y_max) / 2
                px = arr[:, 0] - cx
                py = arr[:, 1] - cy
                rx = px * self._cos_rot - py * self._sin_rot + cx
                ry = px * self._sin_rot + py * self._cos_rot + cy
                arr = np.column_stack((rx, ry))
            return arr

        self._world_inner_arr = _rotate_arr(self.world_inner_points)
        self._world_outer_arr = _rotate_arr(self.world_outer_points)

    def _apply_world_transform(self):
        """Project baked world geometry to screen using the current camera.

        Writes into preallocated buffers (no per-frame array allocation);
        only the small lists handed to Arcade are rebuilt each frame.
        """
        s = self.world_scale
        tx = self.tx
        ty = self.ty

        if self._world_inner_arr is not None:
            # Boundary polylines are only used by the fallback path; keep
            # them as cheap vectorised expressions (drawn rarely).
            self.screen_inner_points = self._world_inner_arr * s + (tx, ty)
            self.screen_outer_points = self._world_outer_arr * s + (tx, ty)

        centers = getattr(self, "_track_centers_arr", None)
        if centers is not None and len(centers) > 1:
            buf = self._centers_screen
            if buf.shape != centers.shape:
                buf = np.empty_like(centers)
                self._centers_screen = buf
            np.multiply(centers, s, out=buf)
            buf[:, 0] += tx
            buf[:, 1] += ty
        elif getattr(self, "_centers_screen", None) is None:
            self._centers_screen = np.zeros((0, 2))

        self._track_width_px_now = max(6.0, self.track_width_world * s)
        strips = getattr(self, "_drs_strips_arr", None)
        if strips:
            for src, dst in zip(strips, self._drs_strips_screen):
                np.multiply(src, s, out=dst)
                dst[:, 0] += tx
                dst[:, 1] += ty

    def _viewport_center(self):
        """Centre of the track viewport (between the side UI margins)."""
        inner_w = max(1.0, self.width - self.left_ui_margin - self.right_ui_margin)
        return self.left_ui_margin + inner_w / 2, self.height / 2

    def _closest_battle(self, frame):
        """Return (ahead_code, trailer_code, gap_s) for the closest same-lap
        battle on track, or None. Uses frame positions + speeds only."""
        cars = sorted(frame["drivers"].items(),
                      key=lambda kv: (kv[1].get("lap", 0), kv[1].get("dist", 0.0)),
                      reverse=True)
        best = None
        for (code_a, info_a), (code_t, info_t) in zip(cars, cars[1:]):
            if info_a.get("lap") != info_t.get("lap"):
                continue  # lapped pair, not a battle
            gap_m = (info_a.get("dist", 0.0) or 0.0) - (info_t.get("dist", 0.0) or 0.0)
            if gap_m <= 0.0 or gap_m > 400.0:
                continue
            speed_ms = max((info_t.get("speed", 200.0) or 200.0) / 3.6, 20.0)
            gap_s = gap_m / speed_ms
            if best is None or gap_s < best[2]:
                best = (code_a, code_t, gap_s)
        return best

    def _camera_target_for(self, wx, wy):
        """(scale, tx, ty) that centres the rotated world point (wx, wy)."""
        scale = self._base_scale * self.chase_zoom
        cx = (self.x_min + self.x_max) / 2
        cy = (self.y_min + self.y_max) / 2
        if self._rot_rad:
            px, py = wx - cx, wy - cy
            rx = px * self._cos_rot - py * self._sin_rot + cx
            ry = px * self._sin_rot + py * self._cos_rot + cy
        else:
            rx, ry = wx, wy
        ccx, ccy = self._viewport_center()
        return scale, ccx - scale * rx, ccy - scale * ry

    def _update_camera(self, delta_time: float):
        """Lerp the camera toward the active target (battle / chase / free)."""
        frame = self.frames[min(int(self.frame_index), self.n_frames - 1)]
        sel = getattr(self, "selected_drivers", []) or []
        chase_code = sel[0] if len(sel) == 1 else None

        target = None
        self._battle_info = None

        if self.battle_cam:
            battle = self._closest_battle(frame)
            if battle:
                code_a, code_t, gap_s = battle
                self._battle_info = battle
                info_a = frame["drivers"][code_a]
                info_t = frame["drivers"][code_t]
                target = self._camera_target_for(
                    (info_a["x"] + info_t["x"]) * 0.5,
                    (info_a["y"] + info_t["y"]) * 0.5)
        if target is None and chase_code is not None and chase_code in frame["drivers"]:
            info = frame["drivers"][chase_code]
            target = self._camera_target_for(info["x"], info["y"])
        if target is None:
            target = (self._base_scale, self._base_tx, self._base_ty)

        if self._cam_scale is None:
            self._cam_scale, self._cam_tx, self._cam_ty = target
        else:
            k = min(1.0, delta_time * 4.5)
            self._cam_scale += (target[0] - self._cam_scale) * k
            self._cam_tx += (target[1] - self._cam_tx) * k
            self._cam_ty += (target[2] - self._cam_ty) * k

        self.world_scale = self._cam_scale
        self.tx = self._cam_tx
        self.ty = self._cam_ty

    def _draw_interval_tags(self, frame):
        """Broadcast-style +X.Xs chips between cars battling within 1.0s.

        Uses the raw timing distance (same metric as the battle cam) rather
        than KD-projected progress, which distorts gaps near the lap seam.
        """
        cars = sorted(frame["drivers"].items(),
                      key=lambda kv: (kv[1].get("lap", 0), kv[1].get("dist", 0.0)),
                      reverse=True)
        drawn = 0
        for (code_a, info_a), (code_t, info_t) in zip(cars, cars[1:]):
            if drawn >= 6:
                break
            if info_a.get("lap") != info_t.get("lap"):
                continue  # lapped pair, not a battle
            gap_m = (info_a.get("dist", 0.0) or 0.0) - (info_t.get("dist", 0.0) or 0.0)
            if gap_m <= 0.0 or gap_m > 400.0:
                continue
            speed_ms = max((info_t.get("speed", 200.0) or 200.0) / 3.6, 20.0)
            gap_s = gap_m / speed_ms
            if gap_s >= 1.0:
                continue
            ax, ay = self.world_to_screen(info_a["x"], info_a["y"])
            tx_, ty_ = self.world_to_screen(info_t["x"], info_t["y"])
            mx, my = (ax + tx_) * 0.5, (ay + ty_) * 0.5

            self._interval_text.value = f"+{gap_s:.2f}s"
            base_color = self.driver_colors.get(code_t, arcade.color.WHITE)
            self._interval_text.color = (*tuple(base_color)[:3], 255)
            tw = (self._interval_text.content_width or 32) + 12
            draw_pill(mx, my, tw, 16, (14, 16, 20, 220), border=Theme.CHIP_BORDER)
            self._interval_text.x = mx
            self._interval_text.y = my + 1
            self._interval_text.draw()
            drawn += 1

    def _draw_camera_badge(self, frame):
        """Top-centre badge showing the active camera mode (none in free cam)."""
        sel = getattr(self, "selected_drivers", []) or []
        chase_code = sel[0] if len(sel) == 1 and sel[0] in frame["drivers"] else None
        if self.battle_cam and self._battle_info:
            code_a, code_t, gap_s = self._battle_info
            text = f"BATTLE CAM \u00b7 {code_a} vs {code_t}  +{gap_s:.2f}s"
        elif self.battle_cam:
            text = "BATTLE CAM \u00b7 SEARCHING"
        elif chase_code:
            text = f"CHASE CAM \u00b7 {chase_code}"
        else:
            return

        self._cam_badge_text.value = text
        bw = (self._cam_badge_text.content_width or 140) + 28
        bx = self.width / 2
        by = self.height - 76
        draw_pill(bx, by, bw, 24, (14, 16, 20, 230), border=Theme.CHIP_BORDER)
        self._cam_badge_text.x = bx
        self._cam_badge_text.y = by + 1
        self._cam_badge_text.draw()

    def _draw_track(self, surface_color):
        """Render the circuit: white edge band, asphalt surface, DRS stripes.

        World geometry is projected through the live camera transform as a
        single numpy scale+translate, so edges stay pixel-crisp while zoomed.
        """
        points = self._centers_screen
        if len(points) < 2:
            # Fallback: boundaries only (geometry not built yet)
            if self.screen_inner_points is not None and len(self.screen_inner_points) > 1:
                arcade.draw_line_strip(self.screen_inner_points.tolist(), Theme.TRACK_EDGE, 3)
            if self.screen_outer_points is not None and len(self.screen_outer_points) > 1:
                arcade.draw_line_strip(self.screen_outer_points.tolist(), Theme.TRACK_EDGE, 3)
            return

        width = self._track_width_px_now
        # white edge line thickness scales with the ribbon (clamped for readability)
        edge = max(2.0, min(6.0, width * 0.22))
        # 1. White outline band (slightly wider than the ribbon)
        arcade.draw_line_strip(points.tolist(), Theme.TRACK_EDGE, width + edge * 2)
        # 2. Asphalt surface, tinted by the current track status
        arcade.draw_line_strip(points.tolist(), surface_color, width)
        # 3. DRS activation stripes along the outer half of the track
        if self.toggle_drs_zones and self._drs_strips_screen:
            stripe_width = max(3.0, width * 0.34)
            for strip in self._drs_strips_screen:
                arcade.draw_line_strip(strip.tolist(), Theme.DRS_STRIPE, stripe_width)

    def on_resize(self, width, height):
        """Called automatically by Arcade when window is resized."""
        super().on_resize(width, height)
        self.update_scaling(width, height)
        # notify components
        self.leaderboard_comp.x = max(20, self.width - self.right_ui_margin + 12)
        for c in (self.leaderboard_comp, self.weather_comp, self.legend_comp,
                  self.driver_info_comp, self.progress_bar_comp, self.race_controls_comp):
            c.on_resize(self)

    def world_to_screen(self, x, y):
        # Rotate around the track centre (if rotation is set), then scale+translate
        world_cx = (self.x_min + self.x_max) / 2
        world_cy = (self.y_min + self.y_max) / 2

        if self._rot_rad:
            tx = x - world_cx
            ty = y - world_cy
            rx = tx * self._cos_rot - ty * self._sin_rot
            ry = tx * self._sin_rot + ty * self._cos_rot
            x, y = rx + world_cx, ry + world_cy

        sx = self.world_scale * x + self.tx
        sy = self.world_scale * y + self.ty
        return sx, sy

    def _format_wind_direction(self, degrees):
        if degrees is None:
            return "N/A"
        deg_norm = degrees % 360
        dirs = [
            "N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
            "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW",
        ]
        idx = int((deg_norm / 22.5) + 0.5) % len(dirs)
        return dirs[idx]

    def on_draw(self):
        self.clear()

        # Project world geometry through the live camera transform
        self._apply_world_transform()

        # 1. Draw Background (stretched to fit new window size)
        if self.bg_texture:
            arcade.draw_lrbt_rectangle_textured(
                left=0, right=self.width,
                bottom=0, top=self.height,
                texture=self.bg_texture
            )

        # 2. Draw Track (using pre-calculated screen points)
        idx = min(int(self.frame_index), self.n_frames - 1)
        frame = self.frames[idx]
        current_time = frame["t"]
        current_track_status = self._track_status_at(current_time)

        # Map track status -> surface colour (the ribbon is tinted by flags)
        STATUS_COLORS = {
            "GREEN": Theme.TRACK_GREEN,
            "YELLOW": Theme.TRACK_YELLOW,
            "RED": Theme.TRACK_RED,
            "VSC": Theme.TRACK_VSC,
            "SC": Theme.TRACK_SC,
        }
        track_color = STATUS_COLORS["GREEN"]

        if current_track_status == "2":
            track_color = STATUS_COLORS["YELLOW"]
        elif current_track_status == "4":
            track_color = STATUS_COLORS["SC"]
        elif current_track_status == "5":
            track_color = STATUS_COLORS["RED"]
        elif current_track_status == "6" or current_track_status == "7":
            track_color = STATUS_COLORS["VSC"]

        # 2. Draw the circuit (white edges + asphalt ribbon + DRS stripes)
        self._draw_track(track_color)

        # 2.5 Screen-edge flag glow so cautions read instantly
        if current_track_status in ("2", "4", "5", "6", "7"):
            glow = {
                "2": Theme.AMBER,
                "4": (255, 140, 0, 255),
                "5": Theme.RED,
                "6": (255, 165, 0, 255),
                "7": (255, 165, 0, 255),
            }[current_track_status]
            arcade.draw_rect_outline(
                arcade.XYWH(self.width / 2, self.height / 2, self.width - 8, self.height - 8),
                (*glow[:3], 200), 5
            )

        draw_finish_line(self)

        # 2.6 Driver race progress — reuse the stored per-frame positions.
        # ``dist`` is within-lap distance in metres (precomputed during
        # telemetry extraction), so ordering by (lap, dist) matches the
        # KD-tree projection without 20 tree queries per frame.
        driver_progress = {}
        for code, pos in frame["drivers"].items():
            try:
                lap = int(pos.get("lap", 1))
            except Exception:
                lap = 1
            try:
                dist = float(pos.get("dist", 0.0))
            except Exception:
                dist = 0.0
            # Cumulative progress: lap * track_length + within-lap dist.
            # Single float sorts correctly and supports gap math via subtraction.
            driver_progress[code] = float((max(lap, 1) - 1) * self._ref_total_length + dist)

        # Leader is the one with greatest cumulative progress.
        if driver_progress:
            leader_code = max(driver_progress, key=lambda c: driver_progress[c])
            leader_lap = frame["drivers"][leader_code].get("lap", 1)
        else:
            leader_code = None
            leader_lap = 1

        # 3. Draw Cars
        frame = self.frames[idx]
        
        # Get selected drivers list safely
        selected_drivers = getattr(self, "selected_drivers", [])
        if not selected_drivers and getattr(self, "selected_driver", None):
            selected_drivers = [self.selected_driver]

        for i, (code, pos) in enumerate(frame["drivers"].items()):
            sx, sy = self.world_to_screen(pos["x"], pos["y"])
            color = self.driver_colors.get(code, arcade.color.WHITE)

            is_selected = code in selected_drivers

            if self.show_driver_labels or is_selected:
                # Find closest point index on reference track (Optimized KD-Tree)
                _, idx = self.track_tree.query([pos["x"], pos["y"]])
                idx = int(idx)

                # Get normal vector in world space
                nx = self._ref_nx[idx]
                ny = self._ref_ny[idx]

                # Rotate normal to screen space
                if self._rot_rad:
                    snx = nx * self._cos_rot - ny * self._sin_rot
                    sny = nx * self._sin_rot + ny * self._cos_rot
                else:
                    snx, sny = nx, ny

                offset_dist = 45 if i % 2 == 0 else 75

                lx = sx + snx * offset_dist
                ly = sy + sny * offset_dist

                arcade.draw_line(sx, sy, lx, ly, color, 1)

                # Driver code chip (cached label + pill backdrop)
                label = self._code_label(code, color)
                text_w = (label.content_width or 24) + 12
                chip_cx = lx + (text_w / 2 + 4 if snx >= 0 else -(text_w / 2 + 4))
                draw_pill(chip_cx, ly, text_w, 16, (14, 16, 20, 230),
                          border=Theme.CHIP_BORDER if not is_selected else Theme.TEXT)
                label.x = chip_cx
                label.y = ly + 1
                label.anchor_x = "center"
                label.anchor_y = "center"
                label.draw()

            # Car marker: dark halo keeps packed cars readable on the asphalt
            arcade.draw_circle_filled(sx, sy, 8, (10, 12, 15, 190))
            arcade.draw_circle_filled(sx, sy, 6, color)
            if is_selected:
                arcade.draw_circle_outline(sx, sy, 9.5, arcade.color.WHITE, 1.5)
        
        # 3b. Draw Safety Car (if active)
        sc_data = frame.get("safety_car")
        if sc_data is not None:
            sc_x = sc_data["x"]
            sc_y = sc_data["y"]
            sc_phase = sc_data.get("phase", "on_track")
            sc_alpha = sc_data.get("alpha", 1.0)
            
            sc_sx, sc_sy = self.world_to_screen(sc_x, sc_y)
            
            # Safety car color: bright orange/amber
            sc_base_color = (255, 165, 0)  # Orange
            
            # Calculate alpha for the car body
            body_alpha = int(255 * max(0.1, sc_alpha))
            sc_color_with_alpha = (*sc_base_color, body_alpha)
            
            # Pulsing glow effect during deploying/returning phases
            if sc_phase in ("deploying", "returning"):
                pulse = 0.5 + 0.5 * np.sin(time.time() * 8.0)  # Fast pulse
                glow_radius = 16 + pulse * 6
                glow_alpha = int(80 * sc_alpha * pulse)
                
                # Outer glow ring
                arcade.draw_circle_filled(sc_sx, sc_sy, glow_radius, (255, 200, 0, glow_alpha))
                arcade.draw_circle_outline(sc_sx, sc_sy, glow_radius + 2, (255, 100, 0, int(glow_alpha * 0.6)), 2)
                
                # Draw dashed trail line from pit to track position
                trail_alpha = int(120 * sc_alpha)
                trail_color = (255, 165, 0, trail_alpha)
                arcade.draw_circle_outline(sc_sx, sc_sy, 12, trail_color, 2)
            else:
                # Steady glow when on track
                arcade.draw_circle_filled(sc_sx, sc_sy, 14, (255, 165, 0, 40))
            
            # Draw SC body (larger than regular cars)
            arcade.draw_circle_filled(sc_sx, sc_sy, 8, sc_color_with_alpha)
            
            # Orange outline ring
            outline_alpha = int(255 * sc_alpha)
            arcade.draw_circle_outline(sc_sx, sc_sy, 9, (255, 100, 0, outline_alpha), 2)
            
            # "SC" label - always visible
            label_alpha = int(255 * max(0.3, sc_alpha))
            self._sc_label.color = (255, 255, 255, label_alpha)
            self._sc_label.x = sc_sx + 14
            self._sc_label.y = sc_sy + 2
            self._sc_label.draw()

            # Phase indicator text during transitions
            phase_label = None
            if sc_phase == "deploying":
                phase_label = self._sc_deploy
            elif sc_phase == "returning":
                phase_label = self._sc_return
            if phase_label is not None:
                phase_label.color = (255, 200, 0, int(200 * sc_alpha))
                phase_label.x = sc_sx
                phase_label.y = sc_sy - 16
                phase_label.draw()
        
        # 3c. On-track interval tags (+X.Xs chips for battles under 1.0s)
        if self.show_interval_tags:
            self._draw_interval_tags(frame)

        # --- UI ELEMENTS (Dynamic Positioning) ---
        
        # (driver_progress and leader were computed above the car loop)

        # Time Calculation
        t = frame["t"]
        hours = int(t // 3600)
        minutes = int((t % 3600) // 60)
        seconds = int(t % 60)
        time_str = f"{hours:02}:{minutes:02}:{seconds:02}"

        # Format Lap String (the HUD card already carries the "LAP" label)
        lap_str = f"{leader_lap}"
        if self.total_laps is not None:
            lap_str += f"/{self.total_laps}"

        # Draw HUD - Top Left (lap, clock, speed pill, flag badge)
        flag_text, flag_color = "", Theme.AMBER
        if current_track_status == "2":
            flag_text, flag_color = "YELLOW FLAG", Theme.AMBER
        elif current_track_status == "5":
            flag_text, flag_color = "RED FLAG", Theme.RED
        elif current_track_status == "6":
            flag_text, flag_color = "VIRTUAL SAFETY CAR", (255, 165, 0, 255)
        elif current_track_status == "4":
            flag_text, flag_color = "SAFETY CAR", (255, 140, 0, 255)

        self.hud_comp.set_data(lap_str, time_str, self.playback_speed,
                               flag_text=flag_text, flag_color=flag_color)
        self.hud_comp.draw(self)

        # Weather component (set info then draw)
        weather_info = frame.get("weather") if frame else None
        self.weather_comp.set_info(weather_info)
        self.weather_comp.draw(self)

        # Sector splits panel (chased driver, else race leader)
        if self._sector_by_driver and self.sector_splits_comp.visible:
            sel_now = getattr(self, "selected_drivers", []) or []
            sector_code = None
            if len(sel_now) == 1 and sel_now[0] in self._sector_by_driver:
                sector_code = sel_now[0]
            elif leader_code in self._sector_by_driver:
                sector_code = leader_code
            if sector_code:
                self.sector_splits_comp.set_driver(sector_code,
                                                   self._sector_by_driver.get(sector_code),
                                                   self._sector_bests)
            self.sector_splits_comp.draw(self)

        # Draw leaderboard via component
        driver_list = []
        for code, pos in frame["drivers"].items():
            color = self.driver_colors.get(code, arcade.color.WHITE)
            try:
                _fallback = (int(pos.get("lap", 1)), float(pos.get("dist", 0.0)))
            except Exception:
                _fallback = (1, 0.0)
            progress_m = driver_progress.get(code, _fallback)
            driver_list.append((code, color, pos, progress_m))
        driver_list.sort(key=lambda x: x[3], reverse=True)

        self.last_leaderboard_order = [c for c, _, _, _ in driver_list]
        self.leaderboard_comp.set_entries(driver_list)
        self.leaderboard_comp.draw(self)
        # expose rects for existing hit test compatibility if needed
        self.leaderboard_rects = self.leaderboard_comp.rects

        # Controls Legend - Bottom Left (keeps small offset from left UI edge)
        self.legend_comp.draw(self)
        
        # Selected driver info component
        self.driver_info_comp.draw(self)
        
        # Race Progress Bar with event markers (DNF, flags, leader changes)
        self.progress_bar_comp.draw(self)
        
        # Race playback control buttons
        self.race_controls_comp.draw(self)
        
        # Session info banner (top of screen)
        self.session_info_comp.draw(self)

        # Camera-mode badge (top centre, below the banner; hidden in free cam)
        self._draw_camera_badge(frame)

        # Draw Controls popup box
        self.controls_popup_comp.draw(self)
        
        # Draw tooltips and overlays on top of everything
        self.progress_bar_comp.draw_overlays(self)
                    
    def on_update(self, delta_time: float):
        self.race_controls_comp.on_update(delta_time)

        # Camera keeps animating even while playback is paused
        self._update_camera(delta_time)
        
        seek_speed = 3.0 * max(1.0, self.playback_speed) # Multiplier for seeking speed, scales with current playback speed
        if self.is_rewinding:
            self.frame_index = max(0.0, self.frame_index - delta_time * FPS * seek_speed)
            self.race_controls_comp.flash_button('rewind')
        elif self.is_forwarding:
            self.frame_index = min(self.n_frames - 1, self.frame_index + delta_time * FPS * seek_speed)
            self.race_controls_comp.flash_button('forward')

        if self.paused:
            return

        self.frame_index += delta_time * FPS * self.playback_speed
        
        if self.frame_index >= self.n_frames:
            self.frame_index = float(self.n_frames - 1)
            
        # Broadcast telemetry state during playback (throttled to 25 Hz).
        self._broadcast_telemetry_state()

    def on_key_press(self, symbol: int, modifiers: int):
        # Allow ESC to close window at any time
        if symbol == arcade.key.ESCAPE:
            arcade.close_window()
            return
        if symbol == arcade.key.SPACE:
            self.paused = not self.paused
            self._broadcast_telemetry_state(force=True)
            self.race_controls_comp.flash_button('play_pause')
        elif symbol == arcade.key.RIGHT:
            self.was_paused_before_hold = self.paused
            self.is_forwarding = True
            self.paused = True
        elif symbol == arcade.key.LEFT:
            self.was_paused_before_hold = self.paused
            self.is_rewinding = True
            self.paused = True
        elif symbol == arcade.key.UP:
            if self.playback_speed < PLAYBACK_SPEEDS[-1]:
                # Increase to next higher speed
                for spd in PLAYBACK_SPEEDS:
                    if spd > self.playback_speed:
                        self.playback_speed = spd
                        self._broadcast_telemetry_state(force=True)
                        break
            self.race_controls_comp.flash_button('speed_increase')
        elif symbol == arcade.key.DOWN:
            if self.playback_speed > PLAYBACK_SPEEDS[0]:
                # Decrease to next lower speed
                for spd in reversed(PLAYBACK_SPEEDS):
                    if spd < self.playback_speed:
                        self.playback_speed = spd
                        self._broadcast_telemetry_state(force=True)
                        break
            self.race_controls_comp.flash_button('speed_decrease')
        elif symbol == arcade.key.KEY_1:
            self.playback_speed = 0.5
            self._broadcast_telemetry_state(force=True)
            self.race_controls_comp.flash_button('speed_decrease')
        elif symbol == arcade.key.KEY_2:
            self.playback_speed = 1.0
            self._broadcast_telemetry_state(force=True)
            self.race_controls_comp.flash_button('speed_decrease')
        elif symbol == arcade.key.KEY_3:
            self.playback_speed = 2.0
            self._broadcast_telemetry_state(force=True)
            self.race_controls_comp.flash_button('speed_increase')
        elif symbol == arcade.key.KEY_4:
            self.playback_speed = 4.0
            self._broadcast_telemetry_state(force=True)
            self.race_controls_comp.flash_button('speed_increase')
        elif symbol == arcade.key.R:
            self.frame_index = 0.0
            self.playback_speed = 1.0
            self._broadcast_telemetry_state(force=True)
            # Clear degradation cache on restart
            if self.degradation_integrator:
                self.degradation_integrator.clear_cache()
            self.race_controls_comp.flash_button('rewind')
        elif symbol == arcade.key.D:
            self.toggle_drs_zones = not self.toggle_drs_zones
        elif symbol == arcade.key.L:
            self.show_driver_labels = not self.show_driver_labels
        elif symbol == arcade.key.H:
            # Toggle Controls popup with 'H' key - anchored above the help chip
            popup = self.controls_popup_comp
            left_pos = float(popup.margin_x)
            top_pos = float(popup.margin_y + popup.height)
            if popup.visible:
                popup.hide()
            else:
                popup.show_over(left_pos, top_pos)
        elif symbol == arcade.key.B:
            self.progress_bar_comp.toggle_visibility() # toggle progress bar visibility
        elif symbol == arcade.key.I:
            self.session_info_comp.toggle_visibility() # toggle session info banner
        elif symbol == arcade.key.C:
            self.battle_cam = not self.battle_cam       # auto battle camera
        elif symbol == arcade.key.S:
            self.sector_splits_comp.toggle_visibility() # sector splits panel
        elif symbol == arcade.key.T:
            self.show_interval_tags = not self.show_interval_tags # on-track gap chips

    def on_key_release(self, symbol: int, modifiers: int):
        if symbol == arcade.key.RIGHT:
            self.is_forwarding = False
            self.paused = self.was_paused_before_hold
        elif symbol == arcade.key.LEFT:
            self.is_rewinding = False
            self.paused = self.was_paused_before_hold

    def on_mouse_release(self, x: float, y: float, button: int, modifiers: int):
        if self.is_forwarding or self.is_rewinding:
            self.is_forwarding = False
            self.is_rewinding = False
            self.paused = self.was_paused_before_hold

    def on_mouse_press(self, x: float, y: float, button: int, modifiers: int):
        # forward to components; stop at first that handled it
        if self.controls_popup_comp.on_mouse_press(self, x, y, button, modifiers):
            return
        if self.race_controls_comp.on_mouse_press(self, x, y, button, modifiers):
            return
        if self.progress_bar_comp.on_mouse_press(self, x, y, button, modifiers):
            return
        if self.leaderboard_comp.on_mouse_press(self, x, y, button, modifiers):
            return
        if self.legend_comp.on_mouse_press(self, x, y, button, modifiers):
            return
        # default: clear selection if clicked elsewhere
        self.selected_driver = None
        
    def on_mouse_motion(self, x: float, y: float, dx: float, dy: float):
        """Handle mouse motion for hover effects on progress bar, controls and leaderboard."""
        self.progress_bar_comp.on_mouse_motion(self, x, y, dx, dy)
        self.race_controls_comp.on_mouse_motion(self, x, y, dx, dy)
        self.leaderboard_comp.on_mouse_motion(self, x, y, dx, dy)
        
    def close(self):
        """Clean up resources when window closes."""
        if hasattr(self, 'telemetry_stream') and self.telemetry_stream:
            print("Stopping telemetry stream server...")
            self.telemetry_stream.stop()
        super().close()