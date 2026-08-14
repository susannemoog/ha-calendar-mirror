"""Smoke test to confirm HA's integration loader can find and load the domain."""

from homeassistant.core import HomeAssistant
from homeassistant.loader import async_get_integration


async def test_integration_is_discoverable(hass: HomeAssistant) -> None:
    """HA's loader should resolve calendar_mirror from custom_components/."""
    integration = await async_get_integration(hass, "calendar_mirror")
    assert integration.domain == "calendar_mirror"
