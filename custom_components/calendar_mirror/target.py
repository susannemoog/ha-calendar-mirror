"""Target-calendar abstraction: one interface, two backends.

`coordinator.py` syncs into a target calendar without knowing whether
that target is Google Calendar (via gcal_sync) or a generic CalDAV server
(via the `caldav` package's async client). Each backend adapts its own
client's event shape into the three operations the coordinator needs:
list, create, delete.

STATUS: verified against real library source/introspection, not guessed
(checked 2026-08-14):
  - gcal_sync: see coordinator.py's module docstring for the page-vs-event
    iteration gotcha this preserves.
  - caldav (async client, `caldav.async_davclient.AsyncDAVClient`):
    - `Calendar.search(start=, end=, event=True, expand=True)` returns
      (a coroutine resolving to) a list of `Event` objects - the dual-mode
      client dispatches sync/async internally via the same method names.
    - `Event.id` extracts the iCalendar UID via a cheap accessor (no
      server round-trip). `Event.icalendar_component` gives the parsed
      VEVENT, from which `.get("description")` reads our sync tag back.
    - `Calendar.add_event(summary=, description=, dtstart=, dtend=)`
      delegates to `add_object(Event, **ical_data)`, which builds the
      VEVENT directly from those kwargs - no manual iCalendar text needed.
    - `CalendarObjectResource.delete()` removes the fetched event; since
      `async_list_events` below already holds the fetched objects, no
      separate fetch-by-UID round trip is needed to delete them.
    - `caldav.lib.error.AuthorizationError` (subclass of `DAVError`) is
      raised for 401/403 responses. Network-level failures aren't wrapped
      by caldav at all - they surface as the underlying `niquests`
      library's `niquests.exceptions.ConnectionError` / `.Timeout`
      (both subclass `OSError`), the async equivalent of the
      `requests.ConnectionError` / `.Timeout` HA core's own sync caldav
      integration catches.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from caldav import Calendar as CalDavCalendar
from caldav.async_davclient import AsyncDAVClient
from gcal_sync.api import GoogleCalendarService, ListEventsRequest
from gcal_sync.model import DateOrDatetime, Event as GoogleEvent


@dataclass
class TargetEvent:
    """A previously-synced event as seen in the target calendar.

    `native` is opaque to the coordinator - each backend stashes whatever
    it needs to delete this exact event again (a Google event id, or a
    CalDAV `Event` object) and interprets it in its own `async_delete_event`.
    """

    description: str | None
    native: Any


class TargetCalendarClient(ABC):
    """Common interface `coordinator.py` uses regardless of target backend."""

    @abstractmethod
    async def async_list_events(
        self, start: datetime, end: datetime
    ) -> list[TargetEvent]:
        """Return events starting in [start, end) in the target calendar."""

    @abstractmethod
    async def async_delete_event(self, event: TargetEvent) -> None:
        """Delete one previously-listed event from the target calendar."""

    @abstractmethod
    async def async_create_event(
        self,
        *,
        summary: str,
        description: str,
        start: date | datetime,
        end: date | datetime,
    ) -> None:
        """Create one event in the target calendar."""

    async def async_close(self) -> None:  # noqa: B027 - intentional optional hook
        """Release any backend resources (HTTP sessions, etc).

        No-op by default - HA-managed sessions (e.g. the shared aiohttp
        session gcal_sync's auth bridge uses) are HA's to close, not ours.
        Only backends that own a dedicated client override this.
        """


class GoogleTargetCalendar(TargetCalendarClient):
    """Target calendar backed by Google Calendar via gcal_sync."""

    def __init__(self, service: GoogleCalendarService, calendar_id: str) -> None:
        """Init the Google target calendar."""
        self._service = service
        self._calendar_id = calendar_id

    async def async_list_events(
        self, start: datetime, end: datetime
    ) -> list[TargetEvent]:
        """Return events in range, translating gcal_sync's page iteration."""
        request = ListEventsRequest(
            calendar_id=self._calendar_id, start_time=start, end_time=end
        )
        result = await self._service.async_list_events(request)
        events: list[TargetEvent] = []
        # `result` iterates *pages*, each exposing its events via `.items`
        # - gcal_sync's ListEventsResponse.__aiter__ yields pages, not
        # individual events.
        async for page in result:
            events.extend(
                TargetEvent(description=event.description, native=event.id)
                for event in page.items
                if event.id
            )
        return events

    async def async_delete_event(self, event: TargetEvent) -> None:
        """Delete by the Google event id stashed in `native`."""
        await self._service.async_delete_event(
            calendar_id=self._calendar_id, event_id=event.native
        )

    async def async_create_event(
        self,
        *,
        summary: str,
        description: str,
        start: date | datetime,
        end: date | datetime,
    ) -> None:
        """Create a Google Calendar event."""
        event = GoogleEvent(
            summary=summary,
            description=description,
            start=DateOrDatetime.parse(start),
            end=DateOrDatetime.parse(end),
        )
        await self._service.async_create_event(self._calendar_id, event)


class CalDavTargetCalendar(TargetCalendarClient):
    """Target calendar backed by a CalDAV calendar via the async caldav client."""

    def __init__(self, client: AsyncDAVClient, calendar: CalDavCalendar) -> None:
        """Init the CalDAV target calendar.

        Keeps a reference to the owning client (not just the calendar) so
        `async_close` can release its HTTP session - unlike the Google
        path, this client is dedicated to this config entry, not a
        session HA manages elsewhere.
        """
        self._client = client
        self._calendar = calendar

    async def async_list_events(
        self, start: datetime, end: datetime
    ) -> list[TargetEvent]:
        """Return events in range, reading description from the parsed VEVENT."""
        results = await self._calendar.search(
            start=start, end=end, event=True, expand=True
        )
        events: list[TargetEvent] = []
        for result in results:
            description = result.icalendar_component.get("description")
            events.append(
                TargetEvent(
                    description=str(description) if description else None,
                    native=result,
                )
            )
        return events

    async def async_delete_event(self, event: TargetEvent) -> None:
        """Delete the fetched CalDAV event object stashed in `native`."""
        await event.native.delete()

    async def async_create_event(
        self,
        *,
        summary: str,
        description: str,
        start: date | datetime,
        end: date | datetime,
    ) -> None:
        """Create a CalDAV event."""
        await self._calendar.add_event(
            summary=summary,
            description=description,
            dtstart=start,
            dtend=end,
        )

    async def async_close(self) -> None:
        """Close this entry's dedicated CalDAV HTTP session."""
        await self._client.close()
