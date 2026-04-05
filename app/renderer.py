"""
renderer.py
===========
Renders a day-column calendar grid as a 1-bit PNG optimised for e-ink displays.

Layout (top → bottom):
  - Header row:    dark bar with day name + date per column
  - Legend row:    calendar color swatches + names
  - All-day strip: one-line banner per day for DATE-only events
  - Time grid:     timed events positioned on a proportional hour axis;
                   window start and span are configurable

Public API
----------
render_days(days, events_by_cal, ...)   – core: render any list of dates
render_week(events_by_cal, week_start, ...)  – 7-day Mon–Sun view
"""

from __future__ import annotations

import dataclasses
from datetime import date, datetime, timedelta
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from caldav_client import CalEvent
from scheduling import (
    MAX_ALLDAY_ROWS,
    _assign_allday_rows,
    _assign_lanes,
    _collect_day_events,
)

# ── Fixed layout constants ────────────────────────────────────────────────────
HEADER_H        = 28   # day-name + date row
LEGEND_H        = 18   # calendar legend row
BODY_TOP        = HEADER_H + LEGEND_H
ALLDAY_ROW_H    = 18   # height of one all-day event row
TIME_AXIS_W     = 28   # width of left-side hour-label column

PADDING     = 2    # inner cell padding
EVENT_MIN_H = 14   # minimum px height for a timed event block

# ── Colors ────────────────────────────────────────────────────────────────────
BLACK = 0
WHITE = 255
LGREY = 235   # today column highlight (used only when today_highlight is enabled)
MGREY = 130   # grid lines
DGREY = 80    # header background

# Greyscale fill levels assigned to calendars in order.
# Spaced so each dithers to a visually distinct dot pattern on e-ink.
_CAL_FILLS = [0, 80, 160, 220]   # black / dark / medium / light


def _hex_to_grey(hex_color: str) -> int:
    """Convert a hex color string to 0–255 greyscale via luminance."""
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return round(0.299 * r + 0.587 * g + 0.114 * b)


def _event_fill(grey: int) -> int:
    """Map a 0–255 greyscale value to a discrete fill level for e-ink rendering."""
    if grey < 80:
        return BLACK
    if grey < 160:
        return DGREY
    return MGREY

# ── Fonts ─────────────────────────────────────────────────────────────────────
def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    candidates = [
        f"/usr/share/fonts/truetype/dejavu/DejaVuSans{'-Bold' if bold else ''}.ttf",
        f"/usr/share/fonts/dejavu/DejaVuSans{'-Bold' if bold else ''}.ttf",
    ]
    for path in candidates:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()

FONT_DAY    = _font(12, bold=True)
FONT_EVENT  = _font(10, bold=True)
FONT_TIME   = _font(10)
FONT_LEGEND = _font(10)

DAY_NAMES = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]


# ── Calendar fill map ─────────────────────────────────────────────────────────

def _build_fill_map(
    events_by_cal: dict[str, tuple[str, list[CalEvent]]],
) -> dict[str, int]:
    """
    Assign each calendar a distinct greyscale fill level by its position in
    events_by_cal.  The same color hex always maps to the same fill within a
    render, so legend swatches, all-day bars, and timed event stripes are
    consistent.
    """
    fills: dict[str, int] = {}
    idx = 0
    for _, (color, _) in events_by_cal.items():
        if color not in fills:
            fills[color] = _CAL_FILLS[idx % len(_CAL_FILLS)]
            idx += 1
    return fills


# ── Layout ────────────────────────────────────────────────────────────────────
@dataclasses.dataclass
class _Layout:
    width:           int
    height:          int
    num_cols:        int
    col_w:           int
    allday_top:      int
    allday_rows:     int   # actual number of all-day rows in use (1–MAX_ALLDAY_ROWS)
    grid_top:        int
    px_per_hour:     float
    time_start_hour: int
    time_end_hour:   int
    today_highlight: bool

    @classmethod
    def build(
        cls,
        num_cols: int,
        width: int,
        height: int,
        time_window_hours: int,
        time_start_hour: int,
        today_highlight: bool,
        allday_rows: int = 1,
    ) -> "_Layout":
        col_w        = (width - TIME_AXIS_W) // num_cols
        allday_top   = BODY_TOP
        allday_strip = allday_rows * ALLDAY_ROW_H
        grid_top     = allday_top + allday_strip + 1   # +1 for separator line
        grid_h       = height - grid_top
        return cls(
            width           = width,
            height          = height,
            num_cols        = num_cols,
            col_w           = col_w,
            allday_top      = allday_top,
            allday_rows     = allday_rows,
            grid_top        = grid_top,
            px_per_hour     = grid_h / time_window_hours,
            time_start_hour = time_start_hour,
            time_end_hour   = time_start_hour + time_window_hours,
            today_highlight = today_highlight,
        )


# ── Drawing helpers ───────────────────────────────────────────────────────────

def _draw_header(
    draw: ImageDraw.ImageDraw,
    layout: _Layout,
    days: list[date],
    today: date,
    text_pass: bool = False,
) -> None:
    if not text_pass:
        draw.rectangle([0, 0, layout.width, HEADER_H - 1], fill=DGREY)
        for col, day in enumerate(days):
            x0 = TIME_AXIS_W + col * layout.col_w
            x1 = x0 + layout.col_w - 1
            if day == today and layout.today_highlight:
                draw.rectangle([x0, HEADER_H, x1, layout.height - 1], fill=LGREY)
                draw.rectangle([x0, HEADER_H, x1, layout.height - 1], outline=MGREY, width=1)
            if col > 0:
                draw.line([x0, 0, x0, layout.height], fill=MGREY, width=1)
    else:
        for col, day in enumerate(days):
            x0    = TIME_AXIS_W + col * layout.col_w
            label = f"{DAY_NAMES[day.weekday()]} {day.day:02d}.{day.month:02d}."
            tw    = draw.textlength(label, font=FONT_DAY)
            draw.text((x0 + (layout.col_w - tw) / 2, 5), label, font=FONT_DAY, fill=WHITE)


def _draw_legend(
    draw: ImageDraw.ImageDraw,
    layout: _Layout,
    events_by_cal: dict[str, tuple[str, list[CalEvent]]],
    fill_map: dict[str, int],
    text_pass: bool = False,
) -> None:
    lx = TIME_AXIS_W + PADDING
    if not text_pass:
        draw.rectangle([0, HEADER_H, layout.width, HEADER_H + LEGEND_H - 1], fill=245)
        draw.line([0, HEADER_H + LEGEND_H - 1, layout.width, HEADER_H + LEGEND_H - 1],
                  fill=MGREY, width=1)
        for cal_name, (color, _) in events_by_cal.items():
            fill = fill_map.get(color, BLACK)
            draw.rectangle([lx, HEADER_H + 3, lx + 10, HEADER_H + LEGEND_H - 4],
                           fill=fill, outline=BLACK)
            lx += 14 + int(draw.textlength(cal_name, font=FONT_LEGEND)) + 14
    else:
        for cal_name, (color, _) in events_by_cal.items():
            lx += 14
            draw.text((lx, HEADER_H + 3), cal_name, font=FONT_LEGEND, fill=BLACK)
            lx += int(draw.textlength(cal_name, font=FONT_LEGEND)) + 14


def _draw_allday_strip(
    draw: ImageDraw.ImageDraw,
    layout: _Layout,
    allday_assignments: list[tuple[CalEvent, int, int, int]],
    fill_map: dict[str, int],
    text_pass: bool = False,
) -> None:
    strip_h = layout.allday_rows * ALLDAY_ROW_H
    if not text_pass:
        draw.line([TIME_AXIS_W - 1, layout.allday_top, TIME_AXIS_W - 1, layout.height],
                  fill=MGREY, width=1)
        draw.line([0, layout.allday_top + strip_h, layout.width, layout.allday_top + strip_h],
                  fill=MGREY, width=1)
        for ev, first_col, last_col, row in allday_assignments:
            y0   = layout.allday_top + row * ALLDAY_ROW_H + 1
            y1   = y0 + ALLDAY_ROW_H - 2
            x0   = TIME_AXIS_W + first_col * layout.col_w + PADDING
            x1   = TIME_AXIS_W + (last_col + 1) * layout.col_w - PADDING
            fill = fill_map.get(ev.color, BLACK)
            draw.rectangle([x0, y0, x1, y1], fill=WHITE, outline=BLACK)
            draw.rectangle([x0, y0, x0 + 3, y1], fill=fill)
    else:
        for ev, first_col, last_col, row in allday_assignments:
            y0  = layout.allday_top + row * ALLDAY_ROW_H + 1
            x0  = TIME_AXIS_W + first_col * layout.col_w + PADDING
            x1  = TIME_AXIS_W + (last_col + 1) * layout.col_w - PADDING
            lbl = _truncate(ev.summary, x1 - x0 - 6, FONT_EVENT, draw)
            draw.text((x0 + 6, y0 + 2), lbl, font=FONT_EVENT, fill=BLACK)


def _draw_time_axis(draw: ImageDraw.ImageDraw, layout: _Layout, text_pass: bool = False) -> None:
    for h in range(layout.time_start_hour, min(layout.time_end_hour + 1, 25)):
        y = layout.grid_top + int((h - layout.time_start_hour) * layout.px_per_hour)
        if y >= layout.height:
            break
        if not text_pass:
            draw.line([TIME_AXIS_W, y, layout.width, y], fill=MGREY, width=1)
        else:
            draw.text((1, y - 9), f"{h:02d}", font=FONT_TIME, fill=BLACK)


def _draw_now_indicator(
    draw: ImageDraw.ImageDraw,
    layout: _Layout,
    days: list[date],
    today: date,
) -> None:
    now      = datetime.now()
    now_frac = now.hour + now.minute / 60
    if not (layout.time_start_hour <= now_frac < layout.time_end_hour):
        return
    try:
        col = days.index(today)
    except ValueError:
        return
    ny  = layout.grid_top + int((now_frac - layout.time_start_hour) * layout.px_per_hour)
    tx0 = TIME_AXIS_W + col * layout.col_w
    tx1 = tx0 + layout.col_w
    # Dot sits on the time axis so it's never covered by an event block;
    # line starts from the dot and runs across the today column.
    draw.ellipse([TIME_AXIS_W - 5, ny - 3, TIME_AXIS_W + 1, ny + 3], fill=BLACK)
    draw.line([tx0, ny, tx1, ny], fill=BLACK, width=2)


def _draw_timed_events(
    draw: ImageDraw.ImageDraw,
    layout: _Layout,
    day_events: dict[int, list[CalEvent]],
    fill_map: dict[str, int],
    text_pass: bool = False,
) -> None:
    for col, evs in day_events.items():
        col_x0 = TIME_AXIS_W + col * layout.col_w + PADDING
        col_w  = layout.col_w - PADDING * 2
        for ev, lane, num_lanes in _assign_lanes(evs):
            lw         = col_w // num_lanes
            x0         = col_x0 + lane * lw
            start_frac = ev.start.hour + ev.start.minute / 60
            end_frac   = ev.end.hour   + ev.end.minute   / 60
            vis_start  = max(start_frac, layout.time_start_hour)
            vis_end    = min(end_frac,   layout.time_end_hour)
            if vis_end <= vis_start:
                continue
            y0 = layout.grid_top + int((vis_start - layout.time_start_hour) * layout.px_per_hour)
            y1 = layout.grid_top + int((vis_end   - layout.time_start_hour) * layout.px_per_hour) - 1
            if y1 - y0 < EVENT_MIN_H:
                y1 = y0 + EVENT_MIN_H
            _draw_timed_block(draw, ev, x0, y0, y1, lw, fill_map.get(ev.color, BLACK),
                              text_pass=text_pass)


# ── Core renderer ─────────────────────────────────────────────────────────────

def render_days(
    days: list[date],
    events_by_cal: dict[str, tuple[str, list[CalEvent]]],
    width: int = 800,
    height: int = 480,
    time_window_hours: int = 12,
    time_start_hour: int = 8,
    today_highlight: bool = False,
) -> Image.Image:
    """
    Render a calendar image for an arbitrary ordered list of dates.

    This is the single place where all drawing happens. View functions
    (render_week, etc.) are thin wrappers that build the day list and delegate here.

    Args:
        days:               Ordered list of dates to display (one column each).
        events_by_cal:      {cal_name: (hex_color, [CalEvent, ...])}
        width, height:      Canvas dimensions in pixels.
        time_window_hours:  How many hours the time grid spans.
        time_start_hour:    First visible hour (0–23).
        today_highlight:    Shade today's column.
    """
    fill_map            = _build_fill_map(events_by_cal)
    timed_events, allday_events = _collect_day_events(days, events_by_cal)
    allday_assignments  = _assign_allday_rows(allday_events, days)
    used_rows           = max((r for _, _, _, r in allday_assignments), default=-1) + 1
    allday_rows         = max(1, min(used_rows, MAX_ALLDAY_ROWS))
    layout              = _Layout.build(len(days), width, height,
                                        time_window_hours, time_start_hour, today_highlight,
                                        allday_rows)
    today               = date.today()

    # Pass 1: graphics only (fills, lines, outlines) — will be dithered
    img  = Image.new("L", (width, height), WHITE)
    draw = ImageDraw.Draw(img)
    _draw_header(draw, layout, days, today, text_pass=False)
    _draw_legend(draw, layout, events_by_cal, fill_map, text_pass=False)
    _draw_allday_strip(draw, layout, allday_assignments, fill_map, text_pass=False)
    _draw_time_axis(draw, layout, text_pass=False)
    _draw_now_indicator(draw, layout, days, today)
    _draw_timed_events(draw, layout, timed_events, fill_map, text_pass=False)

    # Dither graphics; text is drawn after so anti-aliased edges are never dithered
    img  = img.convert("1", dither=Image.Dither.FLOYDSTEINBERG).convert("L")
    draw = ImageDraw.Draw(img)

    # Pass 2: text only — drawn on clean 1-bit result
    _draw_header(draw, layout, days, today, text_pass=True)
    _draw_legend(draw, layout, events_by_cal, fill_map, text_pass=True)
    _draw_allday_strip(draw, layout, allday_assignments, fill_map, text_pass=True)
    _draw_time_axis(draw, layout, text_pass=True)
    _draw_timed_events(draw, layout, timed_events, fill_map, text_pass=True)

    return img


# ── Public view functions ─────────────────────────────────────────────────────

def render_week(
    events_by_cal: dict[str, tuple[str, list[CalEvent]]],
    week_start: date,
    width: int = 800,
    height: int = 480,
    time_window_hours: int = 12,
    time_start_hour: int = 8,
    today_highlight: bool = False,
) -> Image.Image:
    """Render a Mon–Sun week grid."""
    days = [week_start + timedelta(days=i) for i in range(7)]
    return render_days(days, events_by_cal, width, height,
                       time_window_hours, time_start_hour, today_highlight)


def render_rolling(
    events_by_cal: dict[str, tuple[str, list[CalEvent]]],
    start: date,
    width: int = 800,
    height: int = 480,
    time_window_hours: int = 12,
    time_start_hour: int = 8,
    today_highlight: bool = False,
) -> Image.Image:
    """Render a 7-day rolling view starting from start."""
    days = [start + timedelta(days=i) for i in range(7)]
    return render_days(days, events_by_cal, width, height,
                       time_window_hours, time_start_hour, today_highlight)


def render_3day(
    events_by_cal: dict[str, tuple[str, list[CalEvent]]],
    start: date,
    width: int = 800,
    height: int = 480,
    time_window_hours: int = 12,
    time_start_hour: int = 8,
    today_highlight: bool = False,
) -> Image.Image:
    """Render a 3-day view starting from start."""
    days = [start + timedelta(days=i) for i in range(3)]
    return render_days(days, events_by_cal, width, height,
                       time_window_hours, time_start_hour, today_highlight)


# ── Event block renderer ──────────────────────────────────────────────────────

def _wrap_text(text: str, max_px: int, font: ImageFont.FreeTypeFont,
               draw: ImageDraw.ImageDraw) -> list[str]:
    """Split text into lines that each fit within max_px."""
    words = text.split()
    if not words:
        return []
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        candidate = current + " " + word
        if draw.textlength(candidate, font=font) <= max_px:
            current = candidate
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def _draw_timed_block(draw: ImageDraw.ImageDraw, ev: CalEvent,
                      x: int, y0: int, y1: int, w: int, fill: int,
                      text_pass: bool = False) -> None:
    """Draw a timed event block spanning y0→y1 with wrapped text."""
    if not text_pass:
        draw.rectangle([x, y0, x + w, y1], fill=WHITE, outline=fill)
        draw.rectangle([x, y0, x + 3, y1], fill=fill)      # color bar on left
        return

    block_h = y1 - y0
    if block_h < 12:
        return

    tx     = x + 5
    tw     = w - 9      # usable text width (past bar + right padding)
    ty     = y0 + 1
    lh_sm  = 12         # FONT_TIME  (size 10) line height
    lh_ev  = 13         # FONT_EVENT (size 10 bold) line height

    # Line 1: time
    draw.text((tx, ty), ev.start.strftime("%H:%M"), font=FONT_TIME, fill=BLACK)
    ty += lh_sm

    # Remaining lines: word-wrapped summary
    for line in _wrap_text(ev.summary, tw, FONT_EVENT, draw):
        if ty + lh_ev > y1:                      # last pixel row — truncate
            draw.text((tx, ty), _truncate(line, tw, FONT_EVENT, draw),
                      font=FONT_EVENT, fill=BLACK)
            break
        draw.text((tx, ty), line, font=FONT_EVENT, fill=BLACK)
        ty += lh_ev


def _truncate(text: str, max_px: int, font: ImageFont.FreeTypeFont,
              draw: ImageDraw.ImageDraw) -> str:
    if draw.textlength(text, font=font) <= max_px:
        return text
    while text and draw.textlength(text + "…", font=font) > max_px:
        text = text[:-1]
    return text + "…" if text else ""
