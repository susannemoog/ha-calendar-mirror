"""Fixtures for Calendar Mirror tests."""

from pathlib import Path

import pytest

pytest_plugins = "pytest_homeassistant_custom_component"


@pytest.fixture
def hass_config_dir() -> str:
    """Point hass's config dir at this repo root.

    pytest-homeassistant-custom-component's `hass` fixture defaults
    `hass.config.config_dir` to its own bundled `testing_config/` inside
    site-packages, so HA's integration loader (which resolves custom
    integrations from `<config_dir>/custom_components/<domain>`) never
    sees our actual `custom_components/calendar_mirror` - it silently
    raises `ModuleNotFoundError` instead. Overriding this fixture (an
    explicit extension point provided by the plugin, see its
    `hass_config_dir` fixture in plugins.py) to our repo root fixes that,
    since the repo already has the exact `custom_components/<domain>`
    layout HA expects. Confirmed necessary and sufficient by running the
    smoke test 2026-08-13; not documented clearly in the plugin's own
    README, so recording it here as a gotcha.
    """
    return str(Path(__file__).parent.parent)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Enable custom integrations for every test in this suite.

    Required by pytest-homeassistant-custom-component so that hass will
    load our custom_components/calendar_mirror instead of only looking
    at core integrations.
    """
    return
