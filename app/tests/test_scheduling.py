"""
Tests for scheduling.py — pure algorithm functions with no PIL dependency.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from caldav_client import CalEvent
from scheduling import (
    MAX_ALLDAY_ROWS,
    _assign_allday_rows,
    _assign_lanes,
    _collect_day_events,
)

TZ = ZoneInfo("Europe/Berlin")
MONDAY = date(2026, 4, 6)  # a known Monday


def _days(n: int = 7) -> list[date]:
    return [MONDAY + timedelta(days=i) for i in range(n)]


def _timed(hour_start: int, hour_end: int, day_offset: int = 0) -> CalEvent:
    d = MONDAY + timedelta(days=day_offset)
    return CalEvent(
        summary="Event",
        start=datetime(d.year, d.month, d.day, hour_start, 0, tzinfo=TZ),
        end=datetime(d.year, d.month, d.day, hour_end, 0, tzinfo=TZ),
        all_day=False,
        calendar="Cal",
        color="#000000",
    )


def _allday(start_offset: int, end_offset: int) -> CalEvent:
    """end_offset is exclusive (CalDAV convention)."""
    s = MONDAY + timedelta(days=start_offset)
    e = MONDAY + timedelta(days=end_offset)
    return CalEvent(
        summary="AllDay",
        start=datetime(s.year, s.month, s.day, 0, 0, tzinfo=TZ),
        end=datetime(e.year, e.month, e.day, 0, 0, tzinfo=TZ),
        all_day=True,
        calendar="Cal",
        color="#000000",
    )


# ── _assign_lanes ─────────────────────────────────────────────────────────────

def test_assign_lanes_empty():
    assert _assign_lanes([]) == []


def test_assign_lanes_single():
    ev = _timed(9, 10)
    result = _assign_lanes([ev])
    assert result == [(ev, 0, 1)]


def test_assign_lanes_non_overlapping():
    ev1 = _timed(9, 10)
    ev2 = _timed(11, 12)
    result = _assign_lanes([ev1, ev2])
    assert result == [(ev1, 0, 1), (ev2, 0, 1)]


def test_assign_lanes_fully_overlapping():
    ev1 = _timed(9, 11)
    ev2 = _timed(9, 11)
    result = _assign_lanes([ev1, ev2])
    assert len(result) == 2
    lanes = [lane for _, lane, _ in result]
    totals = [total for _, _, total in result]
    assert set(lanes) == {0, 1}
    assert all(t == 2 for t in totals)


def test_assign_lanes_third_reuses_lane():
    # ev1: 9-11, ev2: 9-10 (overlaps ev1), ev3: 10-12
    # sorted by start: ev1+ev2 at 9, ev3 at 10
    # ev1 → lane 0, ev2 → lane 1, ev3: start(10) >= end of lane 1(10) → reuses lane 1
    ev1 = _timed(9, 11)
    ev2 = _timed(9, 10)
    ev3 = _timed(10, 12)
    result = _assign_lanes([ev1, ev2, ev3])
    assert len(result) == 3
    # ev1 and ev2 are both at 9:00 so first two items share no ordering guarantee,
    # but ev3 must be last (latest start)
    ev3_result = next((r for r in result if r[0] is ev3), None)
    assert ev3_result is not None
    # ev3 should reuse an existing lane (total lanes stays at 2)
    assert ev3_result[2] == 2


def test_assign_lanes_sorted_by_start():
    ev_late = _timed(11, 12)
    ev_early = _timed(9, 10)
    result = _assign_lanes([ev_late, ev_early])
    assert result[0][0] is ev_early
    assert result[1][0] is ev_late


# ── _assign_allday_rows ───────────────────────────────────────────────────────

def test_assign_allday_rows_single():
    days = _days()
    ev = _allday(0, 1)
    result = _assign_allday_rows([ev], days)
    assert len(result) == 1
    _, first_col, last_col, row = result[0]
    assert first_col == 0
    assert row == 0


def test_assign_allday_rows_non_overlapping_same_row():
    days = _days()
    ev1 = _allday(0, 1)   # Monday only
    ev2 = _allday(3, 4)   # Thursday only
    result = _assign_allday_rows([ev1, ev2], days)
    assert len(result) == 2
    # sorted by first_col: ev1 (col 0) comes before ev2 (col 3)
    assert result[0][0] is ev1 and result[0][3] == 0
    assert result[1][0] is ev2 and result[1][3] == 0


def test_assign_allday_rows_overlapping_different_rows():
    days = _days()
    ev1 = _allday(0, 3)   # Mon–Wed
    ev2 = _allday(1, 4)   # Tue–Thu (overlaps ev1)
    result = _assign_allday_rows([ev1, ev2], days)
    assert len(result) == 2
    row1 = result[0][3]
    row2 = result[1][3]
    assert row1 != row2


def test_assign_allday_rows_clamp_start_before_view():
    days = _days()
    ev = _allday(-2, 2)   # starts 2 days before view
    result = _assign_allday_rows([ev], days)
    assert len(result) == 1
    _, first_col, _, _ = result[0]
    assert first_col == 0


def test_assign_allday_rows_clamp_end_after_view():
    days = _days()
    ev = _allday(5, 10)   # ends after 7-day view
    result = _assign_allday_rows([ev], days)
    assert len(result) == 1
    _, _, last_col, _ = result[0]
    assert last_col == len(days) - 1


def test_assign_allday_rows_overflow_drops_extras():
    days = _days()
    # MAX_ALLDAY_ROWS + 1 events all on the same day → last one dropped
    events = [_allday(0, 1) for _ in range(MAX_ALLDAY_ROWS + 1)]
    result = _assign_allday_rows(events, days)
    assert len(result) == MAX_ALLDAY_ROWS


# ── _collect_day_events ───────────────────────────────────────────────────────

def _cal(events: list[CalEvent]) -> dict:
    return {"Cal": ("#000000", events)}


def test_collect_timed_event_in_correct_column():
    days = _days()
    ev = _timed(9, 10, day_offset=2)   # Wednesday = column 2
    timed, allday = _collect_day_events(days, _cal([ev]))
    assert ev in timed[2]
    assert allday == []


def test_collect_timed_event_outside_view_ignored():
    days = _days()
    ev = _timed(9, 10, day_offset=10)  # beyond the 7-day window
    timed, allday = _collect_day_events(days, _cal([ev]))
    assert all(len(v) == 0 for v in timed.values())


def test_collect_allday_event_in_view_included():
    days = _days()
    ev = _allday(2, 5)
    timed, allday = _collect_day_events(days, _cal([ev]))
    assert ev in allday


def test_collect_allday_event_outside_view_excluded():
    days = _days()
    ev = _allday(8, 10)  # starts after the 7-day window
    timed, allday = _collect_day_events(days, _cal([ev]))
    assert allday == []


def test_collect_multiple_calendars():
    days = _days()
    ev1 = _timed(9, 10, day_offset=0)
    ev2 = _timed(11, 12, day_offset=1)
    events_by_cal = {
        "CalA": ("#ff0000", [ev1]),
        "CalB": ("#0000ff", [ev2]),
    }
    timed, _ = _collect_day_events(days, events_by_cal)
    assert ev1 in timed[0]
    assert ev2 in timed[1]
