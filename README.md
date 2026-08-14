# Calendar Mirror

A Home Assistant custom integration that mirrors one or more local HA
calendar entities into an external, write-enabled calendar (starting with
Google Calendar) — with real create/update/delete sync, not just a
one-way ICS export. Shows up in Home Assistant's UI as **"Mirror to
Google Calendar"**.

**Status: working, tested locally end-to-end** (real OAuth, real source
calendar, real create/delete sync against a real Google Calendar) but not
yet installed on a production instance, not yet published to HACS, and
the icon/branding submission hasn't been done. See
[`docs/notebook.md`](docs/notebook.md) for the design rationale, prior-art
research, and running development log.

## Why this exists

Home Assistant can read calendars from many sources and combine them for
in-HA display, but there's no supported way to *push* combined events out
to an external calendar service with proper sync. This matters if you
want your HA calendar data to show up somewhere HA doesn't control the
display — a smart speaker's native calendar widget, a family member's
phone, etc. Concretely: HA's own `calendar.create_event` service exists,
but there's no generic `calendar.delete_event`, so a naive approach can
create events but can never clean them up.

## How it's different from existing options

- **[Calendar Merge](https://community.home-assistant.io/t/calendar-merge-combine-multiple-calendar-entities-into-one-hacs/994159)** and **[Aurora Calendar](https://community.home-assistant.io/t/aurora-calendar-family-calendar-integration-and-card/1009402)** combine calendars for display *inside* HA — they don't push anywhere external.
- **[ha-icalendar](https://github.com/codyc1515/ha-icalendar)** exposes an ICS feed, but is documented as only supplying the first upcoming event, and ICS-subscribe is pull-based with slow (often hours-delayed) refresh on the consuming side.
- This project pushes events with full create/update/delete sync, using [`gcal-sync`](https://github.com/allenporter/gcal_sync) — the same library HA's own core Google Calendar integration depends on.

## Setup

1. Create a Google Cloud OAuth client (**Web application** type),
   authorized to redirect to `https://my.home-assistant.io/redirect/oauth`
   — same process as HA's own Google Calendar integration. Enable the
   Google Calendar API on the project too.
2. In Home Assistant: **Settings → Devices & Services → Application
   Credentials → Add Credential**, pick "Mirror to Google Calendar", and
   enter the client ID/secret from step 1.
3. **Settings → Devices & Services → Add Integration → Mirror to Google
   Calendar**, then sign in with the Google account whose calendar you
   want to sync into.
4. Pick which existing `calendar.*` entities to mirror, and which target
   Google Calendar to sync into (offered as a dropdown of your writable
   calendars, or enter an ID manually if the fetch fails).
5. Done — HA handles the recurring sync internally on a coordinator
   interval, no external script, cron, or manually-managed token
   required. Source/target calendars can be changed later via the
   integration's **Configure** button, without redoing the Google login.

## Contributing / status

Functional and covered by a test suite (`pytest-homeassistant-custom-component`),
verified locally against a real Google Calendar and a real waste-collection
source calendar — but not yet installed on a production HA instance, not
yet published to HACS, and it doesn't have official branding/icons yet
(see notebook for what's still open). Contributions and testing on other
calendar sources welcome.

## License

MIT — see [LICENSE](LICENSE).
