"""
Tests for cli.py — specifically _load_events JSON parsing.
"""

from __future__ import annotations

import json
from datetime import datetime

import pytest

from cli import _load_events


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def timed_json(tmp_path):
    data = [{"name": "Work", "color": "#000000", "events": [
        {"summary": "Stand-up",
         "start": "2026-04-07T09:00:00+02:00",
         "end":   "2026-04-07T09:30:00+02:00",
         "all_day": False},
    ]}]
    f = tmp_path / "events.json"
    f.write_text(json.dumps(data))
    return str(f)


@pytest.fixture
def allday_json(tmp_path):
    data = [{"name": "Holidays", "color": "#ff0000", "events": [
        {"summary": "Easter",
         "start": "2026-04-06",
         "end":   "2026-04-07"},
    ]}]
    f = tmp_path / "events.json"
    f.write_text(json.dumps(data))
    return str(f)


@pytest.fixture
def empty_cal_json(tmp_path):
    data = [{"name": "Empty", "color": "#888888", "events": []}]
    f = tmp_path / "events.json"
    f.write_text(json.dumps(data))
    return str(f)


# ── _load_events ──────────────────────────────────────────────────────────────

def test_load_timed_event_structure(timed_json):
    result = _load_events(timed_json)
    assert "Work" in result
    color, events = result["Work"]
    assert color == "#000000"
    assert len(events) == 1


def test_load_timed_event_fields(timed_json):
    _, events = _load_events(timed_json)["Work"]
    ev = events[0]
    assert ev.summary == "Stand-up"
    assert not ev.all_day
    assert ev.calendar == "Work"
    assert ev.color == "#000000"
    assert isinstance(ev.start, datetime)
    assert ev.start.hour == 9


def test_load_allday_event(allday_json):
    _, events = _load_events(allday_json)["Holidays"]
    ev = events[0]
    assert ev.all_day
    assert ev.summary == "Easter"


def test_load_allday_auto_detected(tmp_path):
    """all_day flag inferred from 10-char date string when not set explicitly."""
    data = [{"name": "Cal", "color": "#000000", "events": [
        {"summary": "Day off", "start": "2026-04-07", "end": "2026-04-08"},
    ]}]
    f = tmp_path / "events.json"
    f.write_text(json.dumps(data))
    _, events = _load_events(str(f))["Cal"]
    assert events[0].all_day


def test_load_empty_calendar(empty_cal_json):
    result = _load_events(empty_cal_json)
    color, events = result["Empty"]
    assert color == "#888888"
    assert events == []


def test_load_missing_end_defaults_to_start(tmp_path):
    data = [{"name": "Cal", "color": "#000000", "events": [
        {"summary": "Point-in-time", "start": "2026-04-07T10:00:00+02:00"},
    ]}]
    f = tmp_path / "events.json"
    f.write_text(json.dumps(data))
    _, events = _load_events(str(f))["Cal"]
    assert events[0].start == events[0].end


def test_load_multiple_calendars(tmp_path):
    data = [
        {"name": "Work",     "color": "#000000", "events": [
            {"summary": "A", "start": "2026-04-07T09:00:00+02:00",
             "end": "2026-04-07T10:00:00+02:00"}]},
        {"name": "Personal", "color": "#ff0000", "events": [
            {"summary": "B", "start": "2026-04-07T11:00:00+02:00",
             "end": "2026-04-07T12:00:00+02:00"}]},
    ]
    f = tmp_path / "events.json"
    f.write_text(json.dumps(data))
    result = _load_events(str(f))
    assert set(result.keys()) == {"Work", "Personal"}


def test_load_missing_summary_defaults(tmp_path):
    data = [{"name": "Cal", "color": "#000000", "events": [
        {"start": "2026-04-07T09:00:00+02:00", "end": "2026-04-07T10:00:00+02:00"},
    ]}]
    f = tmp_path / "events.json"
    f.write_text(json.dumps(data))
    _, events = _load_events(str(f))["Cal"]
    assert events[0].summary == "(No title)"
