"""
Tests for server.py: fetch fallback, health, webhook skipping, time window,
and the admin calendar form.
"""

# pylint: disable=protected-access

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

import config
import server
from caldav_client import CalEvent

TZ = ZoneInfo("Europe/Berlin")

CAL = {
    "name": "Work", "url": "https://dav.example.com/work/",
    "user": "alice", "password": "s3cret", "color": "#000000",
}


@pytest.fixture(autouse=True)
def fresh_state(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(config, "_config", None)
    config.load()
    cfg = config.get()
    cfg["calendars"] = [dict(CAL)]
    cfg["timezone"] = "Europe/Berlin"
    config.update(cfg)
    monkeypatch.setattr(server, "_last_good", {})
    monkeypatch.setattr(server, "_cal_status", {})
    monkeypatch.setattr(server, "_last_error", None)


def _event(start: datetime, end: datetime, summary: str = "Meeting") -> CalEvent:
    return CalEvent(summary=summary, start=start, end=end, all_day=False,
                    calendar="Work", color="#000000")


# ── _fetch_all ────────────────────────────────────────────────────────────────

def test_failed_fetch_reuses_last_good_events(monkeypatch):
    ev = _event(datetime(2026, 11, 2, 9, tzinfo=TZ), datetime(2026, 11, 2, 10, tzinfo=TZ))
    monkeypatch.setattr(server, "fetch_range", lambda **_kw: [ev])
    server._fetch_all(config.calendars(), date(2026, 11, 2), date(2026, 11, 16))

    def boom(**_kw):
        raise ConnectionError("server down")
    monkeypatch.setattr(server, "fetch_range", boom)
    events_by_cal, missing = server._fetch_all(
        config.calendars(), date(2026, 11, 2), date(2026, 11, 16))

    assert not missing
    assert [e.summary for e in events_by_cal["Work"][1]] == ["Meeting"]
    status = server._cal_status[CAL["url"]]
    assert status["error"] == "server down"
    assert status["last_ok"] is not None


def test_failed_fetch_without_earlier_data_is_reported_missing(monkeypatch):
    def boom(**_kw):
        raise ConnectionError("server down")
    monkeypatch.setattr(server, "fetch_range", boom)
    events_by_cal, missing = server._fetch_all(
        config.calendars(), date(2026, 11, 2), date(2026, 11, 16))

    assert missing == ["Work"]
    assert events_by_cal["Work"] == ("#000000", [])
    assert server._cal_status[CAL["url"]] == {"last_ok": None, "error": "server down"}


def test_cached_events_follow_renamed_calendar(monkeypatch):
    ev = _event(datetime(2026, 11, 2, 9, tzinfo=TZ), datetime(2026, 11, 2, 10, tzinfo=TZ))
    monkeypatch.setattr(server, "fetch_range", lambda **_kw: [ev])
    server._fetch_all(config.calendars(), date(2026, 11, 2), date(2026, 11, 16))

    def boom(**_kw):
        raise ConnectionError("server down")
    monkeypatch.setattr(server, "fetch_range", boom)
    renamed = [dict(CAL, name="Job", color="#ff0000")]
    events_by_cal, _ = server._fetch_all(renamed, date(2026, 11, 2), date(2026, 11, 16))

    color, evs = events_by_cal["Job"]
    assert color == "#ff0000"
    assert (evs[0].calendar, evs[0].color) == ("Job", "#ff0000")


# ── refresh ───────────────────────────────────────────────────────────────────

def test_refresh_fetches_each_calendar_once_for_both_windows(monkeypatch):
    calls = []
    monkeypatch.setattr(server, "fetch_range", lambda **kw: calls.append(kw) or [])
    monkeypatch.setattr(server, "_render_to_bytes", lambda *_a, **_kw: b"png")
    monkeypatch.setattr(server, "_fire_webhooks", lambda: None)

    server.refresh()

    assert len(calls) == 1
    # Covers this week and next week: 14 days.
    assert calls[0]["end"] - calls[0]["start"] == timedelta(days=14)


def test_refresh_skips_webhooks_when_a_calendar_has_no_data(monkeypatch):
    def boom(**_kw):
        raise ConnectionError("server down")
    fired = []
    monkeypatch.setattr(server, "fetch_range", boom)
    monkeypatch.setattr(server, "_render_to_bytes", lambda *_a, **_kw: b"png")
    monkeypatch.setattr(server, "_fire_webhooks", lambda: fired.append(1))

    server.refresh()
    assert not fired

    monkeypatch.setattr(server, "fetch_range", lambda **_kw: [])
    server.refresh()
    assert fired == [1]


# ── /health ───────────────────────────────────────────────────────────────────

def test_health_is_degraded_when_a_calendar_fails(monkeypatch):
    def boom(**_kw):
        raise ConnectionError("server down")
    monkeypatch.setattr(server, "fetch_range", boom)
    server._fetch_all(config.calendars(), date(2026, 11, 2), date(2026, 11, 16))

    body = json.loads(server.admin.test_client().get("/health").data)
    assert body["status"] == "degraded"
    assert body["calendars"]["Work"]["error"] == "server down"


def test_health_is_ok_when_all_calendars_succeed(monkeypatch):
    monkeypatch.setattr(server, "fetch_range", lambda **_kw: [])
    server._fetch_all(config.calendars(), date(2026, 11, 2), date(2026, 11, 16))

    body = json.loads(server.admin.test_client().get("/health").data)
    assert body["status"] == "ok"
    assert body["calendars"]["Work"]["error"] is None


# ── Time window ───────────────────────────────────────────────────────────────

def test_timed_spans_split_overnight_event_per_day():
    days = [date(2026, 11, 2), date(2026, 11, 3)]
    ev = _event(datetime(2026, 11, 2, 22, tzinfo=TZ), datetime(2026, 11, 3, 2, tzinfo=TZ))
    assert server._timed_spans(days, {"Work": ("#000000", [ev])}) == [(22.0, 24.0), (0.0, 2.0)]


def test_timed_spans_ignore_days_outside_window():
    days = [date(2026, 11, 2)]
    ev = _event(datetime(2026, 11, 9, 9, tzinfo=TZ), datetime(2026, 11, 9, 10, tzinfo=TZ))
    assert not server._timed_spans(days, {"Work": ("#000000", [ev])})


def test_auto_window_ignores_events_outside_rendered_days():
    cfg = config.get()
    cfg["time_start_mode"] = "auto"
    cfg["time_window_hours"] = 12
    config.update(cfg)

    days = [date(2026, 11, 2) + timedelta(days=i) for i in range(7)]
    now  = datetime(2026, 11, 2, 12, tzinfo=TZ)
    # An early event next week must not pull this week's window up.
    next_week = _event(datetime(2026, 11, 9, 0, tzinfo=TZ), datetime(2026, 11, 9, 1, tzinfo=TZ))
    with_other = server._time_start_for(days, {"Work": ("#000000", [next_week])}, now=now)
    without    = server._time_start_for(days, {}, now=now)
    assert with_other == without == 8


def test_local_today_uses_configured_timezone():
    # These two zones are 26 hours apart, so their dates always differ.
    cfg = config.get()
    cfg["timezone"] = "Pacific/Kiritimati"
    config.update(cfg)
    ahead = server._local_today()
    cfg["timezone"] = "Etc/GMT+12"
    config.update(cfg)
    behind = server._local_today()
    assert ahead != behind


# ── Admin calendar form ───────────────────────────────────────────────────────

def test_admin_page_does_not_contain_password():
    page = server.admin.test_client().get("/").data.decode()
    assert CAL["password"] not in page


def test_edit_with_blank_password_keeps_stored_password():
    form = {"name": "Work", "url": CAL["url"], "user": "alice",
            "password": "", "color": "#000000"}
    server.admin.test_client().post("/calendar/0/edit", data=form)
    assert config.calendars()[0]["password"] == "s3cret"


def test_edit_with_new_password_replaces_it():
    form = {"name": "Work", "url": CAL["url"], "user": "alice",
            "password": "n3w", "color": "#000000"}
    server.admin.test_client().post("/calendar/0/edit", data=form)
    assert config.calendars()[0]["password"] == "n3w"


# ── Config migration ──────────────────────────────────────────────────────────

def test_migrate_drops_unused_image_base_url():
    cfg = {"webhooks": [{"name": "BYOS", "url": "http://x", "image_base_url": "http://y"}]}
    assert config._migrate(cfg)["webhooks"] == [{"name": "BYOS", "url": "http://x"}]
