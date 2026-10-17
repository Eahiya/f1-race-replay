import sys
from collections import deque

import matplotlib
matplotlib.use("QtAgg")
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
import matplotlib.ticker as ticker
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas

from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QComboBox, QFrame, QProgressBar
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QColor
from src.gui.pit_wall_window import PitWallWindow
from src.services.ui_theme import THEME

_TIME_WINDOW = 30  # seconds kept in rolling-time mode

# Driver-specific colours (not UI theme - these identify drivers in charts)
_DEFAULT_COL_A = "#E10600"  # Ferrari / Red default
_DEFAULT_COL_B = "#3671C6"  # Red Bull / Blue default

# Shared theme colours from the design system
_BG = THEME.background
_PANEL_BG = THEME.surface
_TEXT_COLOR = THEME.text_primary
_SUB_COLOR = THEME.text_secondary
_ACCENT = THEME.accent


class HeadToHeadWindow(PitWallWindow):
    """
    Pit wall insight for side-by-side comparison between any two drivers.
    Displays live digital readouts, speed delta, gear, throttle, brake,
    and multi-panel telemetry comparison curves.
    """

    def __init__(self):
        self._known_drivers = []
        self._driver_colors: dict[str, str] = {}
        self._time_buffers: dict[str, deque] = {}
        self._lap_buffers: dict[str, dict] = {}
        self._lap_lengths: dict[str, float] = {}
        self._circuit_length_m: float | None = None
        self._x_mode = "time"  # "time" | "lap"

        super().__init__()
        self.setWindowTitle("F1 Pit Wall - Head-to-Head Telemetry Comparison")
        self.resize(1150, 780)

    # ── UI setup ─────────────────────────────────────────────────────────

    def setup_ui(self):
        central_widget = QWidget()
        central_widget.setStyleSheet(f"background-color: {_BG}; color: {_TEXT_COLOR};")
        self.setCentralWidget(central_widget)

        root_layout = QVBoxLayout(central_widget)
        root_layout.setSpacing(8)
        root_layout.setContentsMargins(12, 12, 12, 12)

        # ── Header & Driver Selection Controls ──
        ctrl_card = QFrame()
        ctrl_card.setStyleSheet(f"background-color: {_PANEL_BG}; border-radius: 8px; padding: 6px;")
        ctrl_layout = QHBoxLayout(ctrl_card)
        ctrl_layout.setContentsMargins(10, 6, 10, 6)

        title_lbl = QLabel("⚔️ HEAD-TO-HEAD COMPARISON")
        title_lbl.setFont(QFont("Segoe UI", 12, QFont.Bold))
        title_lbl.setStyleSheet("color: #FFFFFF; font-weight: bold;")
        ctrl_layout.addWidget(title_lbl)

        ctrl_layout.addSpacing(20)

        # Driver A Select
        lbl_a = QLabel("Driver 1:")
        lbl_a.setFont(QFont("Segoe UI", 10, QFont.Bold))
        lbl_a.setStyleSheet(f"color: {_DEFAULT_COL_A};")
        self.combo_a = QComboBox()
        self.combo_a.setMinimumWidth(110)
        self.combo_a.setFont(QFont("Segoe UI", 10))
        self.combo_a.setStyleSheet("background-color: #333333; color: white; padding: 4px; border-radius: 4px;")
        self.combo_a.currentTextChanged.connect(self._on_drivers_changed)

        # Driver B Select
        lbl_b = QLabel("Driver 2:")
        lbl_b.setFont(QFont("Segoe UI", 10, QFont.Bold))
        lbl_b.setStyleSheet(f"color: {_DEFAULT_COL_B};")
        self.combo_b = QComboBox()
        self.combo_b.setMinimumWidth(110)
        self.combo_b.setFont(QFont("Segoe UI", 10))
        self.combo_b.setStyleSheet("background-color: #333333; color: white; padding: 4px; border-radius: 4px;")
        self.combo_b.currentTextChanged.connect(self._on_drivers_changed)

        # X-Mode
        lbl_mode = QLabel("Domain:")
        lbl_mode.setFont(QFont("Segoe UI", 10))
        self.combo_mode = QComboBox()
        self.combo_mode.setFont(QFont("Segoe UI", 10))
        self.combo_mode.addItems(["Last 30 Seconds", "Current Lap"])
        self.combo_mode.setStyleSheet("background-color: #333333; color: white; padding: 4px; border-radius: 4px;")
        self.combo_mode.currentIndexChanged.connect(self._on_xmode_changed)

        ctrl_layout.addWidget(lbl_a)
        ctrl_layout.addWidget(self.combo_a)
        ctrl_layout.addSpacing(15)
        ctrl_layout.addWidget(lbl_b)
        ctrl_layout.addWidget(self.combo_b)
        ctrl_layout.addSpacing(25)
        ctrl_layout.addWidget(lbl_mode)
        ctrl_layout.addWidget(self.combo_mode)
        ctrl_layout.addStretch()

        root_layout.addWidget(ctrl_card)

        # ── Live Telemetry Cards (Driver A | Delta Box | Driver B) ──
        cards_layout = QHBoxLayout()
        cards_layout.setSpacing(10)

        # Card A
        self.card_a, self.ui_a = self._create_driver_card("Driver A", _DEFAULT_COL_A)
        cards_layout.addWidget(self.card_a, 2)

        # Delta Box
        self.delta_card = self._create_delta_card()
        cards_layout.addWidget(self.delta_card, 1)

        # Card B
        self.card_b, self.ui_b = self._create_driver_card("Driver B", _DEFAULT_COL_B)
        cards_layout.addWidget(self.card_b, 2)

        root_layout.addLayout(cards_layout)

        # ── Matplotlib Telemetry Plots ──
        self._fig = plt.figure(figsize=(10, 5.5), facecolor=_PANEL_BG)
        gs = gridspec.GridSpec(
            3, 1,
            figure=self._fig,
            height_ratios=[2.2, 1.0, 1.2],
            hspace=0.08,
            top=0.96, bottom=0.08, left=0.07, right=0.97
        )

        # Speed Plot
        self._ax_speed = self._fig.add_subplot(gs[0])
        self._line_speed_a, = self._ax_speed.plot([], [], color=_DEFAULT_COL_A, linewidth=1.8, label="Driver 1")
        self._line_speed_b, = self._ax_speed.plot([], [], color=_DEFAULT_COL_B, linewidth=1.8, label="Driver 2", linestyle="--")
        self._ax_speed.set_facecolor(_PANEL_BG)
        self._ax_speed.set_ylabel("Speed (km/h)", color=_TEXT_COLOR, fontsize=9)
        self._ax_speed.set_ylim(0, 370)
        self._ax_speed.tick_params(colors=_SUB_COLOR, labelbottom=False)
        self._ax_speed.grid(True, color="#383838", linestyle=":", alpha=0.6)
        for spine in self._ax_speed.spines.values():
            spine.set_edgecolor("#444444")
        self._leg_speed = self._ax_speed.legend(loc="upper left", facecolor=_PANEL_BG, edgecolor="#444444", labelcolor=_TEXT_COLOR, fontsize=8)

        # Gear Plot
        self._ax_gear = self._fig.add_subplot(gs[1])
        self._line_gear_a, = self._ax_gear.plot([], [], color=_DEFAULT_COL_A, linewidth=1.5, drawstyle="steps-post")
        self._line_gear_b, = self._ax_gear.plot([], [], color=_DEFAULT_COL_B, linewidth=1.5, drawstyle="steps-post", linestyle="--")
        self._ax_gear.set_facecolor(_PANEL_BG)
        self._ax_gear.set_ylabel("Gear", color=_TEXT_COLOR, fontsize=9)
        self._ax_gear.set_ylim(0.5, 8.5)
        self._ax_gear.set_yticks(range(1, 9))
        self._ax_gear.tick_params(colors=_SUB_COLOR, labelbottom=False)
        self._ax_gear.grid(True, color="#383838", linestyle=":", alpha=0.6)
        for spine in self._ax_gear.spines.values():
            spine.set_edgecolor("#444444")

        # Throttle & Brake Plot
        self._ax_ctrl = self._fig.add_subplot(gs[2])
        self._line_throt_a, = self._ax_ctrl.plot([], [], color=_DEFAULT_COL_A, linewidth=1.5, label="Thr 1")
        self._line_throt_b, = self._ax_ctrl.plot([], [], color=_DEFAULT_COL_B, linewidth=1.5, linestyle="--", label="Thr 2")
        self._line_brake_a, = self._ax_ctrl.plot([], [], color="#E74C3C", linewidth=1.2, alpha=0.7, label="Brk 1")
        self._line_brake_b, = self._ax_ctrl.plot([], [], color="#C0392B", linewidth=1.2, linestyle=":", alpha=0.7, label="Brk 2")
        self._ax_ctrl.set_facecolor(_PANEL_BG)
        self._ax_ctrl.set_ylabel("Input (%)", color=_TEXT_COLOR, fontsize=9)
        self._ax_ctrl.set_ylim(-5, 105)
        self._ax_ctrl.tick_params(colors=_SUB_COLOR)
        self._ax_ctrl.grid(True, color="#383838", linestyle=":", alpha=0.6)
        for spine in self._ax_ctrl.spines.values():
            spine.set_edgecolor("#444444")
        self._ax_ctrl.set_xlabel("Time (s)", color=_TEXT_COLOR, fontsize=9)

        self._canvas = FigureCanvas(self._fig)
        root_layout.addWidget(self._canvas)

    def _create_driver_card(self, title: str, default_color: str):
        card = QFrame()
        card.setStyleSheet(f"background-color: {_PANEL_BG}; border: 1px solid #333333; border-radius: 8px; padding: 8px;")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # Header: Driver Code & Pos
        hdr = QHBoxLayout()
        code_lbl = QLabel(title)
        code_lbl.setFont(QFont("Segoe UI", 16, QFont.Bold))
        code_lbl.setStyleSheet(f"color: {default_color}; font-weight: bold;")

        pos_lbl = QLabel("P-")
        pos_lbl.setFont(QFont("Segoe UI", 12, QFont.Bold))
        pos_lbl.setStyleSheet("background-color: #333333; color: white; padding: 2px 6px; border-radius: 4px;")

        hdr.addWidget(code_lbl)
        hdr.addStretch()
        hdr.addWidget(pos_lbl)
        layout.addLayout(hdr)

        # Speed & Gear row
        sg_row = QHBoxLayout()
        speed_lbl = QLabel("--- km/h")
        speed_lbl.setFont(QFont("Segoe UI", 14, QFont.Bold))

        gear_lbl = QLabel("GEAR -")
        gear_lbl.setFont(QFont("Segoe UI", 11, QFont.Bold))
        gear_lbl.setStyleSheet("color: #FFD700;")

        drs_lbl = QLabel("DRS OFF")
        drs_lbl.setFont(QFont("Segoe UI", 9, QFont.Bold))
        drs_lbl.setStyleSheet("background-color: #383838; color: #888888; padding: 2px 6px; border-radius: 3px;")

        sg_row.addWidget(speed_lbl)
        sg_row.addStretch()
        sg_row.addWidget(gear_lbl)
        sg_row.addSpacing(6)
        sg_row.addWidget(drs_lbl)
        layout.addLayout(sg_row)

        # Throttle Bar
        thr_row = QHBoxLayout()
        thr_txt = QLabel("THR:")
        thr_txt.setFont(QFont("Segoe UI", 8))
        thr_bar = QProgressBar()
        thr_bar.setRange(0, 100)
        thr_bar.setValue(0)
        thr_bar.setFixedHeight(8)
        thr_bar.setTextVisible(False)
        thr_bar.setStyleSheet("QProgressBar { background-color: #2D2D2D; border-radius: 4px; } QProgressBar::chunk { background-color: #2ECC71; border-radius: 4px; }")
        thr_val = QLabel("0%")
        thr_val.setFont(QFont("Segoe UI", 8))
        thr_val.setFixedWidth(32)

        thr_row.addWidget(thr_txt)
        thr_row.addWidget(thr_bar)
        thr_row.addWidget(thr_val)
        layout.addLayout(thr_row)

        # Brake Bar
        brk_row = QHBoxLayout()
        brk_txt = QLabel("BRK:")
        brk_txt.setFont(QFont("Segoe UI", 8))
        brk_bar = QProgressBar()
        brk_bar.setRange(0, 100)
        brk_bar.setValue(0)
        brk_bar.setFixedHeight(8)
        brk_bar.setTextVisible(False)
        brk_bar.setStyleSheet("QProgressBar { background-color: #2D2D2D; border-radius: 4px; } QProgressBar::chunk { background-color: #E74C3C; border-radius: 4px; }")
        brk_val = QLabel("0%")
        brk_val.setFont(QFont("Segoe UI", 8))
        brk_val.setFixedWidth(32)

        brk_row.addWidget(brk_txt)
        brk_row.addWidget(brk_bar)
        brk_row.addWidget(brk_val)
        layout.addLayout(brk_row)

        ui_refs = {
            "code": code_lbl,
            "pos": pos_lbl,
            "speed": speed_lbl,
            "gear": gear_lbl,
            "drs": drs_lbl,
            "thr_bar": thr_bar,
            "thr_val": thr_val,
            "brk_bar": brk_bar,
            "brk_val": brk_val,
        }
        return card, ui_refs

    def _create_delta_card(self):
        card = QFrame()
        card.setStyleSheet(f"background-color: {_PANEL_BG}; border: 1px solid #333333; border-radius: 8px; padding: 6px;")
        layout = QVBoxLayout(card)
        layout.setAlignment(Qt.AlignCenter)
        layout.setSpacing(4)

        lbl = QLabel("LIVE DELTA")
        lbl.setFont(QFont("Segoe UI", 9, QFont.Bold))
        lbl.setStyleSheet("color: #888888;")
        layout.addWidget(lbl, alignment=Qt.AlignCenter)

        self.delta_speed_lbl = QLabel("Δ 0 km/h")
        self.delta_speed_lbl.setFont(QFont("Segoe UI", 13, QFont.Bold))
        self.delta_speed_lbl.setStyleSheet("color: #FFFFFF;")
        layout.addWidget(self.delta_speed_lbl, alignment=Qt.AlignCenter)

        self.delta_dist_lbl = QLabel("GAP: --- m")
        self.delta_dist_lbl.setFont(QFont("Segoe UI", 10))
        self.delta_dist_lbl.setStyleSheet("color: #AAAAAA;")
        layout.addWidget(self.delta_dist_lbl, alignment=Qt.AlignCenter)

        return card

    # ── Telemetry Handling ────────────────────────────────────────────────

    def on_telemetry_data(self, data):
        frame = data.get("frame")
        if not frame:
            return

        session_t = float(frame.get("t") or 0.0)
        drivers = frame.get("drivers", {})
        if not drivers:
            return

        # Update available colors
        raw_colors = data.get("driver_colors", {})
        if raw_colors:
            self._driver_colors.update(raw_colors)

        # Update driver selector
        self._refresh_driver_lists(drivers)

        # Buffer samples for all drivers
        for code, drv in drivers.items():
            self._append_sample(code, drv, session_t)

        code_a = self.combo_a.currentText()
        code_b = self.combo_b.currentText()

        # Update digital readout cards
        drv_a = drivers.get(code_a)
        drv_b = drivers.get(code_b)

        if drv_a:
            self._update_card(self.ui_a, code_a, drv_a)
        if drv_b:
            self._update_card(self.ui_b, code_b, drv_b)

        # Update Live Delta
        if drv_a and drv_b:
            sp_a = float(drv_a.get("speed") or 0)
            sp_b = float(drv_b.get("speed") or 0)
            d_sp = sp_a - sp_b
            if d_sp >= 0:
                self.delta_speed_lbl.setText(f"+{d_sp:.1f} km/h {code_a}")
                self.delta_speed_lbl.setStyleSheet(f"color: {self._driver_colors.get(code_a, _DEFAULT_COL_A)};")
            else:
                self.delta_speed_lbl.setText(f"+{abs(d_sp):.1f} km/h {code_b}")
                self.delta_speed_lbl.setStyleSheet(f"color: {self._driver_colors.get(code_b, _DEFAULT_COL_B)};")

            dist_a = float(drv_a.get("dist") or 0)
            dist_b = float(drv_b.get("dist") or 0)
            gap_m = dist_a - dist_b
            leader_code = code_a if gap_m >= 0 else code_b
            self.delta_dist_lbl.setText(f"{leader_code} +{abs(gap_m):.1f} m")

        # Redraw plots
        self._redraw(code_a, code_b)

    def _update_card(self, ui: dict, code: str, drv: dict):
        col = self._driver_colors.get(code, "#FFFFFF")
        ui["code"].setText(code)
        ui["code"].setStyleSheet(f"color: {col}; font-weight: bold;")

        pos = drv.get("position", "-")
        ui["pos"].setText(f"P{pos}")

        spd = float(drv.get("speed") or 0)
        ui["speed"].setText(f"{spd:.0f} km/h")

        gear = drv.get("gear", "-")
        ui["gear"].setText(f"G {gear}")

        drs = int(drv.get("drs") or 0)
        if drrs := (drs in (10, 12, 14, 1)):
            ui["drs"].setText("DRS ON")
            ui["drs"].setStyleSheet("background-color: #2ECC71; color: black; font-weight: bold; padding: 2px 6px; border-radius: 3px;")
        else:
            ui["drs"].setText("DRS OFF")
            ui["drs"].setStyleSheet("background-color: #383838; color: #888888; padding: 2px 6px; border-radius: 3px;")

        thr = int(float(drv.get("throttle") or 0))
        ui["thr_bar"].setValue(thr)
        ui["thr_val"].setText(f"{thr}%")

        brk = int(float(drv.get("brake") or 0) * 100)
        ui["brk_bar"].setValue(brk)
        ui["brk_val"].setText(f"{brk}%")

    def _ensure_buffers(self, code: str):
        if code not in self._time_buffers:
            self._time_buffers[code] = deque()
        if code not in self._lap_buffers:
            self._lap_buffers[code] = {"lap": None, "start_dist": 0.0, "samples": []}

    def _append_sample(self, code: str, driver: dict, session_t: float):
        self._ensure_buffers(code)
        speed = float(driver.get("speed") or 0)
        gear = int(driver.get("gear") or 0)
        throttle = float(driver.get("throttle") or 0)
        brake = float(driver.get("brake") or 0) * 100.0
        dist = float(driver.get("dist") or 0)
        lap = driver.get("lap")

        # Time buffer
        tb = self._time_buffers[code]
        tb.append({"t": session_t, "speed": speed, "gear": gear, "throttle": throttle, "brake": brake})
        cutoff = session_t - _TIME_WINDOW
        while tb and tb[0]["t"] < cutoff:
            tb.popleft()

        # Lap buffer
        lb = self._lap_buffers[code]
        if lap is not None and lap != lb["lap"]:
            if lb["samples"]:
                self._lap_lengths[code] = lb["samples"][-1]["dist"]
            lb["lap"] = lap
            lb["start_dist"] = dist
            lb["samples"] = []
        lap_dist = dist - lb["start_dist"]
        lb["samples"].append({"dist": lap_dist, "speed": speed, "gear": gear, "throttle": throttle, "brake": brake})

    def _refresh_driver_lists(self, drivers: dict):
        incoming = sorted(drivers.keys())
        if incoming == self._known_drivers:
            return

        current_a = self.combo_a.currentText()
        current_b = self.combo_b.currentText()

        self.combo_a.blockSignals(True)
        self.combo_b.blockSignals(True)

        self.combo_a.clear()
        self.combo_b.clear()

        self.combo_a.addItems(incoming)
        self.combo_b.addItems(incoming)

        if current_a in incoming:
            self.combo_a.setCurrentText(current_a)
        elif len(incoming) > 0:
            self.combo_a.setCurrentIndex(0)

        if current_b in incoming:
            self.combo_b.setCurrentText(current_b)
        elif len(incoming) > 1:
            self.combo_b.setCurrentIndex(1)

        self.combo_a.blockSignals(False)
        self.combo_b.blockSignals(False)
        self._known_drivers = incoming

    def _on_drivers_changed(self):
        col_a = self._driver_colors.get(self.combo_a.currentText(), _DEFAULT_COL_A)
        col_b = self._driver_colors.get(self.combo_b.currentText(), _DEFAULT_COL_B)

        self._line_speed_a.set_color(col_a)
        self._line_speed_b.set_color(col_b)
        self._line_gear_a.set_color(col_a)
        self._line_gear_b.set_color(col_b)
        self._line_throt_a.set_color(col_a)
        self._line_throt_b.set_color(col_b)

        self._line_speed_a.set_label(self.combo_a.currentText() or "Driver 1")
        self._line_speed_b.set_label(self.combo_b.currentText() or "Driver 2")
        self._ax_speed.legend(loc="upper left", facecolor=_PANEL_BG, edgecolor="#444444", labelcolor=_TEXT_COLOR, fontsize=8)

        self._redraw(self.combo_a.currentText(), self.combo_b.currentText())

    def _on_xmode_changed(self, idx: int):
        self._x_mode = "time" if idx == 0 else "lap"
        if self._x_mode == "time":
            self._ax_ctrl.set_xlabel("Time (s)", color=_TEXT_COLOR, fontsize=9)
            self._ax_ctrl.xaxis.set_major_formatter(ticker.FormatStrFormatter("%.0f"))
        else:
            self._ax_ctrl.set_xlabel("Distance into Lap (m)", color=_TEXT_COLOR, fontsize=9)
            self._ax_ctrl.xaxis.set_major_formatter(ticker.FormatStrFormatter("%.0f"))
        self._redraw(self.combo_a.currentText(), self.combo_b.currentText())

    def _redraw(self, code_a: str, code_b: str):
        if self._x_mode == "time":
            self._redraw_time(code_a, code_b)
        else:
            self._redraw_lap(code_a, code_b)
        self._canvas.draw_idle()

    def _redraw_time(self, code_a: str, code_b: str):
        tb_a = self._time_buffers.get(code_a, deque())
        tb_b = self._time_buffers.get(code_b, deque())

        # Extract A
        xs_a = [s["t"] for s in tb_a]
        ys_sp_a = [s["speed"] for s in tb_a]
        ys_gr_a = [s["gear"] for s in tb_a]
        ys_th_a = [s["throttle"] for s in tb_a]
        ys_br_a = [s["brake"] for s in tb_a]

        # Extract B
        xs_b = [s["t"] for s in tb_b]
        ys_sp_b = [s["speed"] for s in tb_b]
        ys_gr_b = [s["gear"] for s in tb_b]
        ys_th_b = [s["throttle"] for s in tb_b]
        ys_br_b = [s["brake"] for s in tb_b]

        self._line_speed_a.set_data(xs_a, ys_sp_a)
        self._line_speed_b.set_data(xs_b, ys_sp_b)
        self._line_gear_a.set_data(xs_a, ys_gr_a)
        self._line_gear_b.set_data(xs_b, ys_gr_b)
        self._line_throt_a.set_data(xs_a, ys_th_a)
        self._line_throt_b.set_data(xs_b, ys_th_b)
        self._line_brake_a.set_data(xs_a, ys_br_a)
        self._line_brake_b.set_data(xs_b, ys_br_b)

        # Align X bounds to latest session time
        max_t = max(xs_a[-1] if xs_a else 0, xs_b[-1] if xs_b else 0)
        if max_t > 0:
            min_t = max(0, max_t - _TIME_WINDOW)
            self._ax_speed.set_xlim(min_t, max_t)
            self._ax_gear.set_xlim(min_t, max_t)
            self._ax_ctrl.set_xlim(min_t, max_t)

    def _redraw_lap(self, code_a: str, code_b: str):
        lb_a = self._lap_buffers.get(code_a, {}).get("samples", [])
        lb_b = self._lap_buffers.get(code_b, {}).get("samples", [])

        xs_a = [s["dist"] for s in lb_a]
        ys_sp_a = [s["speed"] for s in lb_a]
        ys_gr_a = [s["gear"] for s in lb_a]
        ys_th_a = [s["throttle"] for s in lb_a]
        ys_br_a = [s["brake"] for s in lb_a]

        xs_b = [s["dist"] for s in lb_b]
        ys_sp_b = [s["speed"] for s in lb_b]
        ys_gr_b = [s["gear"] for s in lb_b]
        ys_th_b = [s["throttle"] for s in lb_b]
        ys_br_b = [s["brake"] for s in lb_b]

        self._line_speed_a.set_data(xs_a, ys_sp_a)
        self._line_speed_b.set_data(xs_b, ys_sp_b)
        self._line_gear_a.set_data(xs_a, ys_gr_a)
        self._line_gear_b.set_data(xs_b, ys_gr_b)
        self._line_throt_a.set_data(xs_a, ys_th_a)
        self._line_throt_b.set_data(xs_b, ys_th_b)
        self._line_brake_a.set_data(xs_a, ys_br_a)
        self._line_brake_b.set_data(xs_b, ys_br_b)

        max_d = max(xs_a[-1] if xs_a else 1000, xs_b[-1] if xs_b else 1000, 1000)
        self._ax_speed.set_xlim(0, max_d)
        self._ax_gear.set_xlim(0, max_d)
        self._ax_ctrl.set_xlim(0, max_d)
