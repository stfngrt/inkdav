"""
config.py
=========
Persistent configuration for the calendar sidecar.

On first run, bootstraps from environment variables and writes /app/config.json.
On subsequent runs, reads exclusively from that file (env vars are ignored).

Schema (config.json):
{
  "calendars": [
    {"url": "...", "user": "...", "password": "...", "name": "...", "color": "#rrggbb"}
  ],
  "webhooks": [
    {
      "name": "BYOS",
      "url": "http://byos:8080/webhooks/{token}",
      "image_base_url": "http://calendar:8080",
      "enabled": true
    }
  ],
  "refresh_seconds": 900,
  "timezone": "Europe/Berlin",
  "render_width": 800,
  "render_height": 480,
  "time_window_hours": 12,
  "time_start_mode": "auto",
  "time_start_hour": 8
}

Webhook payload (POSTed after each successful refresh):
{
  "merge_variables": {
    "image_url":      "http://calendar:8080/week.png",
    "next_image_url": "http://calendar:8080/next.png",
    "week":           "Apr 7 – Apr 13, 2026",
    "refreshed_at":   "2026-04-04T10:30:00"
  }
}
"""

from __future__ import annotations

import copy
import json
import logging
import os
import threading
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

log = logging.getLogger(__name__)

CONFIG_PATH = Path(os.getenv("CONFIG_PATH", "/app/data/config.json"))

_lock:   threading.Lock = threading.Lock()
_config: dict | None    = None          # loaded on first call to load()

_REQUIRED_CAL_KEYS  = ("url", "user", "password", "name", "color")
_REQUIRED_HOOK_KEYS = ("name", "url")


# ── Public API ────────────────────────────────────────────────────────────────

def load() -> None:
    """Load config from file (or bootstrap from env vars). Call once at startup."""
    global _config
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH) as f:
                data = json.load(f)
            data = _migrate(data)
            _validate(data)
            with _lock:
                _config = data
            log.info("Config loaded from %s", CONFIG_PATH)
            return
        except Exception as e:
            log.warning("Failed to read %s (%s) – falling back to env vars", CONFIG_PATH, e)

    # Bootstrap from environment variables
    data = _from_env()
    with _lock:
        _config = data
    _save(data)
    log.info("Config bootstrapped from env vars and saved to %s", CONFIG_PATH)


def get() -> dict:
    """Return a deep copy of the current config (safe for mutation by caller)."""
    with _lock:
        if _config is None:
            raise RuntimeError("config.load() has not been called")
        return copy.deepcopy(_config)


def update(new_cfg: dict) -> None:
    """Validate, store, and persist a new config dict."""
    _validate(new_cfg)
    with _lock:
        global _config
        _config = copy.deepcopy(new_cfg)
    _save(new_cfg)


# Convenience accessors (always return current values from live config)

def calendars() -> list[dict]:
    return get()["calendars"]


def refresh_seconds() -> int:
    return int(get()["refresh_seconds"])


def timezone() -> str:
    return get()["timezone"]


def render_dims() -> tuple[int, int]:
    cfg = get()
    return int(cfg["render_width"]), int(cfg["render_height"])


def webhooks() -> list[dict]:
    return get().get("webhooks", [])


# ── Internal helpers ──────────────────────────────────────────────────────────

# Keys that may be absent in config files created by older versions.
# Applied automatically when loading from disk.
_DEFAULTS: dict = {
    "webhooks":           [],
    "time_window_hours":  12,
    "time_start_mode":    "auto",
    "time_start_hour":    8,
    "today_highlight":    False,
    "view_mode":          "week",
    "hyphenation_lang":   "de_DE",
    "event_font_size":    10,
    "event_bold":         True,
}


def _migrate(cfg: dict) -> dict:
    """Fill in keys introduced in newer versions (non-destructive)."""
    for k, v in _DEFAULTS.items():
        if k not in cfg:
            cfg[k] = v
    return cfg

def _from_env() -> dict:
    return {
        "calendars":        [],   # managed exclusively via the web UI
        "webhooks":         [],
        "refresh_seconds":  int(os.getenv("REFRESH_SECONDS", "900")),
        "timezone":         os.getenv("TZ", "Europe/Berlin"),
        "render_width":     int(os.getenv("RENDER_WIDTH",  "800")),
        "render_height":    int(os.getenv("RENDER_HEIGHT", "480")),
        "time_window_hours": 12,
        "time_start_mode":  "auto",
        "time_start_hour":  8,
        "today_highlight":   False,
        "view_mode":         "week",
        "hyphenation_lang":  "de_DE",
        "event_font_size":   10,
        "event_bold":        True,
    }


def _validate(cfg: dict) -> None:
    """Raise ValueError if cfg has structural problems."""
    for key in ("calendars", "refresh_seconds", "timezone",
                "render_width", "render_height",
                "time_window_hours", "time_start_mode", "time_start_hour"):
        if key not in cfg:
            raise ValueError(f"Config missing required key '{key}'")

    for c in cfg["calendars"]:
        for k in _REQUIRED_CAL_KEYS:
            if k not in c:
                raise ValueError(f"Calendar entry missing key '{k}': {c}")

    for h in cfg.get("webhooks", []):
        for k in _REQUIRED_HOOK_KEYS:
            if k not in h:
                raise ValueError(f"Webhook entry missing key '{k}': {h}")

    # Validate timezone string
    try:
        ZoneInfo(cfg["timezone"])
    except (ZoneInfoNotFoundError, KeyError):
        raise ValueError(f"Unknown timezone: {cfg['timezone']!r}")

    if int(cfg["refresh_seconds"]) < 10:
        raise ValueError("refresh_seconds must be at least 10")


def _save(cfg: dict) -> None:
    """Atomically write cfg to CONFIG_PATH."""
    tmp = CONFIG_PATH.with_suffix(".json.tmp")
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(tmp, "w") as f:
        json.dump(cfg, f, indent=2)
    os.replace(tmp, CONFIG_PATH)
