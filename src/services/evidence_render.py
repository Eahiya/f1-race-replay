"""Shared HTML rendering for evidence-backed findings (Updates 7/8).

Both the qualifying and battles intelligence windows present the same
kind of content: a finding with a classification, a human explanation,
measured values, evidence channels, data quality, and provenance. This
module renders those into a single structured, human-readable panel so
the two windows never dump raw Python containers into the UI.

The renderer is a pure function over plain data (no Qt), so it is
offscreen-testable. Values are shown verbatim; only presentation changes.
"""

import html
import math

_QUALITY_COLORS = {
    "full": "#28C77A", "partial": "#F4B942", "frozen": "#56A8FF",
    "missing": "#FF5A5F", "unknown": "#888888",
}

_TRACK_STATUS_LABELS = {
    "1": "Green flag (1)", "2": "Yellow flag (2)",
    "4": "Safety car (4)", "5": "Red flag (5)",
    "6": "Virtual safety car (6)", "7": "VSC ending (7)",
}

# Human label for a measurement/evidence key. Unknown keys fall back to a
# de-underscored, capitalised form, so new fields stay readable.
_LABELS = {
    # generic / shared
    "label": "Section",
    "channel": "Channel",
    "channels": "Channels",
    "residual": "Residual",
    "type": "Type",
    "status": "Status",
    "order": "Order",
    "gap": "Gap",
    "tyre": "Tyre",
    "driver": "Driver",
    "old": "From compound",
    "new": "To compound",
    "step": "Step",
    "t": "At",
    "from": "From",
    "to": "To",
    # battles
    "duration_s": "Duration",
    "min_gap_s": "Minimum gap",
    "max_closing_mps": "Peak closing rate",
    "n_transitions": "State changes",
    "transitions": "State path",
    "interruptions": "Interruptions",
    "interval": "Time interval",
    "hold_s": "New order held for",
    "gap_before_s": "Gap at the flip",
    "persist_s": "Order persistence",
    "rel_speed_kmh": "Relative speed",
    "closing_rate_mps": "Closing rate",
    "time_to_contact_s": "Estimated time to contact",
    "min_gap_t": "Closest-approach moment",
    "gap_trend_s_per_s": "Gap trend",
    "event_lap": "Lap",
    "event_dist_m": "Distance into lap",
    "speed_new_kmh": "Overtaker speed",
    "speed_old_kmh": "Defender speed",
    "status_during": "Track status",
    "opening_lap": "Opening lap",
    "n_gap_samples": "Gap samples",
    "window_t": "Analysis window",
    "statuses_in_window": "Track statuses seen",
    "tyre_changes": "Tyre changes nearby",
    "non_running": "Non-running cars",
    "dt_s": "Offset from flip",
    "t_last_pre": "Last sample (still ahead)",
    "t_first_post": "First sample (now behind)",
    "event_step": "Simulation step",
    "Classification confidence": "Classification confidence",
    "Options": "Options",
    # qualifying
    "delta_s": "Time delta (s)",
    "lap_a_s": "Lap time A (s)",
    "lap_b_s": "Lap time B (s)",
    "section_delta_s": "Section delta (s)",
    "delta_start_s": "Delta at start (s)",
    "delta_end_s": "Delta at end (s)",
    "pickup_a_m": "Pickup point A (m)",
    "pickup_b_m": "Pickup point B (m)",
    "gap_m": "Pickup gap (m)",
    "threshold_pct": "Throttle threshold (%)",
    "exit_speed_a_kmh": "Exit speed A (km/h)",
    "exit_speed_b_kmh": "Exit speed B (km/h)",
    "length_m": "Lift length (m)",
    "n_lifts_total": "Total lifts",
    "residual_s": "Residual (s)",
    "sections_compared": "Sections compared",
    "d_start": "Start (m)",
    "d_end": "End (m)",
}

_UNITS = {
    "duration_s": "s", "min_gap_s": "s", "gap_before_s": "s", "hold_s": "s",
    "persist_s": "s", "time_to_contact_s": "s", "interval": "s",
    "window_t": "s", "min_gap_t": "s", "t": "s", "t_last_pre": "s",
    "t_first_post": "s", "dt_s": "s", "max_closing_mps": "m/s",
    "closing_rate_mps": "m/s", "rel_speed_kmh": "km/h",
    "speed_new_kmh": "km/h", "speed_old_kmh": "km/h",
    "event_dist_m": "m", "gap_trend_s_per_s": "s/s",
}

# Keys that duplicate information already carried by the label/unit.
_SKIP_KEYS = {"units", "unit"}


def _humanize(token):
    text = str(token).replace("_", " ").strip()
    return text[:1].upper() + text[1:] if text else text


def _label_for(key):
    return _LABELS.get(key, _humanize(key))


def _trim_float(value):
    magnitude = abs(value)
    if magnitude == 0:
        return "0"
    if magnitude >= 1000:
        return f"{value:,.0f}"
    if magnitude >= 100:
        return f"{value:.1f}"
    if magnitude >= 1:
        return f"{value:.2f}"
    return f"{value:.3f}"


def _scalar_text(key, value):
    if value is None:
        return "<span style='color:#888888'>not available</span>"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        if not math.isfinite(value):
            return "<span style='color:#888888'>n/a</span>"
        text = _trim_float(value)
    elif isinstance(value, int):
        text = f"{value:,}"
    else:
        return html.escape(str(value))
    unit = _UNITS.get(key) if key else None
    if unit:
        return f"{text}&nbsp;<span style='color:#888888'>{html.escape(unit)}</span>"
    return text


def _track_status_text(value):
    if value is None:
        return _scalar_text(None, None)
    return html.escape(_TRACK_STATUS_LABELS.get(str(value), f"Status {value}"))


def _quality_badge(value):
    color = _QUALITY_COLORS.get(str(value), "#888888")
    return (f"<span style='color:{color};font-weight:600'>"
            f"{html.escape(str(value))}</span>")


def _render_transitions(transitions):
    lines = []
    for transition in transitions:
        if not isinstance(transition, dict):
            continue
        when = _scalar_text("t", transition.get("t"))
        lines.append(
            f"{html.escape(_humanize(transition.get('from')))} "
            f"&rarr; <b>{html.escape(_humanize(transition.get('to')))}</b> "
            f"<span style='color:#888888'>@ {when}</span>")
    return "<br>".join(lines) if lines else "<span style='color:#888888'>none</span>"


def _render_list(key, values):
    if not values:
        return "<span style='color:#888888'>none</span>"
    if all(isinstance(item, dict) for item in values):
        return "<br>".join(_render_inline_dict(item) for item in values)
    return ", ".join(_scalar_text(key, item) for item in values)


def _render_inline_dict(mapping):
    parts = []
    for key, value in mapping.items():
        if key in _SKIP_KEYS:
            continue
        rendered = _render_list(key, value) if isinstance(value, list) \
            else _scalar_text(key, value)
        parts.append(
            f"<span style='color:#9AA0A6'>{html.escape(_label_for(key))}:</span> "
            f"{rendered}")
    return " &nbsp;·&nbsp; ".join(parts) if parts \
        else "<span style='color:#888888'>—</span>"


def _render_entry(label, key, value, override=None):
    if override is not None:
        cell = override
    elif key == "transitions" and isinstance(value, list):
        cell = _render_transitions(value)
    elif key == "status_during":
        cell = _track_status_text(value)
    elif key == "statuses_in_window" and isinstance(value, list):
        cell = ", ".join(_track_status_text(item) for item in value) \
            if value else "<span style='color:#888888'>none</span>"
    elif isinstance(value, dict):
        cell = _render_inline_dict(value)
    elif isinstance(value, list):
        cell = _render_list(key, value)
    else:
        cell = _scalar_text(key, value)
    return (f"<tr><td valign='top' style='color:#9AA0A6;padding:2px 10px 2px 0;"
            f"white-space:nowrap'>{html.escape(label)}</td>"
            f"<td valign='top' width='100%'>{cell}</td></tr>")


def _render_mapping(mapping):
    if not isinstance(mapping, dict) or not mapping:
        return "<div style='color:#888888'>No details recorded.</div>"
    rows = [_render_entry(_label_for(key), key, value)
            for key, value in mapping.items() if key not in _SKIP_KEYS]
    if not rows:
        return "<div style='color:#888888'>No details recorded.</div>"
    return ("<table cellspacing='0' cellpadding='2' width='100%'>"
            + "".join(rows) + "</table>")


def _render_quality(quality, tags=None):
    if not isinstance(quality, dict) or not quality:
        return "<div style='color:#888888'>No quality assessment.</div>"
    parts = []
    aggregate = quality.get("aggregate")
    if aggregate is not None:
        parts.append(f"<div>Overall: {_quality_badge(aggregate)}</div>")
    for tag in ("A", "B"):
        fields = quality.get(tag)
        if not isinstance(fields, dict) or not fields:
            continue
        name = (tags or {}).get(tag) or f"Driver {tag}"
        chips = []
        for field in ("speed", "dist", "throttle", "brake"):
            finding = fields.get(field)
            if finding is None:
                continue
            value = finding.get("quality") if isinstance(finding, dict) \
                else finding
            chips.append(f"{html.escape(field)} {_quality_badge(value)}")
        parts.append(f"<div style='margin-top:3px'><b>{html.escape(str(name))}"
                     f"</b>: " + " &nbsp; ".join(chips) + "</div>")
    if not parts:
        return "<div style='color:#888888'>No quality assessment.</div>"
    return "".join(parts)


def _render_provenance(provenance):
    if not isinstance(provenance, dict) or not provenance:
        return "<div style='color:#888888'>No provenance recorded.</div>"
    rows = []
    for key, value in provenance.items():
        override = html.escape(_humanize(value)) if isinstance(value, str) \
            else None
        rows.append(_render_entry(_label_for(key), key, value,
                                  override=override))
    return ("<table cellspacing='0' cellpadding='2' width='100%'>"
            + "".join(rows) + "</table>")


def _section(title, body):
    return (f"<div style='margin-top:12px;font-size:11px;font-weight:600;"
            f"letter-spacing:0.5px;color:#E10600;border-bottom:1px solid "
            f"#3A3F44;padding-bottom:2px'>{html.escape(title).upper()}</div>"
            f"<div style='margin-top:5px'>{body}</div>")


def render_evidence_html(finding_id, kind, explanation, measured, quality,
                         provenance, tags=None, extras=None,
                         kind_color="#E10600", kind_label=None):
    """Structured, human-readable HTML for one finding's evidence panel.

    ``finding_id`` / ``kind`` head the panel; ``kind_label`` overrides the
    kind's display text and ``kind_color`` its colour (both default to the
    raw kind). ``measured`` and each ``extras`` mapping render as labelled
    sections; ``quality`` and ``provenance`` render as their own sections.
    """
    blocks = [
        f"<div style='font-size:11px;color:#9AA0A6'>{html.escape(str(finding_id))}"
        f"</div>",
        f"<div style='font-size:15px;font-weight:600;color:{kind_color}'>"
        f"{html.escape(kind_label if kind_label is not None else str(kind))}"
        f"</div>",
        f"<div style='margin-top:6px'>{html.escape(str(explanation))}</div>",
        _section("Measured", _render_mapping(measured)),
    ]
    for title, mapping in (extras or []):
        blocks.append(_section(title, _render_mapping(mapping)))
    blocks.append(_section("Data quality", _render_quality(quality, tags)))
    blocks.append(_section("Provenance", _render_provenance(provenance)))
    return "".join(blocks)
