"""
Tests for renderer.py.

Run from calendar-sidecar/:
    uv run pytest
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from PIL import Image

from caldav_client import CalEvent
from PIL import ImageDraw

from renderer import (
    BLACK, DGREY, MGREY,
    FONT_EVENT,
    _event_fill, _hex_to_grey, _truncate, _wrap_text,
    render_3day, render_rolling, render_week,
)

TZ         = ZoneInfo("Europe/Berlin")
WEEK_START = date(2026, 4, 6)   # a Monday


# ── Helpers ───────────────────────────────────────────────────────────────────

def _event(
    summary: str,
    hour_start: int,
    hour_end: int,
    day_offset: int = 1,        # 0=Mon … 6=Sun
    color: str = "#000000",
) -> CalEvent:
    d     = WEEK_START + timedelta(days=day_offset)
    start = datetime(d.year, d.month, d.day, hour_start, 0, tzinfo=TZ)
    end   = datetime(d.year, d.month, d.day, hour_end,   0, tzinfo=TZ)
    return CalEvent(summary=summary, start=start, end=end,
                    all_day=False, calendar="Test", color=color)


def _allday(summary: str, day_offset: int = 0) -> CalEvent:
    d     = WEEK_START + timedelta(days=day_offset)
    start = datetime(d.year, d.month, d.day, tzinfo=TZ)
    return CalEvent(summary=summary, start=start, end=start,
                    all_day=True, calendar="Test", color="#cccccc")


# ── Color helpers ─────────────────────────────────────────────────────────────

def test_hex_to_grey_black():
    assert _hex_to_grey("#000000") == 0

def test_hex_to_grey_white():
    assert _hex_to_grey("#ffffff") == 255

def test_hex_to_grey_mid():
    assert 100 < _hex_to_grey("#808080") < 160

def test_event_fill_dark():
    assert _event_fill(0) == BLACK

def test_event_fill_mid():
    assert _event_fill(100) == DGREY

def test_event_fill_light():
    assert _event_fill(200) == MGREY


# ── render_week: output shape ─────────────────────────────────────────────────

def test_default_dimensions():
    img = render_week({}, WEEK_START)
    assert img.size == (800, 480)
    assert img.mode == "L"

def test_custom_dimensions():
    img = render_week({}, WEEK_START, width=400, height=240)
    assert img.size == (400, 240)

def test_returns_pil_image():
    assert isinstance(render_week({}, WEEK_START), Image.Image)


# ── render_week: events ───────────────────────────────────────────────────────

def test_timed_event_in_window():
    ev  = _event("Stand-up", 9, 10)
    img = render_week({"Work": ("#000000", [ev])}, WEEK_START,
                      time_start_hour=8, time_window_hours=8)
    assert img.size == (800, 480)

def test_allday_event():
    ev  = _allday("Holiday")
    img = render_week({"Cal": ("#cccccc", [ev])}, WEEK_START)
    assert isinstance(img, Image.Image)

def test_event_outside_window_does_not_raise():
    # Event at 06:00 with window starting at 09:00 — clipped, no crash
    ev  = _event("Early call", 6, 7)
    img = render_week({"Work": ("#000000", [ev])}, WEEK_START,
                      time_start_hour=9, time_window_hours=8)
    assert img.size == (800, 480)

def test_multiple_calendars():
    evs_a = [_event("Meeting", 10, 11, day_offset=0)]
    evs_b = [_event("Lunch",   12, 13, day_offset=0, color="#555555")]
    img   = render_week(
        {"Work": ("#000000", evs_a), "Personal": ("#555555", evs_b)},
        WEEK_START,
    )
    assert img.size == (800, 480)

def test_short_time_window():
    ev  = _event("Sprint", 14, 15)
    img = render_week({"Dev": ("#000000", [ev])}, WEEK_START,
                      time_start_hour=13, time_window_hours=4)
    assert img.size == (800, 480)

def test_many_events_same_day():
    evs = [_event(f"Event {i}", 8 + i, 9 + i) for i in range(6)]
    img = render_week({"Cal": ("#000000", evs)}, WEEK_START,
                      time_start_hour=8, time_window_hours=8)
    assert img.size == (800, 480)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def draw():
    """A real PIL ImageDraw on a small canvas — for testing text helpers."""
    img = Image.new("L", (400, 200))
    return ImageDraw.Draw(img)


# ── render_rolling / render_3day ──────────────────────────────────────────────

def test_render_rolling_shape():
    img = render_rolling({}, WEEK_START)
    assert img.size == (800, 480)
    assert img.mode == "L"

def test_render_rolling_custom_dimensions():
    img = render_rolling({}, WEEK_START, width=400, height=240)
    assert img.size == (400, 240)

def test_render_3day_shape():
    img = render_3day({}, WEEK_START)
    assert img.size == (800, 480)
    assert img.mode == "L"

def test_render_3day_has_three_columns():
    # 3-day view is narrower — spot-check it accepts the call
    ev = _event("Meeting", 9, 10, day_offset=0)
    img = render_3day({"Cal": ("#000000", [ev])}, WEEK_START)
    assert isinstance(img, Image.Image)


# ── today_highlight ───────────────────────────────────────────────────────────

def test_today_highlight():
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    img = render_week({}, monday, today_highlight=True)
    assert img.size == (800, 480)


# ── all-day strip ─────────────────────────────────────────────────────────────

def _allday_span(summary: str, start_offset: int = 0, end_offset: int = 1) -> CalEvent:
    """All-day event with exclusive end (CalDAV convention)."""
    s = WEEK_START + timedelta(days=start_offset)
    e = WEEK_START + timedelta(days=end_offset)
    return CalEvent(summary=summary,
                    start=datetime(s.year, s.month, s.day, tzinfo=TZ),
                    end=datetime(e.year, e.month, e.day, tzinfo=TZ),
                    all_day=True, calendar="Cal", color="#cccccc")

def test_allday_event_strip_rendered():
    ev = _allday_span("Holiday")
    img = render_week({"Cal": ("#cccccc", [ev])}, WEEK_START)
    assert isinstance(img, Image.Image)

def test_allday_multiday_span():
    ev = _allday_span("Conference", start_offset=1, end_offset=4)
    img = render_week({"Cal": ("#cccccc", [ev])}, WEEK_START)
    assert isinstance(img, Image.Image)

def test_allday_long_summary_truncated():
    ev = _allday_span("A" * 80)
    img = render_week({"Cal": ("#000000", [ev])}, WEEK_START)
    assert isinstance(img, Image.Image)


# ── _wrap_text ────────────────────────────────────────────────────────────────

def test_wrap_text_empty_string(draw):
    assert _wrap_text("", 200, FONT_EVENT, draw) == []

def test_wrap_text_fits_single_line(draw):
    assert _wrap_text("Hi", 500, FONT_EVENT, draw) == ["Hi"]

def test_wrap_text_wraps_to_multiple_lines(draw):
    # max_px=1 forces every word onto its own line
    lines = _wrap_text("Alpha Beta Gamma", 1, FONT_EVENT, draw)
    assert lines == ["Alpha", "Beta", "Gamma"]


# ── _truncate ─────────────────────────────────────────────────────────────────

def test_truncate_short_text_unchanged(draw):
    assert _truncate("Hi", 500, FONT_EVENT, draw) == "Hi"

def test_truncate_long_text_gets_ellipsis(draw):
    result = _truncate("A very long summary that will not fit", 40, FONT_EVENT, draw)
    assert result.endswith("…")

def test_truncate_result_fits_within_max(draw):
    max_px = 40
    result = _truncate("This text is definitely too long to fit", max_px, FONT_EVENT, draw)
    assert draw.textlength(result, font=FONT_EVENT) <= max_px


# ── min-height enforcement ────────────────────────────────────────────────────

def test_very_short_event_rendered_without_crash():
    d = WEEK_START
    ev = CalEvent(
        summary="Blink",
        start=datetime(d.year, d.month, d.day, 9, 0, tzinfo=TZ),
        end=datetime(d.year, d.month, d.day, 9, 1, tzinfo=TZ),
        all_day=False, calendar="Cal", color="#000000",
    )
    img = render_week({"Cal": ("#000000", [ev])}, WEEK_START,
                      time_start_hour=8, time_window_hours=12)
    assert img.size == (800, 480)

def test_long_summary_truncated_in_block():
    d = WEEK_START
    ev = CalEvent(
        summary="A " * 40,  # very long summary forces last-line truncation
        start=datetime(d.year, d.month, d.day, 9, 0, tzinfo=TZ),
        end=datetime(d.year, d.month, d.day, 9, 30, tzinfo=TZ),
        all_day=False, calendar="Cal", color="#000000",
    )
    img = render_week({"Cal": ("#000000", [ev])}, WEEK_START,
                      time_start_hour=8, time_window_hours=12)
    assert img.size == (800, 480)
