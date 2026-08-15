"""Tests for the CalendarMirrorCoordinator sync logic.

The coordinator is backend-agnostic - it talks to a `TargetCalendarClient`,
not gcal_sync or caldav directly. These tests mock that abstraction; the
backend-specific adapters (including the gcal_sync pagination regression
test) live in test_target.py.
"""

from datetime import UTC, date, datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

from gcal_sync.exceptions import AuthException
from homeassistant.core import ServiceCall, SupportsResponse
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
import pytest

from custom_components.calendar_mirror.const import SYNC_TAG, SYNC_TITLE_PREFIX
from custom_components.calendar_mirror.coordinator import (
    CalendarMirrorCoordinator,
    _parse_ha_event_datetime,
)
from custom_components.calendar_mirror.target import TargetCalendarClient, TargetEvent

TEST_ENTRY_ID = "test-entry-id"
TEST_SYNC_TAG = f"{SYNC_TAG}:{TEST_ENTRY_ID}"


@pytest.fixture
def mock_target() -> MagicMock:
    """Return a mocked TargetCalendarClient with async methods stubbed."""
    target = MagicMock(spec=TargetCalendarClient)
    target.async_list_events = AsyncMock(return_value=[])
    target.async_delete_event = AsyncMock()
    target.async_create_event = AsyncMock()
    return target


@pytest.fixture
def coordinator(hass, mock_target: MagicMock) -> CalendarMirrorCoordinator:
    """Return a coordinator wired up with the mocked target client."""
    return CalendarMirrorCoordinator(
        hass,
        entry_id=TEST_ENTRY_ID,
        target=mock_target,
        source_calendars=["calendar.test_source"],
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
        mock_target: MagicMock,
    ) -> None:
        """Events without our sync tag must never be deleted."""
        tagged = TargetEvent(
            description=f"{TEST_SYNC_TAG} from calendar.foo", native="tagged-1"
        )
        untagged = TargetEvent(
            description="A manually created event", native="untagged-1"
        )
        no_description = TargetEvent(description=None, native="no-desc-1")
        mock_target.async_list_events.return_value = [tagged, untagged, no_description]

        start = datetime.now(UTC)
        end = start + timedelta(days=30)
        await coordinator._clear_previously_synced_events(start, end)  # noqa: SLF001

        mock_target.async_delete_event.assert_awaited_once_with(tagged)

    async def test_does_not_delete_another_entrys_synced_events(
        self,
        coordinator: CalendarMirrorCoordinator,
        mock_target: MagicMock,
    ) -> None:
        """Two config entries sharing a target calendar must not delete each other's events.

        Regression test for the tag being scoped per config entry
        (`SYNC_TAG:{entry_id}`) rather than a single global marker.
        """
        other_entrys_event = TargetEvent(
            description=f"{SYNC_TAG}:some-other-entry-id from calendar.foo",
            native="other-entry-1",
        )
        mock_target.async_list_events.return_value = [other_entrys_event]

        start = datetime.now(UTC)
        end = start + timedelta(days=30)
        await coordinator._clear_previously_synced_events(start, end)  # noqa: SLF001

        mock_target.async_delete_event.assert_not_awaited()


class TestCreateTargetEvent:
    """Tests for building and creating one target-calendar event."""

    async def test_builds_event_with_sync_tag_and_creates_it(
        self,
        coordinator: CalendarMirrorCoordinator,
        mock_target: MagicMock,
    ) -> None:
        """A normal HA event should be created with the sync tag in its description."""
        ha_event = {
            "summary": "Waste collection",
            "description": "Bio bin",
            "start": "2026-08-20",
            "end": "2026-08-21",
        }
        await coordinator._create_target_event(ha_event, "calendar.waste")  # noqa: SLF001

        mock_target.async_create_event.assert_awaited_once()
        kwargs = mock_target.async_create_event.await_args.kwargs
        assert kwargs["summary"] == f"{SYNC_TITLE_PREFIX}Waste collection"
        assert SYNC_TAG in kwargs["description"]
        assert "calendar.waste" in kwargs["description"]
        assert "overwritten on the next sync" in kwargs["description"]
        assert "Bio bin" in kwargs["description"]
        assert kwargs["start"] == date(2026, 8, 20)
        assert kwargs["end"] == date(2026, 8, 21)

    async def test_timed_event_uses_datetime(
        self,
        coordinator: CalendarMirrorCoordinator,
        mock_target: MagicMock,
    ) -> None:
        """A timed HA event should produce a target event with a datetime, not a date."""
        ha_event = {
            "summary": "Meeting",
            "start": "2026-08-20T09:00:00+02:00",
            "end": "2026-08-20T10:00:00+02:00",
        }
        await coordinator._create_target_event(ha_event, "calendar.work")  # noqa: SLF001

        kwargs = mock_target.async_create_event.await_args.kwargs
        assert kwargs["start"] == datetime(
            2026, 8, 20, 9, 0, 0, tzinfo=timezone(timedelta(hours=2))
        )

    async def test_missing_summary_falls_back(
        self,
        coordinator: CalendarMirrorCoordinator,
        mock_target: MagicMock,
    ) -> None:
        """An HA event with no summary should fall back to a placeholder title."""
        ha_event = {"start": "2026-08-20", "end": "2026-08-21"}
        await coordinator._create_target_event(ha_event, "calendar.foo")  # noqa: SLF001

        kwargs = mock_target.async_create_event.await_args.kwargs
        assert kwargs["summary"] == f"{SYNC_TITLE_PREFIX}(no title)"


class TestAsyncUpdateData:
    """End-to-end tests for one full coordinator refresh."""

    async def test_full_sync_pass_clears_then_recreates(
        self,
        hass,
        coordinator: CalendarMirrorCoordinator,
        mock_target: MagicMock,
    ) -> None:
        """A full refresh should read source events and create them on the target."""
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
        mock_target.async_create_event.assert_awaited_once()

    async def test_auth_failure_triggers_reauth(
        self,
        coordinator: CalendarMirrorCoordinator,
        mock_target: MagicMock,
    ) -> None:
        """An expired/revoked target-calendar grant should surface as ConfigEntryAuthFailed.

        DataUpdateCoordinator turns this specific exception into HA's
        standard "reauthenticate" prompt - a generic exception would not.
        """
        mock_target.async_list_events.side_effect = AuthException("token revoked")

        with pytest.raises(ConfigEntryAuthFailed):
            await coordinator._async_update_data()  # noqa: SLF001

    async def test_one_bad_source_does_not_block_others(
        self, hass, mock_target: MagicMock
    ) -> None:
        """One source calendar erroring shouldn't prevent the others from syncing."""
        coordinator = CalendarMirrorCoordinator(
            hass,
            entry_id=TEST_ENTRY_ID,
            target=mock_target,
            source_calendars=["calendar.bad_source", "calendar.good_source"],
            sync_window_days=30,
        )

        async def fake_get_events(call: ServiceCall) -> dict:
            if call.data["entity_id"] == "calendar.bad_source":
                raise HomeAssistantError("calendar.bad_source is unavailable")
            return {
                "calendar.good_source": {
                    "events": [
                        {
                            "summary": "Still works",
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

        mock_target.async_create_event.assert_awaited_once()
