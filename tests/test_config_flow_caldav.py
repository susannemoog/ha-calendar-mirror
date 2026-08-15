"""Tests for the CalDAV branch of the Calendar Mirror config flow.

Mirrors test_config_flow.py's approach for the Google branch: isolated
step-method tests driven directly against a flow instance, with
`AsyncDAVClient` mocked out so no real network call happens. Error
handling is checked against every branch of `_async_connect_and_list_calendars`
since that's what stands between a typo'd URL/password and a broken entry.
"""

from unittest.mock import AsyncMock, MagicMock, patch

from caldav.lib.error import AuthorizationError, DAVError
from homeassistant.config_entries import SOURCE_REAUTH
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import AbortFlow, FlowResultType
import niquests
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.calendar_mirror.config_flow import CalendarMirrorConfigFlow
from custom_components.calendar_mirror.const import (
    CONF_CALDAV_PASSWORD,
    CONF_CALDAV_URL,
    CONF_CALDAV_USERNAME,
    CONF_CALDAV_VERIFY_SSL,
    CONF_SOURCE_CALENDARS,
    CONF_TARGET_CALENDAR_ID,
    CONF_TARGET_TYPE,
    DOMAIN,
    TARGET_TYPE_CALDAV,
)

CALDAV_CREDENTIALS = {
    CONF_CALDAV_URL: "https://dav.example.com/",
    CONF_CALDAV_USERNAME: "user",
    CONF_CALDAV_PASSWORD: "secret",
    CONF_CALDAV_VERIFY_SSL: True,
}


class _FakeCalDavCalendar:
    """Minimal stand-in for a caldav `Calendar` as seen by the picker."""

    def __init__(self, url: str, display_name: str | None) -> None:
        self.url = url
        self.props = {"{DAV:}displayname": display_name} if display_name else {}


def _new_flow(hass: HomeAssistant) -> CalendarMirrorConfigFlow:
    flow = CalendarMirrorConfigFlow()
    flow.hass = hass
    flow.context = {}
    flow.handler = DOMAIN
    return flow


def _patch_client(**client_kwargs):
    """Patch the AsyncDAVClient class used by config_flow.py."""
    return patch(
        "custom_components.calendar_mirror.config_flow.AsyncDAVClient",
        **client_kwargs,
    )


class TestAsyncStepUser:
    """Tests for the initial target-backend choice."""

    async def test_shows_target_type_form_initially(self, hass: HomeAssistant) -> None:
        """No input should just show the backend-choice form."""
        flow = _new_flow(hass)

        result = await flow.async_step_user()

        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "user"

    async def test_caldav_choice_routes_to_credentials_step(
        self, hass: HomeAssistant
    ) -> None:
        """Choosing CalDAV should route into the CalDAV credentials step."""
        flow = _new_flow(hass)
        flow.async_step_caldav_credentials = AsyncMock(
            return_value={"type": FlowResultType.FORM}
        )

        await flow.async_step_user({CONF_TARGET_TYPE: TARGET_TYPE_CALDAV})

        flow.async_step_caldav_credentials.assert_awaited_once()

    async def test_google_choice_routes_to_pick_implementation(
        self, hass: HomeAssistant
    ) -> None:
        """Choosing Google should route into the inherited OAuth2 picker step."""
        flow = _new_flow(hass)
        flow.async_step_pick_implementation = AsyncMock(
            return_value={"type": FlowResultType.FORM}
        )

        await flow.async_step_user({CONF_TARGET_TYPE: "google"})

        flow.async_step_pick_implementation.assert_awaited_once()


class TestCaldavCredentialsStep:
    """Tests for validating and storing CalDAV credentials."""

    async def test_successful_connection_transitions_to_source_calendars(
        self, hass: HomeAssistant
    ) -> None:
        """A working connection should store credentials and move on."""
        flow = _new_flow(hass)

        with _patch_client() as mock_client_cls:
            mock_client_cls.return_value.get_calendars = AsyncMock(
                return_value=[
                    _FakeCalDavCalendar("https://dav.example.com/cal/", "Mine")
                ]
            )
            mock_client_cls.return_value.close = AsyncMock()

            result = await flow.async_step_caldav_credentials(CALDAV_CREDENTIALS)

        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "source_calendars"
        assert flow._caldav_data == CALDAV_CREDENTIALS  # noqa: SLF001
        assert len(flow._caldav_target_calendars) == 1  # noqa: SLF001
        mock_client_cls.return_value.close.assert_awaited_once()

    async def test_unauthorized_shows_invalid_auth(self, hass: HomeAssistant) -> None:
        """A 401 Unauthorized should surface as invalid_auth, not a generic error."""
        flow = _new_flow(hass)

        with _patch_client() as mock_client_cls:
            mock_client_cls.return_value.get_calendars = AsyncMock(
                side_effect=AuthorizationError(reason="Unauthorized")
            )
            mock_client_cls.return_value.close = AsyncMock()

            result = await flow.async_step_caldav_credentials(CALDAV_CREDENTIALS)

        assert result["errors"] == {"base": "invalid_auth"}

    async def test_other_authorization_error_shows_cannot_connect(
        self, hass: HomeAssistant
    ) -> None:
        """A non-Unauthorized AuthorizationError (e.g. bad URL) should be cannot_connect."""
        flow = _new_flow(hass)

        with _patch_client() as mock_client_cls:
            mock_client_cls.return_value.get_calendars = AsyncMock(
                side_effect=AuthorizationError(reason="Forbidden")
            )
            mock_client_cls.return_value.close = AsyncMock()

            result = await flow.async_step_caldav_credentials(CALDAV_CREDENTIALS)

        assert result["errors"] == {"base": "cannot_connect"}

    @pytest.mark.parametrize(
        "exc",
        [
            niquests.exceptions.ConnectionError("boom"),
            niquests.exceptions.Timeout("boom"),
            DAVError(reason="server error"),
        ],
    )
    async def test_network_errors_show_cannot_connect(
        self, hass: HomeAssistant, exc: Exception
    ) -> None:
        """Connection/timeout/generic DAV errors should all be cannot_connect."""
        flow = _new_flow(hass)

        with _patch_client() as mock_client_cls:
            mock_client_cls.return_value.get_calendars = AsyncMock(side_effect=exc)
            mock_client_cls.return_value.close = AsyncMock()

            result = await flow.async_step_caldav_credentials(CALDAV_CREDENTIALS)

        assert result["errors"] == {"base": "cannot_connect"}

    async def test_unexpected_error_shows_unknown(self, hass: HomeAssistant) -> None:
        """A genuinely unexpected exception shouldn't be silently swallowed."""
        flow = _new_flow(hass)

        with _patch_client() as mock_client_cls:
            mock_client_cls.return_value.get_calendars = AsyncMock(
                side_effect=ValueError("unexpected")
            )
            mock_client_cls.return_value.close = AsyncMock()

            result = await flow.async_step_caldav_credentials(CALDAV_CREDENTIALS)

        assert result["errors"] == {"base": "unknown"}

    async def test_client_is_closed_even_on_error(self, hass: HomeAssistant) -> None:
        """The dedicated validation client must not leak its HTTP session on failure."""
        flow = _new_flow(hass)

        with _patch_client() as mock_client_cls:
            mock_client_cls.return_value.get_calendars = AsyncMock(
                side_effect=AuthorizationError(reason="Unauthorized")
            )
            mock_client_cls.return_value.close = AsyncMock()

            await flow.async_step_caldav_credentials(CALDAV_CREDENTIALS)

        mock_client_cls.return_value.close.assert_awaited_once()


class TestCaldavTargetCalendarStep:
    """Tests for picking the target CalDAV calendar and creating the entry."""

    def _flow_ready_for_target_step(
        self, hass: HomeAssistant
    ) -> CalendarMirrorConfigFlow:
        flow = _new_flow(hass)
        flow._target_type = TARGET_TYPE_CALDAV  # noqa: SLF001
        flow._caldav_data = CALDAV_CREDENTIALS  # noqa: SLF001
        flow._source_calendars = ["calendar.waste"]  # noqa: SLF001
        flow._caldav_target_calendars = [  # noqa: SLF001
            _FakeCalDavCalendar("https://dav.example.com/cal/mirror/", "Mirror")
        ]
        return flow

    async def test_creates_entry_with_merged_data(self, hass: HomeAssistant) -> None:
        """A valid selection should create the entry with all CalDAV data merged."""
        flow = self._flow_ready_for_target_step(hass)

        result = await flow.async_step_caldav_target_calendar(
            {CONF_TARGET_CALENDAR_ID: "https://dav.example.com/cal/mirror/"}
        )

        assert result["type"] == FlowResultType.CREATE_ENTRY
        assert result["data"][CONF_TARGET_TYPE] == TARGET_TYPE_CALDAV
        assert result["data"][CONF_CALDAV_URL] == CALDAV_CREDENTIALS[CONF_CALDAV_URL]
        assert result["data"][CONF_SOURCE_CALENDARS] == ["calendar.waste"]
        assert (
            result["data"][CONF_TARGET_CALENDAR_ID]
            == "https://dav.example.com/cal/mirror/"
        )

    async def test_blank_id_shows_error(self, hass: HomeAssistant) -> None:
        """A blank target id should redisplay the form with an error."""
        flow = self._flow_ready_for_target_step(hass)

        result = await flow.async_step_caldav_target_calendar(
            {CONF_TARGET_CALENDAR_ID: "   "}
        )

        assert result["type"] == FlowResultType.FORM
        assert result["errors"] == {"base": "invalid_calendar_id"}

    async def test_aborts_if_target_calendar_already_configured(
        self, hass: HomeAssistant
    ) -> None:
        """A second entry targeting the same CalDAV calendar must not be allowed."""
        MockConfigEntry(
            domain=DOMAIN, unique_id="https://dav.example.com/cal/mirror/"
        ).add_to_hass(hass)
        flow = self._flow_ready_for_target_step(hass)

        with pytest.raises(AbortFlow) as exc_info:
            await flow.async_step_caldav_target_calendar(
                {CONF_TARGET_CALENDAR_ID: "https://dav.example.com/cal/mirror/"}
            )
        assert exc_info.value.reason == "already_configured"


class TestAsyncStepReauth:
    """Tests for routing reauth to the right backend-specific flow."""

    async def test_caldav_entry_routes_to_caldav_reauth(
        self, hass: HomeAssistant
    ) -> None:
        """A CalDAV entry's reauth should go through the password-only step."""
        flow = _new_flow(hass)
        flow.async_step_caldav_reauth_confirm = AsyncMock(
            return_value={"type": FlowResultType.FORM}
        )

        await flow.async_step_reauth({CONF_TARGET_TYPE: TARGET_TYPE_CALDAV})

        flow.async_step_caldav_reauth_confirm.assert_awaited_once()

    async def test_google_entry_routes_to_oauth_reauth(
        self, hass: HomeAssistant
    ) -> None:
        """A Google entry's reauth should go through the OAuth reauth confirm step."""
        flow = _new_flow(hass)
        flow.async_step_reauth_confirm = AsyncMock(
            return_value={"type": FlowResultType.FORM}
        )

        await flow.async_step_reauth({})

        flow.async_step_reauth_confirm.assert_awaited_once()


class TestCaldavReauthConfirm:
    """Tests for the CalDAV password-only reauth step."""

    def _reauth_flow(self, hass: HomeAssistant, entry: MockConfigEntry):
        flow = _new_flow(hass)
        flow.context["source"] = SOURCE_REAUTH
        flow.context["entry_id"] = entry.entry_id
        return flow

    async def test_successful_password_update_reloads_entry(
        self, hass: HomeAssistant
    ) -> None:
        """A working new password should update and reload the entry."""
        entry = MockConfigEntry(domain=DOMAIN, data=CALDAV_CREDENTIALS, unique_id="x")
        entry.add_to_hass(hass)
        flow = self._reauth_flow(hass, entry)

        with (
            _patch_client() as mock_client_cls,
            patch.object(hass.config_entries, "async_schedule_reload", MagicMock()),
        ):
            mock_client_cls.return_value.get_calendars = AsyncMock(return_value=[])
            mock_client_cls.return_value.close = AsyncMock()

            result = await flow.async_step_caldav_reauth_confirm(
                {CONF_CALDAV_PASSWORD: "new-secret"}
            )

        assert result["type"] == FlowResultType.ABORT
        assert result["reason"] == "reauth_successful"
        assert entry.data[CONF_CALDAV_PASSWORD] == "new-secret"

    async def test_failed_password_shows_error(self, hass: HomeAssistant) -> None:
        """A still-wrong password should redisplay the form with an error."""
        entry = MockConfigEntry(domain=DOMAIN, data=CALDAV_CREDENTIALS, unique_id="x")
        entry.add_to_hass(hass)
        flow = self._reauth_flow(hass, entry)

        with _patch_client() as mock_client_cls:
            mock_client_cls.return_value.get_calendars = AsyncMock(
                side_effect=AuthorizationError(reason="Unauthorized")
            )
            mock_client_cls.return_value.close = AsyncMock()

            result = await flow.async_step_caldav_reauth_confirm(
                {CONF_CALDAV_PASSWORD: "still-wrong"}
            )

        assert result["type"] == FlowResultType.FORM
        assert result["errors"] == {"base": "invalid_auth"}
