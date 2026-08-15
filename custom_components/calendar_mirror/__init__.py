"""The Calendar Mirror integration.

STATUS: config entry -> target-backend client -> coordinator is wired up
for both supported target types.

Google: OAuth session -> gcal_sync auth bridge. The AbstractAuth bridge
below (`ApiAuthImpl`) follows the exact pattern HA core's own Google
Calendar integration uses in `homeassistant/components/google/api.py`
(verified against home-assistant/core source, checked 2026-08-13): wrap a
`config_entry_oauth2_flow.OAuth2Session`, call
`async_ensure_token_valid()` before every request, and hand back
`session.token["access_token"]`.

CalDAV: no OAuth session to reuse - `caldav.async_davclient.AsyncDAVClient`
is built directly from stored credentials, and `client.calendar(url=...)`
rebuilds the target `Calendar` object from its stored URL with no network
round-trip (verified via introspection, checked 2026-08-14; see target.py).
"""

from __future__ import annotations

from datetime import timedelta
from typing import cast

import aiohttp
from caldav.async_davclient import AsyncDAVClient
from gcal_sync.api import GoogleCalendarService
from gcal_sync.auth import AbstractAuth
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_entry_oauth2_flow
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    CONF_CALDAV_PASSWORD,
    CONF_CALDAV_URL,
    CONF_CALDAV_USERNAME,
    CONF_CALDAV_VERIFY_SSL,
    CONF_SOURCE_CALENDARS,
    CONF_SYNC_INTERVAL_MINUTES,
    CONF_SYNC_WINDOW_DAYS,
    CONF_TARGET_CALENDAR_ID,
    CONF_TARGET_TYPE,
    DEFAULT_SYNC_INTERVAL_MINUTES,
    DEFAULT_SYNC_WINDOW_DAYS,
    DOMAIN,
    TARGET_TYPE_CALDAV,
)
from .coordinator import CalendarMirrorCoordinator
from .target import CalDavTargetCalendar, GoogleTargetCalendar, TargetCalendarClient

PLATFORMS: list[str] = []  # no entities exposed yet - sync-only integration for now


class ApiAuthImpl(AbstractAuth):
    """Bridge HA's OAuth2Session to gcal_sync's AbstractAuth interface."""

    def __init__(
        self,
        websession: aiohttp.ClientSession,
        session: config_entry_oauth2_flow.OAuth2Session,
    ) -> None:
        """Init the auth implementation."""
        super().__init__(websession)
        self._session = session

    async def async_get_access_token(self) -> str:
        """Return a valid access token, refreshing via HA's session if needed."""
        await self._session.async_ensure_token_valid()
        return cast(str, self._session.token["access_token"])


class AccessTokenAuthImpl(AbstractAuth):
    """Bridge a raw OAuth access token to gcal_sync's AbstractAuth interface.

    Used only during the config flow, before a config entry (and thus an
    OAuth2Session) exists yet - e.g. to list the user's calendars for the
    target-calendar picker. No refresh support, which is fine since the
    token was just minted moments earlier. Same pattern as HA core's
    google integration's AccessTokenAuthImpl.
    """

    def __init__(self, websession: aiohttp.ClientSession, access_token: str) -> None:
        """Init the auth implementation."""
        super().__init__(websession)
        self._access_token = access_token

    async def async_get_access_token(self) -> str:
        """Return the stored access token."""
        return self._access_token


async def _async_build_target(
    hass: HomeAssistant, entry: ConfigEntry, target_calendar_id: str
) -> TargetCalendarClient:
    """Build the target-backend client this entry is configured for."""
    if entry.data.get(CONF_TARGET_TYPE) == TARGET_TYPE_CALDAV:
        client = AsyncDAVClient(
            url=entry.data[CONF_CALDAV_URL],
            username=entry.data[CONF_CALDAV_USERNAME],
            password=entry.data[CONF_CALDAV_PASSWORD],
            ssl_verify_cert=entry.data.get(CONF_CALDAV_VERIFY_SSL, True),
        )
        # No network traffic - rebuilds the Calendar object from its
        # stored URL directly (see target.py module docstring).
        calendar = client.calendar(url=target_calendar_id)
        return CalDavTargetCalendar(client, calendar)

    implementation = (
        await config_entry_oauth2_flow.async_get_config_entry_implementation(
            hass, entry
        )
    )
    session = config_entry_oauth2_flow.OAuth2Session(hass, entry, implementation)
    auth = ApiAuthImpl(async_get_clientsession(hass), session)
    return GoogleTargetCalendar(GoogleCalendarService(auth), target_calendar_id)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Calendar Mirror from a config entry."""
    # Options (set via the options flow after creation) override the
    # original data set at entry-creation time - lets source/target
    # calendars be changed later without redoing setup.
    config = {**entry.data, **entry.options}
    target_calendar_id = config.get(CONF_TARGET_CALENDAR_ID, "")

    target = await _async_build_target(hass, entry, target_calendar_id)

    coordinator = CalendarMirrorCoordinator(
        hass,
        entry_id=entry.entry_id,
        target=target,
        source_calendars=config.get(CONF_SOURCE_CALENDARS, []),
        sync_window_days=config.get(CONF_SYNC_WINDOW_DAYS, DEFAULT_SYNC_WINDOW_DAYS),
        update_interval=timedelta(
            minutes=config.get(
                CONF_SYNC_INTERVAL_MINUTES, DEFAULT_SYNC_INTERVAL_MINUTES
            )
        ),
    )

    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    coordinator: CalendarMirrorCoordinator | None = hass.data[DOMAIN].pop(
        entry.entry_id, None
    )
    if coordinator is not None:
        await coordinator.target.async_close()
    return True
