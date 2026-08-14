"""Tests for the Calendar Mirror options flow (editing an existing entry).

`OptionsFlow.config_entry` is a read-only property that resolves via
`self.handler` against `hass.config_entries` (see HA core's
`config_entries.OptionsFlow`, checked 2026-08-13) - it's only available
after the entry is registered with `add_to_hass()` and `flow.handler` is
set to the entry_id, mirroring what HA's real options flow manager does
internally when it constructs the flow via `async_get_options_flow`.
"""

from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import config_entry_oauth2_flow
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.calendar_mirror.config_flow import CalendarMirrorOptionsFlow
from custom_components.calendar_mirror.const import (
    CONF_SOURCE_CALENDARS,
    CONF_TARGET_CALENDAR_ID,
    DOMAIN,
)

CALENDAR_LIST_URL = "https://www.googleapis.com/calendar/v3/users/me/calendarList"
CALENDAR_LIST_RESPONSE = {
    "items": [
        {"id": "primary", "summary": "My Calendar", "accessRole": "owner"},
        {
            "id": "family@group.calendar.google.com",
            "summary": "Family",
            "accessRole": "writer",
        },
    ]
}


def _new_options_flow(
    hass: HomeAssistant, entry: MockConfigEntry
) -> CalendarMirrorOptionsFlow:
    flow = CalendarMirrorOptionsFlow()
    flow.hass = hass
    flow.handler = entry.entry_id
    return flow


async def _setup_entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "auth_implementation": DOMAIN,
            "token": {
                "access_token": "abc",
                "refresh_token": "def",
                "expires_at": 9999999999,
            },
            CONF_SOURCE_CALENDARS: ["calendar.old"],
            CONF_TARGET_CALENDAR_ID: "old-target",
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


class TestOptionsFlowInit:
    """Tests for editing an existing entry's source/target calendars."""

    async def test_shows_form_prefilled_with_current_values(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """Calling the step with no input should show the form."""
        aioclient_mock.get(CALENDAR_LIST_URL, json=CALENDAR_LIST_RESPONSE)
        entry = await _setup_entry(hass)
        flow = _new_options_flow(hass, entry)

        result = await flow.async_step_init()

        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "init"

    async def test_saving_new_selection_updates_options_and_reloads(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """A valid new selection should save into entry.options."""
        aioclient_mock.get(CALENDAR_LIST_URL, json=CALENDAR_LIST_RESPONSE)
        entry = await _setup_entry(hass)
        flow = _new_options_flow(hass, entry)

        result = await flow.async_step_init(
            {
                CONF_SOURCE_CALENDARS: ["calendar.new_one", "calendar.new_two"],
                CONF_TARGET_CALENDAR_ID: "primary",
            }
        )

        assert result["type"] == FlowResultType.CREATE_ENTRY
        assert result["data"][CONF_SOURCE_CALENDARS] == [
            "calendar.new_one",
            "calendar.new_two",
        ]
        assert result["data"][CONF_TARGET_CALENDAR_ID] == "primary"

    async def test_no_calendars_selected_shows_error(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """An empty source-calendar selection should redisplay the form with an error."""
        aioclient_mock.get(CALENDAR_LIST_URL, json=CALENDAR_LIST_RESPONSE)
        entry = await _setup_entry(hass)
        flow = _new_options_flow(hass, entry)

        result = await flow.async_step_init(
            {CONF_SOURCE_CALENDARS: [], CONF_TARGET_CALENDAR_ID: "primary"}
        )

        assert result["type"] == FlowResultType.FORM
        assert result["errors"] == {"base": "no_calendars_selected"}

    async def test_blank_target_shows_error(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """A blank target calendar ID should redisplay the form with an error."""
        aioclient_mock.get(CALENDAR_LIST_URL, json=CALENDAR_LIST_RESPONSE)
        entry = await _setup_entry(hass)
        flow = _new_options_flow(hass, entry)

        result = await flow.async_step_init(
            {CONF_SOURCE_CALENDARS: ["calendar.new_one"], CONF_TARGET_CALENDAR_ID: "  "}
        )

        assert result["type"] == FlowResultType.FORM
        assert result["errors"] == {"base": "invalid_calendar_id"}
