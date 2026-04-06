"""
server.py
=========
Runs two servers in the same process:

  Port 8080 – minimal HTTP server (BaseHTTPRequestHandler)
               Serves /week.png, /next.png, /health, /

  Port 5000 – Flask admin GUI
               Manages calendars, settings, and webhook targets.
               Accessible at http://localhost:5000

After each successful refresh, POSTs to every enabled webhook with:
  { "merge_variables": { "image_url", "next_image_url", "week", "refreshed_at" } }
"""

from __future__ import annotations

import io
import json
import logging
import threading
import time
from datetime import date, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from zoneinfo import ZoneInfo

import mimetypes
from pathlib import Path

import requests as _requests

import config
from caldav_client import CalEvent, fetch_range
from flask import Flask, redirect, render_template, request, url_for
from jinja2 import Environment, FileSystemLoader

_jinja = Environment(loader=FileSystemLoader(Path(__file__).parent / "templates"), autoescape=True)
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


# ── Fetch + render ────────────────────────────────────────────────────────────

def _monday_of(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _time_start_for(window_start: date) -> int:
    """Compute the first visible hour for a given window start date based on config."""
    cfg    = config.get()
    window = int(cfg["time_window_hours"])
    mode   = cfg["time_start_mode"]
    fixed  = int(cfg["time_start_hour"])

    today = date.today()
    if mode == "auto" and window_start <= today <= window_start + timedelta(days=6):
        local_tz = ZoneInfo(config.timezone())
        now      = datetime.now(tz=local_tz)
        frac     = now.hour + now.minute / 60
        # Place current time ~1/3 from top of the window
        raw = frac - window / 3
        return max(0, min(int(raw), 24 - window))

    return fixed


def _render_to_bytes(days: list[date], calendars: list[dict]) -> bytes:
    cfg           = config.get()
    local_tz      = ZoneInfo(cfg["timezone"])
    width, height = int(cfg["render_width"]), int(cfg["render_height"])
    time_window   = int(cfg["time_window_hours"])
    time_start    = _time_start_for(days[0])
    fetch_start   = days[0]
    fetch_end     = days[-1] + timedelta(days=1)
    events_by_cal: dict[str, tuple[str, list[CalEvent]]] = {}

    for cal in calendars:
        name  = cal["name"]
        color = cal["color"]
        try:
            evs = fetch_range(
                url      = cal["url"],
                user     = cal["user"],
                password = cal["password"],
                cal_name = name,
                color    = color,
                start    = fetch_start,
                end      = fetch_end,
                local_tz = local_tz,
            )
            events_by_cal[name] = (color, evs)
            log.info("  %-20s → %d events", name, len(evs))
        except Exception as e:
            log.warning("  %-20s → FAILED: %s", name, e)
            events_by_cal[name] = (color, [])

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
    )
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def _window_days(view_mode: str, offset: int = 0) -> list[date]:
    """Return the list of days to render for the given view mode and offset window."""
    today = date.today()
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

    calendars = config.calendars()
    view_mode = config.get().get("view_mode", "week")
    current_days = _window_days(view_mode, offset=0)
    next_days    = _window_days(view_mode, offset=1)

    log.info("Refreshing calendars (view=%s, start=%s)…", view_mode, current_days[0].isoformat())

    try:
        current = _render_to_bytes(current_days, calendars)
        nxt     = _render_to_bytes(next_days,    calendars)
        with _lock:
            _png_current = current
            _png_next    = nxt
            _last_fetch  = time.time()
            _last_error  = None
        log.info("Refresh complete.")
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


# ── Port-8080 HTTP handler ────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):  # silence default access log spam
        pass

    def do_GET(self):
        path = self.path.split("?")[0]

        if path == "/week.png":
            self._serve_png(_png_current)
        elif path == "/next.png":
            self._serve_png(_png_next)
        elif path == "/health":
            self._serve_health()
        elif path in ("/", "/debug"):
            self._serve_debug()
        elif path.startswith("/static/"):
            self._serve_static(path[len("/static/"):])
        else:
            self.send_error(404, "Not found")

    def _serve_png(self, data: bytes | None):
        if data is None:
            self.send_error(503, "Not ready yet – refresh in progress")
            return
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)

    def _serve_health(self):
        refresh_secs = config.refresh_seconds()
        with _lock:
            body = json.dumps({
                "status":      "ok" if _last_error is None else "degraded",
                "last_fetch":  _last_fetch,
                "next_fetch":  _last_fetch + refresh_secs,
                "error":       _last_error,
                "png_ready":   _png_current is not None,
            }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def _serve_static(self, filename: str):
        static_dir = Path(__file__).parent / "static"
        filepath = (static_dir / filename).resolve()
        if not str(filepath).startswith(str(static_dir)):  # prevent path traversal
            self.send_error(403, "Forbidden")
            return
        if not filepath.is_file():
            self.send_error(404, "Not found")
            return
        mime, _ = mimetypes.guess_type(str(filepath))
        data = filepath.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mime or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "max-age=3600")
        self.end_headers()
        self.wfile.write(data)

    def _serve_debug(self):
        body = _jinja.get_template("debug.html").render(
            this_week    = _monday_of(date.today()),
            refresh_secs = config.refresh_seconds(),
            ts           = int(time.time()),
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


# ── Flask admin app (port 5000) ───────────────────────────────────────────────

admin = Flask(__name__, template_folder="templates")


@admin.get("/")
def admin_index():
    cfg    = config.get()
    saved  = request.args.get("saved") == "1"
    error  = request.args.get("error", "")
    host   = request.host.split(":")[0]
    return render_template("admin.html",
                           cfg=cfg, saved=saved, error=error,
                           host=host, ts=int(time.time()))


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
        cfg["calendars"][idx] = {
            "name":     request.form["name"].strip(),
            "url":      request.form["url"].strip(),
            "user":     request.form["user"].strip(),
            "password": request.form["password"],
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
            "image_base_url": request.form["image_base_url"].strip(),
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
            "image_base_url": request.form["image_base_url"].strip(),
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
        log.warning("No calendars configured. Open http://localhost:5000 to add calendars.")
    else:
        log.info("Loaded %d calendar(s):", len(cals))
        for c in cals:
            log.info("  • %-20s %s", c["name"], c["url"])

    # Background scheduler
    threading.Thread(target=_scheduler, daemon=True).start()

    # Flask admin on port 5000
    threading.Thread(
        target=lambda: admin.run(host="0.0.0.0", port=5000, use_reloader=False),
        daemon=True,
    ).start()

    port = 8080
    log.info("PNG server   : http://0.0.0.0:%d/week.png", port)
    log.info("Admin GUI    : http://0.0.0.0:5000/")

    HTTPServer(("0.0.0.0", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
