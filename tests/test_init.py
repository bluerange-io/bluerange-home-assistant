"""Tests for setting the BlueRange integration up and tearing it down."""

from __future__ import annotations

from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.bluerange.const import DOMAIN, SERVICE_SET_ACTUATOR
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .conftest import (
    BASE_URL,
    DEVICE_UUID,
    entity_id_for,
    mock_bluerange_api,
    setup_integration,
)


async def test_setup_and_unload(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_api: AiohttpClientMocker
) -> None:
    """The entry loads, creates entities and unloads again."""
    await setup_integration(hass, config_entry)

    assert config_entry.state is ConfigEntryState.LOADED
    assert hass.services.has_service(DOMAIN, SERVICE_SET_ACTUATOR)

    assert await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.NOT_LOADED


async def test_device_registry_entry(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_api: AiohttpClientMocker
) -> None:
    """The BlueRange device metadata reaches the device registry."""
    await setup_integration(hass, config_entry)

    device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, DEVICE_UUID)})

    assert device is not None
    assert device.name == "Thermostat Office"
    assert device.manufacturer == "BlueRange"
    assert device.model == "BlueRange Thermostat"
    # The module a device is built around is hardware, not the model.
    assert device.hw_version == "nRF52"
    assert device.sw_version == "26.1.30"
    assert device.serial_number == "FM-0001"
    assert device.configuration_url == BASE_URL
    # Lets Home Assistant tie this device to the same one seen over Bluetooth.
    assert (dr.CONNECTION_BLUETOOTH, "aa:bb:cc:dd:ee:ff") in device.connections


async def test_devices_hang_off_the_organisation(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_api: AiohttpClientMocker
) -> None:
    """The organisation is what every device is reached through."""
    await setup_integration(hass, config_entry)
    registry = dr.async_get(hass)

    hub = registry.async_get_device(identifiers={(DOMAIN, config_entry.entry_id)})
    assert hub is not None
    assert hub.entry_type is dr.DeviceEntryType.SERVICE
    assert hub.name == config_entry.title
    assert hub.manufacturer == "BlueRange"

    device = registry.async_get_device(identifiers={(DOMAIN, DEVICE_UUID)})
    assert device is not None
    assert device.via_device_id == hub.id


async def test_setup_retries_when_the_server_is_down(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """An unreachable server is a temporary problem."""
    aioclient_mock.post(
        f"{BASE_URL}/api/v2/iot/devices/baseInfo/query", exc=TimeoutError
    )
    config_entry.add_to_hass(hass)

    assert not await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_rejected_token_starts_a_reauth_flow(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """A revoked token asks the user for a new one instead of retrying."""
    aioclient_mock.post(f"{BASE_URL}/api/v2/iot/devices/baseInfo/query", status=401)
    config_entry.add_to_hass(hass)

    assert not await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [flow["context"]["source"] for flow in flows] == ["reauth"]


async def test_withdrawn_devices_are_ignored(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """Devices on their way out of the inventory get no entities."""
    mock_bluerange_api(
        aioclient_mock,
        devices={
            "status": "0",
            "results": [
                {"uuid": "gone-1", "name": "Old node", "status": "WITHDRAWN"},
            ],
        },
    )
    await setup_integration(hass, config_entry)

    assert config_entry.state is ConfigEntryState.LOADED
    assert dr.async_get(hass).async_get_device(identifiers={(DOMAIN, "gone-1")}) is None


async def test_a_failing_device_does_not_break_the_others(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """Sensor metadata is requested per device, so failures stay local."""
    mock_bluerange_api(aioclient_mock, sensors={"status": "0", "results": []})
    await setup_integration(hass, config_entry)

    # No sensors at all, but the actuator based entities are still there.
    assert config_entry.state is ConfigEntryState.LOADED
    switch_id = entity_id_for(hass, "switch", "switch_SET_CHILD_PROTECTION[0]")
    assert hass.states.get(switch_id) is not None
