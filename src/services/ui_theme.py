"""
Shared design system for F1 Race Replay (Update 9).

Provides a single source of truth for colors, typography, spacing, and
component styling used by both the Arcade replay engine (src/ui_components.py)
and the PySide6 desktop GUI (src/gui/theme.py, insight windows, etc.).

All values are intentional design decisions - changing them here propagates
consistently across the entire application.

Usage::

    from src.services.ui_theme import Theme, SPACING, TYPOGRAPHY
    from src.services.ui_theme import apply_pyside_theme, apply_arcade_theme
"""

from dataclasses import dataclass
from typing import Final

# ============================================================================
# COLOUR PALETTE — Premium F1 Pit Wall
# ============================================================================

# Surfaces
BACKGROUND: Final[str] = "#0B0D10"      # Application background
SURFACE: Final[str] = "#11151B"         # Primary panels
SURFACE_ALT: Final[str] = "#1B2029"     # Cards / raised surfaces
SURFACE_HOVER: Final[str] = "#202833"   # Hover / active surfaces
PANEL_HEADER: Final[str] = "#1A1F26"    # Panel header backgrounds

# Borders
BORDER_SUBTLE: Final[str] = "#2A323D"   # Panel boundaries
BORDER_LIGHT: Final[str] = "#3D4657"    # Lighter borders
BORDER_FOCUS: Final[str] = "#E10600"    # Focus / active borders (F1 red)

# Text
TEXT_PRIMARY: Final[str] = "#F2F5F8"    # Main content
TEXT_SECONDARY: Final[str] = "#A7B1AE"  # Supporting text
TEXT_MUTED: Final[str] = "#727E8C"      # Secondary metadata
TEXT_ON_ACCENT: Final[str] = "#FFFFFF"  # Text on accent colour

# Accents — use sparingly
ACCENT: Final[str] = "#E10600"          # Primary F1 red — primary actions, selected states
ACCENT_HOVER: Final[str] = "#F32116"    # Hovered accent

# Semantic colours
SUCCESS: Final[str] = "#28C77A"         # Positive / healthy states
WARNING: Final[str] = "#F4B942"         # Warnings / caution
DANGER: Final[str] = "#FF5A5F"          # Errors / critical states
INFO: Final[str] = "#56A8FF"            # Informational emphasis

# Track status colours (Arcade)
TRACK_GREEN: Final[str] = "#2C3037"
TRACK_YELLOW: Final[str] = "#685414"
TRACK_SC: Final[str] = "#6C4210"
TRACK_VSC: Final[str] = "#6C501E"
TRACK_RED: Final[str] = "#7A1C1C"

# DRS colours
DRS_ON: Final[str] = "#00E676"
DRS_OFF: Final[str] = "#4E5560"

# Driver compound colours (FastF1 official)
COMPOUND_SOFT: Final[str] = "#FF1801"
COMPOUND_MEDIUM: Final[str] = "#FFD700"
COMPOUND_HARD: Final[str] = "#FFFFFF"
COMPOUND_INTERMEDIATE: Final[str] = "#39B54A"
COMPOUND_WET: Final[str] = "#00A3E0"
COMPOUND_UNKNOWN: Final[str] = "#888888"

# ============================================================================
# TYPOGRAPHY
# ============================================================================

FONT_FAMILY: Final[str] = "Segoe UI, Calibri, Arial, sans-serif"
FONT_MONO: Final[str] = "Consolas, Monaco, monospace"

@dataclass(frozen=True)
class Typography:
    """Font sizes and weights for the design system."""
    display: int = 24      # Application / window titles
    heading: int = 18      # Section headings
    subheading: int = 14   # Panel headings
    body: int = 13         # Body text
    small: int = 11        # Labels, captions
    caption: int = 10      # Metadata, timestamps
    mono: int = 11         # Monospace values

    # Weights
    weight_normal: int = 400
    weight_medium: int = 500
    weight_semibold: int = 600
    weight_bold: int = 700


TYPOGRAPHY: Final[Typography] = Typography()

# ============================================================================
# SPACING
# ============================================================================

@dataclass(frozen=True)
class Spacing:
    """Spacing scale in pixels."""
    xs: int = 4      # Tight alignment
    sm: int = 8      # Related controls
    md: int = 12     # Compact groups
    lg: int = 16     # Normal panel spacing
    xl: int = 20     # Major section spacing
    xxl: int = 24    # Major layout separation
    xxxl: int = 32   # Section separation


SPACING: Final[Spacing] = Spacing()

# ============================================================================
# COMPONENT SIZES
# ============================================================================

@dataclass(frozen=True)
class ComponentSizes:
    """Standard component dimensions."""
    button_height: int = 32
    button_padding_h: int = 14
    button_padding_v: int = 6
    button_radius: int = 6
    pill_height: int = 24
    pill_radius: int = 12
    keycap_height: int = 17
    badge_width: int = 46
    badge_height: int = 19
    row_height: int = 25
    tooltip_padding: int = 8


COMPONENT: Final[ComponentSizes] = ComponentSizes()

# ============================================================================
# BORDER RADIUS
# ============================================================================

@dataclass(frozen=True)
class BorderRadius:
    """Border radius values."""
    sm: int = 4
    md: int = 6
    lg: int = 8
    pill: int = 9999  # Fully rounded


BORDER_RADIUS: Final[BorderRadius] = BorderRadius()

# ============================================================================
# THEME DATACLASS — single source of truth for both Arcade and PySide6
# ============================================================================

@dataclass(frozen=True)
class Theme:
    """Unified theme for both Arcade and PySide6 rendering."""

    # Colours
    background: str = BACKGROUND
    surface: str = SURFACE
    surface_alt: str = SURFACE_ALT
    surface_hover: str = SURFACE_HOVER
    panel_header: str = PANEL_HEADER

    border_subtle: str = BORDER_SUBTLE
    border_light: str = BORDER_LIGHT
    border_focus: str = BORDER_FOCUS

    text_primary: str = TEXT_PRIMARY
    text_secondary: str = TEXT_SECONDARY
    text_muted: str = TEXT_MUTED
    text_on_accent: str = TEXT_ON_ACCENT

    accent: str = ACCENT
    accent_hover: str = ACCENT_HOVER

    success: str = SUCCESS
    warning: str = WARNING
    danger: str = DANGER
    info: str = INFO

    # Track colours (Arcade)
    track_green: str = TRACK_GREEN
    track_yellow: str = TRACK_YELLOW
    track_sc: str = TRACK_SC
    track_vsc: str = TRACK_VSC
    track_red: str = TRACK_RED

    # DRS
    drs_on: str = DRS_ON
    drs_off: str = DRS_OFF

    # Compounds
    compound_soft: str = COMPOUND_SOFT
    compound_medium: str = COMPOUND_MEDIUM
    compound_hard: str = COMPOUND_HARD
    compound_intermediate: str = COMPOUND_INTERMEDIATE
    compound_wet: str = COMPOUND_WET
    compound_unknown: str = COMPOUND_UNKNOWN

    # Typography
    font_family: str = FONT_FAMILY
    font_mono: str = FONT_MONO
    typography: Typography = TYPOGRAPHY

    # Spacing
    spacing: Spacing = SPACING

    # Components
    component: ComponentSizes = COMPONENT

    # Border radius
    border_radius: BorderRadius = BORDER_RADIUS


THEME: Final[Theme] = Theme()

# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def hex_to_rgba(hex_color: str, alpha: int = 255) -> tuple:
    """Convert a hex colour string (#RRGGBB) to an (r, g, b, a) tuple."""
    hex_color = hex_color.lstrip("#")
    r = int(hex_color[0:2], 16)
    g = int(hex_color[2:4], 16)
    b = int(hex_color[4:6], 16)
    return (r, g, b, alpha)


def hex_to_rgb(hex_color: str) -> tuple:
    """Convert a hex colour string (#RRGGBB) to an (r, g, b) tuple."""
    hex_color = hex_color.lstrip("#")
    return (
        int(hex_color[0:2], 16),
        int(hex_color[2:4], 16),
        int(hex_color[4:6], 16),
    )


def rgba_to_hex(r: int, g: int, b: int, a: int = 255) -> str:
    """Convert RGBA values to a hex colour string (#RRGGBBAA or #RRGGBB)."""
    if a == 255:
        return f"#{r:02X}{g:02X}{b:02X}"
    return f"#{r:02X}{g:02X}{b:02X}{a:02X}"


def lighten(hex_color: str, factor: float) -> str:
    """Lighten a hex colour by the given factor (0.0 to 1.0)."""
    hex_color = hex_color.lstrip("#")
    r = int(hex_color[0:2], 16)
    g = int(hex_color[2:4], 16)
    b = int(hex_color[4:6], 16)
    r = int(r + (255 - r) * factor)
    g = int(g + (255 - g) * factor)
    b = int(b + (255 - b) * factor)
    return f"#{r:02X}{g:02X}{b:02X}"


def darken(hex_color: str, factor: float) -> str:
    """Darken a hex colour by the given factor (0.0 to 1.0)."""
    hex_color = hex_color.lstrip("#")
    r = int(hex_color[0:2], 16)
    g = int(hex_color[2:4], 16)
    b = int(hex_color[4:6], 16)
    r = int(r * (1.0 - factor))
    g = int(g * (1.0 - factor))
    b = int(b * (1.0 - factor))
    return f"#{r:02X}{g:02X}{b:02X}"


# ============================================================================
# PYSIDE6 THEME APPLICATION
# ============================================================================

_DARK_QSS_TEMPLATE = """
/* ---------- base ---------- */
QWidget {{
    background-color: {bg};
    color: {text_primary};
    font-family: "{font_family}";
    font-size: {body_size}px;
    font-weight: {weight_normal};
}}
QMainWindow, QDialog {{ background-color: {bg}; }}
QLabel {{ background: transparent; color: {text_primary}; }}
QLabel#subtitle, QLabel.subtle {{ color: {text_muted}; }}
QLabel#title {{ font-size: {display_size}px; font-weight: {weight_bold}; }}

/* ---------- buttons ---------- */
QPushButton {{
    background-color: {surface_alt};
    border: 1px solid {border_light};
    border-radius: {border_radius_md}px;
    padding: {button_padding_v}px {button_padding_h}px;
    color: {text_primary};
    font-size: {body_size}px;
}}
QPushButton:hover {{ background-color: {surface_hover}; border-color: {border_focus}; }}
QPushButton:pressed {{ background-color: {surface}; }}
QPushButton:disabled {{ color: #5A6270; border-color: {border_subtle}; background-color: {surface}; }}
QPushButton#accent {{
    background-color: {accent};
    border: 1px solid {accent};
    color: {text_on_accent};
    font-weight: {weight_semibold};
}}
QPushButton#accent:hover {{ background-color: {accent_hover}; }}
QPushButton#flat {{
    background: transparent;
    border: 1px solid {border_subtle};
}}
QPushButton#insight {{
    background-color: {surface};
    border: 1px solid {border_subtle};
    border-left: 3px solid {accent};
    border-radius: {border_radius_md}px;
    text-align: left;
    padding: 8px 10px;
}}
QPushButton#insight:hover {{
    background-color: {surface_hover};
    border: 1px solid {border_light};
    border-left: 3px solid {accent_hover};
}}
QPushButton#insight:pressed {{ background-color: {surface}; }}

/* ---------- form controls ---------- */
QComboBox, QSpinBox, QLineEdit, QPlainTextEdit {{
    background-color: {surface};
    border: 1px solid {border_light};
    border-radius: {border_radius_md}px;
    padding: 5px 10px;
    selection-background-color: {accent};
    selection-color: #ffffff;
}}
QComboBox:hover, QLineEdit:hover {{ border-color: #4C576B; }}
QComboBox::drop-down {{ subcontrol-origin: padding; subcontrol-position: center right;
    border: none; width: 24px; }}
QComboBox::down-arrow {{ image: none; width: 0; height: 0;
    border-left: 5px solid transparent; border-right: 5px solid transparent;
    border-top: 6px solid {text_muted}; margin-right: 8px; }}
QComboBox QAbstractItemView {{
    background-color: {surface};
    border: 1px solid {border_light};
    selection-background-color: {accent};
    selection-color: #ffffff;
    outline: none;
}}

/* ---------- tree / tables ---------- */
QTreeWidget, QTableWidget {{
    background-color: #10131A;
    alternate-background-color: #141822;
    border: 1px solid {border_subtle};
    border-radius: {border_radius_md}px;
    gridline-color: {border_subtle};
    outline: none;
}}
QTreeWidget::item, QTableWidget::item {{ padding: 7px 6px; border-radius: 4px; }}
QTreeWidget::item:hover, QTableWidget::item:hover {{ background-color: {surface_hover}; }}
QTreeWidget::item:selected, QTableWidget::item:selected {{ background-color: {accent}; color: #ffffff; }}
QHeaderView::section {{
    background-color: {surface};
    color: {text_muted};
    border: none;
    border-bottom: 1px solid {border_subtle};
    padding: 7px 6px;
    font-weight: {weight_semibold};
}}

/* ---------- scrollbars ---------- */
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{
    background: {border_light}; border-radius: 5px; min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{ background: #55607A; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{
    background: {border_light}; border-radius: 5px; min-width: 30px;
}}
QScrollBar::handle:horizontal:hover {{ background: #55607A; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}

/* ---------- misc ---------- */
QToolTip {{
    background-color: {surface_alt};
    color: {text_primary};
    border: 1px solid {border_light};
    padding: 4px 8px;
}}
QProgressBar {{
    background-color: {surface};
    border: 1px solid {border_subtle};
    border-radius: {border_radius_md}px;
    text-align: center;
    color: {text_primary};
}}
QProgressBar::chunk {{ background-color: {accent}; border-radius: 5px; }}
QStatusBar {{ background-color: {surface}; color: {text_muted}; }}
QMenu {{ background-color: {surface}; border: 1px solid {border_light}; padding: 4px; }}
QMenu::item {{ padding: 6px 20px; border-radius: 4px; }}
QMenu::item:selected {{ background-color: {accent}; color: #ffffff; }}
QTabWidget::pane {{ border: 1px solid {border_subtle}; border-radius: {border_radius_md}px; top: -1px; }}
QTabBar::tab {{
    background: {surface}; border: 1px solid {border_subtle}; border-bottom: none;
    padding: 6px 14px; margin-right: 2px;
    border-top-left-radius: {border_radius_md}px; border-top-right-radius: {border_radius_md}px;
    color: {text_muted};
}}
QTabBar::tab:selected {{ background: {surface_alt}; color: {text_primary}; }}
"""


def apply_dark_theme(app_or_widget) -> None:
    """Apply the shared dark theme to a QApplication / QWidget / window.

    This is the single entry point for theming all PySide6 windows.
    Calling this once on the QApplication applies the theme globally.
    Calling it on an individual widget applies it to that widget and its children.
    """
    qss = _DARK_QSS_TEMPLATE.format(
        bg=THEME.background,
        surface=THEME.surface,
        surface_alt=THEME.surface_alt,
        surface_hover=THEME.surface_hover,
        panel_header=THEME.panel_header,
        border_subtle=THEME.border_subtle,
        border_light=THEME.border_light,
        border_focus=THEME.border_focus,
        text_primary=THEME.text_primary,
        text_secondary=THEME.text_secondary,
        text_muted=THEME.text_muted,
        text_on_accent=THEME.text_on_accent,
        accent=THEME.accent,
        accent_hover=THEME.accent_hover,
        success=THEME.success,
        warning=THEME.warning,
        danger=THEME.danger,
        info=THEME.info,
        track_green=THEME.track_green,
        track_yellow=THEME.track_yellow,
        track_sc=THEME.track_sc,
        track_vsc=THEME.track_vsc,
        track_red=THEME.track_red,
        drs_on=THEME.drs_on,
        drs_off=THEME.drs_off,
        compound_soft=THEME.compound_soft,
        compound_medium=THEME.compound_medium,
        compound_hard=THEME.compound_hard,
        compound_intermediate=THEME.compound_intermediate,
        compound_wet=THEME.compound_wet,
        compound_unknown=THEME.compound_unknown,
        font_family=THEME.font_family.replace(", ", ","),  # Qt uses comma-separated fonts
        font_mono=THEME.font_mono.replace(", ", ","),
        display_size=TYPOGRAPHY.display,
        heading_size=TYPOGRAPHY.heading,
        subheading_size=TYPOGRAPHY.subheading,
        body_size=TYPOGRAPHY.body,
        small_size=TYPOGRAPHY.small,
        caption_size=TYPOGRAPHY.caption,
        mono_size=TYPOGRAPHY.mono,
        weight_normal=TYPOGRAPHY.weight_normal,
        weight_medium=TYPOGRAPHY.weight_medium,
        weight_semibold=TYPOGRAPHY.weight_semibold,
        weight_bold=TYPOGRAPHY.weight_bold,
        xs=SPACING.xs,
        sm=SPACING.sm,
        md=SPACING.md,
        lg=SPACING.lg,
        xl=SPACING.xl,
        xxl=SPACING.xxl,
        xxxl=SPACING.xxxl,
        button_height=COMPONENT.button_height,
        button_padding_h=COMPONENT.button_padding_h,
        button_padding_v=COMPONENT.button_padding_v,
        button_radius=COMPONENT.button_radius,
        pill_height=COMPONENT.pill_height,
        pill_radius=COMPONENT.pill_radius,
        keycap_height=COMPONENT.keycap_height,
        badge_width=COMPONENT.badge_width,
        badge_height=COMPONENT.badge_height,
        row_height=COMPONENT.row_height,
        tooltip_padding=COMPONENT.tooltip_padding,
        border_radius_sm=BORDER_RADIUS.sm,
        border_radius_md=BORDER_RADIUS.md,
        border_radius_lg=BORDER_RADIUS.lg,
        border_radius_pill=BORDER_RADIUS.pill,
    )
    app_or_widget.setStyleSheet(qss)


# ============================================================================
# ARCADE THEME APPLICATION
# ============================================================================

def apply_arcade_theme(theme_obj) -> None:
    """Apply the shared design system values to an Arcade Theme-like object.

    This updates the Arcade `Theme` class in ``src.ui_components`` in place
    so that Arcade rendering uses the same colours as the PySide6 GUI.
    """
    theme_obj.TRACK_EDGE = hex_to_rgba(THEME.track_edge if hasattr(THEME, 'track_edge') else "#E8EBEE")
    # Arcade Theme class uses RGB tuples; convert hex colours
    theme_obj.TRACK_GREEN = hex_to_rgba(THEME.track_green)
    theme_obj.TRACK_YELLOW = hex_to_rgba(THEME.track_yellow)
    theme_obj.TRACK_SC = hex_to_rgba(THEME.track_sc)
    theme_obj.TRACK_VSC = hex_to_rgba(THEME.track_vsc)
    theme_obj.TRACK_RED = hex_to_rgba(THEME.track_red)
    theme_obj.DRS_ON = hex_to_rgba(THEME.drs_on)
    theme_obj.DRS_OFF = hex_to_rgba(THEME.drs_off)
    theme_obj.TEXT = hex_to_rgba(THEME.text_primary)
    theme_obj.TEXT_DIM = hex_to_rgba(THEME.text_muted)
    theme_obj.TEXT_FAINT = hex_to_rgba("#585858")  # Keep existing faint
    theme_obj.ACCENT = hex_to_rgba(THEME.accent)
    theme_obj.GREEN = hex_to_rgba(THEME.success)
    theme_obj.AMBER = hex_to_rgba(THEME.warning)
    theme_obj.RED = hex_to_rgba(THEME.danger)
    theme_obj.BLUE = hex_to_rgba(THEME.info)
    theme_obj.PANEL_FILL = hex_to_rgba(THEME.surface)
    theme_obj.PANEL_BORDER = hex_to_rgba(THEME.border_subtle)
    theme_obj.PANEL_HEADER = hex_to_rgba(THEME.panel_header)
    theme_obj.ROW_ALT = hex_to_rgba("#FFFFFF1C")
    theme_obj.ROW_HOVER = hex_to_rgba("#FFFFFF1E")
    theme_obj.ROW_SELECTED = hex_to_rgba(THEME.accent + "37")  # ~55 alpha
    theme_obj.CHIP_FILL = hex_to_rgba("#242830")
    theme_obj.CHIP_BORDER = hex_to_rgba(THEME.border_subtle)
    theme_obj.FONT = (THEME.font_family.split(",")[0].strip(),)