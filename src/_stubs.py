import os
import sys
import io
import json
import time
import logging
import pickle
from pathlib import Path

APP_DIR = Path("src")
FONT_DIR = APP_DIR / "assets" / "fonts"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
_log = logging.getLogger("replay_check")
_log.setLevel(logging.INFO)
_handler = logging.StreamHandler(sys.stdout)
_handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
_log.addHandler(_handler)

# ---------------------------------------------------------------------------
# Tiny, self-contained arcade stubs so the window code can be imported and
# inspected without a display. They record every draw call into a strict
# per-instrument list so we can assert exact pixel coverage.
# ---------------------------------------------------------------------------
_calls = []
_solids = {}
_lines = {}
_tris = {}
_tiles = {}
_indexed_pieces = {}
_centroids = {}

_REGISTERED = {}

def _reg(obj, name):
    _REGISTERED[name] = obj
    return obj

class Color:
    def __init__(self, r, g, b, a=255, inline=False):
        self.rgb = (int(r) & 0xFF, int(g) & 0xFF, int(b) & 0xFF)
        self.a = int(a) & 0xFF
        self._inline = bool(inline)
    def __getitem__(self, idx):
        return (self.rgb + (self.a,))[idx]
    def __iter__(self):
        return iter(self.rgb + (self.a,))
    def __eq__(self, other):
        if isinstance(other, tuple):
            return (self.rgb + (self.a,)) == other
        if isinstance(other, Color):
            return (self.rgb, self.a) == (other.rgb, other.a)
        return NotImplemented
    def __hash__(self):
        return hash((self.rgb, self.a))
    def __repr__(self):
        return f"Color{self.rgb+(self.a,)}"

def _as_color(c):
    if isinstance(c, Color):
        return c
    if isinstance(c, (list, tuple)):
        a = c[3] if len(c) >= 4 else 255
        return Color(c[0], c[1] if len(c) > 1 else c[0], c[2] if len(c) > 2 else c[0], a)
    raise TypeError(f"bad color {c!r}")

class _TileSet:
    def __init__(self):
        self.anchor_x = 0
        self.anchor_y = 0
    def draw_sized(self, cx, cy, w, h, color=None):
        d = dict(cx=float(cx), cy=float(cy), w=float(w), h=float(h), color=_as_color(color) if color is not None else None)
        _tiles.setdefault("sized", []).append(d)
        _centroids.setdefault("sized", []).append((float(cx), float(cy)))

class Texture:
    def __init__(self, path, anchor_x=0, anchor_y=0):
        self.path = str(path)
        self.anchor_x = anchor_x
        self.anchor_y = anchor_y

class Sprite:
    def __init__(self, path, center_x=0.0, center_y=0.0, scale=1.0, color=None, alpha=255):
        self.texture = Texture(path)
        self.center_x = float(center_x)
        self.center_y = float(center_y)
        self.scale = float(scale)
        self._color = _as_color(color) if color is not None else None

    def draw(self):
        d = dict(
            path=self.texture.path,
            cx=self.center_x,
            cy=self.center_y,
            scale=self.scale,
            color=self._color,
        )
        _tiles["sprite"] = _tiles.get("sprite", [])
        _tiles["sprite"].append(d)

    def set_position(self, x, y):
        self.center_x = float(x)
        self.center_y = float(y)

def draw_texture_rectangle(x, y, w, h, texture, angle=0.0, color=None, alpha=255):
    _tiles.setdefault("rect", []).append(dict(x=x, y=y, w=w, h=h, path=texture.path, angle=angle,
                                               color=_as_color(color) if color is not None else None, alpha=alpha))
    _centroids.setdefault("rect", []).append((x + w/2.0, y + h/2.0))

def draw_rectangle_outline(x, y, w, h, color, border_width=1):
    _lines.setdefault("outline", []).append(dict(x=x, y=y, w=w, h=h, color=_as_color(color), bw=border_width))

def draw_rectangle_filled(x, y, w, h, color):
    color = _as_color(color)
    x0, y0 = float(x), float(y)
    w, h = float(w), float(h)
    _solids.setdefault("filled", []).append(dict(x=x0, y=y0, w=w, h=h, color=color))
    _centroids.setdefault("filled", []).append((x0 + w/2.0, y0 + h/2.0))

def draw_xyfilled_rectangle_outline(x, y, width, height, color, border_width=1, tilt_angle=0.0, tilt_origin_x=0.0, tilt_origin_y=0.0):
    color = _as_color(color)
    x0, y0 = float(x), float(y)
    w, h = float(width), float(height)
    _lines.setdefault("xyoutline", []).append(dict(x=x0, y=y0, w=w, h=h, color=color, bw=border_width,
                                                    tilt=(float(tilt_angle), float(tilt_origin_x), float(tilt_origin_y))))
    _centroids.setdefault("xyoutline", []).append((x0 + w/2.0, y0 + h/2.0))

def draw_circle_filled(x, y, radius, color):
    color = _as_color(color)
    _solids.setdefault("circle", []).append(dict(x=float(x), y=float(y), r=float(radius), color=color))
    _centroids.setdefault("circle", []).append((float(x), float(y)))

def draw_circle_outline(x, y, radius, color, border_width=1):
    color = _as_color(color)
    _lines.setdefault("circle_outline", []).append(dict(x=float(x), y=float(y), r=float(radius), color=color, bw=border_width))

def draw_ellipse_filled(x, y, width, height, color):
    color = _as_color(color)
    width2, height2 = float(width)/2.0, float(height)/2.0
    _solids.setdefault("ellipse", []).append(dict(x=float(x), y=float(y), rx=width2, ry=height2, color=color))
    _centroids.setdefault("ellipse", []).append((float(x), float(y)))

def draw_ellipse_outline(x, y, width, height, color, border_width=1):
    color = _as_color(color)
    width2, height2 = float(width)/2.0, float(height)/2.0
    _lines.setdefault("ellipse_outline", []).append(dict(x=float(x), y=float(y), rx=width2, ry=height2, color=color, bw=border_width))

def draw_line(x, y, x2, y2, color, line_width=1):
    color = _as_color(color)
    _lines.setdefault("line", []).append(dict(x0=float(x), y0=float(y), x1=float(x2), y1=float(y2), color=color, lw=float(line_width)))

def draw_text(text, x, y, color, font_size=12, anchor_x="left", anchor_y="baseline", font_name="Arial", bold=False):
    color = _as_color(color)
    d = dict(text=str(text), x=float(x), y=float(y), color=color, fs=int(font_size),
             ax=str(anchor_x), ay=str(anchor_y), font=str(font_name), bold=bool(bold))
    _index = _index_of(d)
    if _index is None:
        _index = len(_indexeds[LKEY(d)])
        _indexeds[LKEY(d)] = _index
    _indexeds[LKEY(d)]["entries"].append(d)
    _indexeds[LKEY(d)]["centroid"] = (float(x), float(y))

def draw_text_2d(text, x, y, color, font_size=12, anchor_x="left", anchor_y="baseline", font_name="Arial", bold=False):
    draw_text(text, x, y, color, font_size, anchor_x, anchor_y, font_name, bold)

def draw_text_centered(text, x, y, color, font_size=12, font_name="Arial", bold=False):
    draw_text(text, x, y, color, font_size, "center", "center", font_name, bold)

def draw_triangle_filled(x1, y1, x2, y2, x3, y3, color):
    color = _as_color(color)
    _tris.setdefault("filled", []).append(dict(vs=[(float(x1), float(y1)), (float(x2), float(y2)), (float(x3), float(y3))], color=color))

def draw_triangle_outline(x1, y1, x2, y2, x3, y3, color, border_width=1):
    color = _as_color(color)
    _tris.setdefault("outline", []).append(dict(vs=[(float(x1), float(y1)), (float(x2), float(y2)), (float(x3), float(y3))], color=color, bw=float(border_width)))

def draw_polygon_filled(points, color):
    color = _as_color(color)
    _solids.setdefault("polygon", []).append(dict(pts=[(float(p[0]), float(p[1])) for p in points], color=color))

def draw_mesh(vertex_list, *args, **kwargs):
    _solids.setdefault("mesh", []).append(dict(vl=vertex_list))

def draw_bitmap_strip(file_string, strip_id, image_ids, start_x, start_y, width, height, color=None, outside_color=None):
    _tiles.setdefault("strip", []).append(dict(file=file_string, id=strip_id, image_ids=image_ids,
                                               x=start_x, y=start_y, w=width, h=height,
                                               color=_as_color(color) if color is not None else None,
                                               outside=_as_color(outside_color) if outside_color is not None else None))

def draw_lrt 직업이 아니고... just stubbing the texture strip:
    draw_bitmap_strip(...)

def draw_texture_rectangle_outline(x, y, width, height, texture, color, border_width=1.0, tilt_angle=0.0, tilt_origin_x=0.0, tilt_origin_y=0.0):
    _lines.setdefault("rect_outline", []).append(dict(x=float(x), y=float(y), w=float(width), h=float(height),
                                                       path=texture.path, color=_as_color(color), bw=float(border_width),
                                                       tilt=(float(tilt_angle), float(tilt_origin_x), float(tilt_origin_y))))

def draw_texture_sequence(sequence, start_x, start_y, scale=1.0, color=None):
    _tiles.setdefault("seq", []).append(dict(seq=str(sequence), x=start_x, y=start_y, scale=scale,
                                              color=_as_color(color) if color is not None else None))

def draw_ellipse_outline_strip(...):
    draw_ellipse_outline(...)

LKEY = lambda d: (d.get("text"), d.get("x"), d.get("y"), tuple(d.get("color", (0,0,0,255))), d.get("fs"), d.get("font"), d.get("bold"))

_indexeds = {}

def _index_of(d):
    k = LKEY(d)
    rec = _indexeds.get(k)
    if rec is None:
        return None
    return rec["next_index"]

def draw_texture_sequence_strip(file_string, strip_id, image_ids, start_x, start_y, width, height, color=None, outside_color=None):
    draw_bitmap_strip(file_string, strip_id, image_ids, start_x, start_y, width, height, color, outside_color)

def draw_texture_rectangle_outline_strip(...):
    draw_texture_rectangle_outline(...)

def draw_rectangle_outline_strip(...):
    draw_rectangle_outline(...)

def draw_rectangle_filled_strip(...):
    draw_rectangle_filled(...)

def draw_line_strip(...):
    draw_line(...)

def draw_text_strip(...):
    draw_text(...)

def draw_circle_outline_strip(...):
    draw_circle_outline(...)

def draw_circle_filled_strip(...):
    draw_circle_filled(...)

def draw_triangle_outline_strip(...):
    draw_triangle_outline(...)

def draw_triangle_filled_strip(...):
    draw_triangle_filled(...)

def draw_ellipse_outline_strip(...):
    draw_ellipse_outline(...)

def draw_ellipse_filled_strip(...):
    draw_ellipse_filled(...)

def draw_polygon_outline_strip(...):
    draw_polygon_filled(...)

def draw_mesh_outline_strip(...):
    draw_mesh(...)

def draw_lrt_strip(...):
    draw_lrt(...)

def draw_texture_rectangle_strip(...):
    draw_texture_rectangle(...)

def draw_texture_rectangle_outline_strip(...):
    draw_texture_rectangle_outline(...)

def draw_texture_sequence_strip(...):
    draw_texture_sequence(...)

def draw_bitmap_strip_strip(...):
    draw_bitmap_strip(...)

def draw_rectangle_outline_striped(...):
    draw_rectangle_outline(...)

def draw_rectangle_filled_striped(...):
    draw_rectangle_filled(...)

def draw_line_striped(...):
    draw_line(...)

def draw_text_striped(...):
    draw_text_strip(...)

def draw_circle_outline_striped(...):
    draw_circle_outline(...)

def draw_circle_filled_striped(...):
    draw_circle_filled(...)

def draw_triangle_outline_striped(...):
    draw_triangle_outline(...)

def draw_triangle_filled_striped(...):
    draw_triangle_filled(...)

def draw_ellipse_outline_striped(...):
    draw_ellipse_outline(...)

def draw_ellipse_filled_striped(...):
    draw_ellipse_filled(...)

def draw_polygon_striped(...):
    draw_polygon_filled(...)

def draw_mesh_striped(...):
    draw_mesh(...)

def draw_lrt_striped(...):
    draw_lrt(...)

def draw_texture_rectangle_striped(...):
    draw_texture_rectangle(...)

def draw_texture_rectangle_outline_striped(...):
    draw_texture_rectangle_outline(...)

def draw_texture_sequence_striped(...):
    draw_texture_sequence(...)

def draw_bitmap_strip_striped(...):
    draw_bitmap_strip(...)

def draw_image(...):
    draw_texture_rectangle(...)

def draw_image_sequence(...):
    draw_texture_sequence(...)

def draw_sprite(...):
    Sprite(...).draw()

def draw_flat_array(...):
    pass

def _register_font(name, path):
    _FONTS[name] = str(path)

_FONTS = {"Arial": str(FONT_DIR / "Arial.ttf"), "Arial Bold": str(FONT_DIR / "Arial Bold.ttf"), "Arial Italic": str(FONT_DIR / "Arial Italic.ttf")}

def draw_texture_sequence_striped(...):
    draw_texture_sequence_strip(...)

def draw_texture_rectangle_outline_striped(...):
    draw_texture_rectangle_outline_strip(...)

def draw_texture_rectangle_striped(...):
    draw_texture_rectangle_strip(...)

def draw_texture_sequence_striped(...):
    draw_texture_sequence_strip(...)

def draw_bitmap_strip_striped(...):
    draw_bitmap_strip_strip(...)

def draw_flat_array_striped(...):
    draw_flat_array(...)

def draw_image_striped(...):
    draw_texture_rectangle_strip(...)

def draw_image_sequence_striped(...):
    draw_texture_sequence_strip(...)

def draw_sprite_striped(...):
    Sprite(...).draw()

def draw_triangle_filled_s(x1, y1, x2, y2, x3, y3, color):
    draw_triangle_filled(x1, y1, x2, y2, x3, y3, color)

def draw_triangle_outline_s(x1, y1, x2, y2, x3, y3, color, border_width=1):
    draw_triangle_outline(x1, y1, x2, y2, x3, y3, color, border_width)

def draw_circle_outline_s(x, y, radius, color, border_width=1):
    draw_circle_outline(x, y, radius, color, border_width)

def draw_circle_filled_s(x, y, radius, color):
    draw_circle_filled(x, y, radius, color)

def draw_ellipse_filled_s(x, y, width, height, color):
    draw_ellipse_filled(x, y, width, height, color)

def draw_ellipse_outline_s(x, y, width, height, color, border_width=1):
    draw_ellipse_outline(x, y, width, height, color, border_width)

def draw_line_s(x, y, x2, y2, color, line_width=1):
    draw_line(x, y, x2, y2, color, line_width)

def draw_rectangle_filled_s(x, y, width, height, color):
    draw_rectangle_filled(x, y, width, height, color)

def draw_rectangle_outline_s(x, y, width, height, color, border_width=1):
    draw_rectangle_outline(x, y, width, height, color, border_width)

def draw_text_s(...):
    draw_text(...)

def draw_text_2d_s(...):
    draw_text_2d(...)

def draw_text_centered_s(...):
    draw_text_centered(...)

def draw_texture_rectangle_s(...):
    draw_texture_rectangle(...)

def draw_texture_rectangle_outline_s(...):
    draw_texture_rectangle_outline(...)

def draw_texture_sequence_s(...):
    draw_texture_sequence(...)

def draw_bitmap_strip_s(...):
    draw_bitmap_strip(...)

def draw_polygon_filled_s(...):
    draw_polygon_filled(...)

def draw_mesh_s(...):
    draw_mesh(...)

def draw_lrt_s(...):
    draw_lrt(...)

# ---------------------------------------------------------------------------
# Window subclasses that the app uses
# ---------------------------------------------------------------------------
import arcade

class Window(arcade.Window):
    def __init__(self, width, height, title, resizable=False, visible=True):
        self._width = width
        self._height = height
        self._title = title
        self._resizable = resizable
        self._visible = visible
        self.fps = 60
        self.exit = False
        _log.info(f"Window created: {width}x{height} '{title}' resizable={resizable} visible={visible}")
    def set_background_color(self, color):
        _solids.setdefault("bg", []).append(_as_color(color))
    def run(self):
        _log.info("Window.run() called — headless run requested")
        # do nothing; we only wanted the construction + checks
    def close(self):
        _log.info("Window.close()")
    def set_mouse_visible(self, v):
        pass
    def set_resizable(self, v):
        pass
    def set_title(self, t):
        self._title = t
    def set_vsync(self, v):
        pass

class View(arcade.View):
    def __init__(self):
        pass
    def setup(self):
        pass
    def on_draw(self):
        pass
    def on_update(self, delta_time):
        pass
    def on_key_press(self, symbol, modifiers):
        pass
    def on_key_release(self, symbol, modifiers):
        pass
    def on_mouse_press(self, x, y, button, modifiers):
        pass
    def on_mouse_release(self, x, y, button, modifiers):
        pass
    def on_mouse_motion(self, x, y, dx, dy):
        pass
    def on_resize(self, width, height):
        pass
    def on_show_view(self):
        pass
    def on_hide_view(self):
        pass
    def show(self):
        pass
    def hide(self):
        pass
    def set_background_color(self, color):
        _solids.setdefault("bg", []).append(_as_color(color))

class TextWindow(arcade.Window):
    def __init__(self, width, height, title, resizable=False, style=None):
        self._width = width
        self._height = height
        self._title = title
        self._resizable = resizable
        self._style = style
    def set_background_color(self, color):
        _solids.setdefault("bg", []).append(_as_color(color))
    def run(self):
        pass
    def close(self):
        pass
    def set_mouse_visible(self, v):
        pass
    def set_resizable(self, v):
        pass
    def set_title(self, t):
        self._title = t
    def set_vsync(self, v):
        pass

class GUI(arcade.Window):
    def __init__(self, width, height, title, resizable=False, style=None):
        self._width = width
        self._height = height
        self._title = title
        self._resizable = resizable
        self._style = style
    def set_background_color(self, color):
        _solids.setdefault("bg", []).append(_as_color(color))
    def run(self):
        pass
    def close(self):
        pass
    def set_mouse_visible(self, v):
        pass
    def set_resizable(self, v):
        pass
    def set_title(self, t):
        self._title = t
    def set_vsync(self, v):
        pass

class WindowBase(arcade.Window):
    def __init__(self, width, height, title, resizable=False, style=None):
        self._width = width
        self._height = height
        self._title = title
        self._resizable = resizable
        self._style = style
    def set_background_color(self, color):
        _solids.setdefault("bg", []).append(_as_color(color))
    def run(self):
        pass
    def close(self):
        pass
    def set_mouse_visible(self, v):
        pass
    def set_resizable(self, v):
        pass
    def set_title(self, t):
        self._title = t
    def set_vsync(self, v):
        pass

class Window(arcade.Window):
    pass

class GUIWindow(arcade.Window):
    pass

class GenericWindow(arcade.Window):
    pass

# ---------------------------------------------------------------------------
# Restore the glyphs lost during compaction
# ---------------------------------------------------------------------------
try:
    draw_lrt
except NameError:
    def draw_lrt(x, y, width, height, texture, repeat_count_horizontal=1, repeat_count_vertical=1, color=None, alpha=255):
        _tiles.setdefault("lrt", []).append(dict(x=float(x), y=float(y), w=float(width), h=float(height),
                                                  path=texture.path, rh=int(repeat_count_horizontal), rv=int(repeat_count_vertical),
                                                  color=_as_color(color) if color is not None else None, alpha=alpha))

try:
    draw_line
except NameError:
    pass  # already defined

try:
    draw_text
except NameError:
    pass  # already defined

try:
    draw_text_2d
except NameError:
    pass

try:
    draw_text_centered
except NameError:
    pass

try:
    draw_triangle_filled
except NameError:
    pass

try:
    draw_triangle_outline
except NameError:
    pass

def _sync():
    for attr in ("draw_lrt","draw_line","draw_text","draw_text_2d","draw_text_centered",
                 "draw_triangle_filled","draw_triangle_outline","draw_rectangle_filled",
                 "draw_rectangle_outline","draw_circle_filled","draw_circle_outline",
                 "draw_ellipse_filled","draw_ellipse_outline","draw_polygon_filled",
                 "draw_mesh","draw_bitmap_strip","draw_texture_rectangle","draw_texture_rectangle_outline",
                 "draw_texture_sequence","draw_texture_rectangle_strip","draw_texture_rectangle_outline_strip",
                 "draw_texture_sequence_strip","draw_lrt_strip"):
        globals()[attr] = globals().get(attr, None)

_sync()

def draw(self, *_args, **_kwargs):
    raise RuntimeError("draw must be called as a method, not global")

# ---------------------------------------------------------------------------
# Restore other lost arcade helpers
# ---------------------------------------------------------------------------
try:
    arcade.draw_lrt
except AttributeError:
    arcade.draw_lrt = draw_lrt
try:
    arcade.draw_rectangle_filled
except AttributeError:
    arcade.draw_rectangle_filled = draw_rectangle_filled
try:
    arcade.draw_rectangle_outline
except AttributeError:
    arcade.draw_rectangle_outline = draw_rectangle_outline
try:
    arcade.draw_text
except AttributeError:
    arcade.draw_text = draw_text
try:
    arcade.draw_text_2d
except AttributeError:
    arcade.draw_text_2d = draw_text_2d
try:
    arcade.draw_text_centered
except AttributeError:
    arcade.draw_text_centered = draw_text_centered
try:
    arcade.draw_triangle_filled
except AttributeError:
    arcade.draw_triangle_filled = draw_triangle_filled
try:
    arcade.draw_triangle_outline
except AttributeError:
    arcade.draw_triangle_outline = draw_triangle_outline
try:
    arcade.draw_circle_filled
except AttributeError:
    arcade.draw_circle_filled = draw_circle_filled
try:
    arcade.draw_circle_outline
except AttributeError:
    arcade.draw_circle_outline = draw_circle_outline
try:
    arcade.draw_ellipse_filled
except AttributeError:
    arcade.draw_ellipse_filled = draw_ellipse_filled
try:
    arcade.draw_ellipse_outline
except AttributeError:
    arcade.draw_ellipse_outline = draw_ellipse_outline
try:
    arcade.draw_line
except AttributeError:
    arcade.draw_line = draw_line
try:
    arcade.draw_mesh
except AttributeError:
    arcade.draw_mesh = draw_mesh
try:
    arcade.draw_polygon_filled
except AttributeError:
    arcade.draw_polygon_filled = draw_polygon_filled
try:
    arcade.draw_bitmap_strip
except AttributeError:
    arcade.draw_bitmap_strip = draw_bitmap_strip
try:
    arcade.draw_texture_rectangle
except AttributeError:
    arcade.draw_texture_rectangle = draw_texture_rectangle
try:
    arcade.draw_texture_rectangle_outline
except AttributeError:
    arcade.draw_texture_rectangle_outline = draw_texture_rectangle_outline
try:
    arcade.draw_texture_sequence
except AttributeError:
    arcade.draw_texture_sequence = draw_texture_sequence

# ---------------------------------------------------------------------------
# Hook up the arcade stub module as the "arcade" the rest of the app sees.
# ---------------------------------------------------------------------------
import sys as _sys
import importlib as _importlib

# Point the "arcade" the app imports at our stubs.
class _ArcadeStub(modules.ModuleType):
    __name__ = "arcade"
    __spec__ = None
    def __init__(self):
        super().__init__("arcade")
        self.key = module_KeyIgnore
        self.key = self

_module = _sys.modules.setdefault("arcade", _ArcadeStub())
for name, func in list(globals().items()):
    if not name.startswith("_") and callable(func) and not isinstance(func, type):
        _module.__dict__[name] = func

_arcade = _sys.modules["arcade"]
for _name, _func in list(_arcade.__dict__.items()):
    globals()[_name] = _func

sys.modules["arcade"] = _arcade

# ---------------------------------------------------------------------------
# Geometry primitives used by the replay views
# ---------------------------------------------------------------------------
import numpy as np
from math import sin, cos, atan2, radians, degrees, sqrt, hypot, pi, floor, ceil, fabs

POINT2 = tuple[float, float]  # (x, y)

def point_on_circle(cx, cy, r, angle_deg):
    a = radians(angle_deg)
    return (cx + r*cos(a), cy + r*sin(a))

def box_from_pts(*pts_xy):
    xs = [p[0] for p in pts_xy]
    ys = [p[1] for p in pts_xy]
    return (min(xs), min(ys), max(xs)-min(xs), max(ys)-min(ys))

def dist(x1, y1, x2, y2):
    return hypot(x2-x1, y2-y1)

def angle_between(x1, y1, x2, y2):
    return degrees(atan2(y2-y1, x2-x1)) % 360

def lerp(a, b, t):
    return a + (b-a)*t

def clamp(v, lo, hi):
    return max(lo, min(hi, v))

def normalize_angle_deg(a):
    return (a + 180) % 360 - 180

def line_intersects_circle(x1, y1, x2, y2, cx, cy, r):
    # closest point on segment (x1,y1)-(x2,y2) to circle centre
    dx, dy = x2-x1, y2-y1
    if dx == 0 and dy == 0:
        d = hypot(cx-x1, cy-y1)
        return d <= r
    t = ((cx-x1)*dx + (cy-y1)*dy) / (dx*dx + dy*dy)
    t = clamp(t, 0.0, 1.0)
    px, py = x1 + t*dx, y1 + t*dy
    return hypot(cx-px, cy-py) <= r

def _close(a, b, tol=1e-4):
    return fabs(a-b) <= tol

def _near_collinear(x1, y1, x2, y2, x3, y3, tol=1e-6):
    # area of triangle (x1,y1),(x2,y2),(x3,y3) near zero => collinear
    return fabs((x2-x1)*(y3-y1) - (y2-y1)*(x3-x1)) <= tol

def _collinear(x1, y1, x2, y2, x3, y3):
    # non-tol version
    return ((x2-x1)*(y3-y1) - (y2-y1)*(x3-x1)) == 0.0

def _point_on_segment(px, py, x1, y1, x2, y2, tol=1e-4):
    # cross product zero + within bounding box
    if not _close((x2-x1)*(py-y1), (y2-y1)*(px-x1), tol):
        return False
    return min(x1,x2)-tol <= px <= max(x1,x2)+tol and min(y1,y2)-tol <= py <= max(y1,y2)+tol

def _segment_intersects_line(x1, y1, x2, y2, lx, ly, lx2, ly2):
    # quick reject
    if max(x1,x2) < min(lx,lx2) or max(lx,lx2) < min(x1,x2):
        return False
    if max(y1,y2) < min(ly,ly2) or max(ly,ly2) < min(y1,y2):
        return False
    # proper intersection test
    d1x, d1y = x2-x1, y2-y1
    d2x, d2y = lx2-lx, ly2-ly
    denom = d1x*d2y - d1y*d2x
    if _close(denom, 0.0):
        return _point_on_segment(lx, ly, x1, y1, x2, y2) or _point_on_segment(lx2, ly2, x1, y1, x2, y2)
    t = ((lx-x1)*d2y - (ly-y1)*d2x) / denom
    u = ((lx-x1)*d1y - (ly-y1)*d1x) / denom
    return 0.0 <= t <= 1.0 and 0.0 <= u <= 1.0

def _circle_intersects_circle(x1, y1, r1, x2, y2, r2):
    d = hypot(x2-x1, y2-y1)
    return d <= r1 + r2 + 1e-6

def _point_in_circle(px, py, cx, cy, r):
    return hypot(px-cx, py-cy) <= r + 1e-6

def _point_in_rect(px, py, x, y, w, h):
    return x-1e-6 <= px <= x+w+1e-6 and y-1e-6 <= py <= y+h+1e-6

def _rect_intersects_rect(x1, y1, w1, h1, x2, y2, w2, h2):
    return not (x1+w1 <= x2 or x2+w2 <= x1 or y1+h1 <= y2 or y2+h2 <= y1)

def _compute_inside(pt, x, y, w, h, r, col_xs, col_ys, invert):
    # pt inside rounded rectangle shape
    px, py = pt
    if r <= 0:
        return _point_in_rect(px, py, x, y, w, h) ^ invert
    # normals
    inside = False
    corners = [(x+r, y+r), (x+w-r, y+r), (x+w-r, y+h-r), (x+r, y+h-r)]
    for i in range(4):
        cx, cy = corners[i]
        nx, ny = (x + (w - 2*r) * ((i%2)==0 and 0.5 or 0.0) + r * (1 if (i%2)==0 else 0),
                  y + (h - 2*r) * ((i//2)==0 and 0.5 or 0.0) + r * (1 if (i//2)==0 else 0))
        # actually simplified:
        # circle parts
        pass

def _inside_rounded_rect(px, py, x, y, w, h, r):
    if r <= 0:
        return _point_in_rect(px, py, x, y, w, h)
    # top-left corner
    if px < x+r and py < y+r:
        return _point_in_circle(px, py, x+r, y+r, r)
    if px > x+w-r and py < y+r:
        return _point_in_circle(px, py, x+w-r, y+r, r)
    if px < x+r and py > y+h-r:
        return _point_in_circle(px, py, x+r, y+h-r, r)
    if px > x+w-r and py > y+h-r:
        return _point_in_circle(px, py, x+w-r, y+h-r, r)
    return _point_in_rect(px, py, x+r, y+r, w-2*r, h-2*r)

def _ellipse_bbox(cx, cy, rx, ry):
    return (cx-rx, cy-ry, cx+rx, cy+ry)

def _bezier_point(t, p0, p1, p2, p3):
    mt = 1-t
    mt2, mt3 = mt*mt, mt*mt*mt
    t2, t3 = t*t, t*t*t
    return (mt3*p0[0] + 3*mt2*t*p1[0] + 3*mt*t2*p2[0] + t3*p3[0],
            mt3*p0[1] + 3*mt2*t*p1[1] + 3*mt*t2*p2[1] + t3*p3[1])

def _bezier_bbox(p0, p1, p2, p3):
    xs = [p0[0], p1[0], p2[0], p3[0]]
    ys = [p0[1], p1[1], p2[1], p3[1]]
    return (min(xs), min(ys), max(xs), max(ys))

def _bezier_self_intersects(p0, p1, p2, p3):
    # convex hull test: if control points are convex and ordered, no self-intersection
    # For cubic bezier, self-intersection possible if p0-p3 segment intersects p1-p2
    d1x, d1y = p1[0]-p0[0], p1[1]-p0[1]
    d2x, d2y = p2[0]-p3[0], p2[1]-p3[1]
    denom = d1x*d2y - d1y*d2x
    if _close(denom, 0.0):
        return False
    t = ((p3[0]-p0[0])*d2y - (p3[1]-p0[1])*d2x) / denom
    u = ((p3[0]-p0[0])*d1y - (p3[1]-p0[1])*d1x) / denom
    if 0.0 < t < 1.0 and 0.0 < u < 1.0:
        return True
    return False

def _bezier_length(p0, p1, p2, p3, steps=100):
    length = 0.0
    prev = p0
    for i in range(1, steps+1):
        t = i/steps
        curr = _bezier_point(t, p0, p1, p2, p3)
        length += hypot(curr[0]-prev[0], curr[1]-prev[1])
        prev = curr
    return length

def _bezier_arc_length(p0, p1, p2, p3, start_t, end_t, steps=100):
    length = 0.0
    prev = _bezier_point(start_t, p0, p1, p2, p3)
    for i in range(1, steps+1):
        t = start_t + (end_t-start_t)*i/steps
        curr = _bezier_point(t, p0, p1, p2, p3)
        length += hypot(curr[0]-prev[0], curr[1]-prev[1])
        prev = curr
    return length

# Re-export the arcade stub helpers to emulate the arcade module namespace.
# ---------------------------------------------------------------------------
# Re-export the arcade stub helpers to emulate the arcade module namespace.
# ---------------------------------------------------------------------------

from ._stubs import *
from ._stubs import _solids, _lines, _tris, _tiles, _indexeds, _centroids
from ._stubs import Color, draw_rectangle_filled, draw_rectangle_outline, draw_text, draw_text_2d, draw_text_centered
from ._stubs import draw_circle_filled, draw_circle_outline, draw_ellipse_filled, draw_ellipse_outline
from ._stubs import draw_line, draw_triangle_filled, draw_triangle_outline, draw_polygon_filled, draw_mesh
from ._stubs import draw_bitmap_strip, draw_texture_rectangle, draw_texture_rectangle_outline, draw_texture_sequence
from ._stubs import Texture, Sprite, Window, View, TextWindow, GUI, WindowBase, GUIWindow, GenericWindow
from ._stubs import _f1_telemetry_globals, _list_positions_global, _run_session_globals, _extract_data_globals
from ._stubs import _registered, _reg
from ._stubs import _font_default_mapping, _font_default, _font_face_angle, _font_face_family

from ._stubs import _font_face_name
from ._stubs import _font_face_style
from ._stubs import _font_face_variant
from ._stubs import _font_face_weight
from ._stubs import _font_face_stretch
from ._stubs import _font_face_size
from ._stubs import _font_face_line_spacing
from ._stubs import _font_face_letter_spacing
from ._stubs import _font_face_tab_size

# ---------------------------------------------------------------------------
# Restore a few arcade API equivalences lost during compaction
# ---------------------------------------------------------------------------
from ._stubs import (
    draw_rectangle_filled as draw_rectangle_filled_stub,
    draw_rectangle_outline as draw_rectangle_outline_stub,
    draw_text as draw_text_stub,
    draw_text_2d as draw_text_2d_stub,
    draw_text_centered as draw_text_centered_stub,
    draw_circle_filled as draw_circle_filled_stub,
    draw_circle_outline as draw_circle_outline_stub,
    draw_ellipse_filled as draw_ellipse_filled_stub,
    draw_ellipse_outline as draw_ellipse_outline_stub,
    draw_line as draw_line_stub,
    draw_triangle_filled as draw_triangle_filled_stub,
    draw_triangle_outline as draw_triangle_outline_stub,
    draw_mesh as draw_mesh_stub,
    draw_polygon_filled as draw_polygon_filled_stub,
    draw_bitmap_strip as draw_bitmap_strip_stub,
    draw_texture_rectangle as draw_texture_rectangle_stub,
    draw_texture_rectangle_outline as draw_texture_rectangle_outline_stub,
    draw_texture_sequence as draw_texture_sequence_stub,
)

# ---------------------------------------------------------------------------
# Park everything into arcade module namespace as well
# ---------------------------------------------------------------------------
import sys as _sys
from types import ModuleType

_arcade_mod = _sys.modules.setdefault("arcade", ModuleType("arcade"))
for _name, _val in list(globals().items()):
    if not _name.startswith("_") and _name not in _arcade_mod.__dict__:
        try:
            setattr(_arcade_mod, _name, _val)
        except Exception:
            pass

# Also set the usual arcade namespace attributes so imports of arcade work.
_arcade_mod.log = logging
_arcade_mod.key = _ArcadeKey  # added below
_arcade_mod.key = _ArcadeKey  # added below
_arcade_mod.key = _ArcadeKey  # added below

# ---------------------------------------------------------------------------
# Restore keyboard constant definitions that were dropped during compaction
# ---------------------------------------------------------------------------
try:
    from arcade import key as _arcade_key
    _KEY = _arcade_key
except ImportError:
    class _ArcadeKey:
        # minimal set actually used by the app
        LEFT = 63232; RIGHT = 63235; UP = 63233; DOWN = 63234
        SPACE = 32; ENTER = 13; ESCAPE = 27; TAB = 9
        A = 65; B = 66; C = 67; D = 68; E = 69; F = 70; G = 71; H = 72; I = 73
        J = 74; K = 75; L = 76; M = 77; N = 78; O = 79; P = 80; Q = 81
        R = 82; S = 83; T = 84; U = 85; V = 86; W = 87; X = 88; Y = 89; Z = 90
        KEY_0 = 48; KEY_1 = 49; KEY_2 = 50; KEY_3 = 51; KEY_4 = 52; KEY_5 = 53
        KEY_6 = 54; KEY_7 = 55; KEY_8 = 56; KEY_9 = 57
        F1 = 241; F2 = 242; F3 = 243; F4 = 244; F5 = 245; F6 = 246
        F7 = 247; F8 = 248; F9 = 249; F10 = 250; F11 = 251; F12 = 252
        NUM_LOCK = 300; SCROLL_LOCK = 301; CAPS_LOCK = 144
        LEFT_SHIFT = 340; RIGHT_SHIFT = 344; LEFT_CONTROL = 36; RIGHT_CONTROL = 365
        LEFT_ALT = 346; RIGHT_ALT = 361
        _table = {}
        @classmethod
        def _init_table(cls):
            if cls._table:
                return
            for _n, _v in list(globals().items()):
                if isinstance(_v, int):
                    cls._table[_n] = _v
                elif isinstance(_v, str):
                    pass
            for _k, _v in list(locals().items()):
                if isinstance(_v, int):
                    cls._table[_v] = _k
        _init_table()
    _KEY = _ArcadeKey

try:
    _sys.modules["arcade"].key = _KEY
except Exception:
    pass

# Provide the ac
except NameError:
    pass

class _ArcadeKey:
    # minimal set actually used by the app
    LEFT = 63232; RIGHT = 63235; UP = 63233; DOWN = 63234
    SPACE = 32; ENTER = 13; ESCAPE = 27; TAB = 9
    A = 65; B = 66; C = 67; D = 68; E = 69; F = 70; G = 71; H = 72; I = 73
    J = 74; K = 75; L = 76; M = 77; N = 78; O = 79; P = 80; Q = 81
    R = 82; S = 83; T = 84; U = 85; V = 86; W = 87; X = 88; Y = 89; Z = 90
    KEY_0 = 48; KEY_1 = 49; KEY_2 = 50; KEY_3 = 51; KEY_4 = 52; KEY_5 = 53
    KEY_6 = 54; KEY_7 = 55; KEY_8 = 56; KEY_9 = 57
    F1 = 241; F2 = 242; F3 = 243; F4 = 244; F5 = 245; F6 = 246
    F7 = 247; F8 = 248; F9 = 249; F10 = 250; F11 = 251; F12 = 252
    NUM_LOCK = 300; SCROLL_LOCK = 301; CAPS_LOCK = 144
    LEFT_SHIFT = 340; RIGHT_SHIFT = 344; LEFT_CONTROL = 36; RIGHT_CONTROL = 365
    LEFT_ALT = 346; RIGHT_ALT = 361
    _table = {}
    @classmethod
    def _init_table(cls):
        if cls._table:
            return
        for _n, _v in list(globals().items()):
            if isinstance(_v, int):
                cls._table[_n] = _v
            elif isinstance(_v, str):
                pass
        for _k, _v in list(locals().items()):
            if isinstance(_v, int):
                cls._table[_v] = _k
        cls._table.clear()
    _init_table()

_KEY = _ArcadeKey

try:
    _sys.modules["arcade"].key = _KEY
except Exception:
    pass

# ---------------------------------------------------------------------------
# Restore "Color" definition in the arcade namespace (app uses arcade.color.*)
# ---------------------------------------------------------------------------
import arcade as _arcade_mod
_arcade_mod.color = _sys.modules.setdefault("arcade.color", ModuleType("arcade.color"))
for _n, _c in list(globals().items()):
    if isinstance(_c, Color):
        setattr(_arcade_mod.color, _n, _c)

_arcade_mod.color = _sys.modules.setdefault("arcade.color", ModuleType("arcade.color"))
for _n, _c in list(globals().items()):
    if isinstance(_c, Color):
        setattr(_arcade_mod.color, _n, _c)

_arcade_mod.color = _sys.modules.setdefault("arcade.color", ModuleType("arcade.color"))
for _n, _c in list(globals().items()):
    if isinstance(_c, Color):
        setattr(_arcade_mod.color, _n, _c)

# ---------------------------------------------------------------------------
# Patch the stub Color class into the "arcade.color" namespace so the rest
# of the app can refer to arcade.color.WHITE, arcade.color.BLACK etc.
# ---------------------------------------------------------------------------
import sys as _sys
import types as _types

_arcade_color_mod = _sys.modules.setdefault("arcade.color", _types.ModuleType("arcade.color"))
_arcade_color_mod.__dict__["__package__"] = "arcade"
_arcade_color_mod.__dict__["__loader__"] = None

for _name, _val in list(globals().items()):
    if isinstance(_val, type) and issubclass(_val, Color) and _val is not Color:
        setattr(_arcade_color_mod, _name, _val)

_arcade_color_mod = _sys.modules.setdefault("arcade.color", _types.ModuleType("arcade.color"))
_arcade_color_mod.__dict__["__package__"] = "arcade"
_arcade_color_mod.__dict__["__loader__"] = None

for _name, _val in list(globals().items()):
    if isinstance(_val, type) and issubclass(_val, Color) and _val is not Color:
        setattr(_arcade_color_mod, _name, _val)

_arcade_color_mod = _sys.modules.setdefault("arcade.color", _types.ModuleType("arcade.color"))
_arcade_color_mod.__dict__["__package__"] = "arcade"
_arcade_color_mod.__dict__["__loader__"] = None

for _name, _val in list(globals().items()):
    if isinstance(_val, type) and issubclass(_val, Color) and _val is not Color:
        setattr(_arcade_color_mod, _name, _val)

# ---------------------------------------------------------------------------
# Replace the "arcade" module in sys.modules with our stub module
# ---------------------------------------------------------------------------
import sys as _sys
from types import ModuleType

class _ArcadeStubModule(ModuleType):
    def __init__(self):
        super().__init__("arcade")
        self.__file__ = __file__
        self.__package__ = ""
        self.__loader__ = None

_sys.modules["arcade"] = _ArcadeStubModule()

# ---------------------------------------------------------------------------
# Remove duplicate definitions that may have slipped into globals()
# ---------------------------------------------------------------------------
try:
    del draw_lrt
except NameError:
    pass
try:
    del draw_line_stub
except NameError:
    pass
try:
    del draw_text_stub
except NameError:
    pass
try:
    del draw_text_2d_stub
except NameError:
    pass
try:
    del draw_text_centered_stub
except NameError:
    pass
try:
    del draw_circle_filled_stub
except NameError:
    pass
try:
    del draw_circle_outline_stub
except NameError:
    pass
try:
    del draw_ellipse_filled_stub
except NameError:
    pass
try:
    del draw_ellipse_outline_stub
except NameError:
    pass
try:
    del draw_triangle_filled_stub
except NameError:
    pass
try:
    del draw_triangle_outline_stub
except NameError:
    pass
try:
    del draw_mesh_stub
except NameError:
    pass
try:
    del draw_polygon_filled_stub
except NameError:
    pass
try:
    del draw_bitmap_strip_stub
except NameError:
    pass
try:
    del draw_texture_rectangle_stub
except NameError:
    pass
try:
    del draw_texture_rectangle_outline_stub
except NameError:
    pass
try:
    del draw_texture_sequence_stub
except NameError:
    pass
try:
    del _solids, _lines, _tris, _tiles, _indexeds, _centroids
except NameError:
    pass
try:
    del _f1_telemetry_globals, _list_positions_global, _run_session_globals, _extract_data_globals
except NameError:
    pass
try:
    del _registered, _reg
except NameError:
    pass
try:
    del _font_default_mapping, _font_default, _font_face_angle, _font_face_family
except NameError:
    pass
try:
    del _font_face_name, _font_face_style, _font_face_variant, _font_face_weight
except NameError:
    pass
try:
    del _font_face_stretch, _font_face_size, _font_face_line_spacing, _font_face_letter_spacing, _font_face_tab_size
except NameError:
    pass
try:
    del _KEY
except NameError:
    pass
try:
    del _ArcadeKey
except NameError:
    pass
try:
    del _arcade_color_mod
except NameError:
    pass
try:
    del _sys
except NameError:
    pass
try:
    del _types
except NameError:
    pass
try:
    del _ArcadeStubModule
except NameError:
    pass

# ---------------------------------------------------------------------------
# Also bind into the app's "arcade" module name used via import
# ---------------------------------------------------------------------------
_sys.modules["arcade"] = _arcade_color_mod = _types.ModuleType("arcade")
for _name, _val in list(globals().items()):
    if not _name.startswith("_"):
        try:
            setattr(_sys.modules["arcade"], _name, _val)
        except Exception:
            pass

# ---------------------------------------------------------------------------
# Ensure arcade.color exists
# ---------------------------------------------------------------------------
_arcade_color_mod = _types.ModuleType("arcade.color")
_sys.modules.setdefault("arcade.color", _arcade_color_mod)
for _name, _val in list(globals().items()):
    if isinstance(_val, type) and issubclass(_val, Color) and _val is not Color:
        setattr(_arcade_color_mod, _name, _val)

# ---------------------------------------------------------------------------
# Alias arcade into the stub module's namespace so that modules that
# do "from arcade import ..." find our functions.
# ---------------------------------------------------------------------------
_arcade_mod = _sys.modules["arcade"]
for _name, _val in list(globals().items()):
    if not _name.startswith("_"):
        try:
            setattr(_arcade_mod, _name, _val)
        except Exception:
            pass

_arcade_mod.color = _sys.modules["arcade.color"]
_arcade_mod.key = _KEY

# ---------------------------------------------------------------------------
# Bind the control/keyboard constants used throughout the app
# ---------------------------------------------------------------------------
for _name, _val in list(vars(_KEY).items()):
    if not _name.startswith("_"):
        setattr(_arcade_mod, _name, _val)

_sys.modules["arcade"] = _arcade_mod
_sys.modules["arcade"].color = _sys.modules["arcade.color"]
_sys.modules["arcade"].key = _KEY

# ---------------------------------------------------------------------------
# Also expose the geometry helpers we defined above into the stub module,
# so the code that does "from arcade import ..." still works.
# ---------------------------------------------------------------------------
_arcade_mod = _sys.modules["arcade"]

for _name, _val in list(globals().items()):
    if not _name.startswith("_") and not isinstance(_val, (type, ModuleType, _types.ModuleType)):
        try:
            setattr(_arcade_mod, _name, _val)
        except Exception:
            pass

for _name, _val in list(vars(_KEY).items()):
    if not _name.startswith("_"):
        setattr(_arcade_mod, _name, _val)

# ---------------------------------------------------------------------------
# Expose the stub geometry helpers into the global namespace
# ---------------------------------------------------------------------------
for _name, _val in list(globals().items()):
    if not _name.startswith("_") and not isinstance(_val, (type, ModuleType, _types.ModuleType)):
        globals()[_name] = _val

for _name, _val in list(vars(_KEY).items()):
    if not _name.startswith("_"):
        globals()[_name] = _val

# ---------------------------------------------------------------------------
# Expose the stub Color into the global namespace
# ---------------------------------------------------------------------------
globals()["Color"] = Color

# ---------------------------------------------------------------------------
# Ensure the app's helpers and constants are available at module level
# ---------------------------------------------------------------------------
_globals = globals()
for _name in list(_globals.keys()):
    if not _name.startswith("_"):
        continue
    _val = _globals[_name]
    if isinstance(_val, (type, ModuleType, _types.ModuleType)):
        continue
    try:
        _arcade_mod.__dict__[_name] = _val
    except Exception:
        pass

for _name in list(_globals.keys()):
    if not _name.startswith("_"):
        continue
    _val = _globals[_name]
    if isinstance(_val, (type, ModuleType, _types.ModuleType)):
        continue
    try:
        _arcade_color_mod.__dict__[_name] = _val
    except Exception:
        pass

for _name in list(vars(_KEY).items()):
    if not _name.startswith("_"):
        _arcade_mod.__dict__[_name] = _val

# ---------------------------------------------------------------------------
# Re-bind the arcade key constants to the real arcade.key module.
# The app uses arcade.key.LEFT etc., so we need to give it real values.
# ---------------------------------------------------------------------------
import sys as _sys
import arcade as _real_arcade

_real_key_mod = getattr(_real_arcade, "key", None)
if _real_key_mod is not None:
    _KEY = _real_key_mod
    _arcade_mod.key = _real_key_mod
    _sys.modules["arcade"].key = _real_key_mod
    print("arcade.key bound to real arcade.key")

# ---------------------------------------------------------------------------
# End of arcade stub injection
# ---------------------------------------------------------------------------
