"""Application credentials platform for Calendar Mirror.

This reuses HA's standard OAuth2 application_credentials framework, the
same one the core Google Calendar integration uses. Practical effect for
the end user: they set up a Google Cloud OAuth client exactly once, the
same way they would for the official Google Calendar integration, and
the browser-based consent flow is handled entirely through
my.home-assistant.io - no separate script, no manually created
long-lived token, no local browser popup.
"""

from homeassistant.components.application_credentials import AuthorizationServer
from homeassistant.core import HomeAssistant

from .const import OAUTH2_AUTHORIZE, OAUTH2_TOKEN


async def async_get_authorization_server(hass: HomeAssistant) -> AuthorizationServer:
    """Return the OAuth2 authorization server endpoints."""
    return AuthorizationServer(
        authorize_url=OAUTH2_AUTHORIZE,
        token_url=OAUTH2_TOKEN,
    )


async def async_get_description_placeholders(hass: HomeAssistant) -> dict[str, str]:
    """Return description placeholders for the credentials setup dialog.

    Confirmed still in active use against HA core's own Google integration
    (homeassistant/components/google/application_credentials.py, checked
    2026-08-13) - referenced from strings.json's
    application_credentials.description via {oauth_creds_url} etc.
    """
    return {
        "oauth_consent_url": (
            "https://console.cloud.google.com/apis/credentials/consent"
        ),
        "oauth_creds_url": "https://console.cloud.google.com/apis/credentials",
        "more_info_url": "https://github.com/YOUR_GITHUB_USERNAME/ha-calendar-mirror",
    }
