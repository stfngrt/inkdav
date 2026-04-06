"""
cli.py
======
Command-line entry point for rendering a calendar PNG from JSON event data.

Usage:
    python cli.py <events.json> [--week YYYY-MM-DD] [--width 800] [--height 480]
                                [--time-window 12] [--time-start 8] [-o output.png]
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from caldav_client import CalEvent


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
    parser.add_argument("--renderer", choices=["pillow", "weasy"], default="pillow",
                        help="Rendering backend: 'pillow' (default) or 'weasy' (WeasyPrint)")
    args = parser.parse_args()

    if args.week:
        week_start = date.fromisoformat(args.week)
        if week_start.weekday() != 0:
            print(f"Error: --week {args.week} is not a Monday", file=sys.stderr)
            sys.exit(1)
    else:
        today      = date.today()
        week_start = today - timedelta(days=today.weekday())

    if args.renderer == "weasy":
        from renderer_weasy import render_week
    else:
        from renderer import render_week

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
