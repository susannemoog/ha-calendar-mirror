"""Tests for custom_components/calendar_mirror/__init__.py.

Covers the two things that were previously unverified placeholders:
`ApiAuthImpl` bridging HA's OAuth2Session to gcal_sync's AbstractAuth, and
`async_setup_entry`/`async_unload_entry` wiring the coordinator into
`hass.data`.
"""

from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_entry_oauth2_flow
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.calendar_mirror import (
    ApiAuthImpl,
    async_setup_entry,
    async_unload_entry,
)
from custom_components.calendar_mirror.const import (
    CONF_SOURCE_CALENDARS,
    CONF_SYNC_INTERVAL_MINUTES,
    CONF_SYNC_WINDOW_DAYS,
    CONF_TARGET_CALENDAR_ID,
    DOMAIN,
)
from custom_components.calendar_mirror.coordinator import CalendarMirrorCoordinator


class TestApiAuthImpl:
    """Verify the AbstractAuth bridge against a mocked OAuth2Session."""

    async def test_returns_access_token_after_ensuring_validity(self) -> None:
        """The bridge should refresh via the session, then return its access token."""
        session = MagicMock(spec=config_entry_oauth2_flow.OAuth2Session)
        session.async_ensure_token_valid = AsyncMock()
        session.token = {"access_token": "the-access-token"}

        auth = ApiAuthImpl(MagicMock(), session)
        token = await auth.async_get_access_token()

        session.async_ensure_token_valid.assert_awaited_once()
        assert token == "the-access-token"


@pytest.fixture
def config_entry(hass: HomeAssistant) -> MockConfigEntry:
    """Return a registered config entry with a matching OAuth2 implementation."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "auth_implementation": DOMAIN,
            "token": {
                "access_token": "abc",
                "refresh_token": "def",
                "expires_at": 9999999999,
            },
            CONF_SOURCE_CALENDARS: ["calendar.waste", "calendar.bomo"],
            CONF_TARGET_CALENDAR_ID: "primary",
            CONF_SYNC_WINDOW_DAYS: 30,
            CONF_SYNC_INTERVAL_MINUTES: 20,
        },
    )
    entry.add_to_hass(hass)
    config_entry_oauth2_flow.async_register_implementation(
        hass,
        DOMAIN,
        config_entry_oauth2_flow.LocalOAuth2Implementation(
            hass,
            DOMAIN,
            "client-id",
            "client-secret",
            "https://x/auth",
            "https://x/token",
        ),
    )
    return entry


class TestAsyncSetupEntry:
    """Tests for wiring a config entry into a running coordinator."""

    async def test_creates_coordinator_with_config_data(
        self, hass: HomeAssistant, config_entry: MockConfigEntry
    ) -> None:
        """Setup should build a coordinator using the entry's stored config."""
        with patch.object(
            CalendarMirrorCoordinator,
            "async_config_entry_first_refresh",
            AsyncMock(),
        ):
            result = await async_setup_entry(hass, config_entry)

        assert result is True
        coordinator = hass.data[DOMAIN][config_entry.entry_id]
        assert isinstance(coordinator, CalendarMirrorCoordinator)
        assert coordinator._source_calendars == ["calendar.waste", "calendar.bomo"]  # noqa: SLF001
        assert coordinator._target_calendar_id == "primary"  # noqa: SLF001

    async def test_unload_entry_removes_coordinator(
        self, hass: HomeAssistant, config_entry: MockConfigEntry
    ) -> None:
        """Unloading should remove the coordinator from hass.data."""
        with patch.object(
            CalendarMirrorCoordinator,
            "async_config_entry_first_refresh",
            AsyncMock(),
        ):
            await async_setup_entry(hass, config_entry)

        assert config_entry.entry_id in hass.data[DOMAIN]

        result = await async_unload_entry(hass, config_entry)

        assert result is True
        assert config_entry.entry_id not in hass.data[DOMAIN]
