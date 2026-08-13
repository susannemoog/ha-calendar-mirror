# HA Calendar Mirror — Project Notebook

A running log for building a Home Assistant integration that mirrors one or
more local HA calendar entities into an external write-enabled calendar
(starting with Google Calendar), so services like Alexa's native calendar
widget can display them. Started because Echo Show has no way to read
arbitrary calendar sources directly — only natively linked Google/Microsoft/
Apple accounts.

Working name: **`ha-calendar-mirror`** (placeholder, not checked for
collisions on HACS/PyPI/GitHub yet — do that before publishing).

---

## 1. Problem statement

Home Assistant can *read* calendars from many sources (local, CalDAV, Google,
etc.) and combine them for in-HA display, but there's no supported way to
*push* combined events out to an external calendar service with real
create/update/delete sync. This matters for anyone who wants their HA
calendar data to show up somewhere HA doesn't control the display for —
Alexa Echo Show being our case, but the same gap applies to any
"show it somewhere HA can't render to" scenario.

## 2. Prior art (researched 2026-08-13)

| Project | What it does | Why it doesn't cover this |
|---|---|---|
| [Calendar Merge](https://community.home-assistant.io/t/calendar-merge-combine-multiple-calendar-entities-into-one-hacs/994159) (HACS) | Combines multiple calendar *entities* into one entity | Display-only, stays inside HA, no external push |
| [Aurora Calendar](https://community.home-assistant.io/t/aurora-calendar-family-calendar-integration-and-card/1009402) | Family calendar integration + card | Also HA-internal display, not external sync |
| [ha-icalendar](https://github.com/codyc1515/ha-icalendar) | Exposes an HA calendar as a subscribable ICS URL | Documented limitation: only supplies the *first upcoming event* — not usable for a real feed. Also ICS-subscribe is pull-based and slow (hours-delayed refresh on the Google side) |
| [Entities Calendar](https://github.com/gadgetchnnel/entities_calendar) | Turns arbitrary entities into calendar events | Read/display direction only |
| HA core `google` integration | Read + limited write (`calendar.create_event`) to Google | No delete/update service exposed generically — this is the exact wall we hit |
| [gcal-sync](https://pypi.org/project/gcal-sync/) (PyPI) | Async Python library for Google Calendar, used internally by HA's own core Google integration | Not a gap — this is a **building block** we should depend on rather than reinvent |

**Confirmed demand**: [community thread, Dec 2025](https://community.home-assistant.io/t/best-way-to-sync-home-assistant-calendar-entities-to-google-calendar/967812) — someone with the literal same use case (waste collection + one other calendar, wanting it mirrored to Google for household visibility), unresolved as of that post. Worth reading fully and replying once we have something working — free early user + validation.

**Conclusion**: the "merge for display" problem is solved multiple times over. The "push combined events to an external calendar with real sync" problem is not solved by anything published. That's our actual scope.

## 3. Architecture decision

Build a proper `custom_component`, not a standalone script:

- **Auth**: use HA's `application_credentials` platform + OAuth2 config flow — the same mechanism HA's own Google Calendar integration uses. This is *why* the redirect URI had to be `my.home-assistant.io/...` during our manual setup — replicating that properly means users configure this the same way they already configure the official Google integration, no separate script, no local browser popup, no manually-created long-lived token.
- **Google API access**: depend on `gcal-sync` rather than hand-rolling API calls — it's already a trusted dependency in HA core, async-native, and lower maintenance for us.
- **Source side**: read via HA's own internal calendar entity state/`get_events`, no need for the external REST-API round-trip our prototype script used (that was only necessary because the script ran *outside* HA).
- **Sync engine**: `DataUpdateCoordinator` on a configurable polling interval (default something like 20–30 min), doing the same "clear previously-synced, recreate" strategy as the prototype — full wipe-and-recreate per run rather than diffing individual fields, since it's dramatically simpler and correctness matters more than elegance for a low-frequency personal-calendar sync.
- **Config**: a config flow letting the user pick (a) one or more source calendar entities already in HA, (b) a target external calendar (initially just Google; CalDAV as a stretch goal since the same "no generic delete" gap likely exists there too).
- **Packaging**: HACS-installable custom integration first. Consider submitting to HA core only after it's proven stable with real users — core has a much higher bar (test coverage, maintainer review, ongoing commitment).

## 4. Roadmap

- [x] Validate the problem is real and unsolved (this session)
- [x] Working prototype as an external script (done — proves the sync logic and Google API mechanics work)
- [ ] Get the prototype's logic running reliably for Susi's own use (via HA Add-on packaging, in progress) — this stays as the "dogfooding" reference implementation
- [ ] Scaffold `custom_components/calendar_mirror/` with `manifest.json`, `config_flow.py`, `application_credentials.py`
- [ ] Port sync logic from the prototype script into a `DataUpdateCoordinator`
- [ ] Local testing against Susi's real calendars (`waste_collection_schedule_bremer_stadtreinigung`, `bomo`)
- [ ] Write README with setup instructions, aimed at the level of detail the community thread poster would need
- [ ] Publish to a public GitHub repo, submit to HACS default repo list
- [ ] Reply to the Dec 2025 community thread once there's something installable
- [ ] (Stretch) CalDAV as a second target type, generalizing beyond Google

## 5. Known gotchas worth writing into the README (learned the hard way)

- Google's `redirect_uri_mismatch` error is easy to misdiagnose: HA's OAuth flow proxies through `my.home-assistant.io`, not your own instance's domain — register `https://my.home-assistant.io/redirect/oauth` (not your own domain, not `/oauth2`) as the authorized redirect URI on a **Web application**-type OAuth client, not Desktop.
- OAuth client type matters and can't be changed after creation — Desktop-type clients silently can't do the custom-https-redirect flow HA needs.
- Test users: if the OAuth consent screen is in "Testing" publishing status, only explicitly added test-user accounts can complete sign-in.
- All-day events come back from HA's calendar API as `{"date": ...}`, timed events as `{"dateTime": ..., "timeZone": ...}` — don't assume one shape.
- There's no generic `calendar.delete_event`/`update_event` service in HA core — this is the whole reason this integration needs to exist rather than being a 10-line automation.

## 6. Session log

**2026-08-13** — Identified the gap after building a working prototype (external Python script + cron) for personal use, hit real friction (OAuth redirect URI debugging, venv/cron on a non-always-on Mac), decided to generalize into a proper custom_component instead of just working around it privately. Researched prior art, confirmed the gap, found `gcal-sync` as a reusable building block and a live community thread validating demand. Next session: scaffold the integration skeleton.

---

*This notebook is meant to keep growing across sessions — append new log entries at the bottom of section 6 rather than rewriting history, so the "why" behind decisions stays visible.*
