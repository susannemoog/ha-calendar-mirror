"""The Calendar Mirror integration.

STATUS: skeleton. Wires up config entry -> OAuth session -> coordinator,
but the coordinator's actual create/delete calls are not yet implemented
(see coordinator.py TODOs). Do not deploy this as-is; it's a scaffold to
build on, not working code yet.
"""

from __future__ import annotations

from datetime import timedelta

from gcal_sync.api import GoogleCalendarService

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_entry_oauth2_flow

from .const import (
    CONF_SOURCE_CALENDARS,
    CONF_SYNC_INTERVAL_MINUTES,
    CONF_SYNC_WINDOW_DAYS,
    CONF_TARGET_CALENDAR_ID,
    DEFAULT_SYNC_INTERVAL_MINUTES,
    DEFAULT_SYNC_WINDOW_DAYS,
    DOMAIN,
)
from .coordinator import CalendarMirrorCoordinator

PLATFORMS: list[str] = []  # no entities exposed yet - sync-only integration for now


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Calendar Mirror from a config entry."""
    implementation = (
        await config_entry_oauth2_flow.async_get_config_entry_implementation(
            hass, entry
        )
    )
    session = config_entry_oauth2_flow.OAuth2Session(hass, entry, implementation)

    # TODO: wrap `session` in a gcal_sync AbstractAuth implementation
    # (see application_credentials.py docstring) before this compiles -
    # not yet written.
    auth = None  # placeholder
    google_service = GoogleCalendarService(auth)

    coordinator = CalendarMirrorCoordinator(
        hass,
        google_service=google_service,
        source_calendars=entry.data.get(CONF_SOURCE_CALENDARS, []),
        target_calendar_id=entry.data.get(CONF_TARGET_CALENDAR_ID, ""),
        sync_window_days=entry.data.get(
            CONF_SYNC_WINDOW_DAYS, DEFAULT_SYNC_WINDOW_DAYS
        ),
        update_interval=timedelta(
            minutes=entry.data.get(
                CONF_SYNC_INTERVAL_MINUTES, DEFAULT_SYNC_INTERVAL_MINUTES
            )
        ),
    )

    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    hass.data[DOMAIN].pop(entry.entry_id, None)
    return True
