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

# ── Fixed layout constants ────────────────────────────────────────────────────
HEADER_H        = 28   # day-name + date row
LEGEND_H        = 18   # calendar legend row
BODY_TOP        = HEADER_H + LEGEND_H
ALLDAY_ROW_H    = 18   # height of one all-day event row
MAX_ALLDAY_ROWS = 4    # maximum stacked all-day rows (extras are dropped)
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

FONT_DAY    = _font(11, bold=True)
FONT_EVENT  = _font(10)
FONT_TIME   = _font(9)
FONT_LEGEND = _font(9)

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
) -> None:
    draw.rectangle([0, 0, layout.width, HEADER_H - 1], fill=DGREY)
    for col, day in enumerate(days):
        x0 = TIME_AXIS_W + col * layout.col_w
        x1 = x0 + layout.col_w - 1
        if day == today and layout.today_highlight:
            draw.rectangle([x0, HEADER_H, x1, layout.height - 1], fill=LGREY)
            draw.rectangle([x0, HEADER_H, x1, layout.height - 1], outline=MGREY, width=1)
        if col > 0:
            draw.line([x0, 0, x0, layout.height], fill=MGREY, width=1)
        label = f"{DAY_NAMES[day.weekday()]} {day.day:02d}.{day.month:02d}."
        tw    = draw.textlength(label, font=FONT_DAY)
        draw.text((x0 + (layout.col_w - tw) / 2, 5), label, font=FONT_DAY, fill=WHITE)


def _draw_legend(
    draw: ImageDraw.ImageDraw,
    layout: _Layout,
    events_by_cal: dict[str, tuple[str, list[CalEvent]]],
    fill_map: dict[str, int],
) -> None:
    draw.rectangle([0, HEADER_H, layout.width, HEADER_H + LEGEND_H - 1], fill=245)
    draw.line([0, HEADER_H + LEGEND_H - 1, layout.width, HEADER_H + LEGEND_H - 1],
              fill=MGREY, width=1)
    lx = TIME_AXIS_W + PADDING
    for cal_name, (color, _) in events_by_cal.items():
        fill = fill_map.get(color, BLACK)
        draw.rectangle([lx, HEADER_H + 3, lx + 10, HEADER_H + LEGEND_H - 4],
                       fill=fill, outline=BLACK)
        lx += 14
        draw.text((lx, HEADER_H + 3), cal_name, font=FONT_LEGEND, fill=BLACK)
        lx += int(draw.textlength(cal_name, font=FONT_LEGEND)) + 14


def _collect_day_events(
    days: list[date],
    events_by_cal: dict[str, tuple[str, list[CalEvent]]],
) -> tuple[dict[int, list[CalEvent]], list[CalEvent]]:
    """
    Returns:
        timed   – col → timed events whose start date falls in that column
        allday  – all-day events that overlap the visible day range (any span)
    """
    day_index  = {d: i for i, d in enumerate(days)}
    view_start = days[0]
    view_end   = days[-1] + timedelta(days=1)   # exclusive
    timed:  dict[int, list[CalEvent]] = {i: [] for i in range(len(days))}
    allday: list[CalEvent] = []

    for _, (_, events) in events_by_cal.items():
        for ev in events:
            if ev.all_day:
                # Include if event overlaps the visible window at all
                if ev.start.date() < view_end and ev.end.date() > view_start:
                    allday.append(ev)
            else:
                col = day_index.get(ev.start.date())
                if col is not None:
                    timed[col].append(ev)

    return timed, allday


def _assign_allday_rows(
    events: list[CalEvent],
    days: list[date],
) -> list[tuple[CalEvent, int, int, int]]:
    """
    Greedy row assignment for all-day events.

    Returns a list of (event, first_col, last_col, row) sorted by first_col.
    Events that don't fit within MAX_ALLDAY_ROWS are dropped.
    """
    view_start = days[0]
    view_end   = days[-1] + timedelta(days=1)

    # Pre-compute column spans
    spans: list[tuple[CalEvent, int, int]] = []
    for ev in events:
        span_start = max(ev.start.date(), view_start)
        span_end   = min(ev.end.date(),   view_end)
        first_col  = next(i for i, d in enumerate(days) if d >= span_start)
        last_col   = next((i - 1 for i, d in enumerate(days) if d >= span_end),
                          len(days) - 1)
        spans.append((ev, first_col, last_col))

    spans.sort(key=lambda x: x[1])  # sort by start column

    # row_end[r] = last column occupied in row r (-1 = empty)
    row_end: list[int] = [-1] * MAX_ALLDAY_ROWS
    result: list[tuple[CalEvent, int, int, int]] = []
    for ev, first_col, last_col in spans:
        for r in range(MAX_ALLDAY_ROWS):
            if row_end[r] < first_col:  # no overlap with this row
                row_end[r] = last_col
                result.append((ev, first_col, last_col, r))
                break
        # if all rows are full for this span, the event is silently dropped

    return result


def _draw_allday_strip(
    draw: ImageDraw.ImageDraw,
    layout: _Layout,
    allday_assignments: list[tuple[CalEvent, int, int, int]],
    fill_map: dict[str, int],
) -> None:
    strip_h = layout.allday_rows * ALLDAY_ROW_H
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
        # Stripe style: outline + left color bar (consistent with timed events)
        draw.rectangle([x0, y0, x1, y1], outline=BLACK)
        draw.rectangle([x0, y0, x0 + 3, y1], fill=fill)
        lbl = _truncate(ev.summary, x1 - x0 - 6, FONT_EVENT, draw)
        draw.text((x0 + 6, y0 + 2), lbl, font=FONT_EVENT, fill=BLACK)


def _draw_time_axis(draw: ImageDraw.ImageDraw, layout: _Layout) -> None:
    for h in range(layout.time_start_hour, min(layout.time_end_hour + 1, 25)):
        y = layout.grid_top + int((h - layout.time_start_hour) * layout.px_per_hour)
        if y >= layout.height:
            break
        draw.line([TIME_AXIS_W, y, layout.width, y], fill=MGREY, width=1)
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


def _assign_lanes(
    evs: list[CalEvent],
) -> list[tuple[CalEvent, int, int]]:
    """
    Greedy lane assignment so overlapping events share the column width.

    Returns list of (event, lane_index, total_lanes) sorted by start time.
    """
    sorted_evs = sorted(evs, key=lambda e: e.start)
    lane_end: list[datetime] = []
    ev_lanes: list[int]      = []
    for ev in sorted_evs:
        placed = False
        for i, end in enumerate(lane_end):
            if ev.start >= end:
                lane_end[i] = ev.end
                ev_lanes.append(i)
                placed = True
                break
        if not placed:
            ev_lanes.append(len(lane_end))
            lane_end.append(ev.end)
    num_lanes = len(lane_end)
    return [(ev, lane, num_lanes) for ev, lane in zip(sorted_evs, ev_lanes)]


def _draw_timed_events(
    draw: ImageDraw.ImageDraw,
    layout: _Layout,
    day_events: dict[int, list[CalEvent]],
    fill_map: dict[str, int],
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
            _draw_timed_block(draw, ev, x0, y0, y1, lw, fill_map.get(ev.color, BLACK))


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

    img  = Image.new("L", (width, height), WHITE)
    draw = ImageDraw.Draw(img)

    _draw_header(draw, layout, days, today)
    _draw_legend(draw, layout, events_by_cal, fill_map)
    _draw_allday_strip(draw, layout, allday_assignments, fill_map)
    _draw_time_axis(draw, layout)
    _draw_now_indicator(draw, layout, days, today)   # drawn before events
    _draw_timed_events(draw, layout, timed_events, fill_map)  # events on top

    return img.convert("1", dither=Image.Dither.FLOYDSTEINBERG).convert("L")


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
                      x: int, y0: int, y1: int, w: int, fill: int) -> None:
    """Draw a timed event block spanning y0→y1 with wrapped text."""
    draw.rectangle([x, y0, x + w, y1], outline=fill)
    draw.rectangle([x, y0, x + 3, y1], fill=fill)      # color bar on left

    block_h = y1 - y0
    if block_h < 12:
        return

    tx     = x + 5
    tw     = w - 9      # usable text width (past bar + right padding)
    ty     = y0 + 1
    lh_sm  = 11         # FONT_TIME  (size 9)  line height
    lh_ev  = 12         # FONT_EVENT (size 10) line height

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


# ── CLI ───────────────────────────────────────────────────────────────────────

def _load_events(path: str) -> dict[str, tuple[str, list[CalEvent]]]:
    """
    Parse a JSON calendar file into the events_by_cal dict expected by render_days.

    JSON format:
      [
        {
          "name":   "Work",
          "color":  "#000000",
          "events": [
            {
              "summary":  "Stand-up",
              "start":    "2026-04-07T09:00:00+02:00",
              "end":      "2026-04-07T09:30:00+02:00",
              "all_day":  false
            }
          ]
        }
      ]

    For all-day events use a date string for start/end: "2026-04-07"
    """
    import json
    from zoneinfo import ZoneInfo

    with open(path) as f:
        data = json.load(f)

    events_by_cal: dict[str, tuple[str, list[CalEvent]]] = {}
    for cal in data:
        name   = cal["name"]
        color  = cal["color"]
        events = []
        for e in cal.get("events", []):
            raw_start = e["start"]
            raw_end   = e.get("end", e["start"])
            all_day   = e.get("all_day", len(raw_start) == 10)

            if all_day:
                d     = date.fromisoformat(raw_start[:10])
                start = datetime(d.year, d.month, d.day,
                                 tzinfo=ZoneInfo("Europe/Berlin"))
                d     = date.fromisoformat(raw_end[:10])
                end   = datetime(d.year, d.month, d.day,
                                 tzinfo=ZoneInfo("Europe/Berlin"))
            else:
                start = datetime.fromisoformat(raw_start)
                end   = datetime.fromisoformat(raw_end)

            events.append(CalEvent(
                summary  = e.get("summary", "(No title)"),
                start    = start,
                end      = end,
                all_day  = all_day,
                calendar = name,
                color    = color,
            ))
        events_by_cal[name] = (color, events)

    return events_by_cal


if __name__ == "__main__":
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        description="Render a calendar PNG from JSON event data.",
    )
    parser.add_argument("events",
                        help="Path to JSON events file (see example_events.json)")
    parser.add_argument("-o", "--output", default="week.png",
                        help="Output PNG path (default: week.png)")
    parser.add_argument("--week", default=None,
                        help="Monday of the target week as YYYY-MM-DD "
                             "(default: current week)")
    parser.add_argument("--width",        type=int, default=800)
    parser.add_argument("--height",       type=int, default=480)
    parser.add_argument("--time-window",  type=int, default=12,
                        dest="time_window",
                        help="Number of hours to display (default: 12)")
    parser.add_argument("--time-start",   type=int, default=8,
                        dest="time_start",
                        help="First visible hour 0-23 (default: 8)")
    args = parser.parse_args()

    if args.week:
        week_start = date.fromisoformat(args.week)
        if week_start.weekday() != 0:
            print(f"Error: --week {args.week} is not a Monday", file=sys.stderr)
            sys.exit(1)
    else:
        today      = date.today()
        week_start = today - timedelta(days=today.weekday())

    try:
        events_by_cal = _load_events(args.events)
    except Exception as exc:
        print(f"Error loading {args.events}: {exc}", file=sys.stderr)
        sys.exit(1)

    img = render_week(
        events_by_cal,
        week_start,
        width=args.width,
        height=args.height,
        time_window_hours=args.time_window,
        time_start_hour=args.time_start,
    )
    img.save(args.output)
    print(f"Saved {args.width}×{args.height} PNG → {args.output}")
