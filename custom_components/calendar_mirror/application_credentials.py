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


# TODO: verify against current HA dev docs whether
# async_get_description_placeholders is still needed/expected here for
# custom (non-core) integrations - this varies by HA version and wasn't
# confirmed against a live instance.
