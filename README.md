# TRMNL Week Calendar

Multi-calendar week view for TRMNL e-ink displays.  
Fetches CalDAV calendars, renders a Mo–So week grid as a 1-bit PNG, and pushes it to a self-hosted TRMNL BYOS server via webhook.

```
CalDAV sources ──┐
CalDAV sources ──┤   calendar sidecar :8080
CalDAV sources ──┘   fetches & renders every 15 min
                            │
                     webhook POST (merge_variables)
                            │
                        BYOS :4567
                        (larapaper)
                            │
                        TRMNL device
```

---

## Quickstart

### 1. Clone & configure

```bash
git clone <this-repo>
cd trmnl-week-calendar
cp .env.example .env
$EDITOR .env
```

Fill in `BYOS_APP_KEY` (generate with `openssl rand -base64 32`) and `LOCAL_IP` (LAN IP of your host machine).

### 2. Start

```bash
docker compose up -d
```

| Service | URL |
|---------|-----|
| BYOS dashboard | `http://localhost:4567` |
| Calendar admin UI | `http://localhost:5001` |
| Calendar PNG | `http://localhost:8080/week.png` |
| Health check | `http://localhost:8080/health` |

### 3. Add calendars

Open **http://localhost:5001** and use the web UI to add your CalDAV calendars.

You need per-calendar:
- **CalDAV URL** — e.g. `https://cloud.example.com/remote.php/dav/calendars/alice/personal/`
- **Username** — your Nextcloud username
- **App password** — Nextcloud → Settings → Security → App passwords → create one named `TRMNL`
- **Name** — display label shown in the week grid legend
- **Color** — used as fill pattern on the e-ink display (see color guide below)

**To find your calendar slugs:**
```bash
curl -u "alice:APP-PASSWORD" \
  -X PROPFIND "https://cloud.example.com/remote.php/dav/calendars/alice/" \
  -H "Depth: 1" | grep -o 'href>[^<]*' | grep dav
```

**Color guide** (hex colors are converted to greyscale for 1-bit e-ink):
| Color | Rendered as |
|-------|-------------|
| `#000000` | Solid black |
| `#555555` | Dark grey |
| `#999999` | Mid grey |
| `#cccccc` | Light grey |

### 4. Configure a webhook

In the admin UI (**http://localhost:5001**), add a webhook pointing at your BYOS device webhook URL:

- **Webhook URL** — copy from BYOS → Plugin settings (e.g. `http://byos:8080/webhooks/{token}`)
- **Image base URL** — `http://calendar:8080` (how BYOS reaches the sidecar within Docker)

After saving, click **Trigger refresh** to send the first push.

### 5. Set up the recipe in BYOS

1. BYOS → **Plugins → Recipes**
2. Select the `nextcloud-calendar` recipe (pre-mounted from `./plugin/`)
3. Assign it to your device

The recipe uses these merge variables sent by the webhook:

| Variable | Example |
|----------|---------|
| `image_url` | `http://calendar:8080/week.png` |
| `next_image_url` | `http://calendar:8080/next.png` |
| `week` | `Apr 7 – Apr 13, 2026` |
| `refreshed_at` | `2026-04-04T10:30:00` |

---

## Configuration

All settings (calendars, webhooks, refresh rate, timezone, dimensions) are managed via the admin UI at **http://localhost:5001** and stored in a Docker volume (`calendar_config`). No `.env` editing needed after initial setup.

The following environment variables set defaults on **first run only** (before `config.json` exists):

| Variable | Default | Description |
|----------|---------|-------------|
| `RENDER_WIDTH` | `800` | PNG width in pixels |
| `RENDER_HEIGHT` | `480` | PNG height in pixels |
| `REFRESH_SECONDS` | `900` | Seconds between CalDAV fetches |
| `TZ` | `Europe/Berlin` | Initial timezone |

---

## File structure

```
trmnl-week-calendar/
├── docker-compose.yml
├── .env.example
├── plugin/
│   └── nextcloud-calendar.blade.php   # BYOS recipe template
└── calendar-sidecar/
    ├── Dockerfile
    ├── requirements.txt
    ├── config.py          # persistent config (JSON file + env bootstrap)
    ├── caldav_client.py   # CalDAV fetcher (caldav + icalendar libraries)
    ├── renderer.py        # Pillow-based week grid PNG renderer
    ├── server.py          # PNG server :8080 + Flask admin :5000 + webhook dispatch
    └── templates/
        └── admin.html     # web UI
```

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| No events in PNG | Check `/health` and logs: `docker compose logs calendar` |
| Webhook not received by BYOS | Verify the webhook URL in the admin UI; check `docker compose logs byos` |
| 401 from CalDAV | Wrong app password – create a new one in Nextcloud → Settings → Security |
| 404 from CalDAV | Wrong calendar URL – use the `curl PROPFIND` command above to list slugs |
| Wrong timezone | Change in the admin UI → Settings → Timezone |
| Config lost after restart | The `calendar_config` Docker volume must not be removed (`docker compose down` is safe; `docker compose down -v` deletes it) |
