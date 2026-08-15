"""Tests for the Calendar Mirror config flow's post-OAuth steps.

The OAuth2 authorization dance itself (`async_step_pick_implementation`,
`async_step_auth`, `async_step_creation`) is HA core's own
`AbstractOAuth2FlowHandler` machinery and is already covered by HA's own
test suite - what's ours to test is what we added on top: transitioning
from `async_oauth_create_entry` into a source-calendar picker and a
target-calendar picker, then creating the entry with all three pieces
of data merged. The target-calendar step also fetches the account's real
calendar list (gcal_sync's async_list_calendars()), so tests that reach
it mock that HTTP call via aioclient_mock rather than the actual gcal_sync
service, to exercise the real request/response parsing too.
"""

from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import AbortFlow, FlowResultType
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.calendar_mirror.config_flow import CalendarMirrorConfigFlow
from custom_components.calendar_mirror.const import (
    CONF_SOURCE_CALENDARS,
    CONF_TARGET_CALENDAR_ID,
    DOMAIN,
)

OAUTH_DATA = {
    "auth_implementation": "calendar_mirror",
    "token": {"access_token": "abc123", "refresh_token": "xyz", "expires_in": 3600},
}

CALENDAR_LIST_URL = "https://www.googleapis.com/calendar/v3/users/me/calendarList"
CALENDAR_LIST_RESPONSE = {
    "items": [
        {"id": "primary", "summary": "My Calendar", "accessRole": "owner"},
        {
            "id": "family@group.calendar.google.com",
            "summary": "Family",
            "accessRole": "writer",
        },
        {
            "id": "readonly@group.calendar.google.com",
            "summary": "Read Only",
            "accessRole": "reader",
        },
    ]
}


def _new_flow(hass: HomeAssistant) -> CalendarMirrorConfigFlow:
    flow = CalendarMirrorConfigFlow()
    flow.hass = hass
    # `context`/`handler` are normally set by the real flow manager during
    # async_init(); async_set_unique_id()/_abort_if_unique_id_configured()
    # need both, so tests that reach async_step_target_calendar() with a
    # valid ID must set them up manually here.
    flow.context = {}
    flow.handler = DOMAIN
    return flow


def _mock_calendar_list(aioclient_mock: AiohttpClientMocker) -> None:
    aioclient_mock.get(CALENDAR_LIST_URL, json=CALENDAR_LIST_RESPONSE)


class TestOAuthCreateEntry:
    """Tests for the transition out of the OAuth2 step."""

    async def test_transitions_to_source_calendars_step(
        self, hass: HomeAssistant
    ) -> None:
        """OAuth success should move into the source-calendar picker, not create the entry."""
        flow = _new_flow(hass)

        result = await flow.async_oauth_create_entry(OAUTH_DATA)

        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "source_calendars"
        assert flow._oauth_data == OAUTH_DATA  # noqa: SLF001


class TestSourceCalendarsStep:
    """Tests for picking which HA calendar entities to mirror."""

    async def test_no_selection_shows_error(self, hass: HomeAssistant) -> None:
        """An empty selection should redisplay the form with an error."""
        flow = _new_flow(hass)
        flow._oauth_data = OAUTH_DATA  # noqa: SLF001

        result = await flow.async_step_source_calendars({CONF_SOURCE_CALENDARS: []})

        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "source_calendars"
        assert result["errors"] == {"base": "no_calendars_selected"}

    async def test_valid_selection_transitions_to_target_calendar(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """A valid selection should store it and move to the target-calendar step."""
        _mock_calendar_list(aioclient_mock)
        flow = _new_flow(hass)
        flow._oauth_data = OAUTH_DATA  # noqa: SLF001

        result = await flow.async_step_source_calendars(
            {CONF_SOURCE_CALENDARS: ["calendar.waste", "calendar.bomo"]}
        )

        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "target_calendar"
        assert flow._source_calendars == ["calendar.waste", "calendar.bomo"]  # noqa: SLF001

    async def test_initial_call_shows_form(self, hass: HomeAssistant) -> None:
        """Calling the step with no input should just show the form."""
        flow = _new_flow(hass)

        result = await flow.async_step_source_calendars()

        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "source_calendars"


class TestTargetCalendarStep:
    """Tests for picking the target Google Calendar to sync into."""

    async def test_fetches_and_offers_only_writable_calendars(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """Only owner/writer calendars should be fetched and offered."""
        _mock_calendar_list(aioclient_mock)
        flow = _new_flow(hass)
        flow._oauth_data = OAUTH_DATA  # noqa: SLF001
        flow._source_calendars = ["calendar.waste"]  # noqa: SLF001

        result = await flow.async_step_target_calendar()

        assert result["type"] == FlowResultType.FORM
        # readonly@... must be excluded since its accessRole is "reader",
        # not writable.
        offered_ids = {cal.id for cal in flow._google_target_calendars}  # noqa: SLF001
        assert offered_ids == {"primary", "family@group.calendar.google.com"}

    async def test_fetch_failure_falls_back_to_manual_entry(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """A failed calendar-list fetch shouldn't block finishing setup."""
        aioclient_mock.get(
            CALENDAR_LIST_URL,
            status=500,
            json={"error": {"code": 500, "message": "Internal error"}},
        )
        flow = _new_flow(hass)
        flow._oauth_data = OAUTH_DATA  # noqa: SLF001
        flow._source_calendars = ["calendar.waste"]  # noqa: SLF001

        result = await flow.async_step_target_calendar()

        assert result["type"] == FlowResultType.FORM
        assert flow._google_target_calendars == []  # noqa: SLF001

        # Manual entry still works even without a fetched list.
        result = await flow.async_step_target_calendar(
            {CONF_TARGET_CALENDAR_ID: "some-manual-id"}
        )
        assert result["type"] == FlowResultType.CREATE_ENTRY
        assert result["data"][CONF_TARGET_CALENDAR_ID] == "some-manual-id"

    async def test_blank_id_shows_error(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """A blank/whitespace-only ID should redisplay the form with an error."""
        _mock_calendar_list(aioclient_mock)
        flow = _new_flow(hass)
        flow._oauth_data = OAUTH_DATA  # noqa: SLF001
        flow._source_calendars = ["calendar.waste"]  # noqa: SLF001

        result = await flow.async_step_target_calendar({CONF_TARGET_CALENDAR_ID: "   "})

        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "target_calendar"
        assert result["errors"] == {"base": "invalid_calendar_id"}

    async def test_valid_id_creates_entry_with_merged_data(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """A valid ID should create the entry with OAuth + source + target merged."""
        _mock_calendar_list(aioclient_mock)
        flow = _new_flow(hass)
        flow._oauth_data = OAUTH_DATA  # noqa: SLF001
        flow._source_calendars = ["calendar.waste", "calendar.bomo"]  # noqa: SLF001

        result = await flow.async_step_target_calendar(
            {CONF_TARGET_CALENDAR_ID: "primary"}
        )

        assert result["type"] == FlowResultType.CREATE_ENTRY
        assert result["title"] == "Mirror to Google Calendar"
        assert result["data"][CONF_SOURCE_CALENDARS] == [
            "calendar.waste",
            "calendar.bomo",
        ]
        assert result["data"][CONF_TARGET_CALENDAR_ID] == "primary"
        assert result["data"]["token"] == OAUTH_DATA["token"]

    async def test_aborts_if_target_calendar_already_configured(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """A second entry targeting the same calendar must not be allowed.

        Two entries syncing into the same calendar would delete each
        other's events on every pass (see coordinator's per-entry tag
        scoping) - this is the setup-time guard against that ever
        happening in the first place.
        """
        MockConfigEntry(domain=DOMAIN, unique_id="primary").add_to_hass(hass)
        _mock_calendar_list(aioclient_mock)
        flow = _new_flow(hass)
        flow._oauth_data = OAUTH_DATA  # noqa: SLF001
        flow._source_calendars = ["calendar.waste"]  # noqa: SLF001

        with pytest.raises(AbortFlow) as exc_info:
            await flow.async_step_target_calendar({CONF_TARGET_CALENDAR_ID: "primary"})
        assert exc_info.value.reason == "already_configured"

    async def test_strips_whitespace_from_calendar_id(
        self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
    ) -> None:
        """Leading/trailing whitespace in a manually entered ID should be stripped."""
        _mock_calendar_list(aioclient_mock)
        flow = _new_flow(hass)
        flow._oauth_data = OAUTH_DATA  # noqa: SLF001
        flow._source_calendars = ["calendar.waste"]  # noqa: SLF001

        result = await flow.async_step_target_calendar(
            {CONF_TARGET_CALENDAR_ID: "  primary  "}
        )

        assert result["data"][CONF_TARGET_CALENDAR_ID] == "primary"
