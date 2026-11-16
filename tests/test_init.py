"""Tests for setting the BlueRange integration up and tearing it down."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.bluerange.const import DOMAIN, SERVICE_SET_ACTUATOR
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er

from .conftest import (
    BASE_URL,
    DEVICE_RESPONSE,
    DEVICE_UUID,
    entity_id_for,
    mock_bluerange_api,
    setup_integration,
)

#: The inventory after the one device of the fixture was deleted in the portal.
NO_DEVICES = {"status": "0", "results": []}


async def _refresh_inventory(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Run an update cycle that reloads the device list as well."""
    with patch(
        "custom_components.bluerange.coordinator.DEVICE_REFRESH_INTERVAL",
        timedelta(0),
    ):
        await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()


async def _report_inventory(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    devices: dict,
) -> None:
    """Let the server report another device list and wait for it to be picked up."""
    aioclient_mock.clear_requests()
    mock_bluerange_api(aioclient_mock, devices=devices)
    await _refresh_inventory(hass, entry)


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


async def test_deleted_device_is_removed(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """A device deleted in the portal leaves Home Assistant with its entities."""
    mock_bluerange_api(aioclient_mock)
    await setup_integration(hass, config_entry)
    switch_id = entity_id_for(hass, "switch", "switch_SET_CHILD_PROTECTION[0]")

    await _report_inventory(hass, config_entry, aioclient_mock, NO_DEVICES)

    registry = dr.async_get(hass)
    assert registry.async_get_device(identifiers={(DOMAIN, DEVICE_UUID)}) is None
    assert er.async_get(hass).async_get(switch_id) is None
    assert hass.states.get(switch_id) is None
    # The organisation is not a device of the inventory and stays.
    assert registry.async_get_device(identifiers={(DOMAIN, config_entry.entry_id)})


async def test_device_returning_gets_its_entities_back(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """A device that is enrolled again after its removal is set up again."""
    mock_bluerange_api(aioclient_mock)
    await setup_integration(hass, config_entry)

    await _report_inventory(hass, config_entry, aioclient_mock, NO_DEVICES)
    await _report_inventory(hass, config_entry, aioclient_mock, DEVICE_RESPONSE)

    assert dr.async_get(hass).async_get_device(identifiers={(DOMAIN, DEVICE_UUID)})
    switch_id = entity_id_for(hass, "switch", "switch_SET_CHILD_PROTECTION[0]")
    assert hass.states.get(switch_id) is not None


async def test_device_deleted_while_stopped_is_removed(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """A device that vanished while Home Assistant was not running is cleaned up."""
    config_entry.add_to_hass(hass)
    dr.async_get(hass).async_get_or_create(
        config_entry_id=config_entry.entry_id, identifiers={(DOMAIN, "gone-1")}
    )
    mock_bluerange_api(aioclient_mock)
    await setup_integration(hass, config_entry)

    assert dr.async_get(hass).async_get_device(identifiers={(DOMAIN, "gone-1")}) is None


async def test_shared_device_is_only_detached(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """A device another integration knows as well is left to that integration."""
    mock_bluerange_api(aioclient_mock)
    await setup_integration(hass, config_entry)
    other = MockConfigEntry(domain="bluetooth")
    other.add_to_hass(hass)
    registry = dr.async_get(hass)
    registry.async_get_or_create(
        config_entry_id=other.entry_id,
        connections={(dr.CONNECTION_BLUETOOTH, "aa:bb:cc:dd:ee:ff")},
    )

    await _report_inventory(hass, config_entry, aioclient_mock, NO_DEVICES)

    assert registry.async_get_device(identifiers={(DOMAIN, DEVICE_UUID)}) is None
    device = registry.async_get_device(
        connections={(dr.CONNECTION_BLUETOOTH, "aa:bb:cc:dd:ee:ff")}
    )
    assert device is not None
    assert device.config_entries == {other.entry_id}


async def test_failed_inventory_removes_nothing(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """Devices are only removed on the word of a complete device list."""
    mock_bluerange_api(aioclient_mock)
    await setup_integration(hass, config_entry)

    aioclient_mock.clear_requests()
    aioclient_mock.post(
        f"{BASE_URL}/api/v2/iot/devices/baseInfo/query", exc=TimeoutError
    )
    await _refresh_inventory(hass, config_entry)

    assert dr.async_get(hass).async_get_device(identifiers={(DOMAIN, DEVICE_UUID)})


async def test_device_returning_after_a_failed_cycle_gets_its_entities_back(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """A cycle that fails after the inventory changed does not lose a device."""
    mock_bluerange_api(aioclient_mock)
    await setup_integration(hass, config_entry)

    # The device is gone, and the sensor values of the others cannot be read.
    aioclient_mock.clear_requests()
    mock_bluerange_api(
        aioclient_mock,
        devices={
            "status": "0",
            "results": [{"uuid": "other-1", "name": "Other", "status": "COMPLIANT"}],
        },
        sensors={"status": "1", "message": "boom"},
    )
    await _refresh_inventory(hass, config_entry)
    await _report_inventory(hass, config_entry, aioclient_mock, DEVICE_RESPONSE)

    switch_id = entity_id_for(hass, "switch", "switch_SET_CHILD_PROTECTION[0]")
    assert hass.states.get(switch_id) is not None
