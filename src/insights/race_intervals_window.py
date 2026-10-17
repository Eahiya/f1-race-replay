import sys
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QFrame, QTableWidget, QTableWidgetItem, QHeaderView
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QColor
from src.gui.pit_wall_window import PitWallWindow
from src.services.ui_theme import THEME

_BG = THEME.background
_PANEL_BG = THEME.surface
_TEXT_COLOR = THEME.text_primary
_SUB_COLOR = THEME.text_secondary

_COMPOUND_COLORS = {
    "SOFT": "#FF1801",
    "MEDIUM": "#FFD700",
    "HARD": "#FFFFFF",
    "INTERMEDIATE": "#39B54A",
    "WET": "#00A3E0",
    "UNKNOWN": "#888888",
}

_INT_TO_NAME = {
    0: "SOFT",
    1: "MEDIUM",
    2: "HARD",
    3: "INTERMEDIATE",
    4: "WET",
}


class RaceIntervalsWindow(PitWallWindow):
    """
    Broadcast-grade Live Race Intervals & Timing Tower Matrix.
    Displays live gaps to leader, intervals to car ahead, DRS threat radar,
    position deltas, and tyre compound status.
    """

    def __init__(self):
        self._initial_positions = {}  # code -> start position
        self._driver_colors = {}
        self._current_lap = 1
        self._total_laps = 57
        self._circuit_len = 5000.0

        super().__init__()
        self.setWindowTitle("F1 Pit Wall - Live Race Intervals & Timing Tower")
        self.resize(980, 720)

    def setup_ui(self):
        central_widget = QWidget()
        central_widget.setStyleSheet(f"background-color: {_BG}; color: {_TEXT_COLOR};")
        self.setCentralWidget(central_widget)

        root = QVBoxLayout(central_widget)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        # ── Header bar ──
        hdr = QFrame()
        hdr.setStyleSheet(f"background-color: {_PANEL_BG}; border-radius: 8px; padding: 6px;")
        hdr_layout = QHBoxLayout(hdr)
        hdr_layout.setContentsMargins(12, 6, 12, 6)

        title = QLabel("⏱️ TIMING TOWER & INTERVAL MATRIX")
        title.setFont(QFont("Segoe UI", 12, QFont.Bold))
        title.setStyleSheet("color: #FFFFFF;")
        hdr_layout.addWidget(title)

        hdr_layout.addStretch()

        self.session_info_lbl = QLabel("LAP 1 / 57  •  TRACK: GREEN")
        self.session_info_lbl.setFont(QFont("Segoe UI", 10, QFont.Bold))
        self.session_info_lbl.setStyleSheet("color: #2ECC71;")
        hdr_layout.addWidget(self.session_info_lbl)

        root.addWidget(hdr)

        # ── Table Widget ──
        self.table = QTableWidget()
        self.table.setColumnCount(8)
        self.table.setHorizontalHeaderLabels([
            "POS", "GAIN", "DRIVER", "GAP TO LEADER", "INTERVAL AHEAD", "TYRE", "SPEED", "STATUS"
        ])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionMode(QTableWidget.NoSelection)
        self.table.setStyleSheet(f"""
            QTableWidget {{
                background-color: {_PANEL_BG};
                alternate-background-color: #262626;
                color: {_TEXT_COLOR};
                border: 1px solid #333333;
                border-radius: 6px;
            }}
            QHeaderView::section {{
                background-color: #1A1A1A;
                color: #AAAAAA;
                font-weight: bold;
                font-size: 11px;
                padding: 6px;
                border: none;
                border-bottom: 2px solid #383838;
            }}
        """)
        root.addWidget(self.table)

    def on_telemetry_data(self, data):
        frame = data.get("frame")
        if not frame:
            return

        drivers = frame.get("drivers", {})
        if not drivers:
            return

        raw_colors = data.get("driver_colors", {})
        if raw_colors:
            self._driver_colors.update(raw_colors)

        session_data = data.get("session_data", {})
        self._current_lap = int(session_data.get("lap") or 1)
        self._total_laps = int(session_data.get("total_laps") or self._total_laps)
        self._circuit_len = float(data.get("circuit_length_m") or self._circuit_len or 5000.0)

        # Track status badge
        t_status = str(data.get("track_status") or "1")
        if t_status == "4":
            status_txt = "🟡 SAFETY CAR"
            status_col = "#F39C12"
        elif t_status == "6":
            status_txt = "🟡 VSC DEPLOYED"
            status_col = "#F1C40F"
        elif t_status == "2":
            status_txt = "🟡 YELLOW FLAG"
            status_col = "#F1C40F"
        elif t_status == "5":
            status_txt = "🔴 RED FLAG"
            status_col = "#E74C3C"
        else:
            status_txt = "🟢 GREEN TRACK"
            status_col = "#2ECC71"

        self.session_info_lbl.setText(f"LAP {self._current_lap} / {self._total_laps}  •  {status_txt}")
        self.session_info_lbl.setStyleSheet(f"color: {status_col}; font-weight: bold;")

        # Sort drivers by (lap, dist) descending to obtain accurate live leaderboard
        sorted_cars = sorted(
            drivers.items(),
            key=lambda item: (item[1].get("lap", 0), float(item[1].get("dist") or 0.0)),
            reverse=True
        )

        # Record starting grid on first encounter
        if not self._initial_positions and len(sorted_cars) >= 15:
            for idx, (code, _) in enumerate(sorted_cars):
                self._initial_positions[code] = idx + 1

        self.table.setRowCount(len(sorted_cars))
        leader_dist = float(sorted_cars[0][1].get("dist") or 0.0) if sorted_cars else 0.0
        leader_lap = int(sorted_cars[0][1].get("lap") or 1) if sorted_cars else 1

        prev_dist = leader_dist
        prev_lap = leader_lap

        for idx, (code, drv) in enumerate(sorted_cars):
            pos = idx + 1
            curr_dist = float(drv.get("dist") or 0.0)
            curr_lap = int(drv.get("lap") or 1)
            speed_kmh = float(drv.get("speed") or 0.0)
            avg_speed_mps = max(20.0, speed_kmh / 3.6)

            # 1. POS
            item_pos = QTableWidgetItem(f" P{pos:02d} ")
            item_pos.setTextAlignment(Qt.AlignCenter)
            item_pos.setFont(QFont("Segoe UI", 10, QFont.Bold))
            if pos == 1:
                item_pos.setForeground(QColor("#FFD700"))
            elif pos <= 3:
                item_pos.setForeground(QColor("#FFFFFF"))
            else:
                item_pos.setForeground(QColor(_SUB_COLOR))
            self.table.setItem(idx, 0, item_pos)

            # 2. GAIN (position delta from start)
            start_pos = self._initial_positions.get(code, pos)
            diff = start_pos - pos
            if diff > 0:
                gain_str = f"▲ +{diff}"
                gain_col = QColor("#2ECC71")
            elif diff < 0:
                gain_str = f"▼ {diff}"
                gain_col = QColor("#E74C3C")
            else:
                gain_str = "-  0"
                gain_col = QColor("#777777")

            item_gain = QTableWidgetItem(gain_str)
            item_gain.setTextAlignment(Qt.AlignCenter)
            item_gain.setFont(QFont("Segoe UI", 9, QFont.Bold))
            item_gain.setForeground(gain_col)
            self.table.setItem(idx, 1, item_gain)

            # 3. DRIVER
            item_drv = QTableWidgetItem(f" {code} ")
            item_drv.setTextAlignment(Qt.AlignVCenter | Qt.AlignLeft)
            item_drv.setFont(QFont("Segoe UI", 10, QFont.Bold))
            item_drv.setForeground(QColor(self._driver_colors.get(code, "#FFFFFF")))
            self.table.setItem(idx, 2, item_drv)

            # 4. GAP TO LEADER
            if pos == 1:
                gap_str = "LEADER"
                gap_col = QColor("#FFD700")
            elif leader_lap - curr_lap >= 2:
                gap_str = f"+{leader_lap - curr_lap} LAPS"
                gap_col = QColor("#888888")
            elif leader_lap - curr_lap == 1:
                gap_str = "+1 LAP"
                gap_col = QColor("#888888")
            else:
                dist_gap = max(0.0, leader_dist - curr_dist)
                t_gap = dist_gap / avg_speed_mps
                gap_str = f"+{t_gap:.3f}s"
                gap_col = QColor(_TEXT_COLOR)

            item_gap = QTableWidgetItem(gap_str)
            item_gap.setTextAlignment(Qt.AlignCenter)
            item_gap.setFont(QFont("Segoe UI", 10))
            item_gap.setForeground(gap_col)
            self.table.setItem(idx, 3, item_gap)

            # 5. INTERVAL AHEAD & DRS Threat
            if pos == 1:
                inv_str = "—"
                inv_col = QColor("#666666")
            else:
                delta_m = max(0.0, prev_dist - curr_dist)
                inv_s = delta_m / avg_speed_mps
                if inv_s <= 1.00:
                    inv_str = f"⚡ DRS +{inv_s:.3f}s"
                    inv_col = QColor("#F1C40F")  # Vibrant Yellow/Amber for DRS battle!
                else:
                    inv_str = f"+{inv_s:.3f}s"
                    inv_col = QColor("#CCCCCC")

            item_inv = QTableWidgetItem(inv_str)
            item_inv.setTextAlignment(Qt.AlignCenter)
            item_inv.setFont(QFont("Segoe UI", 10, QFont.Bold if "⚡" in inv_str else QFont.Normal))
            item_inv.setForeground(inv_col)
            self.table.setItem(idx, 4, item_inv)

            # 6. TYRE COMPOUND & AGE
            comp_int = int(drv.get("tyre") or 0)
            comp_name = _INT_TO_NAME.get(comp_int, "UNKNOWN")
            tyre_life = int(float(drv.get("tyre_life") or 1))
            short_comp = comp_name[0] if comp_name else "?"

            item_tyre = QTableWidgetItem(f" {short_comp} ({tyre_life}L) ")
            item_tyre.setTextAlignment(Qt.AlignCenter)
            item_tyre.setFont(QFont("Segoe UI", 9, QFont.Bold))
            item_tyre.setForeground(QColor(_COMPOUND_COLORS.get(comp_name, "#FFFFFF")))
            self.table.setItem(idx, 5, item_tyre)

            # 7. SPEED & GEAR
            gear = drv.get("gear", "-")
            item_spd = QTableWidgetItem(f"{speed_kmh:.0f} km/h (G{gear})")
            item_spd.setTextAlignment(Qt.AlignCenter)
            item_spd.setFont(QFont("Segoe UI", 9))
            item_spd.setForeground(QColor("#BBBBBB"))
            self.table.setItem(idx, 6, item_spd)

            # 8. STATUS
            drs_active = int(drv.get("drs") or 0) in (10, 12, 14, 1)
            if drs_active:
                st_txt = "DRS ON"
                st_col = QColor("#2ECC71")
            elif speed_kmh < 40.0 and curr_lap > 1:
                st_txt = "PIT LANE"
                st_col = QColor("#E67E22")
            else:
                st_txt = "TRACK"
                st_col = QColor("#3498DB")

            item_st = QTableWidgetItem(st_txt)
            item_st.setTextAlignment(Qt.AlignCenter)
            item_st.setFont(QFont("Segoe UI", 8, QFont.Bold))
            item_st.setForeground(st_col)
            self.table.setItem(idx, 7, item_st)

            prev_dist = curr_dist
            prev_lap = curr_lap
