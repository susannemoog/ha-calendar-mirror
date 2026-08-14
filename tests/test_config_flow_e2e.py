"""End-to-end config flow test driven through HA's real flow manager.

Complements test_config_flow.py's isolated step-method tests by proving
the whole pipeline works together through `hass.config_entries.flow`:
pick_implementation -> external auth redirect -> real HTTP callback view
-> token exchange -> our source_calendars step -> our target_calendar
step -> entry creation.

Mirrors HA core's own convention for testing
AbstractOAuth2FlowHandler-based flows (LocalOAuth2Implementation,
`current_request_with_host` fixture, hitting the real
`/auth/external/callback` view, `aioclient_mock` for the token endpoint) -
see tests/helpers/test_config_entry_oauth2_flow.py in home-assistant/core,
checked 2026-08-13.
"""

from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import config_entry_oauth2_flow
import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.calendar_mirror.const import (
    CONF_SOURCE_CALENDARS,
    CONF_TARGET_CALENDAR_ID,
    DOMAIN,
    OAUTH2_AUTHORIZE,
    OAUTH2_TOKEN,
)

CLIENT_ID = "test-client-id"
CLIENT_SECRET = "test-client-secret"

CALENDAR_LIST_URL = "https://www.googleapis.com/calendar/v3/users/me/calendarList"


@pytest.mark.usefixtures("current_request_with_host")
async def test_full_flow_creates_entry_with_calendars(
    hass: HomeAssistant,
    hass_client_no_auth,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """Drive OAuth + both extra steps through the real flow manager."""
    config_entry_oauth2_flow.async_register_implementation(
        hass,
        DOMAIN,
        config_entry_oauth2_flow.LocalOAuth2Implementation(
            hass,
            DOMAIN,
            CLIENT_ID,
            CLIENT_SECRET,
            OAUTH2_AUTHORIZE,
            OAUTH2_TOKEN,
        ),
    )

    # With only one registered implementation and an active request in
    # context (via current_request_with_host), HA's OAuth2 helper skips
    # straight past the pick_implementation form to the external step.
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    assert result["type"] == FlowResultType.EXTERNAL_STEP
    assert result["url"].startswith(OAUTH2_AUTHORIZE)

    state = config_entry_oauth2_flow._encode_jwt(  # noqa: SLF001
        hass,
        {
            "flow_id": result["flow_id"],
            "redirect_uri": "https://example.com/auth/external/callback",
        },
    )

    client = await hass_client_no_auth()
    resp = await client.get(f"/auth/external/callback?code=abcd&state={state}")
    assert resp.status == 200

    aioclient_mock.post(
        OAUTH2_TOKEN,
        json={
            "access_token": "mock-access-token",
            "refresh_token": "mock-refresh-token",
            "type": "Bearer",
            "expires_in": 3600,
        },
    )

    result = await hass.config_entries.flow.async_configure(result["flow_id"])
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "source_calendars"

    aioclient_mock.get(
        CALENDAR_LIST_URL,
        json={
            "items": [{"id": "primary", "summary": "Primary", "accessRole": "owner"}]
        },
    )

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_SOURCE_CALENDARS: [
                "calendar.waste_collection_schedule_bremer_stadtreinigung",
                "calendar.bomo",
            ]
        },
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "target_calendar"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_TARGET_CALENDAR_ID: "primary"}
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == "Mirror to Google Calendar"

    entry = result["result"]
    assert entry.data[CONF_SOURCE_CALENDARS] == [
        "calendar.waste_collection_schedule_bremer_stadtreinigung",
        "calendar.bomo",
    ]
    assert entry.data[CONF_TARGET_CALENDAR_ID] == "primary"
    assert entry.data["token"]["access_token"] == "mock-access-token"
