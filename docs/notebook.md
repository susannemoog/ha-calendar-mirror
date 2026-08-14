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
| [ha-icalendar](https://github.com/codyc1515/ha-icalendar) | Exposes an HA calendar as a subscribable ICS URL, full multi-event feed (4 weeks history + 52 weeks forward per its README) | **Correction 2026-08-14**: the original "only supplies the first upcoming event" claim below was wrong and went unverified until Susi caught it - checked `ical.py` directly, it iterates and emits every event from `calendar.get_events`, no single-event limit. Still structurally different from this project though: it's a read-only *pull* feed for other apps to subscribe to, not a *push* with create/delete sync (this project doesn't do in-place updates either - see README - it deletes and recreates) into an external calendar. The real, sourced differentiator is refresh latency, not event count or "editability": Google/Apple both throttle subscribed ICS feeds to ~12-24h with no manual refresh, vs. this project running on HA's own sync interval - confirmed 2026-08-14 (see README comparison table for sources), so the earlier "unverified, treat as plausible" note about slow refresh no longer applies. |
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
- [x] Scaffold `custom_components/calendar_mirror/` with `manifest.json`, `config_flow.py`, `application_credentials.py`
- [x] Port sync logic from the prototype script into a `DataUpdateCoordinator`
- [x] Test suite (`pytest-homeassistant-custom-component`) covering coordinator, config flow, and auth bridge
- [x] Verified locally against a real (non-mocked) HA instance: loads clean, config flow registers and initiates correctly
- [ ] Complete a real Google OAuth login locally and exercise source/target calendar picker + a real sync
- [ ] Local testing against Susi's real calendars (`waste_collection_schedule_bremer_stadtreinigung`, `bomo`) on `ha.herzundschrotti.de`
- [ ] Write README with setup instructions, aimed at the level of detail the community thread poster would need
- [ ] Publish to a public GitHub repo, submit to HACS default repo list
- [ ] Reply to the Dec 2025 community thread once there's something installable
- [ ] (Stretch) CalDAV as a second target type, generalizing beyond Google

## 5. Known gotchas worth writing into the README (learned the hard way)

- Google's `redirect_uri_mismatch` error is easy to misdiagnose: HA's OAuth flow proxies through `my.home-assistant.io`, not your own instance's domain — register `https://my.home-assistant.io/redirect/oauth` (not your own domain, not `/oauth2`) as the authorized redirect URI on a **Web application**-type OAuth client, not Desktop.
- OAuth client type matters and can't be changed after creation — Desktop-type clients silently can't do the custom-https-redirect flow HA needs.
- Test users: if the OAuth consent screen is in "Testing" publishing status, only explicitly added test-user accounts can complete sign-in.
- All-day events come back from HA's calendar API as `{"date": ...}`, timed events as `{"dateTime": ..., "timeZone": ...}` — **but only from the REST API**, which is what the original prototype script hit from outside HA. The `calendar.get_events` *internal service call* (what the coordinator actually uses, since it runs inside HA) returns a flatter shape instead: `start`/`end` are plain ISO 8601 strings (`"2026-08-20"` for all-day, `"2026-08-20T10:00:00+02:00"` for timed). Confirmed against HA core's `calendar/__init__.py` (`_list_events_dict_factory`/`_event_dict_factory`), 2026-08-13. Don't assume the REST shape applies just because it's "the same data."
- `gcal_sync.api.GoogleCalendarService.async_list_events()` returns a `ListEventsResponse` whose `__aiter__` yields *pages* (each a `ListEventsResponse` with an `.items` list), not individual events. `async for event in result` silently iterates pages instead of events - real bug caught in this session's scaffold, would have made `_clear_previously_synced_events` never actually match `SYNC_TAG` against anything (comparing a page object's `.description`, which doesn't exist, against a string) and thus never delete anything.
- There's no generic `calendar.delete_event`/`update_event` service in HA core — this is the whole reason this integration needs to exist rather than being a 10-line automation.
- **pytest-homeassistant-custom-component gotcha**: the plugin's `hass` fixture defaults `hass.config.config_dir` to its own bundled `testing_config/` inside site-packages, not the repo under test - so `async_get_integration(hass, "your_domain")` raises `ModuleNotFoundError` even with `enable_custom_integrations` and a correct `custom_components/<domain>/` layout, unless you also (a) add an empty `custom_components/__init__.py` at the repo root, so it's an importable regular package, and (b) override the `hass_config_dir` fixture to return the repo root. Not documented clearly in the plugin's own README; cost real debugging time this session.
- **Local dev/testing gotcha**: if a custom integration with `config_flow: true` mysteriously doesn't appear in `/api/config/config_entries/flow_handlers` (or the "Add Integration" UI search) despite the manifest being valid and the loader logging "We found a custom integration <domain>", check the HA log for `Activating recovery mode`. A `configuration.yaml` error elsewhere (in our case: `frontend:`/`config:`/`http:`/`logger:` wrongly nested as children of `homeassistant:` instead of being top-level keys) puts HA into recovery mode, which boots a minimal instance and silently excludes custom integrations from the config-flow index - no error mentioning your integration by name, it just doesn't show up.
- **Local dev/testing gotcha #2**: on this machine (macOS + Python 3.14, checked 2026-08-13), `pymicro-vad` and `pyspeex-noise` (native deps of the optional `assist_pipeline`/voice-assistant feature) fail to build - `fatal error: 'cstdint'/'cstddef' file not found` even with `SDKROOT`/`CPATH` set explicitly, and no prebuilt wheels exist yet for this Python version. This isn't cosmetic: `homeassistant/helpers/service.py`'s `_base_components()` *unconditionally* imports `assist_satellite` (which drags in the broken chain) on every service-schema validation, is `@functools.cache`d but doesn't cache exceptions, and so re-fails on every single call. In practice this made the frontend hang forever on "Loading data" after login, since routine startup calls kept hitting this. Worked around by dropping trivial stub modules (`pymicro_vad.py`, `pyspeex_noise.py`, each just a class that raises `NotImplementedError` if actually instantiated) directly into the venv's site-packages so the import succeeds - fine since this dev instance has no real voice-assistant hardware/use case anyway. Not a calendar_mirror bug, but blocked verifying it, so recording the fix here in case it recurs.

## 6. Session log

**2026-08-13** — Identified the gap after building a working prototype (external Python script + cron) for personal use, hit real friction (OAuth redirect URI debugging, venv/cron on a non-always-on Mac), decided to generalize into a proper custom_component instead of just working around it privately. Researched prior art, confirmed the gap, found `gcal-sync` as a reusable building block and a live community thread validating demand. Next session: scaffold the integration skeleton.

**2026-08-13 (cont'd)** — Turned the scaffold into a working integration:
- Fetched and read `gcal_sync`'s real source (`api.py`, `auth.py`, `model.py` from `allenporter/gcal_sync` on GitHub) to replace the guessed method names in `coordinator.py` with confirmed real ones: `async_create_event(calendar_id, event)`, `async_delete_event(calendar_id, event_id)`, `async_list_events(request) -> ListEventsResponse` (paginated - see gotcha above). Also fixed a real pagination bug in `_clear_previously_synced_events` while doing this, not just filling in the TODOs as originally scoped.
- Implemented `_create_target_event` for real, building `gcal_sync.model.Event`/`DateOrDatetime` from HA's flat ISO event strings (see gotcha above re: internal service vs REST shape).
- Implemented the `AbstractAuth` bridge (`ApiAuthImpl` in `__init__.py`) by reading HA core's own `homeassistant/components/google/api.py` - it's the exact same pattern (wrap `OAuth2Session`, call `async_ensure_token_valid()`, return `session.token["access_token"]`), so no guessing needed.
- Filled in `application_credentials.py`'s `async_get_description_placeholders` TODO - confirmed still in active use by checking HA core's current google integration, not deprecated as the TODO worried it might be.
- Implemented the two missing config_flow steps (`async_step_source_calendars` using `selector.EntitySelector(domain="calendar", multiple=True)`, `async_step_target_calendar`), wired in by overriding `async_oauth_create_entry` - confirmed via HA core source that overriding this specific method to add steps is an explicitly supported extension point, not a hack. Added `strings.json` for proper step labels/errors.
- Wrote the test suite (`pytest-homeassistant-custom-component`, HA's own convention): 20 tests across coordinator sync logic (using gcal_sync's *real* response classes, not hand-rolled fakes, specifically so the pagination bug above would have been caught), config flow steps (both isolated unit-style and one full end-to-end pass through HA's real flow manager + a real HTTP OAuth callback, mirroring HA core's own `tests/helpers/test_config_entry_oauth2_flow.py` pattern), and `__init__.py`'s auth bridge/setup/unload. All passing. Hit and documented the `hass_config_dir` gotcha above along the way.
- Verified locally against a real (non-pytest-mocked) `hass -c ./config` instance the user had already set up. Found and fixed two environment bugs blocking verification, neither in our code: (1) the `custom_components/calendar_mirror` symlink pointed at the repo root instead of the actual integration subfolder, (2) `configuration.yaml` had `frontend:`/`config:`/`http:`/`logger:` wrongly nested under `homeassistant:`, which put HA into recovery mode and silently hid all custom integrations from config_flow discovery (see gotcha above - this one cost the most debugging time since the symptom gave no indication the cause was a config file elsewhere). With both fixed: HA boots clean, `calendar_mirror` correctly appears in `/api/config/config_entries/flow_handlers`, and initiating the flow correctly aborts with `missing_credentials` (expected, since no OAuth client is registered yet in this instance) with zero errors or tracebacks attributable to our code anywhere in the log.
- Scope for this pass was deliberately stopped before completing a real Google OAuth login (per decision with Susi) - confirmed the plumbing (manifest, config_flow registration, application_credentials dependency chain) is sound, but haven't yet exercised the actual authorize-URL generation, the source/target calendar picker UI, or a real create/delete sync against Google.
- **Still open / next session**: add the existing prototype's OAuth client credentials to this local instance and complete a real login to exercise the rest of the flow; then deploy to `ha.herzundschrotti.de` (deployment mechanism - SSH/SFTP vs. manual - still to be decided with Susi) and test against the real `calendar.waste_collection_schedule_bremer_stadtreinigung` and `calendar.bomo` source calendars end-to-end, including verifying delete/recreate behavior across two consecutive sync runs.

**2026-08-14** — Finished local end-to-end verification and got the repo public-ready:
- Fixed a symlink pointing at the wrong directory and an unrelated broken native dependency (`pymicro_vad`/`pyspeex_noise`, see gotcha above) blocking the local HA frontend from loading at all.
- Hit a real `redirect_uri_mismatch` from Google: HA's OAuth helper defaulted to a direct `http://localhost:8123/auth/external/callback` redirect since the local dev instance had no `my:` component configured. Tried enabling `my:` to route through `my.home-assistant.io` instead, but that redirected to `homeassistant.local` via mDNS - almost certainly a different real HA box on Susi's LAN also advertising that hostname, so the callback landed on the wrong server ("Invalid state"). Fixed by reverting `my:` and instead adding `http://localhost:8123/auth/external/callback` as a second authorized redirect URI on the existing Google OAuth client (Google explicitly allows multiple redirect URIs, and permits `http://localhost` unencrypted for local dev) - kept the original `my.home-assistant.io` URI in place for real deployments.
- Completed a real Google OAuth login and full config flow locally, first against a throwaway `local_calendar` test entity, then installed the real `waste_collection_schedule` HACS integration (`mampfes/hacs_waste_collection_schedule`, source `c_trace_de` with `service: bremenabfallkalender` - confirmed this is the correct backend for "Bremer Stadtreinigung" via its doc's municipality table) and pointed calendar_mirror at the real `calendar.waste_collection_schedule_bremer_stadtreinigung` entity.
- **First real sync confirmed working end-to-end**: 6 real events created on a real Google Calendar from real waste-collection data, zero errors. Reloading the entry a second time produced 6 events again (not 12), confirming the delete-then-recreate logic correctly identifies and removes its own previously-created events rather than accumulating duplicates.
- Caught a real usability gap this surfaced: the integration had no way to edit an entry after creation (no options flow), so changing source/target calendars meant deleting and fully redoing OAuth. Added `CalendarMirrorOptionsFlow` (`OptionsFlowWithReload`, matching HA core's own google integration pattern) so source/target calendars can be changed via a "Configure" button, with `entry.options` overriding `entry.data` in `async_setup_entry`. Also made the target-calendar step fetch and offer a real dropdown of the account's writable calendars (`AccessRole.OWNER`/`WRITER`) via `async_list_calendars()`, instead of a raw text field - the original scaffold's own TODO had already floated this as an option.
- Renamed the integration's HA-facing display name to "Mirror to Google Calendar" (manifest.json/hacs.json `name`, config flow entry title) - domain/folder/repo name deliberately left as `calendar_mirror` per discussion, since changing that later (after real users exist) is much harder than changing a display string.
- Set up Ruff + pre-commit + `CONTRIBUTING.md` matching Home Assistant's own real conventions (verified against `developers.home-assistant.io` and home-assistant/core's `pyproject.toml`/`.pre-commit-config.yaml`, not guessed) and fixed everything Ruff flagged across the whole repo (docstrings, import order, a `PLR0917` fixed by making the coordinator's constructor keyword-only past `hass`, matching how HA core avoids the same warning elsewhere).
- Committed and pushed everything to `github.com/susannemoog/ha-calendar-mirror` (`3fa046b`) - first real push since the initial scaffold commit.
- **Correction**: caught and fixed a wrong claim in section 2's prior-art table (ha-icalendar's "only supplies the first upcoming event" limitation) that had gone unverified since the very first research session - see that section for the correction and what's still genuinely unverified there.
- **Still open**: deployment to `ha.herzundschrotti.de` itself (mechanism still undecided - SSH/SFTP vs. manual); testing `calendar.bomo` as a second source calendar; the `home-assistant/brands` icon submission; HACS default-store submission; replying to the Dec 2025 community thread.

---

*This notebook is meant to keep growing across sessions — append new log entries at the bottom of section 6 rather than rewriting history, so the "why" behind decisions stays visible.*
