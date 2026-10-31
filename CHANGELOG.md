# Changelog

All notable changes to Inkdav. Versions follow [semver](https://semver.org); see [README.md](README.md#releases) for how they are picked.

## [0.2.0] - 2026-10-31

### Added
- `/health` reports each calendar's `last_ok` time and `error`, and returns `"degraded"` while any calendar is failing.
- The admin UI shows a warning on a calendar whose last fetch failed, with the error and the age of the data on screen.

### Changed
- When a CalDAV fetch fails, the calendar keeps showing its events from the last successful fetch instead of going blank.
- Webhooks are held back while a failed calendar has no earlier data (for example right after a restart), so the device keeps its previous image.
- Each calendar is fetched once per refresh instead of once per rendered window.
- The admin page no longer contains stored CalDAV passwords. Leave the password field blank when editing a calendar to keep the current one.
- The unused webhook `image_base_url` field is removed. Existing configs drop it automatically on load.

### Fixed
- "Today" follows the timezone set in the admin UI, not the container's `TZ`.
- Multi-day timed events no longer throw off the auto time window, and only events in the rendered days are used to place it.
- Webhook docs now say what is actually sent: the raw PNG (`Content-Type: image/png`), not a `merge_variables` JSON payload.

## [0.1.1] - 2026-10-25

### Fixed
- CalDAV credentials are sent with the first request, saving the extra unauthenticated round trip each calendar used to make on every refresh.

## [0.1.0] - 2026-09-24

### Added
- First tagged release: fetches CalDAV calendars and renders a week, rolling 7-day, or 3-day view as a 1-bit PNG for TRMNL e-ink displays.
- Admin UI for calendars, display settings and webhooks; `/week.png`, `/next.png` and `/health` endpoints.
- Pushes the rendered image to BYOS (larapaper) Image Webhook plugins after each refresh.

[0.2.0]: https://github.com/stfngrt/inkdav/compare/v0.1.1...v0.2.0
[0.1.1]: https://github.com/stfngrt/inkdav/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/stfngrt/inkdav/releases/tag/v0.1.0
