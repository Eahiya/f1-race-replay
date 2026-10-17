import sys
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QFrame, QScrollArea, QComboBox, QGridLayout, QProgressBar
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QColor, QPainter, QBrush, QPen
from src.gui.pit_wall_window import PitWallWindow
from src.services.ui_theme import THEME

_BG = THEME.background
_PANEL_BG = THEME.surface
_CARD_BG = "#2A2A2A"
_TEXT_COLOR = THEME.text_primary
_SUB_COLOR = THEME.text_secondary

# Compound colors and names
_COMPOUND_COLORS = {
    "SOFT": "#FF1801",
    "MEDIUM": "#FFD700",
    "HARD": "#FFFFFF",
    "INTERMEDIATE": "#39B54A",
    "WET": "#00A3E0",
    "UNKNOWN": "#888888",
}

_COMPOUND_SHORT = {
    "SOFT": "S",
    "MEDIUM": "M",
    "HARD": "H",
    "INTERMEDIATE": "I",
    "WET": "W",
    "UNKNOWN": "?",
}

_INT_TO_NAME = {
    0: "SOFT",
    1: "MEDIUM",
    2: "HARD",
    3: "INTERMEDIATE",
    4: "WET",
}


class _StintTimelineWidget(QWidget):
    """Custom paint widget rendering Gantt-style tyre stints for the grid."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.drivers_stints = {}     # code -> list of {"compound": str, "start_lap": int, "end_lap": int}
        self.drivers_pos = {}        # code -> current position (int)
        self.driver_colors = {}      # code -> hex color
        self.current_lap = 1
        self.total_laps = 57
        self.setMinimumSize(600, 520)

    def update_data(self, stints: dict, pos: dict, colors: dict, cur_lap: int, total_laps: int):
        self.drivers_stints = stints
        self.drivers_pos = pos
        self.driver_colors = colors
        self.current_lap = max(1, cur_lap)
        self.total_laps = max(self.current_lap, total_laps)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.TextAntialiasing)

        w, h = self.width(), self.height()
        painter.fillRect(self.rect(), QColor(_PANEL_BG))

        if not self.drivers_stints:
            painter.setPen(QColor(_SUB_COLOR))
            painter.setFont(QFont("Segoe UI", 12))
            painter.drawText(self.rect(), Qt.AlignCenter, "Waiting for race telemetry...")
            return

        # Sort drivers by current position
        sorted_drivers = sorted(
            self.drivers_stints.keys(),
            key=lambda c: self.drivers_pos.get(c, 99)
        )

        left_margin = 85
        right_margin = 35
        top_margin = 35
        track_w = w - left_margin - right_margin
        num_drivers = len(sorted_drivers)
        row_h = min(26.0, (h - top_margin - 20) / max(1, num_drivers))

        # Draw Lap ruler at top
        painter.setFont(QFont("Segoe UI", 8))
        painter.setPen(QColor("#555555"))
        step = max(5, int(self.total_laps / 10))
        for lap_mark in range(0, self.total_laps + 1, step):
            lx = left_margin + (lap_mark / self.total_laps) * track_w
            painter.drawLine(int(lx), top_margin - 12, int(lx), top_margin - 4)
            painter.drawText(int(lx) - 10, top_margin - 16, 25, 12, Qt.AlignCenter, str(lap_mark))

        # Current lap vertical indicator line
        cur_x = left_margin + (self.current_lap / self.total_laps) * track_w
        painter.setPen(QPen(QColor("#E10600"), 1.5, Qt.DashLine))
        painter.drawLine(int(cur_x), top_margin - 10, int(cur_x), int(top_margin + num_drivers * row_h))

        # Draw rows for each driver
        for idx, code in enumerate(sorted_drivers):
            y = top_margin + idx * row_h
            pos = self.drivers_pos.get(code, idx + 1)
            t_col = QColor(self.driver_colors.get(code, "#FFFFFF"))

            # Position & Driver Code
            painter.setFont(QFont("Segoe UI", 8, QFont.Bold))
            painter.setPen(QColor("#AAAAAA"))
            painter.drawText(4, int(y), 24, int(row_h), Qt.AlignVCenter | Qt.AlignRight, f"P{pos}")

            # Color pip
            painter.fillRect(34, int(y + row_h * 0.2), 4, int(row_h * 0.6), t_col)

            painter.setPen(QColor(_TEXT_COLOR))
            painter.drawText(42, int(y), 40, int(row_h), Qt.AlignVCenter | Qt.AlignLeft, code)

            # Stint bars
            stints = self.drivers_stints.get(code, [])
            for stint in stints:
                s_lap = stint["start_lap"]
                e_lap = stint["end_lap"]
                comp = stint["compound"]

                x1 = left_margin + (s_lap / self.total_laps) * track_w
                x2 = left_margin + (e_lap / self.total_laps) * track_w
                bw = max(4.0, x2 - x1)

                c_color = QColor(_COMPOUND_COLORS.get(comp, "#888888"))
                painter.setBrush(QBrush(c_color))
                painter.setPen(QPen(QColor("#111111"), 1))
                painter.drawRoundedRect(int(x1), int(y + 3), int(bw), int(row_h - 6), 3, 3)

                # Stint badge text inside bar if wide enough
                if bw > 18:
                    painter.setFont(QFont("Segoe UI", 7, QFont.Bold))
                    painter.setPen(QColor("#000000" if comp in ("MEDIUM", "HARD") else "#FFFFFF"))
                    short = _COMPOUND_SHORT.get(comp, "?")
                    age = max(1, e_lap - s_lap)
                    lbl = f"{short}{age}" if bw > 32 else short
                    painter.drawText(int(x1), int(y + 3), int(bw), int(row_h - 6), Qt.AlignCenter, lbl)


class TyreStrategyWindow(PitWallWindow):
    """
    Live Pit Wall tyre strategy & stints monitor.
    Tracks compound history, pit stops, degradation, and wear for every driver.
    """

    def __init__(self):
        self._stints = {}            # code -> [{"compound": str, "start_lap": int, "end_lap": int}]
        self._current_compound = {}  # code -> str
        self._current_tyre_life = {} # code -> int
        self._pit_counts = {}        # code -> int
        self._driver_pos = {}        # code -> int
        self._driver_colors = {}
        self._current_lap = 1
        self._total_laps = 57
        self._known_drivers = []

        super().__init__()
        self.setWindowTitle("F1 Pit Wall - Tyre Strategy & Stints")
        self.resize(1100, 720)

    def setup_ui(self):
        central_widget = QWidget()
        central_widget.setStyleSheet(f"background-color: {_BG}; color: {_TEXT_COLOR};")
        self.setCentralWidget(central_widget)

        root = QVBoxLayout(central_widget)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        # ── Header bar ──
        hdr_card = QFrame()
        hdr_card.setStyleSheet(f"background-color: {_PANEL_BG}; border-radius: 8px; padding: 6px;")
        hdr_layout = QHBoxLayout(hdr_card)
        hdr_layout.setContentsMargins(10, 6, 10, 6)

        title = QLabel("🏁 LIVE TYRE STRATEGY & STINT TRACKER")
        title.setFont(QFont("Segoe UI", 12, QFont.Bold))
        title.setStyleSheet("color: #FFFFFF; font-weight: bold;")
        hdr_layout.addWidget(title)

        hdr_layout.addStretch()

        # Legend badges
        for comp_name, color in [("SOFT", "#FF1801"), ("MEDIUM", "#FFD700"), ("HARD", "#FFFFFF"), ("INTER", "#39B54A"), ("WET", "#00A3E0")]:
            dot = QLabel(f"● {comp_name}")
            dot.setFont(QFont("Segoe UI", 9, QFont.Bold))
            dot.setStyleSheet(f"color: {color}; margin-right: 12px;")
            hdr_layout.addWidget(dot)

        root.addWidget(hdr_card)

        # ── Main Content: Grid Stint Gantt Chart (Left) + Driver Detail (Right) ──
        body_layout = QHBoxLayout()
        body_layout.setSpacing(10)

        # Left: Stint Gantt Chart
        self.timeline_widget = _StintTimelineWidget()
        body_layout.addWidget(self.timeline_widget, 3)

        # Right: Single Driver Tyre Health Inspector
        inspector = self._create_inspector_card()
        body_layout.addWidget(inspector, 1)

        root.addLayout(body_layout)

    def _create_inspector_card(self):
        card = QFrame()
        card.setStyleSheet(f"background-color: {_PANEL_BG}; border: 1px solid #333333; border-radius: 8px; padding: 10px;")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        lbl = QLabel("TYRE HEALTH INSPECTOR")
        lbl.setFont(QFont("Segoe UI", 10, QFont.Bold))
        lbl.setStyleSheet("color: #FFD700;")
        layout.addWidget(lbl)

        # Driver picker
        row_drv = QHBoxLayout()
        row_drv.addWidget(QLabel("Driver:"))
        self.inspector_combo = QComboBox()
        self.inspector_combo.setFont(QFont("Segoe UI", 10))
        self.inspector_combo.setStyleSheet("background-color: #333333; color: white; padding: 4px; border-radius: 4px;")
        self.inspector_combo.currentTextChanged.connect(self._on_inspector_driver_changed)
        row_drv.addWidget(self.inspector_combo)
        layout.addLayout(row_drv)

        # Compound & Age Badge
        self.badge_compound = QLabel("MEDIUM - Lap 1")
        self.badge_compound.setFont(QFont("Segoe UI", 12, QFont.Bold))
        self.badge_compound.setStyleSheet("background-color: #FFD700; color: black; padding: 8px; border-radius: 6px; text-align: center;")
        self.badge_compound.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.badge_compound)

        # Stints & Pit stops count
        self.lbl_pit_count = QLabel("Pit Stops: 0 | Current Stint: 1")
        self.lbl_pit_count.setFont(QFont("Segoe UI", 9))
        self.lbl_pit_count.setStyleSheet("color: #AAAAAA;")
        layout.addWidget(self.lbl_pit_count)

        # Grip / Wear Bar
        layout.addSpacing(6)
        layout.addWidget(QLabel("Estimated Remaining Grip:"))
        self.grip_bar = QProgressBar()
        self.grip_bar.setRange(0, 100)
        self.grip_bar.setValue(100)
        self.grip_bar.setFixedHeight(14)
        self.grip_bar.setTextVisible(True)
        self.grip_bar.setStyleSheet("QProgressBar { background-color: #2D2D2D; border-radius: 6px; text-align: center; } QProgressBar::chunk { background-color: #2ECC71; border-radius: 6px; }")
        layout.addWidget(self.grip_bar)

        # Degradation rate & Pit Window
        self.lbl_deg_rate = QLabel("Degradation: ~0.065 s/lap")
        self.lbl_deg_rate.setFont(QFont("Segoe UI", 9))
        layout.addWidget(self.lbl_deg_rate)

        self.lbl_cliff = QLabel("Estimated Cliff: Lap 28")
        self.lbl_cliff.setFont(QFont("Segoe UI", 9))
        self.lbl_cliff.setStyleSheet("color: #E67E22;")
        layout.addWidget(self.lbl_cliff)

        layout.addStretch()
        return card

    def on_telemetry_data(self, data):
        frame = data.get("frame")
        if not frame:
            return

        drivers = frame.get("drivers", {})
        if not drivers:
            return

        session_data = data.get("session_data", {})
        cur_lap = int(session_data.get("lap") or 1)
        total_laps = int(session_data.get("total_laps") or self._total_laps)
        self._current_lap = cur_lap
        self._total_laps = total_laps

        raw_colors = data.get("driver_colors", {})
        if raw_colors:
            self._driver_colors.update(raw_colors)

        # Update driver list in combo
        self._refresh_driver_list(drivers)

        # Process driver stints & wear
        for code, drv in drivers.items():
            comp_int = int(drv.get("tyre") or 0)
            comp_name = _INT_TO_NAME.get(comp_int, "UNKNOWN")
            tyre_life = int(float(drv.get("tyre_life") or 1))
            lap = int(drv.get("lap") or cur_lap)
            pos = int(drv.get("position") or 99)

            self._driver_pos[code] = pos
            prev_comp = self._current_compound.get(code)
            prev_life = self._current_tyre_life.get(code, 0)

            # Detect Pit stop / tyre change: compound changed OR tyre life dropped
            is_new_stint = False
            if prev_comp is None:
                is_new_stint = True
                self._stints[code] = []
                self._pit_counts[code] = 0
            elif prev_comp != comp_name or (tyre_life < prev_life and prev_life > 3):
                is_new_stint = True
                self._pit_counts[code] = self._pit_counts.get(code, 0) + 1

            if is_new_stint:
                self._stints[code].append({
                    "compound": comp_name,
                    "start_lap": max(0, lap - 1),
                    "end_lap": lap
                })
            else:
                if self._stints.get(code):
                    self._stints[code][-1]["end_lap"] = lap

            self._current_compound[code] = comp_name
            self._current_tyre_life[code] = tyre_life

        # Update Timeline Widget
        self.timeline_widget.update_data(
            self._stints,
            self._driver_pos,
            self._driver_colors,
            self._current_lap,
            self._total_laps
        )

        # Update Inspector Card
        self._update_inspector()

    def _refresh_driver_list(self, drivers: dict):
        incoming = sorted(drivers.keys())
        if incoming == self._known_drivers:
            return
        curr = self.inspector_combo.currentText()
        self.inspector_combo.blockSignals(True)
        self.inspector_combo.clear()
        self.inspector_combo.addItems(incoming)
        if curr in incoming:
            self.inspector_combo.setCurrentText(curr)
        elif incoming:
            self.inspector_combo.setCurrentIndex(0)
        self.inspector_combo.blockSignals(False)
        self._known_drivers = incoming

    def _on_inspector_driver_changed(self):
        self._update_inspector()

    def _update_inspector(self):
        code = self.inspector_combo.currentText()
        if not code or code not in self._current_compound:
            return

        comp = self._current_compound.get(code, "UNKNOWN")
        life = self._current_tyre_life.get(code, 1)
        pits = self._pit_counts.get(code, 0)
        stint_num = len(self._stints.get(code, [])) or 1

        bg_col = _COMPOUND_COLORS.get(comp, "#FFFFFF")
        fg_col = "#000000" if comp in ("MEDIUM", "HARD") else "#FFFFFF"

        self.badge_compound.setText(f"{comp}  •  {life} LAPS OLD")
        self.badge_compound.setStyleSheet(
            f"background-color: {bg_col}; color: {fg_col}; font-weight: bold; padding: 8px; border-radius: 6px;"
        )

        self.lbl_pit_count.setText(f"Pit Stops: {pits}  |  Current Stint: #{stint_num}")

        # Tyre grip calculation (100% -> degradation cliff based on compound)
        base_durability = {"SOFT": 22, "MEDIUM": 32, "HARD": 45, "INTERMEDIATE": 30, "WET": 40}.get(comp, 30)
        wear_pct = max(5, int(100 - (life / base_durability) * 90))
        self.grip_bar.setValue(wear_pct)

        bar_color = "#2ECC71" if wear_pct > 50 else ("#F39C12" if wear_pct > 25 else "#E74C3C")
        self.grip_bar.setStyleSheet(f"QProgressBar {{ background-color: #2D2D2D; border-radius: 6px; text-align: center; }} QProgressBar::chunk {{ background-color: {bar_color}; border-radius: 6px; }}")

        deg_rate = {"SOFT": 0.085, "MEDIUM": 0.055, "HARD": 0.038, "INTERMEDIATE": 0.070, "WET": 0.060}.get(comp, 0.050)
        self.lbl_deg_rate.setText(f"Degradation: ~{deg_rate:.3f} s/lap")

        cliff_lap = max(self._current_lap + 1, self._current_lap + (base_durability - life))
        self.lbl_cliff.setText(f"Projected Pit Window: Lap {max(1, cliff_lap - 3)} - {cliff_lap}")
