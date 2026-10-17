import sys
import math
from collections import deque

import matplotlib
matplotlib.use("QtAgg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas

from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QFrame, QGridLayout, QProgressBar
)
from PySide6.QtCore import Qt, QPointF
from PySide6.QtGui import QFont, QColor, QPainter, QPen, QBrush, QPolygonF
from src.gui.pit_wall_window import PitWallWindow
from src.services.ui_theme import THEME

_BG = THEME.background
_PANEL_BG = THEME.surface
_CARD_BG = "#2D2D2D"
_TEXT_COLOR = THEME.text_primary
_SUB_COLOR = THEME.text_secondary


class _WindCompassWidget(QWidget):
    """Custom paint widget rendering an F1 pit wall wind direction compass."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.wind_dir_deg = 0.0
        self.wind_speed_mps = 0.0
        self.setMinimumSize(180, 180)

    def set_wind(self, deg: float, speed_mps: float):
        self.wind_dir_deg = deg
        self.wind_speed_mps = speed_mps
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.TextAntialiasing)

        w, h = self.width(), self.height()
        cx, cy = w / 2, h / 2
        radius = min(w, h) / 2 - 16

        # Outer ring
        painter.setPen(QPen(QColor("#3A3A3A"), 3))
        painter.setBrush(QBrush(QColor("#202020")))
        painter.drawEllipse(QPointF(cx, cy), radius, radius)

        # Cardinal markers
        painter.setFont(QFont("Segoe UI", 8, QFont.Bold))
        cardinals = [("N", 0), ("E", 90), ("S", 180), ("W", 270)]
        for label, angle_deg in cardinals:
            rad = math.radians(angle_deg - 90)
            tx = cx + (radius - 12) * math.cos(rad)
            ty = cy + (radius - 12) * math.sin(rad)
            painter.setPen(QColor("#E10600" if label == "N" else "#888888"))
            painter.drawText(int(tx) - 10, int(ty) - 8, 20, 16, Qt.AlignCenter, label)

        # Wind needle
        rad_wind = math.radians(self.wind_dir_deg - 90)
        nx = cx + (radius - 22) * math.cos(rad_wind)
        ny = cy + (radius - 22) * math.sin(rad_wind)

        # Arrow head
        painter.setPen(QPen(QColor("#3498DB"), 2.5))
        painter.drawLine(int(cx), int(cy), int(nx), int(ny))

        painter.setBrush(QBrush(QColor("#3498DB")))
        painter.drawEllipse(QPointF(nx, ny), 4, 4)

        # Center dot
        painter.setBrush(QBrush(QColor("#FFFFFF")))
        painter.drawEllipse(QPointF(cx, cy), 3, 3)

        # Center speed text
        painter.setFont(QFont("Segoe UI", 9, QFont.Bold))
        painter.setPen(QColor("#FFFFFF"))
        spd_kmh = self.wind_speed_mps * 3.6
        painter.drawText(self.rect(), Qt.AlignCenter, f"\n\n\n{spd_kmh:.1f}\nkm/h")


class WeatherRadarWindow(PitWallWindow):
    """
    Live Weather & Track Meteorology Station.
    Displays track & air temperature, dynamic wind compass, humidity,
    rain radar, and rolling temperature history chart.
    """

    def __init__(self):
        self._history_t = deque(maxlen=200)
        self._history_track = deque(maxlen=200)
        self._history_air = deque(maxlen=200)

        super().__init__()
        self.setWindowTitle("F1 Pit Wall - Weather & Track Conditions")
        self.resize(950, 680)

    def setup_ui(self):
        central_widget = QWidget()
        central_widget.setStyleSheet(f"background-color: {_BG}; color: {_TEXT_COLOR};")
        self.setCentralWidget(central_widget)

        root = QVBoxLayout(central_widget)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        # ── Header ──
        hdr = QFrame()
        hdr.setStyleSheet(f"background-color: {_PANEL_BG}; border-radius: 8px; padding: 6px;")
        hdr_layout = QHBoxLayout(hdr)
        hdr_layout.setContentsMargins(12, 6, 12, 6)

        title = QLabel("🌦️ TRACK METEOROLOGY & WEATHER RADAR")
        title.setFont(QFont("Segoe UI", 12, QFont.Bold))
        title.setStyleSheet("color: #FFFFFF;")
        hdr_layout.addWidget(title)

        hdr_layout.addStretch()

        self.rain_state_badge = QLabel("TRACK CONDITION: DRY")
        self.rain_state_badge.setFont(QFont("Segoe UI", 10, QFont.Bold))
        self.rain_state_badge.setStyleSheet("background-color: #2ECC71; color: black; padding: 4px 10px; border-radius: 4px;")
        hdr_layout.addWidget(self.rain_state_badge)

        root.addWidget(hdr)

        # ── Top Row Gauges: Temp Cards + Wind Compass + Humidity ──
        gauges_row = QHBoxLayout()
        gauges_row.setSpacing(10)

        # Track Temp Card
        card_track = self._create_stat_card("TRACK TEMPERATURE", "--- °C", "#E74C3C")
        self.track_temp_lbl = card_track.findChild(QLabel, "val")
        gauges_row.addWidget(card_track, 1)

        # Air Temp Card
        card_air = self._create_stat_card("AIR TEMPERATURE", "--- °C", "#3498DB")
        self.air_temp_lbl = card_air.findChild(QLabel, "val")
        gauges_row.addWidget(card_air, 1)

        # Humidity Card
        card_hum = self._create_stat_card("HUMIDITY", "--- %", "#9B59B6")
        self.humidity_lbl = card_hum.findChild(QLabel, "val")
        gauges_row.addWidget(card_hum, 1)

        # Wind Compass Card
        wind_card = QFrame()
        wind_card.setStyleSheet(f"background-color: {_PANEL_BG}; border: 1px solid #333333; border-radius: 8px; padding: 8px;")
        wind_layout = QVBoxLayout(wind_card)
        wind_layout.setContentsMargins(6, 6, 6, 6)
        wind_layout.setAlignment(Qt.AlignCenter)

        w_title = QLabel("WIND DIRECTION & SPEED")
        w_title.setFont(QFont("Segoe UI", 8, QFont.Bold))
        w_title.setStyleSheet("color: #888888;")
        wind_layout.addWidget(w_title, alignment=Qt.AlignCenter)

        self.compass = _WindCompassWidget()
        wind_layout.addWidget(self.compass, alignment=Qt.AlignCenter)

        self.wind_info_lbl = QLabel("Direction: 0° | Speed: 0.0 m/s")
        self.wind_info_lbl.setFont(QFont("Segoe UI", 8))
        self.wind_info_lbl.setStyleSheet("color: #CCCCCC;")
        wind_layout.addWidget(self.wind_info_lbl, alignment=Qt.AlignCenter)

        gauges_row.addWidget(wind_card, 1)

        root.addLayout(gauges_row)

        # ── Bottom Row: Rolling Temperature Graph ──
        graph_card = QFrame()
        graph_card.setStyleSheet(f"background-color: {_PANEL_BG}; border: 1px solid #333333; border-radius: 8px; padding: 6px;")
        g_layout = QVBoxLayout(graph_card)
        g_layout.setContentsMargins(6, 6, 6, 6)

        g_title = QLabel("TEMPERATURE EVOLUTION OVER SESSION")
        g_title.setFont(QFont("Segoe UI", 9, QFont.Bold))
        g_title.setStyleSheet("color: #888888;")
        g_layout.addWidget(g_title)

        self._fig, self._ax = plt.subplots(figsize=(9, 3.8), facecolor=_PANEL_BG)
        self._fig.subplots_adjust(top=0.92, bottom=0.15, left=0.08, right=0.96)
        self._line_track, = self._ax.plot([], [], color="#E74C3C", linewidth=2.0, label="Track Temp (°C)")
        self._line_air, = self._ax.plot([], [], color="#3498DB", linewidth=2.0, label="Air Temp (°C)")
        self._ax.set_facecolor(_PANEL_BG)
        self._ax.set_ylabel("Temperature (°C)", color=_TEXT_COLOR, fontsize=9)
        self._ax.set_xlabel("Session Elapsed (s)", color=_TEXT_COLOR, fontsize=9)
        self._ax.tick_params(colors=_SUB_COLOR)
        self._ax.grid(True, color="#383838", linestyle=":", alpha=0.6)
        for spine in self._ax.spines.values():
            spine.set_edgecolor("#444444")
        self._ax.legend(loc="upper right", facecolor=_PANEL_BG, edgecolor="#444444", labelcolor=_TEXT_COLOR, fontsize=8)

        self._canvas = FigureCanvas(self._fig)
        g_layout.addWidget(self._canvas)

        root.addWidget(graph_card)

    def _create_stat_card(self, title: str, default_val: str, accent_color: str):
        card = QFrame()
        card.setStyleSheet(f"background-color: {_PANEL_BG}; border: 1px solid #333333; border-radius: 8px; padding: 12px;")
        layout = QVBoxLayout(card)
        layout.setAlignment(Qt.AlignCenter)
        layout.setSpacing(6)

        lbl = QLabel(title)
        lbl.setFont(QFont("Segoe UI", 9, QFont.Bold))
        lbl.setStyleSheet("color: #888888;")
        layout.addWidget(lbl, alignment=Qt.AlignCenter)

        val = QLabel(default_val)
        val.setObjectName("val")
        val.setFont(QFont("Segoe UI", 20, QFont.Bold))
        val.setStyleSheet(f"color: {accent_color};")
        layout.addWidget(val, alignment=Qt.AlignCenter)

        return card

    def on_telemetry_data(self, data):
        frame = data.get("frame")
        if not frame:
            return

        session_t = float(frame.get("t") or 0.0)
        weather = frame.get("weather", {})
        if not weather:
            return

        t_track = weather.get("track_temp")
        t_air = weather.get("air_temp")
        hum = weather.get("humidity")
        w_speed = weather.get("wind_speed")
        w_dir = weather.get("wind_direction")
        rain_state = weather.get("rain_state", "DRY")

        if t_track is not None:
            self.track_temp_lbl.setText(f"{float(t_track):.1f} °C")
        if t_air is not None:
            self.air_temp_lbl.setText(f"{float(t_air):.1f} °C")
        if hum is not None:
            self.humidity_lbl.setText(f"{float(hum):.0f} %")

        # Compass update
        deg = float(w_dir or 0.0)
        speed = float(w_speed or 0.0)
        self.compass.set_wind(deg, speed)
        self.wind_info_lbl.setText(f"Direction: {deg:.0f}° | Speed: {speed:.1f} m/s")

        # Rain badge
        if rain_state == "RAINING":
            self.rain_state_badge.setText("⚠️ RAIN DETECTED: WET")
            self.rain_state_badge.setStyleSheet("background-color: #3498DB; color: white; padding: 4px 10px; border-radius: 4px; font-weight: bold;")
        else:
            self.rain_state_badge.setText("TRACK CONDITION: DRY")
            self.rain_state_badge.setStyleSheet("background-color: #2ECC71; color: black; padding: 4px 10px; border-radius: 4px; font-weight: bold;")

        # History plot update
        if t_track is not None and t_air is not None:
            self._history_t.append(session_t)
            self._history_track.append(float(t_track))
            self._history_air.append(float(t_air))

            self._line_track.set_data(list(self._history_t), list(self._history_track))
            self._line_air.set_data(list(self._history_t), list(self._history_air))

            min_t = self._history_t[0]
            max_t = max(self._history_t[-1], min_t + 10)
            self._ax.set_xlim(min_t, max_t)

            all_temps = list(self._history_track) + list(self._history_air)
            min_y = min(all_temps) - 3.0
            max_y = max(all_temps) + 3.0
            self._ax.set_ylim(min_y, max_y)

            self._canvas.draw_idle()
