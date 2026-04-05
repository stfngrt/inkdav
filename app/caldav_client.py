"""
caldav_client.py
================
Fetches VEVENT entries from a CalDAV calendar using the `caldav` and
`icalendar` libraries (RFC 4791 / RFC 5545).
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import caldav
from icalendar import Calendar


@dataclass
class CalEvent:
    summary:  str
    start:    datetime          # always timezone-aware, converted to local_tz
    end:      datetime
    all_day:  bool
    calendar: str               # calendar display name
    color:    str               # hex color string


def fetch_range(
    url:        str,
    user:       str,
    password:   str,
    cal_name:   str,
    color:      str,
    start:      date,
    end:        date,           # exclusive end date
    local_tz:   ZoneInfo = ZoneInfo("Europe/Berlin"),
) -> list[CalEvent]:
    """
    Fetch all events in [start, end) from one CalDAV calendar.
    Returns a list of CalEvent, sorted by start time.
    """
    client   = caldav.DAVClient(url=url, username=user, password=password)
    calendar = client.calendar(url=url)

    # expand=True asks the server to expand recurring events in the range
    dav_events = calendar.search(
        start=datetime(start.year, start.month, start.day),
        end=datetime(end.year,   end.month,   end.day),
        event=True,
        expand=True,
    )

    events: list[CalEvent] = []
    for dav_event in dav_events:
        cal: Calendar = Calendar.from_ical(dav_event.data)
        for component in cal.walk("VEVENT"):
            ev = _component_to_event(component, cal_name, color, start, end, local_tz)
            if ev:
                events.append(ev)

    events.sort(key=lambda e: e.start)
    return events


def fetch_week(
    url:        str,
    user:       str,
    password:   str,
    cal_name:   str,
    color:      str,
    week_start: date,           # Monday of the target week
    local_tz:   ZoneInfo = ZoneInfo("Europe/Berlin"),
) -> list[CalEvent]:
    """Fetch all events for the given Monday→Sunday week. Convenience wrapper."""
    return fetch_range(url, user, password, cal_name, color,
                       week_start, week_start + timedelta(days=7), local_tz)


def _component_to_event(
    component,
    cal_name:   str,
    color:      str,
    week_start: date,
    week_end:   date,
    local_tz:   ZoneInfo,
) -> CalEvent | None:
    summary = str(component.get("SUMMARY", "(Kein Titel)"))

    dtstart_prop = component.get("DTSTART")
    if dtstart_prop is None:
        return None

    raw_start = dtstart_prop.dt
    all_day   = isinstance(raw_start, date) and not isinstance(raw_start, datetime)

    if all_day:
        start = datetime(raw_start.year, raw_start.month, raw_start.day, tzinfo=local_tz)
    else:
        start = _to_local(raw_start, local_tz)

    dtend_prop = component.get("DTEND")
    if dtend_prop is not None:
        raw_end = dtend_prop.dt
        if all_day:
            end = datetime(raw_end.year, raw_end.month, raw_end.day, tzinfo=local_tz)
        else:
            end = _to_local(raw_end, local_tz)
    else:
        end = start + timedelta(hours=1)

    # Filter: keep events that actually overlap [week_start, week_end).
    # All-day DTEND is exclusive in CalDAV (DTEND=Apr 5 means ends before Apr 5),
    # so <= is correct there. Timed events ending on week_start (e.g. 15:00 on
    # Apr 5) do overlap the window, so use strict < for those.
    if all_day:
        if start.date() >= week_end or end.date() <= week_start:
            return None
    else:
        if start.date() >= week_end or end.date() < week_start:
            return None

    return CalEvent(
        summary  = summary,
        start    = start,
        end      = end,
        all_day  = all_day,
        calendar = cal_name,
        color    = color,
    )


def _to_local(dt: datetime, local_tz: ZoneInfo) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=local_tz)
    return dt.astimezone(local_tz)
