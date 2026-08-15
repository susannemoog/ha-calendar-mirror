"""Config flow for Calendar Mirror.

Flow shape:
  1. async_step_user - choose the target backend: Google Calendar (OAuth2)
     or a generic CalDAV server. Everything after this branches on that
     choice.
  2a. Google: the standard OAuth2 step (handled by
      AbstractOAuth2FlowHandler, entered via async_step_pick_implementation)
      - user authorizes access to their Google account, same UX as the
      core Google Calendar integration.
  2b. CalDAV: async_step_caldav_credentials - URL/username/password/
      verify_ssl, tested against the real server before continuing.
  3. async_step_source_calendars - multi-select of existing calendar.*
     entities from hass.states, to use as sources. Shared by both backends.
  4a. Google: async_step_target_calendar - the target Google Calendar to
      sync into, then the entry is created. Fetched from the Google
      account that just authorized (via gcal_sync's async_list_calendars(),
      using a short-lived AccessTokenAuthImpl since no config entry/
      OAuth2Session exists yet at this point) and offered as a dropdown of
      calendars the user can actually write to, rather than asking them to
      go find and paste a calendar ID by hand. Falls back to a plain text
      field if the fetch fails for any reason, so a flaky API call can't
      block finishing setup entirely.
  4b. CalDAV: async_step_caldav_target_calendar - same idea, but the
      calendars were already fetched (and their connection validated) in
      step 2b, since caldav's `Calendar.search`/listing needs one
      authenticated client either way.

Overriding `async_oauth_create_entry` to add steps instead of creating
the entry immediately is an explicitly supported extension point - see
its docstring in HA core's `config_entry_oauth2_flow.AbstractOAuth2FlowHandler`
("Ok to override if you want to fetch extra info or even add another
step."), confirmed against source 2026-08-13.

CalDAV connection-error handling (AuthorizationError vs DAVError vs
niquests' ConnectionError/Timeout) mirrors HA core's own caldav
integration's `config_flow.py::_test_connection` (verified against
home-assistant/core source, checked 2026-08-14), adapted to the async
client instead of running the sync client in an executor.
"""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

from caldav.async_davclient import AsyncDAVClient
from caldav.collection import Calendar as CalDavCalendar
from caldav.lib.error import AuthorizationError, DAVError
from gcal_sync.api import GoogleCalendarService
from gcal_sync.exceptions import ApiException
from gcal_sync.model import AccessRole, Calendar as GoogleCalendar
from homeassistant.config_entries import (
    SOURCE_REAUTH,
    ConfigEntry,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.core import callback
from homeassistant.helpers import config_entry_oauth2_flow, selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import niquests
import voluptuous as vol

from . import AccessTokenAuthImpl, ApiAuthImpl
from .const import (
    CONF_CALDAV_PASSWORD,
    CONF_CALDAV_URL,
    CONF_CALDAV_USERNAME,
    CONF_CALDAV_VERIFY_SSL,
    CONF_SOURCE_CALENDARS,
    CONF_TARGET_CALENDAR_ID,
    CONF_TARGET_TYPE,
    DOMAIN,
    TARGET_TYPE_CALDAV,
    TARGET_TYPE_GOOGLE,
)

_LOGGER = logging.getLogger(__name__)

# Only offer calendars the user can actually write events to.
_WRITABLE_ROLES = (AccessRole.OWNER, AccessRole.WRITER)


async def _async_list_writable_google_calendars(
    service: GoogleCalendarService,
) -> list[GoogleCalendar]:
    """Fetch the account's calendars, filtered to ones the user can write to."""
    try:
        response = await service.async_list_calendars()
    except ApiException as err:
        _LOGGER.warning(
            "Could not fetch calendar list, falling back to manual entry: %s", err
        )
        return []
    return [cal for cal in response.items if cal.access_role in _WRITABLE_ROLES]


def _calendar_selector_type(calendars: dict[str, str]) -> Any:
    """Build the target-calendar field type: a dropdown, or free text as a fallback."""
    if calendars:
        return vol.In(calendars)
    return str


def _caldav_display_name(calendar: CalDavCalendar) -> str:
    """Return a CalDAV calendar's cached display name, or its URL as a fallback.

    Reads the `{DAV:}displayname` property cached on the object by
    `Principal.get_calendars()`'s PROPFIND response directly, rather than
    calling the dual-mode `get_display_name()` accessor - that always
    returns a coroutine on an async client (even for an already-cached
    value), which would mean awaiting it just to build a synchronous
    dict comprehension for the picker.
    """
    return calendar.props.get("{DAV:}displayname") or str(calendar.url)


class CalendarMirrorConfigFlow(
    config_entry_oauth2_flow.AbstractOAuth2FlowHandler, domain=DOMAIN
):
    """Config flow for Calendar Mirror, supporting Google Calendar and CalDAV targets."""

    DOMAIN = DOMAIN

    def __init__(self) -> None:
        """Set up instance."""
        super().__init__()
        self._target_type: str | None = None
        self._oauth_data: dict[str, Any] | None = None
        self._caldav_data: dict[str, Any] | None = None
        self._source_calendars: list[str] | None = None
        self._google_target_calendars: list[GoogleCalendar] | None = None
        self._caldav_target_calendars: list[CalDavCalendar] | None = None

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

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose which target backend this entry will sync into."""
        if user_input is not None:
            self._target_type = user_input[CONF_TARGET_TYPE]
            if self._target_type == TARGET_TYPE_CALDAV:
                return await self.async_step_caldav_credentials()
            # AbstractOAuth2FlowHandler.async_step_user normally handles
            # step "user" itself by delegating straight to
            # async_step_pick_implementation - called directly here since
            # this override has taken over "user" for the backend choice.
            return await self.async_step_pick_implementation()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_TARGET_TYPE): vol.In(
                        {
                            TARGET_TYPE_GOOGLE: "Google Calendar",
                            TARGET_TYPE_CALDAV: "CalDAV",
                        }
                    ),
                }
            ),
        )

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
        """Perform reauth, routed to the right flow for this entry's backend."""
        if entry_data.get(CONF_TARGET_TYPE) == TARGET_TYPE_CALDAV:
            return await self.async_step_caldav_reauth_confirm()
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm reauth, then redirect into the Google OAuth flow."""
        if user_input is None:
            return self.async_show_form(step_id="reauth_confirm")
        return await self.async_step_pick_implementation()

    async def async_step_caldav_credentials(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect and validate CalDAV server credentials."""
        errors: dict[str, str] = {}
        if user_input is not None:
            client = AsyncDAVClient(
                url=user_input[CONF_CALDAV_URL],
                username=user_input[CONF_CALDAV_USERNAME],
                password=user_input[CONF_CALDAV_PASSWORD],
                ssl_verify_cert=user_input[CONF_CALDAV_VERIFY_SSL],
            )
            error, calendars = await _async_connect_and_list_calendars(client)
            if error:
                errors["base"] = error
            else:
                self._caldav_data = user_input
                self._caldav_target_calendars = calendars
                return await self.async_step_source_calendars()

        return self.async_show_form(
            step_id="caldav_credentials",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_CALDAV_URL): str,
                    vol.Required(CONF_CALDAV_USERNAME): str,
                    vol.Required(CONF_CALDAV_PASSWORD): selector.TextSelector(
                        selector.TextSelectorConfig(
                            type=selector.TextSelectorType.PASSWORD
                        )
                    ),
                    vol.Optional(
                        CONF_CALDAV_VERIFY_SSL, default=True
                    ): selector.BooleanSelector(),
                }
            ),
            errors=errors,
        )

    async def async_step_caldav_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm reauth for a CalDAV entry, resubmitting just the password."""
        errors: dict[str, str] = {}
        reauth_entry = self._get_reauth_entry()
        if user_input is not None:
            data = {**reauth_entry.data, **user_input}
            client = AsyncDAVClient(
                url=data[CONF_CALDAV_URL],
                username=data[CONF_CALDAV_USERNAME],
                password=data[CONF_CALDAV_PASSWORD],
                ssl_verify_cert=data[CONF_CALDAV_VERIFY_SSL],
            )
            error, _ = await _async_connect_and_list_calendars(client)
            if error:
                errors["base"] = error
            else:
                return self.async_update_reload_and_abort(reauth_entry, data=data)

        return self.async_show_form(
            step_id="caldav_reauth_confirm",
            description_placeholders={
                CONF_CALDAV_USERNAME: reauth_entry.data[CONF_CALDAV_USERNAME],
            },
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_CALDAV_PASSWORD): selector.TextSelector(
                        selector.TextSelectorConfig(
                            type=selector.TextSelectorType.PASSWORD
                        )
                    ),
                }
            ),
            errors=errors,
        )

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
                if self._target_type == TARGET_TYPE_CALDAV:
                    return await self.async_step_caldav_target_calendar()
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
        if self._google_target_calendars is None:
            self._google_target_calendars = await self._async_fetch_writable_calendars()

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
                        CONF_TARGET_TYPE: TARGET_TYPE_GOOGLE,
                        CONF_SOURCE_CALENDARS: self._source_calendars,
                        CONF_TARGET_CALENDAR_ID: target_calendar_id,
                    },
                )

        return self.async_show_form(
            step_id="target_calendar",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_TARGET_CALENDAR_ID): _calendar_selector_type(
                        {
                            cal.id: cal.summary or cal.id
                            for cal in self._google_target_calendars
                        }
                    ),
                }
            ),
            errors=errors,
        )

    async def async_step_caldav_target_calendar(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick the target CalDAV calendar to sync into, then create the entry."""
        errors: dict[str, str] = {}
        assert self._caldav_target_calendars is not None
        calendars_by_url = {
            str(cal.url): _caldav_display_name(cal)
            for cal in self._caldav_target_calendars
        }

        if user_input is not None:
            target_calendar_id = user_input[CONF_TARGET_CALENDAR_ID].strip()
            if not target_calendar_id:
                errors["base"] = "invalid_calendar_id"
            else:
                await self.async_set_unique_id(target_calendar_id)
                self._abort_if_unique_id_configured()

                assert self._caldav_data is not None
                assert self._source_calendars is not None
                return self.async_create_entry(
                    title=f"Mirror to CalDAV ({self._caldav_data[CONF_CALDAV_USERNAME]})",
                    data={
                        CONF_TARGET_TYPE: TARGET_TYPE_CALDAV,
                        CONF_CALDAV_URL: self._caldav_data[CONF_CALDAV_URL],
                        CONF_CALDAV_USERNAME: self._caldav_data[CONF_CALDAV_USERNAME],
                        CONF_CALDAV_PASSWORD: self._caldav_data[CONF_CALDAV_PASSWORD],
                        CONF_CALDAV_VERIFY_SSL: self._caldav_data[
                            CONF_CALDAV_VERIFY_SSL
                        ],
                        CONF_SOURCE_CALENDARS: self._source_calendars,
                        CONF_TARGET_CALENDAR_ID: target_calendar_id,
                    },
                )

        return self.async_show_form(
            step_id="caldav_target_calendar",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_TARGET_CALENDAR_ID): _calendar_selector_type(
                        calendars_by_url
                    ),
                }
            ),
            errors=errors,
        )

    async def _async_fetch_writable_calendars(self) -> list[GoogleCalendar]:
        """Fetch the authorized account's calendars the user can write to."""
        assert self._oauth_data is not None
        service = GoogleCalendarService(
            AccessTokenAuthImpl(
                async_get_clientsession(self.hass),
                self._oauth_data["token"]["access_token"],
            )
        )
        return await _async_list_writable_google_calendars(service)

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> CalendarMirrorOptionsFlow:
        """Create the options flow, so existing entries can be reconfigured."""
        return CalendarMirrorOptionsFlow()


async def _async_connect_and_list_calendars(
    client: AsyncDAVClient,
) -> tuple[str | None, list[CalDavCalendar] | None]:
    """Validate CalDAV credentials and fetch the account's calendars.

    Returns `(error_key, calendars)` - exactly one of the two is set,
    matching the error-vs-result shape `async_show_form`'s callers expect.
    Error handling mirrors HA core's own caldav integration's
    `_test_connection` (see module docstring for details), adapted to the
    async client's exceptions.
    """
    try:
        calendars = await client.get_calendars()
    except AuthorizationError as err:
        _LOGGER.warning("Authorization error connecting to CalDAV server: %s", err)
        if err.reason == "Unauthorized":
            return "invalid_auth", None
        # AuthorizationError can also be raised if the url is incorrect or
        # on some other unexpected server response.
        return "cannot_connect", None
    except (niquests.exceptions.Timeout, niquests.exceptions.ConnectionError) as err:
        _LOGGER.warning("Connection error connecting to CalDAV server: %s", err)
        return "cannot_connect", None
    except DAVError as err:
        _LOGGER.warning("CalDAV client error: %s", err)
        return "cannot_connect", None
    except Exception:
        _LOGGER.exception("Unexpected error connecting to CalDAV server")
        return "unknown", None
    finally:
        await client.close()
    return None, calendars


class CalendarMirrorOptionsFlow(OptionsFlowWithReload):
    """Options flow to change source/target calendars without redoing setup.

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

        calendars_by_id = await self._async_fetch_target_calendars()

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
                    ): _calendar_selector_type(calendars_by_id),
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

    async def _async_fetch_target_calendars(self) -> dict[str, str]:
        """Fetch the existing entry's writable calendars as an id -> label map."""
        if self.config_entry.data.get(CONF_TARGET_TYPE) == TARGET_TYPE_CALDAV:
            client = AsyncDAVClient(
                url=self.config_entry.data[CONF_CALDAV_URL],
                username=self.config_entry.data[CONF_CALDAV_USERNAME],
                password=self.config_entry.data[CONF_CALDAV_PASSWORD],
                ssl_verify_cert=self.config_entry.data.get(
                    CONF_CALDAV_VERIFY_SSL, True
                ),
            )
            _error, calendars = await _async_connect_and_list_calendars(client)
            if not calendars:
                return {}
            return {str(cal.url): _caldav_display_name(cal) for cal in calendars}

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
        calendars = await _async_list_writable_google_calendars(service)
        return {cal.id: cal.summary or cal.id for cal in calendars}
