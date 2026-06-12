# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

For project overview, setup, configuration, and troubleshooting see [README.md](README.md).

## Commands

### Local development (inside `app/`)
```bash
cd app
uv sync                       # install deps into .venv
uv run pytest                 # run all tests
uv run pytest tests/test_renderer.py::test_default_dimensions  # single test
uv run python cli.py example_events.json -o preview.png        # render from JSON
```

### Render CLI flags
```
python cli.py <events.json> [--week YYYY-MM-DD] [--width 800] [--height 480]
                            [--time-window 12] [--time-start 8] [-o output.png]
```

## Architecture

Single Python process in `app/`, two concurrent components:

1. **Flask server** (port 8080) — serves `/week.png`, `/next.png`, `/health`, `/debug`, and the full admin GUI (`/`); `POST /refresh` triggers an immediate re-render
2. **Background scheduler** — calls `refresh()` every `refresh_seconds`; after each successful render it fires all enabled webhooks

### Data flow
```
scheduler / admin POST /refresh
        │
        ▼
server.refresh()
  ├── config.calendars()           # read current calendar list
  ├── caldav_client.fetch_range()  # fetch VEVENT from CalDAV per calendar
  ├── renderer.render_days()       # WeasyPrint → 1-bit PNG bytes
  └── _fire_webhooks()             # POST merge_variables to BYOS
```

### Key modules

| File | Responsibility |
|------|---------------|
| `config.py` | Thread-safe JSON config at `/app/data/config.json`; bootstraps from env vars on first run; exposes `get()`, `update()`, and convenience accessors |
| `caldav_client.py` | `fetch_range()` uses the `caldav` library with `expand=True` to expand recurring events; returns `CalEvent` dataclasses with tz-aware datetimes; `fetch_week()` is a convenience wrapper |
| `scheduling.py` | Pure layout algorithms (no PIL/font deps): lane assignment for timed events, row assignment for all-day events; imported by `renderer.py` |
| `renderer.py` | WeasyPrint-based renderer: Python computes layout → Jinja2 renders `templates/calendar_weasy.html` → WeasyPrint PDF → pypdfium2 → PIL → Floyd-Steinberg 1-bit PNG; `render_days()` is entry point; `render_week`, `render_rolling`, `render_3day` delegate to it |
| `server.py` | Wires everything together; holds in-memory PNG bytes in `_png_current`/`_png_next` behind a `threading.Lock` |

### Config schema

Config stored in `/app/data/config.json` (Docker volume `inkdav_data`). Key fields:
- `calendars` — list of `{url, user, password, name, color}`
- `webhooks` — list of `{name, url, image_base_url, enabled}`
- `view_mode` — `"week"` | `"rolling"` | `"3day"`
- `time_start_mode` — `"auto"` (places current time ~1/3 from top) | `"fixed"`
- `time_window_hours`, `time_start_hour`, `today_highlight`
- `timezone` — IANA tz string (e.g. `"Europe/Berlin"`), validated on load
- `hyphenation_lang`, `event_font_size`, `event_bold` — rendering tweaks

`_migrate()` in `config.py` adds missing keys non-destructively when loading older config files.

## Tests

Tests in `app/tests/`: `test_renderer.py` (color helpers, render output shape), `test_scheduling.py` (lane/row assignment), `test_cli.py` (CLI parsing). Run from `app/` with `uv run pytest`.
