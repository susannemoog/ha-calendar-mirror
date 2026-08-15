"""Tests for the target-calendar backend adapters (`target.py`).

The Google adapter is exercised against the *real* gcal_sync model/response
classes (not hand-rolled fakes) wherever practical, specifically because
the original scaffold had a real bug here: `async_list_events` returns an
object whose `__aiter__` yields pages (each exposing events via `.items`),
not individual events directly. A fake that didn't mirror that shape would
have let the bug pass silently.
"""

from datetime import UTC, date, datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

from gcal_sync.api import (
    GoogleCalendarService,
    ListEventsResponse,
    _ListEventsResponseModel,
)
from gcal_sync.model import DateOrDatetime, Event as GoogleEvent
import pytest

from custom_components.calendar_mirror.target import (
    CalDavTargetCalendar,
    GoogleTargetCalendar,
    TargetEvent,
)

TARGET_CALENDAR_ID = "target@example.com"


def _make_google_event(event_id: str, description: str | None) -> GoogleEvent:
    """Build a minimal real gcal_sync Event for use as target-calendar test data."""
    return GoogleEvent(
        id=event_id,
        summary="Existing event",
        description=description,
        start=DateOrDatetime(date=date(2026, 8, 20)),
        end=DateOrDatetime(date=date(2026, 8, 21)),
    )


def _make_list_events_response(events: list[GoogleEvent]) -> ListEventsResponse:
    """Wrap events in a real (single-page) ListEventsResponse."""
    model = _ListEventsResponseModel(items=events)
    return ListEventsResponse(model)


@pytest.fixture
def mock_google_service() -> MagicMock:
    """Return a mocked GoogleCalendarService with async methods stubbed."""
    service = MagicMock(spec=GoogleCalendarService)
    service.async_list_events = AsyncMock()
    service.async_delete_event = AsyncMock()
    service.async_create_event = AsyncMock()
    return service


@pytest.fixture
def google_target(mock_google_service: MagicMock) -> GoogleTargetCalendar:
    """Return a GoogleTargetCalendar wired to the mocked service."""
    return GoogleTargetCalendar(mock_google_service, TARGET_CALENDAR_ID)


class TestGoogleTargetCalendarList:
    """`async_list_events` must translate gcal_sync's page-based iteration."""

    async def test_returns_events_with_id_and_description(
        self, google_target: GoogleTargetCalendar, mock_google_service: MagicMock
    ) -> None:
        """Events should carry their description and the Google event id as `native`."""
        mock_google_service.async_list_events.return_value = _make_list_events_response(
            [_make_google_event("evt-1", "some description")]
        )

        events = await google_target.async_list_events(
            datetime.now(UTC), datetime.now(UTC) + timedelta(days=1)
        )

        assert len(events) == 1
        assert events[0].description == "some description"
        assert events[0].native == "evt-1"

    async def test_events_without_id_are_skipped(
        self, google_target: GoogleTargetCalendar, mock_google_service: MagicMock
    ) -> None:
        """An event with no id can't be deleted later, so it shouldn't surface."""
        mock_google_service.async_list_events.return_value = _make_list_events_response(
            [_make_google_event(None, "desc")]
        )

        events = await google_target.async_list_events(
            datetime.now(UTC), datetime.now(UTC) + timedelta(days=1)
        )

        assert events == []

    async def test_paginated_results_all_checked(
        self, google_target: GoogleTargetCalendar, mock_google_service: MagicMock
    ) -> None:
        """Verify paged results are walked correctly.

        Regression test: iterating `async_list_events`'s result must walk
        pages and their `.items`, not treat the result as an async iterator
        of events directly.
        """
        page1 = _make_list_events_response([_make_google_event("p1", "d1")])
        page2_model = _ListEventsResponseModel(items=[_make_google_event("p2", "d2")])

        async def fake_get_next_page(page_token: str | None):
            return page2_model

        # Simulate a two-page result by wiring page1's private _get_next_page
        # and page_token, matching how ListEventsResponse.__aiter__ works.
        page1._model.page_token = "next"  # noqa: SLF001
        page1._get_next_page = fake_get_next_page  # noqa: SLF001

        mock_google_service.async_list_events.return_value = page1

        events = await google_target.async_list_events(
            datetime.now(UTC), datetime.now(UTC) + timedelta(days=1)
        )

        assert {e.native for e in events} == {"p1", "p2"}


class TestGoogleTargetCalendarDeleteAndCreate:
    """Tests for deleting and creating events via the Google backend."""

    async def test_delete_uses_native_event_id(
        self, google_target: GoogleTargetCalendar, mock_google_service: MagicMock
    ) -> None:
        """Deletion should pass the stashed Google event id straight through."""
        await google_target.async_delete_event(
            TargetEvent(description=None, native="evt-1")
        )

        mock_google_service.async_delete_event.assert_awaited_once_with(
            calendar_id=TARGET_CALENDAR_ID, event_id="evt-1"
        )

    async def test_create_builds_all_day_event(
        self, google_target: GoogleTargetCalendar, mock_google_service: MagicMock
    ) -> None:
        """A plain `date` start/end should produce an all-day Google event."""
        await google_target.async_create_event(
            summary="Waste collection",
            description="tagged",
            start=date(2026, 8, 20),
            end=date(2026, 8, 21),
        )

        mock_google_service.async_create_event.assert_awaited_once()
        calendar_id, event = mock_google_service.async_create_event.await_args.args
        assert calendar_id == TARGET_CALENDAR_ID
        assert event.summary == "Waste collection"
        assert event.description == "tagged"
        assert event.start.date == date(2026, 8, 20)

    async def test_create_builds_timed_event(
        self, google_target: GoogleTargetCalendar, mock_google_service: MagicMock
    ) -> None:
        """A `datetime` start/end should produce a timed Google event."""
        start = datetime(2026, 8, 20, 9, 0, 0, tzinfo=timezone(timedelta(hours=2)))
        end = datetime(2026, 8, 20, 10, 0, 0, tzinfo=timezone(timedelta(hours=2)))

        await google_target.async_create_event(
            summary="Meeting", description="", start=start, end=end
        )

        _, event = mock_google_service.async_create_event.await_args.args
        assert event.start.date_time == start


class _FakeCalDavEvent:
    """Minimal stand-in for a caldav `Event`, matching the properties we use."""

    def __init__(self, event_id: str, description: str | None) -> None:
        self.id = event_id
        self.icalendar_component = {"description": description} if description else {}
        self.delete = AsyncMock()


@pytest.fixture
def mock_caldav_calendar() -> MagicMock:
    """Return a mocked caldav Calendar with async methods stubbed."""
    calendar = MagicMock()
    calendar.search = AsyncMock()
    calendar.add_event = AsyncMock()
    return calendar


@pytest.fixture
def caldav_target(mock_caldav_calendar: MagicMock) -> CalDavTargetCalendar:
    """Return a CalDavTargetCalendar wired to the mocked calendar."""
    return CalDavTargetCalendar(MagicMock(close=AsyncMock()), mock_caldav_calendar)


class TestCalDavTargetCalendar:
    """Tests for the CalDAV backend adapter."""

    async def test_list_reads_description_from_icalendar_component(
        self,
        caldav_target: CalDavTargetCalendar,
        mock_caldav_calendar: MagicMock,
    ) -> None:
        """Description should come from the parsed VEVENT, id from the event itself."""
        mock_caldav_calendar.search.return_value = [
            _FakeCalDavEvent("uid-1", "tagged description"),
            _FakeCalDavEvent("uid-2", None),
        ]

        events = await caldav_target.async_list_events(
            datetime.now(UTC), datetime.now(UTC) + timedelta(days=1)
        )

        assert events[0].description == "tagged description"
        assert events[1].description is None

    async def test_delete_calls_native_event_delete(
        self, caldav_target: CalDavTargetCalendar
    ) -> None:
        """Deletion should call `.delete()` on the fetched CalDAV event directly."""
        fake_event = _FakeCalDavEvent("uid-1", "tagged")
        target_event = TargetEvent(description="tagged", native=fake_event)

        await caldav_target.async_delete_event(target_event)

        fake_event.delete.assert_awaited_once()

    async def test_create_passes_summary_description_and_dates(
        self,
        caldav_target: CalDavTargetCalendar,
        mock_caldav_calendar: MagicMock,
    ) -> None:
        """Creation should delegate straight to `Calendar.add_event` kwargs."""
        await caldav_target.async_create_event(
            summary="Waste collection",
            description="tagged",
            start=date(2026, 8, 20),
            end=date(2026, 8, 21),
        )

        mock_caldav_calendar.add_event.assert_awaited_once_with(
            summary="Waste collection",
            description="tagged",
            dtstart=date(2026, 8, 20),
            dtend=date(2026, 8, 21),
        )

    async def test_close_closes_the_owning_client(
        self, mock_caldav_calendar: MagicMock
    ) -> None:
        """`async_close` should release the dedicated CalDAV HTTP session."""
        client = MagicMock(close=AsyncMock())
        target = CalDavTargetCalendar(client, mock_caldav_calendar)

        await target.async_close()

        client.close.assert_awaited_once()
