"""
Shared Qt look-and-feel for every PySide6 window in the app.

This module is a thin wrapper around ``src.services.ui_theme`` which is the
single source of truth for colours, typography, spacing and component sizing
across both the Arcade replay engine and the PySide6 GUI.

Usage::

    from src.gui.theme import apply_dark_theme
    apply_dark_theme(self)

The Arcade replay window uses its own ``Theme`` (see ``src/ui_components.py``);
both modules now read from the same ``src.services.ui_theme`` constants so the
whole application reads as one product.
"""

from src.services.ui_theme import (
    THEME, TYPOGRAPHY, SPACING, COMPONENT, BORDER_RADIUS,
    apply_dark_theme as _apply_dark_theme,
)


def apply_dark_theme(app_or_widget) -> None:
    """Apply the shared dark theme to a QApplication / QWidget / window.

    This delegates to ``src.services.ui_theme.apply_dark_theme`` which builds
    the QSS from the shared design tokens. Calling this on the QApplication
    applies the theme globally; calling it on an individual widget applies it
    to that widget and its children.
    """
    _apply_dark_theme(app_or_widget)


# Backward-compatible aliases for code that imports the old constants directly.
ACCENT = THEME.accent
BG = THEME.background
SURFACE = THEME.surface
SURFACE_ALT = THEME.surface_alt
BORDER = THEME.border_subtle
BORDER_LIGHT = THEME.border_light
TEXT = THEME.text_primary
TEXT_DIM = THEME.text_muted

# Expose the full theme object for code that wants direct access.
theme = THEME

__all__ = [
    "apply_dark_theme",
    "THEME",
    "TYPOGRAPHY",
    "SPACING",
    "COMPONENT",
    "BORDER_RADIUS",
    "ACCENT",
    "BG",
    "SURFACE",
    "SURFACE_ALT",
    "BORDER",
    "BORDER_LIGHT",
    "TEXT",
    "TEXT_DIM",
    "theme",
]
