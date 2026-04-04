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
HEADER_H    = 28   # day-name + date row
LEGEND_H    = 18   # calendar legend row
BODY_TOP    = HEADER_H + LEGEND_H
ALLDAY_H    = 18   # all-day event strip height
TIME_AXIS_W = 28   # width of left-side hour-label column

PADDING     = 2    # inner cell padding
EVENT_MIN_H = 14   # minimum px height for a timed event block

# ── Colors ────────────────────────────────────────────────────────────────────
BLACK = 0
WHITE = 255
LGREY = 232   # today column highlight (used only when today_highlight is enabled)
MGREY = 140   # grid lines, secondary text
DGREY = 60    # header background

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


# ── Color helpers ─────────────────────────────────────────────────────────────
def _hex_to_grey(hex_color: str) -> int:
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return int(0.299 * r + 0.587 * g + 0.114 * b)


def _event_fill(grey: int) -> int:
    if grey < 80:   return BLACK
    if grey < 160:  return DGREY
    return MGREY


# ── Layout ────────────────────────────────────────────────────────────────────
@dataclasses.dataclass
class _Layout:
    width:           int
    height:          int
    num_cols:        int
    col_w:           int
    allday_top:      int
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
    ) -> "_Layout":
        col_w      = (width - TIME_AXIS_W) // num_cols
        allday_top = BODY_TOP
        grid_top   = allday_top + ALLDAY_H + 1   # +1 for separator line
        grid_h     = height - grid_top
        return cls(
            width           = width,
            height          = height,
            num_cols        = num_cols,
            col_w           = col_w,
            allday_top      = allday_top,
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
) -> None:
    draw.rectangle([0, HEADER_H, layout.width, HEADER_H + LEGEND_H - 1], fill=240)
    draw.line([0, HEADER_H + LEGEND_H - 1, layout.width, HEADER_H + LEGEND_H - 1],
              fill=MGREY, width=1)
    lx = TIME_AXIS_W + PADDING
    for cal_name, (color, _) in events_by_cal.items():
        grey = _event_fill(_hex_to_grey(color))
        draw.rectangle([lx, HEADER_H + 3, lx + 10, HEADER_H + LEGEND_H - 4],
                       fill=grey, outline=BLACK)
        lx += 14
        draw.text((lx, HEADER_H + 3), cal_name, font=FONT_LEGEND, fill=BLACK)
        lx += int(draw.textlength(cal_name, font=FONT_LEGEND)) + 14


def _collect_day_events(
    days: list[date],
    events_by_cal: dict[str, tuple[str, list[CalEvent]]],
) -> dict[int, list[CalEvent]]:
    """Map column index → events for that day."""
    day_index = {d: i for i, d in enumerate(days)}
    result: dict[int, list[CalEvent]] = {i: [] for i in range(len(days))}
    for _, (_, events) in events_by_cal.items():
        for ev in events:
            col = day_index.get(ev.start.date())
            if col is not None:
                result[col].append(ev)
    return result


def _draw_allday_strip(
    draw: ImageDraw.ImageDraw,
    layout: _Layout,
    day_events: dict[int, list[CalEvent]],
) -> None:
    draw.line([TIME_AXIS_W - 1, layout.allday_top, TIME_AXIS_W - 1, layout.height],
              fill=MGREY, width=1)
    draw.line([0, layout.allday_top + ALLDAY_H, layout.width, layout.allday_top + ALLDAY_H],
              fill=MGREY, width=1)
    for col, evs in day_events.items():
        allday_evs = [e for e in evs if e.all_day]
        if not allday_evs:
            continue
        x0  = TIME_AXIS_W + col * layout.col_w + PADDING
        cw  = layout.col_w - PADDING * 2
        ev  = allday_evs[0]
        pfx = f"({len(allday_evs)}) " if len(allday_evs) > 1 else ""
        lbl = _truncate(pfx + ev.summary, cw - 4, FONT_EVENT, draw)
        grey = _event_fill(_hex_to_grey(ev.color))
        draw.rectangle([x0, layout.allday_top + 1, x0 + cw, layout.allday_top + ALLDAY_H - 2],
                       fill=grey, outline=BLACK)
        draw.text((x0 + 2, layout.allday_top + 2), lbl, font=FONT_EVENT,
                  fill=WHITE if grey < 128 else BLACK)


def _draw_time_axis(draw: ImageDraw.ImageDraw, layout: _Layout) -> None:
    for h in range(layout.time_start_hour, min(layout.time_end_hour + 1, 25)):
        y = layout.grid_top + int((h - layout.time_start_hour) * layout.px_per_hour)
        if y >= layout.height:
            break
        draw.line([TIME_AXIS_W, y, layout.width, y], fill=MGREY, width=1)
        draw.text((1, y - 9), f"{h:02d}", font=FONT_TIME, fill=MGREY)


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
    draw.line([tx0, ny, tx0 + layout.col_w, ny], fill=BLACK, width=2)
    draw.ellipse([tx0 - 3, ny - 3, tx0 + 3, ny + 3], fill=BLACK)


def _draw_timed_events(
    draw: ImageDraw.ImageDraw,
    layout: _Layout,
    day_events: dict[int, list[CalEvent]],
) -> None:
    for col, evs in day_events.items():
        timed_evs = sorted(
            [e for e in evs if not e.all_day],
            key=lambda e: e.start,
        )
        x0 = TIME_AXIS_W + col * layout.col_w + PADDING
        cw = layout.col_w - PADDING * 2
        for ev in timed_evs:
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
            _draw_timed_block(draw, ev, x0, y0, y1, cw, _event_fill(_hex_to_grey(ev.color)))


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
    layout     = _Layout.build(len(days), width, height,
                               time_window_hours, time_start_hour, today_highlight)
    day_events = _collect_day_events(days, events_by_cal)
    today      = date.today()

    img  = Image.new("L", (width, height), WHITE)
    draw = ImageDraw.Draw(img)

    _draw_header(draw, layout, days, today)
    _draw_legend(draw, layout, events_by_cal)
    _draw_allday_strip(draw, layout, day_events)
    _draw_time_axis(draw, layout)
    _draw_now_indicator(draw, layout, days, today)
    _draw_timed_events(draw, layout, day_events)

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


# ── Event block renderer ──────────────────────────────────────────────────────

def _draw_timed_block(draw: ImageDraw.ImageDraw, ev: CalEvent,
                      x: int, y0: int, y1: int, w: int, fill: int) -> None:
    """Draw a timed event block spanning y0→y1."""
    draw.rectangle([x, y0, x + w, y1], outline=fill)
    draw.rectangle([x, y0, x + 3, y1], fill=fill)      # color bar on left

    block_h = y1 - y0
    if block_h >= 14:
        time_str = ev.start.strftime("%H:%M")
        draw.text((x + 5, y0 + 1), time_str, font=FONT_TIME, fill=MGREY)
        time_w  = int(draw.textlength(time_str, font=FONT_TIME)) + 6
        label   = _truncate(ev.summary, w - time_w - 4, FONT_EVENT, draw)
        draw.text((x + 5 + time_w, y0 + 1), label, font=FONT_EVENT, fill=BLACK)
    else:
        label = _truncate(ev.summary, w - 6, FONT_EVENT, draw)
        draw.text((x + 5, y0 + 1), label, font=FONT_EVENT, fill=BLACK)


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
