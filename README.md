# Calendar Mirror

A Home Assistant custom integration that mirrors one or more local HA
calendar entities into an external, write-enabled calendar (starting with
Google Calendar) — with real create/update/delete sync, not just a
one-way ICS export.

**Status: early scaffold, not yet functional.** See
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

## Planned setup (once functional)

1. Create a Google Cloud OAuth client (Web application type), same
   process as HA's own Google Calendar integration.
2. Add the Calendar Mirror integration in HA, authorize via the standard
   OAuth consent screen.
3. Pick which existing `calendar.*` entities to use as sources, and
   which target Google Calendar to sync into.
4. Done — HA handles the recurring sync internally, no external script,
   cron, or manually-managed token required.

## Contributing / status

This is early and not yet installable as a working integration — see the
notebook for the current state and open TODOs before filing issues about
missing functionality. Contributions and testing on other calendar
sources welcome once there's a working baseline.

## License

MIT — see [LICENSE](LICENSE).
