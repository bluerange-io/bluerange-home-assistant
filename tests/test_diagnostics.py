"""Tests for the BlueRange diagnostics."""

from __future__ import annotations

from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.bluerange.diagnostics import async_get_config_entry_diagnostics
from homeassistant.const import CONF_ACCESS_TOKEN, CONF_URL
from homeassistant.core import HomeAssistant

from .conftest import BASE_URL, DEVICE_UUID, TOKEN, setup_integration


async def test_diagnostics(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_api: AiohttpClientMocker,
) -> None:
    """Diagnostics describe every device and how it was classified."""
    await setup_integration(hass, config_entry)

    result = await async_get_config_entry_diagnostics(hass, config_entry)

    assert result["entry"]["data"][CONF_URL] == BASE_URL
    assert result["entry"]["data"][CONF_ACCESS_TOKEN] == "**REDACTED**"
    assert TOKEN not in str(result)

    devices = result["devices"]
    assert len(devices) == 1
    device = devices[0]
    assert device["device"]["uuid"] == DEVICE_UUID
    assert "SET_SETPOINT_TEMPERATURE[0]" in device["actuators"]
    assert device["readings"]["BATTERY[0]"] == 87
    assert device["entities"]["climate"] == ["SET_SETPOINT_TEMPERATURE[0]"]
    assert device["entities"]["switch"] == ["SET_CHILD_PROTECTION[0]"]
    assert device["entities"]["sensor"] == [
        "ACTUAL_TEMPERATURE[0]",
        "BATTERY[0]",
        "VALVE_POSITION[0]",
    ]
