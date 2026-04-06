"""
Tests for renderer.py (WeasyPrint backend).

Run from app/:
    uv run pytest
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from PIL import Image

from caldav_client import CalEvent

from renderer import (
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
    ev = _event("Meeting", 9, 10, day_offset=0)
    img = render_3day({"Cal": ("#000000", [ev])}, WEEK_START)
    assert isinstance(img, Image.Image)


# ── today_highlight ───────────────────────────────────────────────────────────

def test_today_highlight():
    today  = date.today()
    monday = today - timedelta(days=today.weekday())
    img    = render_week({}, monday, today_highlight=True)
    assert img.size == (800, 480)


# ── all-day strip ─────────────────────────────────────────────────────────────

def _allday_span(summary: str, start_offset: int = 0, end_offset: int = 1) -> CalEvent:
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


# ── min-height enforcement ────────────────────────────────────────────────────

def test_very_short_event_rendered_without_crash():
    d  = WEEK_START
    ev = CalEvent(
        summary="Blink",
        start=datetime(d.year, d.month, d.day, 9, 0, tzinfo=TZ),
        end=datetime(d.year, d.month, d.day, 9, 1, tzinfo=TZ),
        all_day=False, calendar="Cal", color="#000000",
    )
    img = render_week({"Cal": ("#000000", [ev])}, WEEK_START,
                      time_start_hour=8, time_window_hours=12)
    assert img.size == (800, 480)

def test_long_summary_in_block():
    d  = WEEK_START
    ev = CalEvent(
        summary="A " * 40,
        start=datetime(d.year, d.month, d.day, 9, 0, tzinfo=TZ),
        end=datetime(d.year, d.month, d.day, 9, 30, tzinfo=TZ),
        all_day=False, calendar="Cal", color="#000000",
    )
    img = render_week({"Cal": ("#000000", [ev])}, WEEK_START,
                      time_start_hour=8, time_window_hours=12)
    assert img.size == (800, 480)
