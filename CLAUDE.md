# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

Inkdav is a Docker-based service that fetches CalDAV calendars, renders a week-grid as a 1-bit PNG, and pushes it to a self-hosted TRMNL BYOS server (larapaper) via webhook. The e-ink display renders at 800×480px in greyscale/1-bit.

## Commands

### Run the stack
```bash
docker compose up -d          # start both byos + inkdav services
docker compose logs inkdav    # tail inkdav logs
docker compose down           # stop (preserves volumes / config)
```

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

The entire application lives in `app/` and runs as a single Python process with two concurrent components:

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
  ├── renderer.render_days()       # Pillow → 1-bit PNG bytes
  └── _fire_webhooks()             # POST merge_variables to BYOS
```

### Key modules

| File | Responsibility |
|------|---------------|
| `config.py` | Thread-safe JSON config at `/app/data/config.json`; bootstraps from env vars on first run; exposes `get()`, `update()`, and convenience accessors |
| `caldav_client.py` | `fetch_range()` uses the `caldav` library with `expand=True` to expand recurring events; returns `CalEvent` dataclasses with tz-aware datetimes |
| `renderer.py` | `render_days(days, events_by_cal, ...)` is the single rendering entry point; all view functions (`render_week`, `render_rolling`, `render_3day`) delegate to it; output is a Floyd-Steinberg dithered 1-bit image converted back to `"L"` mode |
| `server.py` | Wires everything together; holds in-memory PNG bytes in `_png_current`/`_png_next` behind a `threading.Lock` |

### Config schema

Config is stored in `/app/data/config.json` (Docker volume `inkdav_data`). Key fields:
- `calendars` — list of `{url, user, password, name, color}` (hex, converted to greyscale for e-ink)
- `webhooks` — list of `{name, url, image_base_url, enabled}`
- `view_mode` — `"week"` | `"rolling"` | `"3day"`
- `time_start_mode` — `"auto"` (places current time ~1/3 from top) | `"fixed"`
- `time_window_hours`, `time_start_hour`, `today_highlight`

`_migrate()` in `config.py` adds missing keys non-destructively when loading older config files.

### Color mapping (e-ink)
Hex colors are converted to greyscale via luminance (`0.299R + 0.587G + 0.114B`), then mapped to fill patterns: `< 80` → solid black, `< 160` → dark grey (60), else mid grey (140).

## Environment variables

Only read on **first run** (before `config.json` exists):

| Variable | Default |
|----------|---------|
| `RENDER_WIDTH` | `800` |
| `RENDER_HEIGHT` | `480` |
| `REFRESH_SECONDS` | `900` |
| `TZ` | `Europe/Berlin` |

`.env` only needs `BYOS_APP_KEY` and `LOCAL_IP`.

## Tests

Tests are in `app/tests/test_renderer.py` and cover color helpers and `render_week` output shape/correctness. Run from `app/` with `uv run pytest`.
