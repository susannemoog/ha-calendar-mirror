"""Sync coordinator for Calendar Mirror.

Ports the logic from the original prototype script
(ha_google_calendar_sync.py) into HA's own process, using:
  - HA's internal calendar.get_events service for source events
    (no external REST round-trip needed, since we're running inside HA)
  - gcal_sync.api.GoogleCalendarService for the target Google Calendar,
    reusing the OAuth2 session HA's config_entry_oauth2_flow already
    manages for us.

STATUS: skeleton / not yet tested against a live instance. The exact
gcal_sync create/delete event method names and request/response shapes
need verification against gcal_sync.api docs
(https://allenporter.github.io/gcal_sync/gcal_sync/api.html) before this
is trusted - documented confirmed methods so far: async_get_calendar,
ListEventsRequest for reading. Create/delete method names are TODOs
below, marked explicitly rather than guessed with false confidence.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging

from gcal_sync.api import GoogleCalendarService, ListEventsRequest

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import (
    CONF_SOURCE_CALENDARS,
    CONF_SYNC_WINDOW_DAYS,
    CONF_TARGET_CALENDAR_ID,
    DEFAULT_SYNC_WINDOW_DAYS,
    DOMAIN,
    SYNC_TAG,
)

_LOGGER = logging.getLogger(__name__)


class CalendarMirrorCoordinator(DataUpdateCoordinator[None]):
    """Coordinates mirroring source HA calendars into a target Google Calendar."""

    def __init__(
        self,
        hass: HomeAssistant,
        google_service: GoogleCalendarService,
        source_calendars: list[str],
        target_calendar_id: str,
        sync_window_days: int = DEFAULT_SYNC_WINDOW_DAYS,
        update_interval: timedelta | None = None,
    ) -> None:
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
        start = datetime.now(timezone.utc)
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
        """Delete events in the target calendar previously created by us.

        TODO: verify exact gcal_sync request/response classes for listing
        and deleting events - ListEventsRequest is confirmed for reads,
        but the delete call's exact method name
        (async_delete_event? something else?) needs checking against
        gcal_sync.api before this is trusted to run unattended.
        """
        request = ListEventsRequest(
            calendar_id=self._target_calendar_id,
            start_time=start,
            end_time=end,
        )
        result = await self._google_service.async_list_events(request)
        async for event in result:
            if SYNC_TAG in (event.description or ""):
                # TODO: confirm this is the real method/signature
                await self._google_service.async_delete_event(
                    calendar_id=self._target_calendar_id,
                    event_id=event.id,
                )

    async def _create_target_event(self, ha_event: dict, source_entity: str) -> None:
        """Create one event in the target Google Calendar.

        TODO: confirm gcal_sync's event creation call/model - likely an
        Event dataclass passed to something like async_create_event,
        but not yet verified against a live run.
        """
        summary = ha_event.get("summary", "(no title)")
        description = f"{SYNC_TAG} from {source_entity}\n\n{ha_event.get('description') or ''}".strip()

        # TODO: build the actual gcal_sync Event object here once the
        # exact model is confirmed, then call the real create method.
        raise NotImplementedError(
            "Event creation not yet wired up to gcal_sync - see TODOs above"
        )
