"""Sync coordinator for Calendar Mirror.

Ports the logic from the original prototype script
(ha_google_calendar_sync.py) into HA's own process, using:
  - HA's internal calendar.get_events service for source events
    (no external REST round-trip needed, since we're running inside HA)
  - gcal_sync.api.GoogleCalendarService for the target Google Calendar,
    reusing the OAuth2 session HA's config_entry_oauth2_flow already
    manages for us.

STATUS: create/delete/list calls below are verified against the real
gcal_sync source (github.com/allenporter/gcal_sync, gcal_sync/api.py and
gcal_sync/model.py, checked 2026-08-13) rather than guessed:
  - GoogleCalendarService.async_create_event(calendar_id, event: Event) -> None
  - GoogleCalendarService.async_delete_event(calendar_id, event_id) -> None
  - GoogleCalendarService.async_list_events(request: ListEventsRequest)
    -> ListEventsResponse, whose `__aiter__` yields *pages*
    (ListEventsResponse objects), each with an `.items: list[Event]`
    property - NOT individual Event objects. The original skeleton's
    `async for event in result` was wrong for this reason (see notebook
    gotchas).
  - gcal_sync.model.Event requires `start`/`end` as DateOrDatetime;
    DateOrDatetime.parse(date_or_datetime) builds the right one from a
    plain `datetime.date` or `datetime.datetime`.

Source-side event shape: HA's `calendar.get_events` service (internal
service call, not the REST API) returns events built by
`_list_events_dict_factory` in HA core's `calendar/__init__.py`, which
flattens `start`/`end` to plain ISO 8601 strings (e.g. "2026-08-13" for
all-day, "2026-08-13T10:00:00+02:00" for timed) - NOT the nested
`{"date": ...}` / `{"dateTime": ...}` shape documented in the notebook's
gotchas section. That gotcha is real but applies to the *REST* API the
original prototype script hit from outside HA; the internal service call
used here has a different (flatter) response shape. Distinguish all-day
vs timed by checking for "T" in the string, since a plain `date.isoformat()`
never contains one.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
import logging

from gcal_sync.api import GoogleCalendarService, ListEventsRequest
from gcal_sync.model import DateOrDatetime, Event as GoogleEvent
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import DEFAULT_SYNC_WINDOW_DAYS, DOMAIN, SYNC_TAG, SYNC_TITLE_PREFIX

_LOGGER = logging.getLogger(__name__)


class CalendarMirrorCoordinator(DataUpdateCoordinator[None]):
    """Coordinates mirroring source HA calendars into a target Google Calendar."""

    def __init__(
        self,
        hass: HomeAssistant,
        *,
        google_service: GoogleCalendarService,
        source_calendars: list[str],
        target_calendar_id: str,
        sync_window_days: int = DEFAULT_SYNC_WINDOW_DAYS,
        update_interval: timedelta | None = None,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=update_interval,
        )
        self._google_service = google_service
        self._source_calendars = source_calendars
        self._target_calendar_id = target_calendar_id
        self._sync_window_days = sync_window_days

    async def _async_update_data(self) -> None:
        """Run one full sync pass: clear previously-synced events, recreate from sources."""
        start = datetime.now(UTC)
        end = start + timedelta(days=self._sync_window_days)

        await self._clear_previously_synced_events(start, end)

        total = 0
        for entity_id in self._source_calendars:
            events = await self._get_ha_events(entity_id, start, end)
            for event in events:
                await self._create_target_event(event, entity_id)
                total += 1

        _LOGGER.debug(
            "Calendar Mirror: synced %d events from %d source calendars",
            total,
            len(self._source_calendars),
        )

    async def _get_ha_events(
        self, entity_id: str, start: datetime, end: datetime
    ) -> list[dict]:
        """Read events from a source HA calendar entity."""
        response = await self.hass.services.async_call(
            "calendar",
            "get_events",
            {
                "entity_id": entity_id,
                "start_date_time": start.isoformat(),
                "end_date_time": end.isoformat(),
            },
            blocking=True,
            return_response=True,
        )
        return response.get(entity_id, {}).get("events", [])

    async def _clear_previously_synced_events(
        self, start: datetime, end: datetime
    ) -> None:
        """Delete events in the target calendar previously created by us."""
        request = ListEventsRequest(
            calendar_id=self._target_calendar_id,
            start_time=start,
            end_time=end,
        )
        result = await self._google_service.async_list_events(request)
        # `result` iterates *pages*, each exposing its events via `.items`
        # - see module docstring for why this isn't `async for event in result`.
        async for page in result:
            for event in page.items:
                if event.id and SYNC_TAG in (event.description or ""):
                    await self._google_service.async_delete_event(
                        calendar_id=self._target_calendar_id,
                        event_id=event.id,
                    )

    async def _create_target_event(self, ha_event: dict, source_entity: str) -> None:
        """Create one event in the target Google Calendar."""
        summary = SYNC_TITLE_PREFIX + ha_event.get("summary", "(no title)")
        description = (
            f"{SYNC_TAG} Synced automatically from Home Assistant ({source_entity}). "
            "Edits or deletions made here will be overwritten on the next sync - "
            f"edit the source in Home Assistant instead.\n\n"
            f"{ha_event.get('description') or ''}"
        ).strip()

        event = GoogleEvent(
            summary=summary,
            description=description,
            start=DateOrDatetime.parse(_parse_ha_event_datetime(ha_event["start"])),
            end=DateOrDatetime.parse(_parse_ha_event_datetime(ha_event["end"])),
        )
        await self._google_service.async_create_event(self._target_calendar_id, event)


def _parse_ha_event_datetime(value: str) -> date | datetime:
    """Parse a start/end value from HA's calendar.get_events service response.

    These are flat ISO 8601 strings (see module docstring) - a bare date
    for all-day events, a full datetime for timed ones. A `date.isoformat()`
    never contains "T"; `datetime.isoformat()` always does, so that's a
    reliable discriminator.
    """
    if "T" in value:
        return datetime.fromisoformat(value)
    return date.fromisoformat(value)
