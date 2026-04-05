# TRMNL Week Calendar

Multi-calendar week view for TRMNL e-ink displays.  
Fetches CalDAV calendars, renders a Mo–So week grid as a 1-bit PNG, and pushes it to a self-hosted TRMNL BYOS server via webhook.

```
CalDAV sources ──┐
CalDAV sources ──┤   calendar sidecar :8080
CalDAV sources ──┘   fetches & renders every 15 min
                            │
                     webhook POST (PNG bytes)
                            │
                        BYOS :4567          ←── TRMNL device polls /display
                        (larapaper)
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

### 4. Create an Image Webhook plugin in BYOS

The sidecar pushes the rendered PNG directly to larapaper using the **Image Webhook** plugin type.

1. Open the BYOS dashboard at **http://localhost:4567**
2. Go to **Plugins → New Plugin** and choose type **Image Webhook**
3. Give it a name (e.g. `Week Calendar`) and save
4. Copy the webhook URL from the plugin page — it looks like:
   `http://localhost:4567/api/plugin_settings/<uuid>/image`
5. Go to **Devices → your device → Playlist** and add the plugin

### 5. Add the webhook to the calendar sidecar

Open the calendar admin UI at **http://localhost:5001**, go to the **Webhooks** section, and add a new entry:

| Field | Value |
|-------|-------|
| **Name** | any label, e.g. `BYOS` |
| **Webhook URL** | `http://trmnl-byos:8080/api/plugin_settings/<uuid>/image` |

> **Important:** use `http://trmnl-byos:8080` (container name + internal port), **not** `http://localhost:4567`.
> Both containers share the `trmnl_net` Docker bridge network, so `trmnl-byos` resolves correctly from within the sidecar.
> `localhost:4567` is the host-side port mapping and is unreachable from inside Docker.

After saving, click **Trigger refresh**. Check the logs to confirm success:

```bash
docker compose logs calendar --tail=20
# You should see:
# Webhook [BYOS] → HTTP 200
```

The sidecar POSTs the current PNG as raw bytes (`Content-Type: image/png`) to the BYOS endpoint after every successful CalDAV render. BYOS stores the image and serves it to your TRMNL device on the next poll.

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
| `Webhook failed: Connection refused` | URL uses `localhost` — use `http://trmnl-byos:8080/...` instead |
| `Webhook failed: 400 Bad Request` | Plugin type is not **Image Webhook** in BYOS — recreate the plugin with the correct type |
| `Webhook failed: 404 Not Found` | Wrong UUID in the webhook URL — copy it from the BYOS plugin page |
| `Webhook [BYOS] → HTTP 200` but no image on device | Plugin not added to device playlist in BYOS |
| 401 from CalDAV | Wrong app password – create a new one in Nextcloud → Settings → Security |
| 404 from CalDAV | Wrong calendar URL – use the `curl PROPFIND` command above to list slugs |
| Wrong timezone | Change in the admin UI → Settings → Timezone |
| Config lost after restart | The `calendar_config` Docker volume must not be removed (`docker compose down` is safe; `docker compose down -v` deletes it) |
