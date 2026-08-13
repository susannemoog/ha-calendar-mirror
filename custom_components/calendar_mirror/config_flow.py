"""Config flow for Calendar Mirror.

Flow shape (planned):
  1. Standard OAuth2 step (handled by AbstractOAuth2FlowHandler) - user
     authorizes access to their Google account, same UX as the core
     Google Calendar integration.
  2. A follow-up step where the user picks which existing HA calendar
     entities to use as sources, and enters/selects the target Google
     Calendar ID.

STATUS: skeleton only. Step 2 (source/target selection) is not yet
implemented - this is the next thing to build, see notebook section 4.
"""

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlowResult
from homeassistant.helpers import config_entry_oauth2_flow

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


class CalendarMirrorConfigFlow(
    config_entry_oauth2_flow.AbstractOAuth2FlowHandler, domain=DOMAIN
):
    """Config flow for Calendar Mirror, built on HA's OAuth2 helper."""

    DOMAIN = DOMAIN

    @property
    def logger(self) -> logging.Logger:
        return _LOGGER

    @property
    def extra_authorize_data(self) -> dict[str, Any]:
        # access_type=offline + prompt=consent so we reliably get a
        # refresh token back, not just a short-lived access token.
        return {
            "access_type": "offline",
            "prompt": "consent",
            "scope": "https://www.googleapis.com/auth/calendar",
        }

    async def async_oauth_create_entry(self, data: dict[str, Any]) -> ConfigFlowResult:
        """Called after successful OAuth2 authorization.

        TODO: this currently creates the entry immediately after OAuth
        completes. Per the planned flow shape above, this should instead
        transition into a source-calendar-picker + target-calendar-id
        step before creating the entry. Not yet implemented.
        """
        return self.async_create_entry(title="Calendar Mirror", data=data)

    # TODO: async_step_source_calendars - present a multi-select of
    # existing calendar.* entities from hass.states, store the chosen
    # entity_ids.
    #
    # TODO: async_step_target_calendar - text input (or a fetched list
    # via the Google API) for which target Google Calendar to sync into.
