"""
scheduling.py
=============
Pure scheduling algorithms for calendar event layout.

No PIL or font dependencies — safe to import and test in isolation.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from caldav_client import CalEvent

MAX_ALLDAY_ROWS = 4  # maximum stacked all-day rows (extras are dropped)


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
