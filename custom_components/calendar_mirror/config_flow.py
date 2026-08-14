"""Config flow for Calendar Mirror.

Flow shape:
  1. Standard OAuth2 step (handled by AbstractOAuth2FlowHandler) - user
     authorizes access to their Google account, same UX as the core
     Google Calendar integration.
  2. async_step_source_calendars - multi-select of existing calendar.*
     entities from hass.states, to use as sources.
  3. async_step_target_calendar - the target Google Calendar to sync
     into, then the entry is created. Fetched from the Google account
     that just authorized (via gcal_sync's async_list_calendars(),
     using a short-lived AccessTokenAuthImpl since no config entry/
     OAuth2Session exists yet at this point) and offered as a dropdown
     of calendars the user can actually write to, rather than asking
     them to go find and paste a calendar ID by hand. Falls back to a
     plain text field if the fetch fails for any reason, so a flaky API
     call can't block finishing setup entirely.

Overriding `async_oauth_create_entry` to add steps instead of creating
the entry immediately is an explicitly supported extension point - see
its docstring in HA core's `config_entry_oauth2_flow.AbstractOAuth2FlowHandler`
("Ok to override if you want to fetch extra info or even add another
step."), confirmed against source 2026-08-13.
"""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

from gcal_sync.api import GoogleCalendarService
from gcal_sync.exceptions import ApiException
from gcal_sync.model import AccessRole, Calendar
from homeassistant.config_entries import (
    SOURCE_REAUTH,
    ConfigEntry,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.core import callback
from homeassistant.helpers import config_entry_oauth2_flow, selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import voluptuous as vol

from . import AccessTokenAuthImpl, ApiAuthImpl
from .const import CONF_SOURCE_CALENDARS, CONF_TARGET_CALENDAR_ID, DOMAIN

_LOGGER = logging.getLogger(__name__)

# Only offer calendars the user can actually write events to.
_WRITABLE_ROLES = (AccessRole.OWNER, AccessRole.WRITER)


async def _async_list_writable_calendars(
    service: GoogleCalendarService,
) -> list[Calendar]:
    """Fetch the account's calendars, filtered to ones the user can write to."""
    try:
        response = await service.async_list_calendars()
    except ApiException as err:
        _LOGGER.warning(
            "Could not fetch calendar list, falling back to manual entry: %s", err
        )
        return []
    return [cal for cal in response.items if cal.access_role in _WRITABLE_ROLES]


def _target_calendar_selector_type(calendars: list[Calendar]) -> Any:
    """Build the target-calendar field type: a dropdown, or free text as a fallback."""
    if calendars:
        return vol.In({cal.id: cal.summary or cal.id for cal in calendars})
    return str


class CalendarMirrorConfigFlow(
    config_entry_oauth2_flow.AbstractOAuth2FlowHandler, domain=DOMAIN
):
    """Config flow for Calendar Mirror, built on HA's OAuth2 helper."""

    DOMAIN = DOMAIN

    def __init__(self) -> None:
        """Set up instance."""
        super().__init__()
        self._oauth_data: dict[str, Any] | None = None
        self._source_calendars: list[str] | None = None
        self._target_calendars: list[Calendar] | None = None

    @property
    def logger(self) -> logging.Logger:
        """Return the logger for this flow."""
        return _LOGGER

    @property
    def extra_authorize_data(self) -> dict[str, Any]:
        """Return extra data appended to the authorize URL.

        access_type=offline + prompt=consent so we reliably get a refresh
        token back, not just a short-lived access token.

        Scope is deliberately narrower than the blanket `calendar` scope:
        `calendar.events` covers create/read/delete on events (all the
        sync itself needs), and `calendar.calendarlist.readonly` covers
        listing calendars for the target-calendar picker. Neither grants
        calendar management (create/delete/rename whole calendars, change
        sharing/ACLs) the way the full `calendar` scope would. Verified
        against Google's OAuth 2.0 scopes reference, checked 2026-08-14.
        """
        return {
            "access_type": "offline",
            "prompt": "consent",
            "scope": (
                "https://www.googleapis.com/auth/calendar.events "
                "https://www.googleapis.com/auth/calendar.calendarlist.readonly"
            ),
        }

    async def async_oauth_create_entry(self, data: dict[str, Any]) -> ConfigFlowResult:
        """Called after successful OAuth2 authorization.

        On a fresh setup, stash the OAuth token data and move into the
        source-calendar-picker step rather than creating the entry
        immediately - the entry is only created once source calendars and
        a target calendar ID have also been collected. On reauth (Google
        access expired or was revoked), source/target calendars are
        already configured - just refresh the token on the existing entry
        and reload it.
        """
        if self.source == SOURCE_REAUTH:
            return self.async_update_reload_and_abort(
                self._get_reauth_entry(), data=data
            )
        self._oauth_data = data
        return await self.async_step_source_calendars()

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Perform reauth when Google access has expired or been revoked."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm reauth, then redirect into the standard OAuth flow."""
        if user_input is None:
            return self.async_show_form(step_id="reauth_confirm")
        return await self.async_step_user()

    async def async_step_source_calendars(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Let the user pick which existing HA calendar entities to mirror."""
        errors: dict[str, str] = {}
        if user_input is not None:
            selected = user_input[CONF_SOURCE_CALENDARS]
            if not selected:
                errors["base"] = "no_calendars_selected"
            else:
                self._source_calendars = selected
                return await self.async_step_target_calendar()

        return self.async_show_form(
            step_id="source_calendars",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_SOURCE_CALENDARS): selector.EntitySelector(
                        selector.EntitySelectorConfig(domain="calendar", multiple=True)
                    ),
                }
            ),
            errors=errors,
        )

    async def async_step_target_calendar(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick the target Google Calendar to sync into, then create the entry."""
        if self._target_calendars is None:
            self._target_calendars = await self._async_fetch_writable_calendars()

        errors: dict[str, str] = {}
        if user_input is not None:
            target_calendar_id = user_input[CONF_TARGET_CALENDAR_ID].strip()
            if not target_calendar_id:
                errors["base"] = "invalid_calendar_id"
            else:
                # Two entries syncing into the same target calendar would
                # fight each other on every sync pass - each only
                # recognizes its own events, so it'd delete the other's.
                await self.async_set_unique_id(target_calendar_id)
                self._abort_if_unique_id_configured()

                assert self._oauth_data is not None
                assert self._source_calendars is not None
                return self.async_create_entry(
                    title="Mirror to Google Calendar",
                    data={
                        **self._oauth_data,
                        CONF_SOURCE_CALENDARS: self._source_calendars,
                        CONF_TARGET_CALENDAR_ID: target_calendar_id,
                    },
                )

        return self.async_show_form(
            step_id="target_calendar",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_TARGET_CALENDAR_ID
                    ): _target_calendar_selector_type(self._target_calendars),
                }
            ),
            errors=errors,
        )

    async def _async_fetch_writable_calendars(self) -> list[Calendar]:
        """Fetch the authorized account's calendars the user can write to."""
        assert self._oauth_data is not None
        service = GoogleCalendarService(
            AccessTokenAuthImpl(
                async_get_clientsession(self.hass),
                self._oauth_data["token"]["access_token"],
            )
        )
        return await _async_list_writable_calendars(service)

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> CalendarMirrorOptionsFlow:
        """Create the options flow, so existing entries can be reconfigured."""
        return CalendarMirrorOptionsFlow()


class CalendarMirrorOptionsFlow(OptionsFlowWithReload):
    """Options flow to change source/target calendars without redoing OAuth.

    Uses `OptionsFlowWithReload` so saving automatically reloads the entry
    (and thus the coordinator) with the new settings - no manual update
    listener needed. Same pattern as HA core's google integration options
    flow, confirmed against source 2026-08-13.
    """

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Edit source calendars and target calendar for an existing entry."""
        current = {**self.config_entry.data, **self.config_entry.options}
        errors: dict[str, str] = {}

        if user_input is not None:
            selected = user_input[CONF_SOURCE_CALENDARS]
            target_calendar_id = user_input[CONF_TARGET_CALENDAR_ID].strip()
            if not selected:
                errors["base"] = "no_calendars_selected"
            elif not target_calendar_id:
                errors["base"] = "invalid_calendar_id"
            elif self._target_calendar_used_elsewhere(target_calendar_id):
                errors["base"] = "target_already_configured"
            else:
                self.hass.config_entries.async_update_entry(
                    self.config_entry, unique_id=target_calendar_id
                )
                return self.async_create_entry(
                    data={
                        CONF_SOURCE_CALENDARS: selected,
                        CONF_TARGET_CALENDAR_ID: target_calendar_id,
                    }
                )

        target_calendars = await self._async_fetch_writable_calendars()

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_SOURCE_CALENDARS,
                        default=current.get(CONF_SOURCE_CALENDARS, []),
                    ): selector.EntitySelector(
                        selector.EntitySelectorConfig(domain="calendar", multiple=True)
                    ),
                    vol.Required(
                        CONF_TARGET_CALENDAR_ID,
                        default=current.get(CONF_TARGET_CALENDAR_ID, ""),
                    ): _target_calendar_selector_type(target_calendars),
                }
            ),
            errors=errors,
        )

    def _target_calendar_used_elsewhere(self, target_calendar_id: str) -> bool:
        """Return whether another entry already syncs into this calendar.

        Entries are keyed by target_calendar_id as their unique_id (set
        here and at initial setup), so this is a simple lookup rather
        than scanning entry data directly.
        """
        return any(
            other_entry.entry_id != self.config_entry.entry_id
            and other_entry.unique_id == target_calendar_id
            for other_entry in self.hass.config_entries.async_entries(DOMAIN)
        )

    async def _async_fetch_writable_calendars(self) -> list[Calendar]:
        """Fetch the existing entry's account calendars the user can write to."""
        implementation = (
            await config_entry_oauth2_flow.async_get_config_entry_implementation(
                self.hass, self.config_entry
            )
        )
        session = config_entry_oauth2_flow.OAuth2Session(
            self.hass, self.config_entry, implementation
        )
        service = GoogleCalendarService(
            ApiAuthImpl(async_get_clientsession(self.hass), session)
        )
        return await _async_list_writable_calendars(service)
