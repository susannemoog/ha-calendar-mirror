# HA Calendar Mirror — Project Notebook

A running log for building a Home Assistant integration that mirrors one or
more local HA calendar entities into an external write-enabled calendar
(starting with Google Calendar), so services like Alexa's native calendar
widget can display them. Started because Echo Show has no way to read
arbitrary calendar sources directly — only natively linked Google/Microsoft/
Apple accounts.

Name: **`ha-calendar-mirror`** (GitHub repo) / domain **`calendar_mirror`**
(HA integration) / **"Mirror to Google Calendar"** (HA-facing display
name). Checked 2026-08-14, before making the repo public: no other
GitHub repo named `ha-calendar-mirror` or `calendar-mirror`, no other HA
custom integration using the `calendar_mirror` domain (searched GitHub
code search for `manifest.json` files declaring it), nothing matching in
HACS's default store list, and no `calendar-mirror`/`calendar_mirror`
package on PyPI (not that this matters for distribution - this ships as
a HACS custom_component, not a PyPI package, so the PyPI check in the
original note was based on a misunderstanding of how this gets
distributed). Clear to publish under this name.

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
- [x] Complete a real Google OAuth login locally and exercise source/target calendar picker + a real sync
- [x] Local testing against Susi's real calendars (`waste_collection_schedule_bremer_stadtreinigung`, `bomo`) - on the local dev instance; **not yet on `ha.herzundschrotti.de` itself**, see below
- [x] Write README with setup instructions, aimed at the level of detail the community thread poster would need
- [x] Clean-code/security review before publishing
- [ ] Publish to a public GitHub repo (in progress this session), submit to HACS default repo list (later - custom-repository install doesn't require default-store submission)
- [ ] Install via HACS custom repository on `ha.herzundschrotti.de` and confirm it works on the real production instance, not just the local dev one
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

**2026-08-14 (cont'd)** — Full clean-code/security review before HACS publish, then fixed everything found:
- **OAuth scope narrowed**: was requesting the blanket `https://www.googleapis.com/auth/calendar` (full calendar management - create/delete/rename calendars, change sharing/ACLs). Verified against Google's actual OAuth 2.0 scopes reference that `calendar.events` (event CRUD) + `calendar.calendarlist.readonly` (for the target-calendar dropdown) cover everything this integration actually does, with meaningfully less blast radius if a token is ever compromised.
- **Real bug we'd already hit, now actually fixed**: nothing stopped two config entries from targeting the same Google Calendar, and each entry's delete-and-recreate pass only recognized events via a *global* `SYNC_TAG` string - so two entries sharing a target would silently delete each other's events every sync. Fixed two ways: (1) config flow now calls `async_set_unique_id(target_calendar_id)` + `_abort_if_unique_id_configured()` at setup, and the options flow does the equivalent check + updates the stored unique_id on reconfigure, so the mistake can't be made through the UI; (2) defense in depth - `SYNC_TAG` is now scoped per config entry (`SYNC_TAG:{entry_id}`) in the coordinator, so even if two entries did end up on the same target somehow, they still couldn't delete each other's events.
- **Added a reauth flow**: previously, an expired/revoked Google grant surfaced as an unhandled `gcal_sync.exceptions.AuthException` that the coordinator just logged as an "unexpected exception" - no prompt to fix it. Now caught in `_async_update_data` and re-raised as `ConfigEntryAuthFailed`, which `DataUpdateCoordinator` turns into HA's standard reauthenticate flow; added `async_step_reauth`/`async_step_reauth_confirm` (same shape as HA core's google integration) that skips straight to refreshing the token on the existing entry rather than re-running the source/target picker steps.
- **Per-source error isolation**: one source calendar erroring (unavailable, removed, etc.) used to kill the whole sync pass - none of the other sources would sync that cycle either. Now each source's `calendar.get_events` call is isolated in its own try/except (`HomeAssistantError`), logs a warning, and moves on.
- Filled in the `YOUR_GITHUB_USERNAME` placeholders (manifest.json, application_credentials.py) now that the real repo is known, and bumped `manifest.json` version from `0.0.1` to `0.1.0` for the first real release.
- Added 8 new tests covering all of the above (cross-entry tag isolation, duplicate-target rejection in both flows, reauth translation, per-source isolation) - 31 passing total. Also caught that our pyproject.toml's Ruff ignore list was missing `TRY003`/`TRY400`, both of which HA core deliberately ignores too (raising exceptions with an inline message is completely normal HA style) - trimmed out by accident when the config was first written from core's own, added back.
- **Still open from the review, not done yet**: no batching/backoff on the Google API calls (sequential per-event; fine at household scale, would need work for a much larger calendar) - documented as a known limitation rather than built, since it's a meaningfully bigger feature than the rest of this pass. CI workflow (pytest + ruff on push/PR) also still not set up.

**2026-08-14 (cont'd)** — Published: ran the name-collision check the notebook had been flagging as a pre-publish gate since day one and never actually done (see corrected note up top - clear), made `github.com/susannemoog/ha-calendar-mirror` **public**, and tagged/released **v0.1.0**. The repo being installable via HACS as a custom repository turned out to double as the answer to "how do we get this onto `ha.herzundschrotti.de`" - no SSH/SFTP access needed after all, Susi can just add the repo as a custom repository in HACS from the production instance directly and install it there like any other HACS integration. That production install (and finally testing against the real instance, not just the local dev one) is the actual next step.

**Still open**: install + verify on `ha.herzundschrotti.de` itself; `bomo` as a confirmed-working second source; the `home-assistant/brands` icon submission; HACS default-store submission (separate from the custom-repository install that already works); CI workflow; replying to the Dec 2025 community thread once it's confirmed working in production.

---

*This notebook is meant to keep growing across sessions — append new log entries at the bottom of section 6 rather than rewriting history, so the "why" behind decisions stays visible.*
