"""
renderer_weasy.py
=================
WeasyPrint-based renderer.

Pipeline:
  Python computes layout geometry → Jinja2 renders calendar_weasy.html
  → WeasyPrint PDF → pdftoppm -r 96 → PIL RGB
  → greyscale → Floyd-Steinberg 1-bit → L mode

Public API is identical to renderer.py.
"""

from __future__ import annotations

import dataclasses
import os
import subprocess
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

import jinja2
from PIL import Image

from caldav_client import CalEvent
from scheduling import (
    MAX_ALLDAY_ROWS,
    _assign_allday_rows,
    _assign_lanes,
    _collect_day_events,
)

# ── Fixed layout constants (must match renderer.py) ───────────────────────────
HEADER_H     = 28
LEGEND_H     = 18
BODY_TOP     = HEADER_H + LEGEND_H
ALLDAY_ROW_H = 18
TIME_AXIS_W  = 28
PADDING      = 2
EVENT_MIN_H  = 14

# ── Colors ────────────────────────────────────────────────────────────────────
BLACK = 0
WHITE = 255
LGREY = 235
MGREY = 130
DGREY = 80

_CAL_FILLS = [0, 80, 160, 220]

DAY_NAMES = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]

# ── Jinja2 environment ────────────────────────────────────────────────────────
_TEMPLATE_DIR = Path(__file__).parent / "templates"
_jinja_env = jinja2.Environment(
    loader=jinja2.FileSystemLoader(str(_TEMPLATE_DIR)),
    autoescape=True,
)

# ── Hyphenation (module-level, mirrors renderer.py interface) ─────────────────
_hyphenation_lang: str = "de_DE"


def set_hyphenation_lang(lang: str) -> None:
    global _hyphenation_lang
    _hyphenation_lang = lang


# ── Color helpers ─────────────────────────────────────────────────────────────

def _grey_css(v: int) -> str:
    h = format(v, "02x")
    return f"#{h}{h}{h}"


def _build_fill_map(
    events_by_cal: dict[str, tuple[str, list[CalEvent]]],
) -> dict[str, int]:
    fills: dict[str, int] = {}
    idx = 0
    for _, (color, _) in events_by_cal.items():
        if color not in fills:
            fills[color] = _CAL_FILLS[idx % len(_CAL_FILLS)]
            idx += 1
    return fills


# ── Layout (identical to renderer.py) ────────────────────────────────────────
@dataclasses.dataclass
class _Layout:
    width:           int
    height:          int
    num_cols:        int
    col_w:           int
    allday_top:      int
    allday_rows:     int
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
        grid_top     = allday_top + allday_strip + 1
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


# ── Font helpers ──────────────────────────────────────────────────────────────

def _font_path(bold: bool = False) -> str | None:
    candidates = [
        f"/usr/share/fonts/truetype/dejavu/DejaVuSans{'-Bold' if bold else ''}.ttf",
        f"/usr/share/fonts/dejavu/DejaVuSans{'-Bold' if bold else ''}.ttf",
    ]
    for p in candidates:
        if Path(p).exists():
            return p
    return None


def _font_src(bold: bool = False) -> str:
    p = _font_path(bold=bold)
    return f"url('{p}')" if p else f"local('DejaVu Sans{' Bold' if bold else ''}')"


def _measure_text(text: str, size: int = 10, bold: bool = False) -> int:
    """Pixel width of text using Pillow (matches renderer.py legend layout)."""
    try:
        from PIL import ImageDraw, ImageFont
        from PIL import Image as _Image
        p = _font_path(bold=bold)
        font = ImageFont.truetype(p, size) if p else ImageFont.load_default()
        _img  = _Image.new("L", (1, 1))
        _draw = ImageDraw.Draw(_img)
        return int(_draw.textlength(text, font=font))
    except Exception:
        return len(text) * 6


# ── Template context builder ──────────────────────────────────────────────────

def _build_context(
    layout: _Layout,
    days: list[date],
    today: date,
    events_by_cal: dict[str, tuple[str, list[CalEvent]]],
    fill_map: dict[str, int],
    timed_events: dict[int, list[CalEvent]],
    allday_assignments: list[tuple[CalEvent, int, int, int]],
    event_font_size: int = 10,
    event_bold: bool = True,
) -> dict:
    W = layout.width
    H = layout.height

    # ── Column headers ───────────────────────────────────────────────────────
    cols = []
    for col, day in enumerate(days):
        x0    = TIME_AXIS_W + col * layout.col_w
        label = f"{DAY_NAMES[day.weekday()]} {day.day:02d}.{day.month:02d}."
        cols.append({"index": col, "x0": x0, "label": label})

    # ── Legend items ─────────────────────────────────────────────────────────
    legend_items = []
    lx = TIME_AXIS_W + PADDING
    for cal_name, (color, _) in events_by_cal.items():
        fill     = fill_map.get(color, BLACK)
        fill_css = _grey_css(fill)
        swatch_x = lx
        lx      += 14
        tw       = _measure_text(cal_name, size=10)
        legend_items.append({
            "name":            cal_name,
            "fill_css":        fill_css,
            "swatch_x":        swatch_x,
            "text_backing_x":  lx - 1,
            "text_backing_w":  tw + 3,
            "text_x":          lx,
        })
        lx += tw + 14

    # ── All-day events ───────────────────────────────────────────────────────
    strip_h = layout.allday_rows * ALLDAY_ROW_H
    allday_ctx = []
    for ev, first_col, last_col, row in allday_assignments:
        y0       = layout.allday_top + row * ALLDAY_ROW_H + 1
        x0       = TIME_AXIS_W + first_col * layout.col_w + PADDING
        x1       = TIME_AXIS_W + (last_col + 1) * layout.col_w - PADDING
        bar_w    = x1 - x0
        fill_css = _grey_css(fill_map.get(ev.color, BLACK))
        allday_ctx.append({
            "x0":       x0,
            "y0":       y0,
            "width":    bar_w,
            "height":   ALLDAY_ROW_H - 2,
            "fill_css": fill_css,
            "summary":  ev.summary,
        })

    # ── Hour lines ───────────────────────────────────────────────────────────
    hours = []
    for h in range(layout.time_start_hour, min(layout.time_end_hour + 1, 25)):
        y = layout.grid_top + int((h - layout.time_start_hour) * layout.px_per_hour)
        if y >= H:
            break
        hours.append({"y": y, "label": f"{h:02d}"})

    # ── Now indicator ────────────────────────────────────────────────────────
    now_indicator = None
    now      = datetime.now()
    now_frac = now.hour + now.minute / 60
    if layout.time_start_hour <= now_frac < layout.time_end_hour:
        try:
            col = days.index(today)
            ny  = layout.grid_top + int((now_frac - layout.time_start_hour) * layout.px_per_hour)
            tx0 = TIME_AXIS_W + col * layout.col_w
            now_indicator = {
                "dot_x":  TIME_AXIS_W - 5,
                "dot_y":  ny - 3,
                "line_x": tx0,
                "line_y": ny - 1,
            }
        except ValueError:
            pass

    # ── Timed events ─────────────────────────────────────────────────────────
    lang_attr  = _hyphenation_lang.replace("_", "-").lower()
    timed_ctx  = []
    for col, evs in timed_events.items():
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
            y0      = layout.grid_top + int((vis_start - layout.time_start_hour) * layout.px_per_hour)
            y1      = layout.grid_top + int((vis_end   - layout.time_start_hour) * layout.px_per_hour) - 1
            if y1 - y0 < EVENT_MIN_H:
                y1 = y0 + EVENT_MIN_H
            block_h = y1 - y0
            if block_h < 12:
                continue

            short    = (ev.end - ev.start).total_seconds() <= 1800
            fill_css = _grey_css(fill_map.get(ev.color, BLACK))
            timed_ctx.append({
                "x0":       x0,
                "y0":       y0,
                "width":    lw,
                "height":   block_h,
                "fill_css": fill_css,
                "short":    short,
                "summary":  ev.summary,
                "time_str": ev.start.strftime("%H:%M"),
            })

    return {
        # dimensions
        "width":        W,
        "height":       H,
        "num_cols":     layout.num_cols,
        "col_w":        layout.col_w,
        "header_h":     HEADER_H,
        "legend_h":     LEGEND_H,
        "time_axis_w":  TIME_AXIS_W,
        "allday_top":   layout.allday_top,
        "allday_bottom": layout.allday_top + strip_h,
        # colors
        "mgrey_css":    _grey_css(MGREY),
        "lgrey_css":    _grey_css(LGREY),
        # fonts
        "regular_font_src": _font_src(bold=False),
        "bold_font_src":    _font_src(bold=True),
        # dates
        "today":          today,
        "days":           days,
        "today_highlight": layout.today_highlight,
        # elements
        "cols":           cols,
        "legend_items":   legend_items,
        "allday_events":  allday_ctx,
        "hours":          hours,
        "now_indicator":  now_indicator,
        "timed_events":     timed_ctx,
        "lang_attr":        lang_attr,
        "event_font_size":  event_font_size,
        "event_bold_css":   "bold" if event_bold else "normal",
    }


# ── WeasyPrint → PIL ──────────────────────────────────────────────────────────

def _weasy_to_pil(html: str, width: int, height: int) -> Image.Image:
    import weasyprint

    pdf_bytes = weasyprint.HTML(string=html, base_url=str(_TEMPLATE_DIR)).write_pdf()

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        f.write(pdf_bytes)
        pdf_path = f.name

    png_base = pdf_path[:-4]
    png_path = png_base + ".png"
    try:
        subprocess.run(
            ["pdftoppm", "-r", "96", "-png", "-singlefile", pdf_path, png_base],
            check=True,
            capture_output=True,
        )
        return Image.open(png_path).copy()
    finally:
        for p in (pdf_path, png_path):
            if os.path.exists(p):
                os.unlink(p)


# ── Core renderer ─────────────────────────────────────────────────────────────

def render_days(
    days: list[date],
    events_by_cal: dict[str, tuple[str, list[CalEvent]]],
    width: int = 800,
    height: int = 480,
    time_window_hours: int = 12,
    time_start_hour: int = 8,
    today_highlight: bool = False,
    event_font_size: int = 10,
    event_bold: bool = True,
) -> Image.Image:
    """
    Render a calendar image using WeasyPrint (HTML/CSS → PDF → PNG → 1-bit).

    Drop-in replacement for renderer.render_days with identical signature.
    """
    fill_map                    = _build_fill_map(events_by_cal)
    timed_events, allday_events = _collect_day_events(days, events_by_cal)
    allday_assignments          = _assign_allday_rows(allday_events, days)
    used_rows                   = max((r for _, _, _, r in allday_assignments), default=-1) + 1
    allday_rows                 = max(1, min(used_rows, MAX_ALLDAY_ROWS))
    layout                      = _Layout.build(len(days), width, height,
                                                time_window_hours, time_start_hour,
                                                today_highlight, allday_rows)
    today   = date.today()
    ctx     = _build_context(layout, days, today, events_by_cal, fill_map,
                             timed_events, allday_assignments,
                             event_font_size=event_font_size, event_bold=event_bold)
    tmpl    = _jinja_env.get_template("calendar_weasy.html")
    html    = tmpl.render(**ctx)
    img = _weasy_to_pil(html, width, height)
    return img.convert("L")


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
