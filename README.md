# Calendar Mirror

A Home Assistant custom integration that pushes one or more local HA
calendar entities into an external calendar — **Google Calendar** or a
generic **CalDAV** server (Nextcloud, mailbox.org, Radicale, etc.) —
actively creating and deleting events there, not a passive, read-only
ICS export. Sync is **one-directional**: Home Assistant is always the
source of truth, and the target calendar is never read back into HA.
Each config entry picks one target type when it's set up; you can add
multiple entries if you want to mirror into both a Google Calendar and a
CalDAV calendar at once.

## What it does

- Reads events from one or more existing `calendar.*` entities in Home
  Assistant (any source HA already supports: local calendars, CalDAV,
  other integrations' calendars, etc.).
- Pushes those events into a Google Calendar or a CalDAV calendar of
  your choice, using [`gcal-sync`](https://github.com/allenporter/gcal_sync)
  for Google (the same library Home Assistant's own core Google Calendar
  integration depends on) or [`caldav`](https://github.com/python-caldav/caldav)'s
  async client for CalDAV.
- Runs a full sync pass on a recurring interval: every event it
  previously created on the target calendar is deleted, then a fresh
  copy is created from the current source data. There's no in-place
  update — each sync pass produces new events with new IDs, not edited
  versions of the old ones. Events on the target calendar it didn't
  create are left alone.
- **This means the target calendar is not safe to edit by hand.** Any
  manual change or deletion made directly on the target calendar —
  including deleting an event the integration created — is undone on the
  next sync pass, since the integration has no way to know it was
  intentional and just rebuilds from HA's current data. Deleting an
  event on the target calendar does not delete it in Home Assistant;
  nothing is ever written back to HA. Synced events are marked with a 🔒
  in the title and a note in the description explaining this — neither
  Google Calendar nor CalDAV has a per-event read-only flag for the
  calendar owner (permissions are calendar-level, not per-event), so
  this is a visible warning, not an enforced restriction.
- Google Calendar authenticates via Home Assistant's standard OAuth2
  `application_credentials` flow — the same mechanism the official
  Google Calendar integration uses, so there's no separate script,
  browser popup, or manually managed token. CalDAV authenticates with a
  URL, username, and password (an app-specific password if your server
  supports one), tested against the server before the entry is created.
- Source calendars and the target calendar can be changed after setup
  via the integration's **Configure** option, without repeating sign-in.

## How it compares to existing options

| | Direction | Update latency | Notes |
|---|---|---|---|
| [Calendar Merge](https://community.home-assistant.io/t/calendar-merge-combine-multiple-calendar-entities-into-one-hacs/994159), [Aurora Calendar](https://community.home-assistant.io/t/aurora-calendar-family-calendar-integration-and-card/1009402) | Internal only | — | Combine calendars for display inside Home Assistant; don't push anywhere external. |
| [ha-icalendar](https://github.com/codyc1515/ha-icalendar) | Pull (read-only) | Up to the subscribing app; Google Calendar and Apple Calendar both throttle subscribed ICS feeds to roughly every 12–24 hours, with no manual refresh and no faster setting | Exposes an HA calendar as a subscribable ICS feed. Creates and removals in the source do eventually propagate as the feed is re-fetched, just on that timescale. |
| **Calendar Mirror** | Push, one-way (HA → target) | Home Assistant's own sync interval (a few minutes to whatever's configured) | Actively creates and deletes events on the target calendar (Google Calendar or CalDAV); HA controls the timing rather than waiting on the target app to poll. Nothing is ever read back from the target into HA. |

This gap exists because Home Assistant's core `calendar.create_event`
service has no generic counterpart for deleting events, so a naive
one-way sync built from that service alone can create entries but never
clean them up.

## Installation

Not yet available in the default HACS store. Add it as a custom
repository:

1. In HACS: **Integrations → ⋮ → Custom repositories**.
2. Add this repository's URL with category **Integration**.
3. Install **Calendar Mirror**, then restart Home Assistant.

Or install manually by copying
`custom_components/calendar_mirror` into your Home Assistant
`config/custom_components/` directory and restarting.

## Configuration

**Settings → Devices & Services → Add Integration → Calendar Mirror**,
then choose a target type:

### Google Calendar

1. Create a Google Cloud OAuth client (**Web application** type),
   authorized to redirect to `https://my.home-assistant.io/redirect/oauth`
   — the same process used for Home Assistant's own Google Calendar
   integration. Enable the Google Calendar API on the same Cloud
   project.
2. In Home Assistant: **Settings → Devices & Services → Application
   Credentials → Add Credential**, select **Calendar Mirror**, and enter
   the client ID/secret from step 1.
3. Start the integration setup and sign in with the Google account that
   owns the target calendar.
4. Choose which `calendar.*` entities to mirror, and which Google
   Calendar to sync into — offered as a dropdown of calendars you can
   write to, or enter a calendar ID manually.

### CalDAV

1. Start the integration setup and choose **CalDAV**.
2. Enter your CalDAV server's URL, username, and password (an
   app-specific password if your server supports one — e.g. mailbox.org
   and Nextcloud both do). The connection is tested before you can
   continue.
3. Choose which `calendar.*` entities to mirror, and which CalDAV
   calendar to sync into — offered as a dropdown of calendars fetched
   from your account, or enter a calendar URL manually.

Either way, Home Assistant handles the recurring sync from there. To
change the source or target calendars later, use the integration's
**Configure** option. If a Google grant expires or a CalDAV password
changes, Home Assistant will prompt you to reauthenticate.

## Requirements

- For Google Calendar: a Google Cloud project with the Calendar API
  enabled and an OAuth client, as described above.
- For CalDAV: a CalDAV server URL and an account with write access to
  the target calendar.
- One or more `calendar.*` entities already available in Home Assistant
  to use as sources.

## Design notes

Background on why this integration exists, prior-art research, and
architecture decisions live in [`docs/notebook.md`](docs/notebook.md).

## Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for code style and commit
conventions. Contributions and testing against other source calendar
types are welcome.

## License

MIT — see [LICENSE](LICENSE).
