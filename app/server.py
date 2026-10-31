"""
server.py
=========
Single Flask server on port 8080:

  /            – admin GUI (calendars, settings, webhooks)
  /week.png    – current week PNG
  /next.png    – next week PNG
  /health      – JSON health check, including per-calendar fetch status
  /debug       – debug page

After each successful refresh, POSTs the current-window PNG to every enabled
webhook as the raw request body (Content-Type: image/png). The push is skipped
while a calendar has failed and has no earlier data to fall back on.
"""

from __future__ import annotations

import io
import json
import logging
import threading
import time
from dataclasses import replace
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import requests as _requests

import config
from caldav_client import CalEvent, fetch_range
from flask import Flask, Response, redirect, render_template, request, url_for
from renderer import render_days, set_hyphenation_lang

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ── Shared state ──────────────────────────────────────────────────────────────

_lock            = threading.Lock()
_png_current: bytes | None = None
_png_next:    bytes | None = None
_last_fetch:  float        = 0
_last_error:  str | None   = None

# Per-calendar fetch state, keyed by calendar URL.
#   _last_good   – events from the last successful fetch, reused when a fetch fails
#   _cal_status  – {"last_ok": float | None, "error": str | None} for /health and the admin UI
_last_good:  dict[str, list[CalEvent]] = {}
_cal_status: dict[str, dict]           = {}


# ── Fetch + render ────────────────────────────────────────────────────────────

def _local_today() -> date:
    """Today's date in the configured timezone (not the container's TZ)."""
    return datetime.now(tz=ZoneInfo(config.timezone())).date()


def _monday_of(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _timed_spans(
    days: list[date],
    events_by_cal: dict[str, tuple[str, list[CalEvent]]],
) -> list[tuple[float, float]]:
    """Return (start_hour, end_hour) spans of timed events, clipped per visible day.

    A multi-day event contributes one span per day it touches, so an event
    from 22:00 to 02:00 counts as 22–24 on the first day and 0–2 on the next.
    """
    spans: list[tuple[float, float]] = []
    for _color, evs in events_by_cal.values():
        for ev in evs:
            if ev.all_day:
                continue
            tz = ev.start.tzinfo
            for d in days:
                day_start = datetime(d.year, d.month, d.day, tzinfo=tz)
                day_end   = day_start + timedelta(days=1)
                s = max(ev.start, day_start)
                e = min(ev.end, day_end)
                if e <= s:
                    continue
                spans.append((
                    (s - day_start).total_seconds() / 3600,
                    (e - day_start).total_seconds() / 3600,
                ))
    return spans


def _time_start_for(
    days: list[date],
    events_by_cal: dict | None = None,
    today: date | None = None,
    now: datetime | None = None,
) -> int:
    """Compute the first visible hour for the given days based on config.

    In auto mode the window is anchored so the current time sits ~1/3 from the
    top, then shifted (within the constraint that the current time stays
    visible) to maximise the number of timed events inside the window.
    """
    cfg    = config.get()
    window = int(cfg["time_window_hours"])
    mode   = cfg["time_start_mode"]
    fixed  = int(cfg["time_start_hour"])

    if now is None:
        now = datetime.now(tz=ZoneInfo(config.timezone()))
    if today is None:
        today = now.date()

    if mode == "auto" and today in days:
        frac = now.hour + now.minute / 60

        # Anchor: keep current time ~1/3 from the top
        anchor = frac - window / 3

        timed = _timed_spans(days, events_by_cal) if events_by_cal else []
        if timed:
            # Valid range keeping current time visible anywhere in window
            lo = max(0.0, frac - window)
            hi = min(24.0 - window, frac)

            # Candidate positions: anchor + event boundaries
            candidates: list[float] = [anchor]
            for s, e in timed:
                candidates.append(s)           # event start at window top
                candidates.append(e - window)  # event end at window bottom

            # Clamp each candidate into [lo, hi]
            candidates = [max(lo, min(hi, c)) for c in candidates]

            def _count(t_start: float) -> int:
                t_end = t_start + window
                return sum(1 for s, e in timed if s < t_end and e > t_start)

            best = max(
                candidates,
                key=lambda c: (_count(c), -abs(c - anchor)),
            )
            return max(0, min(int(best), 24 - window))

        return max(0, min(int(anchor), 24 - window))

    return fixed


def _fetch_all(
    calendars: list[dict],
    start: date,
    end: date,
) -> tuple[dict[str, tuple[str, list[CalEvent]]], list[str]]:
    """Fetch [start, end) from every calendar.

    When a calendar fails, its events from the last successful fetch are used
    instead, so a short CalDAV outage does not blank the display.

    Returns (events_by_cal, names of failed calendars that had no earlier data).
    """
    local_tz = ZoneInfo(config.timezone())
    events_by_cal: dict[str, tuple[str, list[CalEvent]]] = {}
    missing: list[str] = []

    for cal in calendars:
        name, color, key = cal["name"], cal["color"], cal["url"]
        try:
            evs = fetch_range(
                url      = cal["url"],
                user     = cal["user"],
                password = cal["password"],
                cal_name = name,
                color    = color,
                start    = start,
                end      = end,
                local_tz = local_tz,
            )
            with _lock:
                _last_good[key]  = evs
                _cal_status[key] = {"last_ok": time.time(), "error": None}
            log.info("  %-20s → %d events", name, len(evs))
        except Exception as e:
            with _lock:
                cached = _last_good.get(key)
                prev   = _cal_status.get(key, {})
                _cal_status[key] = {"last_ok": prev.get("last_ok"), "error": str(e)}
            if cached is None:
                log.warning("  %-20s → FAILED, no earlier data: %s", name, e)
                missing.append(name)
                evs = []
            else:
                log.warning("  %-20s → FAILED, using last good data: %s", name, e)
                evs = cached
        # The cached events may have been fetched with an older name/color.
        events_by_cal[name] = (color, [replace(ev, calendar=name, color=color) for ev in evs])

    # Forget calendars that were removed from the config.
    keys = {c["url"] for c in calendars}
    with _lock:
        for key in list(_last_good):
            if key not in keys:
                del _last_good[key]
        for key in list(_cal_status):
            if key not in keys:
                del _cal_status[key]

    return events_by_cal, missing


def _render_to_bytes(
    days: list[date],
    events_by_cal: dict[str, tuple[str, list[CalEvent]]],
    today: date,
) -> bytes:
    cfg           = config.get()
    width, height = int(cfg["render_width"]), int(cfg["render_height"])
    time_window   = int(cfg["time_window_hours"])

    # Only this window's events take part in picking the auto time window.
    time_start = _time_start_for(days, events_by_cal, today=today)

    set_hyphenation_lang(cfg.get("hyphenation_lang", "de_DE"))
    img = render_days(
        days,
        events_by_cal,
        width=width,
        height=height,
        time_window_hours=time_window,
        time_start_hour=time_start,
        today_highlight=bool(cfg.get("today_highlight", False)),
        event_font_size=int(cfg.get("event_font_size", 10)),
        event_bold=bool(cfg.get("event_bold", True)),
        today=today,
    )
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _window_days(view_mode: str, offset: int = 0, today: date | None = None) -> list[date]:
    """Return the list of days to render for the given view mode and offset window."""
    if today is None:
        today = _local_today()
    if view_mode == "rolling":
        start = today + timedelta(days=7 * offset)
        return [start + timedelta(days=i) for i in range(7)]
    if view_mode == "3day":
        start = today + timedelta(days=3 * offset)
        return [start + timedelta(days=i) for i in range(3)]
    # default: "week" — Mon–Sun
    monday = today - timedelta(days=today.weekday())
    start  = monday + timedelta(weeks=offset)
    return [start + timedelta(days=i) for i in range(7)]


def refresh() -> None:
    global _png_current, _png_next, _last_fetch, _last_error

    calendars    = config.calendars()
    view_mode    = config.get().get("view_mode", "week")
    today        = _local_today()
    current_days = _window_days(view_mode, offset=0, today=today)
    next_days    = _window_days(view_mode, offset=1, today=today)

    log.info("Refreshing calendars (view=%s, start=%s)…", view_mode, current_days[0].isoformat())

    try:
        # One fetch covers both windows; the renderer picks out each window's days.
        events_by_cal, missing = _fetch_all(
            calendars, current_days[0], next_days[-1] + timedelta(days=1),
        )
        current = _render_to_bytes(current_days, events_by_cal, today)
        nxt     = _render_to_bytes(next_days,    events_by_cal, today)
        with _lock:
            _png_current = current
            _png_next    = nxt
            _last_fetch  = time.time()
            _last_error  = None
        log.info("Refresh complete.")
        if missing:
            # Pushing now would send an image with those calendars blank.
            # Leave the device on its last image until they can be fetched.
            log.warning("Skipping webhooks: no data for %s", ", ".join(missing))
        else:
            _fire_webhooks()
    except Exception as e:
        log.error("Refresh failed: %s", e)
        with _lock:
            _last_error = str(e)


def _fire_webhooks() -> None:
    """POST the current PNG image to every enabled webhook after a successful refresh."""
    hooks = config.webhooks()
    if not hooks:
        return

    with _lock:
        png_bytes = _png_current

    if not png_bytes:
        log.warning("No PNG available to push via webhooks")
        return

    for hook in hooks:
        if not hook.get("enabled", True):
            continue
        try:
            r = _requests.post(
                hook["url"],
                data=png_bytes,
                headers={"Content-Type": "image/png"},
                timeout=10,
            )
            r.raise_for_status()
            log.info("Webhook [%s] → HTTP %d", hook["name"], r.status_code)
        except Exception as e:
            log.warning("Webhook [%s] failed: %s", hook["name"], e)


def _scheduler() -> None:
    while True:
        refresh()
        time.sleep(config.refresh_seconds())


# ── Flask app (port 8080) ─────────────────────────────────────────────────────

admin = Flask(__name__, template_folder="templates", static_folder="static")


@admin.get("/week.png")
def serve_week_png():
    with _lock:
        data = _png_current
    if data is None:
        return Response("Not ready yet – refresh in progress", status=503)
    return Response(data, mimetype="image/png",
                    headers={"Cache-Control": "no-cache", "Content-Length": str(len(data))})


@admin.get("/next.png")
def serve_next_png():
    with _lock:
        data = _png_next
    if data is None:
        return Response("Not ready yet – refresh in progress", status=503)
    return Response(data, mimetype="image/png",
                    headers={"Cache-Control": "no-cache", "Content-Length": str(len(data))})


@admin.get("/health")
def serve_health():
    refresh_secs = config.refresh_seconds()
    cals = config.calendars()
    with _lock:
        cal_body = {
            c["name"]: {
                "last_ok": _cal_status.get(c["url"], {}).get("last_ok"),
                "error":   _cal_status.get(c["url"], {}).get("error"),
            }
            for c in cals
        }
        healthy = _last_error is None and all(v["error"] is None for v in cal_body.values())
        body = {
            "status":    "ok" if healthy else "degraded",
            "last_fetch": _last_fetch,
            "next_fetch": _last_fetch + refresh_secs,
            "error":      _last_error,
            "png_ready":  _png_current is not None,
            "calendars":  cal_body,
        }
    return Response(json.dumps(body), mimetype="application/json")


@admin.get("/debug")
def serve_debug():
    return render_template("debug.html",
                           this_week=_monday_of(_local_today()),
                           refresh_secs=config.refresh_seconds(),
                           ts=int(time.time()))


@admin.get("/")
def admin_index():
    cfg    = config.get()
    saved  = request.args.get("saved") == "1"
    error  = request.args.get("error", "")
    host   = request.host.split(":")[0]
    tz     = ZoneInfo(cfg["timezone"])
    with _lock:
        status = {k: dict(v) for k, v in _cal_status.items()}
    for v in status.values():
        v["last_ok_str"] = (
            datetime.fromtimestamp(v["last_ok"], tz).strftime("%Y-%m-%d %H:%M")
            if v["last_ok"] else None
        )
    return render_template("admin.html",
                           cfg=cfg, saved=saved, error=error,
                           host=host, ts=int(time.time()),
                           cal_status=status)


@admin.post("/settings")
def admin_settings():
    try:
        cfg = config.get()
        cfg["refresh_seconds"]  = int(request.form["refresh_seconds"])
        cfg["timezone"]         = request.form["timezone"].strip()
        cfg["render_width"]     = int(request.form["render_width"])
        cfg["render_height"]    = int(request.form["render_height"])
        cfg["time_window_hours"] = int(request.form["time_window_hours"])
        cfg["time_start_mode"]  = request.form["time_start_mode"]
        cfg["time_start_hour"]  = int(request.form["time_start_hour"])
        cfg["today_highlight"]    = "today_highlight" in request.form
        cfg["view_mode"]          = request.form["view_mode"]
        cfg["hyphenation_lang"]   = request.form["hyphenation_lang"]
        cfg["event_font_size"]    = int(request.form["event_font_size"])
        cfg["event_bold"]         = "event_bold" in request.form
        config.update(cfg)
    except Exception as e:
        return redirect(url_for("admin_index", error=str(e)))
    return redirect(url_for("admin_index", saved=1))


@admin.post("/calendar/add")
def calendar_add():
    try:
        cfg = config.get()
        cfg["calendars"].append({
            "name":     request.form["name"].strip(),
            "url":      request.form["url"].strip(),
            "user":     request.form["user"].strip(),
            "password": request.form["password"],
            "color":    request.form["color"],
        })
        config.update(cfg)
    except Exception as e:
        return redirect(url_for("admin_index", error=str(e)))
    return redirect(url_for("admin_index", saved=1))


@admin.post("/calendar/<int:idx>/edit")
def calendar_edit(idx: int):
    try:
        cfg = config.get()
        # The edit form never contains the stored password; blank keeps it.
        password = request.form.get("password", "") or cfg["calendars"][idx]["password"]
        cfg["calendars"][idx] = {
            "name":     request.form["name"].strip(),
            "url":      request.form["url"].strip(),
            "user":     request.form["user"].strip(),
            "password": password,
            "color":    request.form["color"],
        }
        config.update(cfg)
    except Exception as e:
        return redirect(url_for("admin_index", error=str(e)))
    return redirect(url_for("admin_index", saved=1))


@admin.post("/calendar/<int:idx>/delete")
def calendar_delete(idx: int):
    try:
        cfg = config.get()
        cfg["calendars"].pop(idx)
        config.update(cfg)
    except Exception as e:
        return redirect(url_for("admin_index", error=str(e)))
    return redirect(url_for("admin_index", saved=1))


@admin.post("/webhook/add")
def webhook_add():
    try:
        cfg = config.get()
        cfg["webhooks"].append({
            "name":           request.form["name"].strip(),
            "url":            request.form["url"].strip(),
            "enabled":        "enabled" in request.form,
        })
        config.update(cfg)
    except Exception as e:
        return redirect(url_for("admin_index", error=str(e)))
    return redirect(url_for("admin_index", saved=1))


@admin.post("/webhook/<int:idx>/edit")
def webhook_edit(idx: int):
    try:
        cfg = config.get()
        cfg["webhooks"][idx] = {
            "name":           request.form["name"].strip(),
            "url":            request.form["url"].strip(),
            "enabled":        "enabled" in request.form,
        }
        config.update(cfg)
    except Exception as e:
        return redirect(url_for("admin_index", error=str(e)))
    return redirect(url_for("admin_index", saved=1))


@admin.post("/webhook/<int:idx>/delete")
def webhook_delete(idx: int):
    try:
        cfg = config.get()
        cfg["webhooks"].pop(idx)
        config.update(cfg)
    except Exception as e:
        return redirect(url_for("admin_index", error=str(e)))
    return redirect(url_for("admin_index", saved=1))


@admin.post("/refresh")
def admin_refresh():
    threading.Thread(target=refresh, daemon=True).start()
    return redirect(url_for("admin_index"))


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    config.load()

    cals = config.calendars()
    if not cals:
        log.warning("No calendars configured. Open http://localhost:8080 to add calendars.")
    else:
        log.info("Loaded %d calendar(s):", len(cals))
        for c in cals:
            log.info("  • %-20s %s", c["name"], c["url"])

    # Background scheduler
    threading.Thread(target=_scheduler, daemon=True).start()

    port = 8080
    log.info("Server       : http://0.0.0.0:%d/", port)
    log.info("PNG          : http://0.0.0.0:%d/week.png", port)

    admin.run(host="0.0.0.0", port=port, use_reloader=False)


if __name__ == "__main__":
    main()
