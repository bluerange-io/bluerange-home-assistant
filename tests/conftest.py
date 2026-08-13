"""Fixtures for the BlueRange tests."""

from __future__ import annotations

from collections.abc import Generator
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.bluerange.const import CONF_ORGANIZATION, DOMAIN
from homeassistant.const import CONF_ACCESS_TOKEN, CONF_URL, CONF_VERIFY_SSL
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

#: Marker asking the mocked server to deny MQTT access.
NO_MQTT = object()

BASE_URL = "https://portal.example.com"
TOKEN = "token-abc"
DEVICE_UUID = "device-1"
ORGANIZATION_UUID = "org-1"

ORGANIZATION_RESPONSE = {
    "status": "0",
    "results": [
        {
            "uuid": "org-1",
            "name": "Acme",
            "uniqueName": "acme",
            "duplicateName": False,
        }
    ],
}

USER_RESPONSE = {
    "uuid": "user-1",
    "name": "tester",
    "organizationUuid": "org-1",
    "organizationName": "Acme",
}

DEVICE_RESPONSE = {
    "status": "0",
    "results": [
        {
            "uuid": DEVICE_UUID,
            "deviceId": "FM-0001",
            "name": "Thermostat Office",
            "status": "COMPLIANT",
            "platform": "MESH_NODE",
            "manufacturer": {"uuid": "m-1", "name": "BlueRange"},
            "model": "nRF52",
            "deviceRepositoryEntry": {"uuid": "c-1", "name": "BlueRange Thermostat"},
            "fwVersion": 260010030,
            "gwVersion": "1.2.3",
            "nodeId": 42,
            "lastConnectionDate": 1767225600000,
            "details": {
                "platform": "MESH_NODE",
                "nodeId": 42,
                "macAddress": "AA:BB:CC:DD:EE:FF",
                "chipId": "0x1234",
                "batteryInfo": 53,
                "dBmTX": 4,
                "dBmRX": -97,
                "calibratedRssi": -52,
            },
            "room": {"uuid": "r-1", "name": "Office"},
            "floor": {"uuid": "f-1", "name": "Ground floor"},
        }
    ],
}

#: A trimmed down thermostat fixture, which covers every platform of the
#: integration except the light.
SENSOR_RESPONSE = {
    "status": "0",
    "results": [
        {
            "type": "ACTUAL_TEMPERATURE",
            "index": 0,
            "module": ["core"],
            "unit": "CELSIUS",
            "lastSensorData": {
                "deviceUuid": DEVICE_UUID,
                "type": "ACTUAL_TEMPERATURE",
                "index": 0,
                "value": 20.5,
                "timestamp": 1767225600000,
            },
        },
        {
            "type": "SETPOINT_TEMPERATURE",
            "index": 0,
            "module": ["core"],
            "unit": "CELSIUS",
            "lastSensorData": {
                "type": "SETPOINT_TEMPERATURE",
                "index": 0,
                "value": 21.0,
            },
        },
        {
            "type": "BATTERY",
            "index": 0,
            "module": ["core"],
            "unit": "PERCENT",
            "lastSensorData": {"type": "BATTERY", "index": 0, "value": 87},
        },
        {
            "type": "VALVE_POSITION",
            "index": 0,
            "module": ["core"],
            "unit": "PERCENT",
            "lastSensorData": {"type": "VALVE_POSITION", "index": 0, "value": 35},
        },
        {
            "type": "CHILD_PROTECTION",
            "index": 0,
            "module": ["core"],
            "unit": "ON_OFF",
            "lastSensorData": {"type": "CHILD_PROTECTION", "index": 0, "value": 0},
        },
        {
            "type": "HEATING_OFF",
            "index": 0,
            "module": ["core"],
            "unit": "ON_OFF",
            "lastSensorData": {"type": "HEATING_OFF", "index": 0, "value": 0},
        },
        {
            "type": "ENERGY_SAVING_MODE",
            "index": 0,
            "module": ["core"],
            "unit": "ON_OFF",
            "lastSensorData": {"type": "ENERGY_SAVING_MODE", "index": 0, "value": 0},
        },
        {
            "type": "MAX_TEMPERATURE",
            "index": 0,
            "module": ["core"],
            "unit": "CELSIUS",
            "lastSensorData": {"type": "MAX_TEMPERATURE", "index": 0, "value": 24.0},
        },
    ],
}

ACTUATOR_RESPONSE = {
    "status": "0",
    "results": [
        {
            "type": "SET_SETPOINT_TEMPERATURE",
            "index": 0,
            "module": ["core"],
            "unit": "CELSIUS",
            "min": 8,
            "max": 28,
        },
        {
            "type": "SET_HEATING_MODE",
            "index": 0,
            "module": ["core"],
            "unit": "ON_OFF",
            "min": 2,
            "max": 2,
        },
        {
            "type": "SET_HEATING_OFF",
            "index": 0,
            "module": ["core"],
            "unit": "ON_OFF",
            "min": 8,
            "max": 8,
        },
        {
            "type": "SET_ENERGY_SAVING_MODE",
            "index": 0,
            "module": ["core"],
            "unit": "ON_OFF",
            "min": 4,
            "max": 4,
        },
        {
            "type": "SET_CHILD_PROTECTION",
            "index": 0,
            "module": ["core"],
            "unit": "ON_OFF",
            "min": 0,
            "max": 1,
        },
        {
            "type": "SET_MAX_TEMPERATURE",
            "index": 0,
            "module": ["core"],
            "unit": "CELSIUS",
            "min": 8,
            "max": 28,
        },
        {
            "type": "TRIGGER_ADAPTATION",
            "index": 0,
            "module": ["core"],
            "unit": "ON_OFF",
            "min": 1,
            "max": 1,
        },
    ],
}


def entity_id_for(hass: HomeAssistant, platform: str, unique_key: str) -> str:
    """Return the entity id of one BlueRange control.

    Home Assistant derives entity ids from names, which depend on the area a
    device sits in, so entities are looked up by their unique id instead.
    """
    entity_id = er.async_get(hass).async_get_entity_id(
        platform, DOMAIN, f"{DEVICE_UUID}_{unique_key}"
    )
    assert entity_id is not None, f"no {platform} entity for {unique_key}"
    return entity_id


async def setup_integration(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Add the entry to Home Assistant and set the integration up."""
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


MQTT_RESPONSE = {
    "serverURIs": ["ssl://mqtt.example.com:8883"],
    "clientId": "Token-user-1-homeassistant",
    "variables": {"organizationUuid": "org-1"},
}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Make the custom integration loadable in every test."""
    return


@pytest.fixture(autouse=True)
def mock_paho() -> Generator[MagicMock]:
    """Keep the MQTT client from opening a socket in any test.

    The double connects but never reports a connection, which is the state the
    integration has to keep working in: polling carries on until the broker
    confirms the subscription.
    """
    with patch("custom_components.bluerange.mqtt.paho.Client") as client_class:
        yield client_class


@pytest.fixture
def config_entry() -> MockConfigEntry:
    """Return a config entry for the mocked server."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Acme (portal.example.com)",
        unique_id="portal.example.com:org-1",
        data={
            CONF_URL: BASE_URL,
            CONF_ACCESS_TOKEN: TOKEN,
            CONF_VERIFY_SSL: True,
            CONF_ORGANIZATION: ORGANIZATION_UUID,
        },
    )


@pytest.fixture
def mock_api(aioclient_mock: AiohttpClientMocker) -> AiohttpClientMocker:
    """Answer every endpoint the integration uses."""
    mock_bluerange_api(aioclient_mock)
    return aioclient_mock


def mock_bluerange_api(
    aioclient_mock: AiohttpClientMocker,
    *,
    user: dict[str, Any] | None = None,
    organizations: dict[str, Any] | None = None,
    devices: dict[str, Any] | None = None,
    sensors: dict[str, Any] | None = None,
    actuators: dict[str, Any] | None = None,
    mqtt: Any = None,
) -> None:
    """Register the mocked BlueRange endpoints."""
    aioclient_mock.get(
        f"{BASE_URL}/api/v1/security/currentAuthorization/user",
        json=USER_RESPONSE if user is None else user,
    )
    aioclient_mock.get(
        f"{BASE_URL}/api/v1/security/tenantOrganizations",
        json=ORGANIZATION_RESPONSE if organizations is None else organizations,
    )
    aioclient_mock.post(
        f"{BASE_URL}/api/v2/iot/devices/baseInfo/query",
        json=DEVICE_RESPONSE if devices is None else devices,
    )
    aioclient_mock.post(
        f"{BASE_URL}/api/v1/iot/sensor/sensorInfo/query",
        json=SENSOR_RESPONSE if sensors is None else sensors,
    )
    aioclient_mock.post(
        f"{BASE_URL}/api/v1/iot/actuator/actuatorInfo/query",
        json=ACTUATOR_RESPONSE if actuators is None else actuators,
    )
    aioclient_mock.post(
        f"{BASE_URL}/api/v1/iot/actuator/actuatorData/action",
        status=204,
    )
    if mqtt is NO_MQTT:
        # A server without a broker answers with an error, not with parameters.
        aioclient_mock.get(f"{BASE_URL}/api/v1/iot/mqtt", status=501)
    else:
        aioclient_mock.get(
            f"{BASE_URL}/api/v1/iot/mqtt",
            json=MQTT_RESPONSE if mqtt is None else mqtt,
        )
