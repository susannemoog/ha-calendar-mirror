"""Tests for the CalendarMirrorCoordinator sync logic.

These exercise the coordinator against the *real* gcal_sync model/response
classes (not hand-rolled fakes) wherever practical, specifically because
the original scaffold had a real bug here: `async_list_events` returns an
object whose `__aiter__` yields pages (each exposing events via `.items`),
not individual events directly. A fake that didn't mirror that shape
would have let the bug pass silently.
"""

from datetime import UTC, date, datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

from gcal_sync.api import (
    GoogleCalendarService,
    ListEventsResponse,
    _ListEventsResponseModel,
)
from gcal_sync.model import DateOrDatetime, Event as GoogleEvent
from homeassistant.core import ServiceCall, SupportsResponse
import pytest

from custom_components.calendar_mirror.const import SYNC_TAG
from custom_components.calendar_mirror.coordinator import (
    CalendarMirrorCoordinator,
    _parse_ha_event_datetime,
)


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
def coordinator(hass, mock_google_service: MagicMock) -> CalendarMirrorCoordinator:
    """Return a coordinator wired up with the mocked Google service."""
    return CalendarMirrorCoordinator(
        hass,
        google_service=mock_google_service,
        source_calendars=["calendar.test_source"],
        target_calendar_id="target@example.com",
        sync_window_days=30,
    )


class TestParseHaEventDatetime:
    """`_parse_ha_event_datetime` must correctly discriminate all-day vs timed."""

    def test_all_day_date_string(self) -> None:
        """A bare date string should parse as a `date`, not a `datetime`."""
        result = _parse_ha_event_datetime("2026-08-20")
        assert result == date(2026, 8, 20)
        assert not isinstance(result, datetime)

    def test_timed_datetime_string(self) -> None:
        """A full datetime string should parse with its timezone offset intact."""
        result = _parse_ha_event_datetime("2026-08-20T10:30:00+02:00")
        assert result == datetime(
            2026, 8, 20, 10, 30, 0, tzinfo=timezone(timedelta(hours=2))
        )


class TestClearPreviouslySyncedEvents:
    """Only events we tagged ourselves should ever be deleted."""

    async def test_deletes_only_tagged_events(
        self,
        coordinator: CalendarMirrorCoordinator,
        mock_google_service: MagicMock,
    ) -> None:
        """Events without our sync tag must never be deleted."""
        tagged = _make_google_event("tagged-1", f"{SYNC_TAG} from calendar.foo")
        untagged = _make_google_event("untagged-1", "A manually created event")
        no_description = _make_google_event("no-desc-1", None)
        mock_google_service.async_list_events.return_value = _make_list_events_response(
            [tagged, untagged, no_description]
        )

        start = datetime.now(UTC)
        end = start + timedelta(days=30)
        await coordinator._clear_previously_synced_events(start, end)  # noqa: SLF001

        mock_google_service.async_delete_event.assert_awaited_once_with(
            calendar_id="target@example.com",
            event_id="tagged-1",
        )

    async def test_paginated_results_all_checked(
        self,
        coordinator: CalendarMirrorCoordinator,
        mock_google_service: MagicMock,
    ) -> None:
        """Verify paged results are walked correctly.

        Regression test: iterating `async_list_events`'s result must walk
        pages and their `.items`, not treat the result as an async iterator
        of events directly.
        """
        page1 = _make_list_events_response([_make_google_event("p1-tagged", SYNC_TAG)])
        page2_model = _ListEventsResponseModel(
            items=[_make_google_event("p2-tagged", SYNC_TAG)]
        )

        async def fake_get_next_page(page_token: str | None):
            return page2_model

        # Simulate a two-page result by wiring page1's private _get_next_page
        # and page_token, matching how ListEventsResponse.__aiter__ works.
        page1._model.page_token = "next"  # noqa: SLF001
        page1._get_next_page = fake_get_next_page  # noqa: SLF001

        mock_google_service.async_list_events.return_value = page1

        start = datetime.now(UTC)
        end = start + timedelta(days=30)
        await coordinator._clear_previously_synced_events(start, end)  # noqa: SLF001

        deleted_ids = {
            call.kwargs["event_id"]
            for call in mock_google_service.async_delete_event.await_args_list
        }
        assert deleted_ids == {"p1-tagged", "p2-tagged"}


class TestCreateTargetEvent:
    """Tests for building and creating one target-calendar event."""

    async def test_builds_event_with_sync_tag_and_creates_it(
        self,
        coordinator: CalendarMirrorCoordinator,
        mock_google_service: MagicMock,
    ) -> None:
        """A normal HA event should be created with the sync tag in its description."""
        ha_event = {
            "summary": "Waste collection",
            "description": "Bio bin",
            "start": "2026-08-20",
            "end": "2026-08-21",
        }
        await coordinator._create_target_event(ha_event, "calendar.waste")  # noqa: SLF001

        mock_google_service.async_create_event.assert_awaited_once()
        args, _ = mock_google_service.async_create_event.await_args
        calendar_id, event = args
        assert calendar_id == "target@example.com"
        assert event.summary == "Waste collection"
        assert SYNC_TAG in event.description
        assert "calendar.waste" in event.description
        assert "Bio bin" in event.description
        assert event.start.date == date(2026, 8, 20)
        assert event.end.date == date(2026, 8, 21)

    async def test_timed_event_uses_datetime(
        self,
        coordinator: CalendarMirrorCoordinator,
        mock_google_service: MagicMock,
    ) -> None:
        """A timed HA event should produce a Google event with a datetime, not a date."""
        ha_event = {
            "summary": "Meeting",
            "start": "2026-08-20T09:00:00+02:00",
            "end": "2026-08-20T10:00:00+02:00",
        }
        await coordinator._create_target_event(ha_event, "calendar.work")  # noqa: SLF001

        _, event = mock_google_service.async_create_event.await_args.args
        assert event.start.date_time == datetime(
            2026, 8, 20, 9, 0, 0, tzinfo=timezone(timedelta(hours=2))
        )

    async def test_missing_summary_falls_back(
        self,
        coordinator: CalendarMirrorCoordinator,
        mock_google_service: MagicMock,
    ) -> None:
        """An HA event with no summary should fall back to a placeholder title."""
        ha_event = {"start": "2026-08-20", "end": "2026-08-21"}
        await coordinator._create_target_event(ha_event, "calendar.foo")  # noqa: SLF001

        _, event = mock_google_service.async_create_event.await_args.args
        assert event.summary == "(no title)"


class TestAsyncUpdateData:
    """End-to-end tests for one full coordinator refresh."""

    async def test_full_sync_pass_clears_then_recreates(
        self,
        hass,
        coordinator: CalendarMirrorCoordinator,
        mock_google_service: MagicMock,
    ) -> None:
        """A full refresh should read source events and create them on the target."""
        mock_google_service.async_list_events.return_value = _make_list_events_response(
            []
        )

        get_events_calls: list[ServiceCall] = []

        async def fake_get_events(call: ServiceCall) -> dict:
            get_events_calls.append(call)
            return {
                "calendar.test_source": {
                    "events": [
                        {
                            "summary": "Event A",
                            "start": "2026-08-20",
                            "end": "2026-08-21",
                        }
                    ]
                }
            }

        hass.services.async_register(
            "calendar",
            "get_events",
            fake_get_events,
            supports_response=SupportsResponse.ONLY,
        )

        await coordinator._async_update_data()  # noqa: SLF001

        assert len(get_events_calls) == 1
        assert get_events_calls[0].data["entity_id"] == "calendar.test_source"
        mock_google_service.async_create_event.assert_awaited_once()
