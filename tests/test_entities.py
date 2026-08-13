"""Tests for the entities the BlueRange integration creates."""

from __future__ import annotations

from typing import Any

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.bluerange.const import DOMAIN
from homeassistant.components.climate import (
    ATTR_CURRENT_TEMPERATURE,
    ATTR_HVAC_ACTION,
    ATTR_HVAC_MODES,
    ATTR_PRESET_MODE,
    ATTR_PRESET_MODES,
    DOMAIN as CLIMATE_DOMAIN,
    PRESET_ECO,
    PRESET_NONE,
    SERVICE_SET_HVAC_MODE,
    SERVICE_SET_PRESET_MODE,
    SERVICE_SET_TEMPERATURE,
    HVACAction,
    HVACMode,
)
from homeassistant.components.cover import (
    ATTR_CURRENT_POSITION,
    ATTR_CURRENT_TILT_POSITION,
    ATTR_POSITION,
    ATTR_TILT_POSITION,
    DOMAIN as COVER_DOMAIN,
    SERVICE_CLOSE_COVER,
    SERVICE_OPEN_COVER,
    SERVICE_SET_COVER_POSITION,
    SERVICE_SET_COVER_TILT_POSITION,
    SERVICE_STOP_COVER,
    CoverDeviceClass,
    CoverEntityFeature,
)
from homeassistant.components.light import ATTR_BRIGHTNESS, DOMAIN as LIGHT_DOMAIN
from homeassistant.components.number import (
    ATTR_VALUE as NUMBER_ATTR_VALUE,
    DOMAIN as NUMBER_DOMAIN,
    SERVICE_SET_VALUE,
)
from homeassistant.components.sensor import ATTR_STATE_CLASS, SensorStateClass
from homeassistant.const import (
    ATTR_ASSUMED_STATE,
    ATTR_DEVICE_CLASS,
    ATTR_ENTITY_ID,
    ATTR_FRIENDLY_NAME,
    ATTR_SUPPORTED_FEATURES,
    ATTR_TEMPERATURE,
    ATTR_UNIT_OF_MEASUREMENT,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_CLOSED,
    STATE_OFF,
    STATE_ON,
    STATE_OPEN,
    Platform,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ServiceValidationError
from homeassistant.helpers import device_registry as dr, entity_registry as er

from .conftest import (
    BASE_URL,
    DEVICE_UUID,
    SENSOR_RESPONSE,
    entity_id_for,
    mock_bluerange_api,
    setup_integration,
)

ACTION_URL = f"{BASE_URL}/api/v1/iot/actuator/actuatorData/action"

#: A dimmable multi-channel luminaire, used for the light tests.
LUMINAIRE_SENSORS: dict[str, Any] = {
    "status": "0",
    "results": [
        {
            "type": "BRIGHTNESS",
            "index": 0,
            "module": ["core"],
            "unit": "PERCENT",
            "min": 0,
            "max": 100,
            "lastSensorData": {"type": "BRIGHTNESS", "index": 0, "value": 40},
        }
    ],
}

LUMINAIRE_ACTUATORS: dict[str, Any] = {
    "status": "0",
    "results": [
        {
            "type": "SET_DIMMING",
            "index": 0,
            "module": ["core"],
            "unit": "PERCENT",
            "min": 0,
            "max": 100,
        },
        {"type": "TURN_ON", "index": 0, "module": ["core"], "min": 1, "max": 1},
        {"type": "TURN_OFF", "index": 0, "module": ["core"], "min": 1, "max": 1},
    ],
}


#: A venetian blind as the blind module reports it, used for the cover tests.
#: BlueRange counts how far the blind is driven down, so 30 % is mostly open.
BLIND_SENSORS: dict[str, Any] = {
    "status": "0",
    "results": [
        {
            "type": "SLAT_POSITION",
            "index": 0,
            "module": ["blind"],
            "unit": "PERCENT",
            "min": 0,
            "max": 100,
            "lastSensorData": {"type": "SLAT_POSITION", "index": 0, "value": 30},
        },
        {
            "type": "SLAT_ANGLE",
            "index": 0,
            "module": ["blind"],
            "unit": "PERCENT",
            "min": 0,
            "max": 100,
            "lastSensorData": {"type": "SLAT_ANGLE", "index": 0, "value": 80},
        },
    ],
}

BLIND_ACTUATORS: dict[str, Any] = {
    "status": "0",
    "results": [
        {
            "type": "SET_SLAT_POSITION",
            "index": 0,
            "module": ["blind"],
            "unit": "PERCENT",
            "min": 0,
            "max": 100,
        },
        {
            "type": "SET_SLAT_ANGLE",
            "index": 0,
            "module": ["blind"],
            "unit": "PERCENT",
            "min": 0,
            "max": 100,
        },
        # The motor commands are pinned to the value that triggers them.
        {
            "type": "REQUEST_UP",
            "index": 0,
            "module": ["blind"],
            "unit": "ON_OFF",
            "min": 2,
            "max": 2,
        },
        {
            "type": "REQUEST_DOWN",
            "index": 0,
            "module": ["blind"],
            "unit": "ON_OFF",
            "min": 3,
            "max": 3,
        },
        {
            "type": "REQUEST_STOP",
            "index": 0,
            "module": ["blind"],
            "unit": "ON_OFF",
            "min": 1,
            "max": 1,
        },
    ],
}

#: The two commands that drive a shutter with nothing to read back.
SHUTTER_ACTUATORS: dict[str, Any] = {
    "status": "0",
    "results": BLIND_ACTUATORS["results"][2:4],
}


def last_write(aioclient_mock: AiohttpClientMocker) -> dict[str, Any]:
    """Return the body of the most recent actuator write."""
    for method, url, body, _ in reversed(aioclient_mock.mock_calls):
        if method == "POST" and url.path.endswith("/actuatorData/action"):
            return body
    raise AssertionError("no actuator write was sent")


async def test_created_entities(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_api: AiohttpClientMocker
) -> None:
    """The thermostat's controls are grouped into exactly these entities."""
    await setup_integration(hass, config_entry)

    entries = er.async_entries_for_config_entry(
        er.async_get(hass), config_entry.entry_id
    )
    created = {
        (entry.domain, entry.unique_id.removeprefix(f"{DEVICE_UUID}_"))
        for entry in entries
    }

    assert created == {
        ("climate", "climate_SET_SETPOINT_TEMPERATURE[0]"),
        ("switch", "switch_SET_CHILD_PROTECTION[0]"),
        ("number", "number_SET_MAX_TEMPERATURE[0]"),
        ("button", "button_TRIGGER_ADAPTATION[0]"),
        ("sensor", "sensor_ACTUAL_TEMPERATURE[0]"),
        ("sensor", "sensor_BATTERY[0]"),
        ("sensor", "sensor_VALVE_POSITION[0]"),
        ("sensor", "device_status"),
        ("sensor", "last_connection"),
        # Reported by mesh nodes in their details, so only created for those.
        ("sensor", "battery_voltage"),
        ("sensor", "transmit_power"),
        ("sensor", "calibrated_rssi"),
    }


async def test_sensor_state(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_api: AiohttpClientMocker
) -> None:
    """A measurement is exposed with its unit and device class."""
    await setup_integration(hass, config_entry)

    state = hass.states.get(
        entity_id_for(hass, "sensor", "sensor_ACTUAL_TEMPERATURE[0]")
    )

    assert state is not None
    assert state.state == "20.5"
    assert state.attributes[ATTR_UNIT_OF_MEASUREMENT] == "°C"
    assert state.attributes[ATTR_DEVICE_CLASS] == "temperature"
    assert state.attributes["control_type"] == "ACTUAL_TEMPERATURE"


async def test_device_status_sensor(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_api: AiohttpClientMocker
) -> None:
    """The enrollment state is exposed as an enum."""
    await setup_integration(hass, config_entry)

    state = hass.states.get(entity_id_for(hass, "sensor", "device_status"))

    assert state is not None
    assert state.state == "compliant"


async def test_climate_state(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_api: AiohttpClientMocker
) -> None:
    """The thermostat reads its state from the mirroring sensors."""
    await setup_integration(hass, config_entry)

    state = hass.states.get(
        entity_id_for(hass, "climate", "climate_SET_SETPOINT_TEMPERATURE[0]")
    )

    assert state is not None
    assert state.state == HVACMode.HEAT
    assert state.attributes[ATTR_TEMPERATURE] == 21.0
    assert state.attributes[ATTR_CURRENT_TEMPERATURE] == 20.5
    # The valve is partly open, so the device is actively heating.
    assert state.attributes[ATTR_HVAC_ACTION] == HVACAction.HEATING
    assert set(state.attributes[ATTR_HVAC_MODES]) == {HVACMode.HEAT, HVACMode.OFF}
    assert set(state.attributes[ATTR_PRESET_MODES]) == {PRESET_NONE, PRESET_ECO}
    assert state.attributes["min_temp"] == 8
    assert state.attributes["max_temp"] == 28


async def test_climate_set_temperature(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_api: AiohttpClientMocker
) -> None:
    """A new setpoint is written and shown before the server confirms it."""
    await setup_integration(hass, config_entry)
    entity_id = entity_id_for(hass, "climate", "climate_SET_SETPOINT_TEMPERATURE[0]")

    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_TEMPERATURE,
        {ATTR_ENTITY_ID: entity_id, ATTR_TEMPERATURE: 22.5},
        blocking=True,
    )

    assert last_write(mock_api) == {
        "deviceUuids": [DEVICE_UUID],
        "type": "SET_SETPOINT_TEMPERATURE",
        "index": 0,
        "module": "core",
        "value": 22.5,
    }
    # The server still reports the old value, so the written one is shown.
    state = hass.states.get(entity_id)
    assert state is not None
    assert state.attributes[ATTR_TEMPERATURE] == 22.5


async def test_climate_turn_off_uses_the_mode_command(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_api: AiohttpClientMocker
) -> None:
    """Switching heating off sends the single value that command accepts."""
    await setup_integration(hass, config_entry)
    entity_id = entity_id_for(hass, "climate", "climate_SET_SETPOINT_TEMPERATURE[0]")

    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_HVAC_MODE,
        {ATTR_ENTITY_ID: entity_id, "hvac_mode": HVACMode.OFF},
        blocking=True,
    )

    assert last_write(mock_api) == {
        "deviceUuids": [DEVICE_UUID],
        "type": "SET_HEATING_OFF",
        "index": 0,
        "module": "core",
        "value": 8,
    }
    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == HVACMode.OFF


async def test_climate_eco_preset(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_api: AiohttpClientMocker
) -> None:
    """The energy saving preset maps onto its own mode command."""
    await setup_integration(hass, config_entry)
    entity_id = entity_id_for(hass, "climate", "climate_SET_SETPOINT_TEMPERATURE[0]")

    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_PRESET_MODE,
        {ATTR_ENTITY_ID: entity_id, ATTR_PRESET_MODE: PRESET_ECO},
        blocking=True,
    )

    assert last_write(mock_api)["type"] == "SET_ENERGY_SAVING_MODE"
    assert last_write(mock_api)["value"] == 4
    state = hass.states.get(entity_id)
    assert state is not None
    assert state.attributes[ATTR_PRESET_MODE] == PRESET_ECO


async def test_switch_turn_on_and_off(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_api: AiohttpClientMocker
) -> None:
    """A switch writes the declared bounds of its actuator."""
    await setup_integration(hass, config_entry)
    entity_id = entity_id_for(hass, "switch", "switch_SET_CHILD_PROTECTION[0]")

    assert hass.states.get(entity_id).state == STATE_OFF

    await hass.services.async_call(
        Platform.SWITCH, SERVICE_TURN_ON, {ATTR_ENTITY_ID: entity_id}, blocking=True
    )

    assert last_write(mock_api)["value"] == 1
    assert hass.states.get(entity_id).state == STATE_ON

    await hass.services.async_call(
        Platform.SWITCH, SERVICE_TURN_OFF, {ATTR_ENTITY_ID: entity_id}, blocking=True
    )

    assert last_write(mock_api)["value"] == 0


async def test_number_state_and_write(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_api: AiohttpClientMocker
) -> None:
    """A setpoint takes its range from the actuator metadata."""
    await setup_integration(hass, config_entry)
    entity_id = entity_id_for(hass, "number", "number_SET_MAX_TEMPERATURE[0]")

    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == "24.0"
    assert state.attributes["min"] == 8
    assert state.attributes["max"] == 28
    assert state.attributes["step"] == 0.5

    await hass.services.async_call(
        NUMBER_DOMAIN,
        SERVICE_SET_VALUE,
        {ATTR_ENTITY_ID: entity_id, NUMBER_ATTR_VALUE: 25.5},
        blocking=True,
    )

    assert last_write(mock_api)["value"] == 25.5
    assert hass.states.get(entity_id).state == "25.5"


async def test_write_auth_failure_starts_reauth(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_api: AiohttpClientMocker
) -> None:
    """A 401 on a write triggers the repair flow, not just an error."""
    await setup_integration(hass, config_entry)
    entity_id = entity_id_for(hass, "number", "number_SET_MAX_TEMPERATURE[0]")

    # The mocker matches the first registration, so the 401 has to be
    # registered before the fallback 204 that ``mock_bluerange_api`` sets up.
    mock_api.clear_requests()
    mock_api.post(ACTION_URL, status=401)
    mock_bluerange_api(mock_api)

    with pytest.raises(ConfigEntryAuthFailed):
        await hass.services.async_call(
            NUMBER_DOMAIN,
            SERVICE_SET_VALUE,
            {ATTR_ENTITY_ID: entity_id, NUMBER_ATTR_VALUE: 25.5},
            blocking=True,
        )

    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert any(flow["context"].get("source") == "reauth" for flow in flows)


async def test_button_sends_its_fixed_value(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_api: AiohttpClientMocker
) -> None:
    """A momentary command carries the only value it accepts."""
    await setup_integration(hass, config_entry)
    entity_id = entity_id_for(hass, "button", "button_TRIGGER_ADAPTATION[0]")

    await hass.services.async_call(
        Platform.BUTTON, "press", {ATTR_ENTITY_ID: entity_id}, blocking=True
    )

    assert last_write(mock_api) == {
        "deviceUuids": [DEVICE_UUID],
        "type": "TRIGGER_ADAPTATION",
        "index": 0,
        "module": "core",
        "value": 1,
    }


@pytest.fixture
def mock_luminaire(aioclient_mock: AiohttpClientMocker) -> AiohttpClientMocker:
    """Answer with a dimmable luminaire instead of a thermostat."""
    mock_bluerange_api(
        aioclient_mock, sensors=LUMINAIRE_SENSORS, actuators=LUMINAIRE_ACTUATORS
    )
    return aioclient_mock


async def test_light_state(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_luminaire: AiohttpClientMocker,
) -> None:
    """Brightness is scaled from percent onto the Home Assistant range."""
    await setup_integration(hass, config_entry)

    state = hass.states.get(entity_id_for(hass, "light", "light_SET_DIMMING[0]"))

    assert state is not None
    assert state.state == STATE_ON
    assert state.attributes[ATTR_BRIGHTNESS] == round(40 * 255 / 100)


async def test_light_set_brightness(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_luminaire: AiohttpClientMocker,
) -> None:
    """Setting a brightness dims the channel in percent."""
    await setup_integration(hass, config_entry)
    entity_id = entity_id_for(hass, "light", "light_SET_DIMMING[0]")

    await hass.services.async_call(
        LIGHT_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: entity_id, ATTR_BRIGHTNESS: 128},
        blocking=True,
    )

    assert last_write(mock_luminaire) == {
        "deviceUuids": [DEVICE_UUID],
        "type": "SET_DIMMING",
        "index": 0,
        "module": "core",
        "value": 50,
    }


async def test_light_turn_off_uses_the_dedicated_command(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_luminaire: AiohttpClientMocker,
) -> None:
    """Turning off prefers the explicit command over dimming to zero."""
    await setup_integration(hass, config_entry)
    entity_id = entity_id_for(hass, "light", "light_SET_DIMMING[0]")

    await hass.services.async_call(
        LIGHT_DOMAIN, SERVICE_TURN_OFF, {ATTR_ENTITY_ID: entity_id}, blocking=True
    )

    assert last_write(mock_luminaire)["type"] == "TURN_OFF"
    assert hass.states.get(entity_id).state == STATE_OFF


async def test_set_actuator_service(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_api: AiohttpClientMocker
) -> None:
    """The service reaches actuators that have no entity of their own."""
    await setup_integration(hass, config_entry)
    device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, DEVICE_UUID)})
    assert device is not None

    await hass.services.async_call(
        DOMAIN,
        "set_actuator",
        {
            "device_id": device.id,
            "control_type": "SET_VENDOR_SPECIFIC",
            "index": 3,
            "module": "core",
            "value": 7,
        },
        blocking=True,
    )

    assert last_write(mock_api) == {
        "deviceUuids": [DEVICE_UUID],
        "type": "SET_VENDOR_SPECIFIC",
        "index": 3,
        "module": "core",
        "value": 7,
    }


#: A luminaire with a presence detector, used for the binary sensor tests.
PRESENCE_SENSORS: dict[str, Any] = {
    "status": "0",
    "results": [
        {
            "type": "PRESENCE",
            "index": 0,
            "module": ["core"],
            "unit": "ON_OFF",
            "lastSensorData": {"type": "PRESENCE", "index": 0, "value": 1},
        },
        {
            "type": "VENDOR_TEST_DEBUG",
            "index": 0,
            "module": ["core"],
            "lastSensorData": {"type": "VENDOR_TEST_DEBUG", "index": 0, "value": "ok"},
        },
    ],
}


async def test_binary_sensor_state(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """An on/off sensor becomes a binary sensor with its device class."""
    mock_bluerange_api(
        aioclient_mock,
        sensors=PRESENCE_SENSORS,
        actuators={"status": "0", "results": []},
    )
    await setup_integration(hass, config_entry)

    state = hass.states.get(
        entity_id_for(hass, "binary_sensor", "binary_sensor_PRESENCE[0]")
    )

    assert state is not None
    assert state.state == STATE_ON
    assert state.attributes[ATTR_DEVICE_CLASS] == "occupancy"


async def test_unitless_sensor_keeps_its_raw_value(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """A value of unknown shape is passed through as a diagnostic."""
    mock_bluerange_api(
        aioclient_mock,
        sensors=PRESENCE_SENSORS,
        actuators={"status": "0", "results": []},
    )
    await setup_integration(hass, config_entry)

    entity_id = entity_id_for(hass, "sensor", "sensor_VENDOR_TEST_DEBUG[0]")
    state = hass.states.get(entity_id)

    assert state is not None
    assert state.state == "ok"
    assert ATTR_UNIT_OF_MEASUREMENT not in state.attributes
    entry = er.async_get(hass).async_get(entity_id)
    assert entry is not None
    assert entry.entity_category == "diagnostic"


async def test_sensor_with_only_a_lower_bound_is_a_measurement(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """One bound is enough to tell a reading from a status code.

    A battery voltage is published with a lower bound and no upper one.  Without
    a state class Home Assistant would keep no statistics for it, leaving the
    sensor without a history.
    """
    mock_bluerange_api(
        aioclient_mock,
        sensors={
            "status": "0",
            "results": [
                {
                    "type": "BATTERY_VOLTAGE",
                    "index": 0,
                    "module": ["node"],
                    "min": 0,
                    "lastSensorData": {
                        "type": "BATTERY_VOLTAGE",
                        "index": 0,
                        "value": 25,
                    },
                }
            ],
        },
        actuators={"status": "0", "results": []},
    )
    await setup_integration(hass, config_entry)

    state = hass.states.get(entity_id_for(hass, "sensor", "sensor_BATTERY_VOLTAGE[0]"))

    assert state.attributes[ATTR_STATE_CLASS] == SensorStateClass.MEASUREMENT
    assert state.state == "25.0"


async def test_light_turn_on_without_brightness(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_luminaire: AiohttpClientMocker,
) -> None:
    """Turning on prefers the explicit command over dimming to full output."""
    await setup_integration(hass, config_entry)
    entity_id = entity_id_for(hass, "light", "light_SET_DIMMING[0]")

    await hass.services.async_call(
        LIGHT_DOMAIN, SERVICE_TURN_ON, {ATTR_ENTITY_ID: entity_id}, blocking=True
    )

    assert last_write(mock_luminaire)["type"] == "TURN_ON"
    assert hass.states.get(entity_id).state == STATE_ON


async def test_light_without_commands_dims_instead(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """A channel with only a dimming actuator is switched by its level."""
    mock_bluerange_api(
        aioclient_mock,
        sensors=LUMINAIRE_SENSORS,
        actuators={"status": "0", "results": [LUMINAIRE_ACTUATORS["results"][0]]},
    )
    await setup_integration(hass, config_entry)
    entity_id = entity_id_for(hass, "light", "light_SET_DIMMING[0]")

    await hass.services.async_call(
        LIGHT_DOMAIN, SERVICE_TURN_OFF, {ATTR_ENTITY_ID: entity_id}, blocking=True
    )

    assert last_write(aioclient_mock) == {
        "deviceUuids": [DEVICE_UUID],
        "type": "SET_DIMMING",
        "index": 0,
        "module": "core",
        "value": 0,
    }
    assert hass.states.get(entity_id).state == STATE_OFF

    await hass.services.async_call(
        LIGHT_DOMAIN, SERVICE_TURN_ON, {ATTR_ENTITY_ID: entity_id}, blocking=True
    )

    assert last_write(aioclient_mock)["value"] == 100


async def test_climate_turn_back_on(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_api: AiohttpClientMocker
) -> None:
    """Selecting the heating mode sends the value that command accepts."""
    await setup_integration(hass, config_entry)
    entity_id = entity_id_for(hass, "climate", "climate_SET_SETPOINT_TEMPERATURE[0]")

    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_HVAC_MODE,
        {ATTR_ENTITY_ID: entity_id, "hvac_mode": HVACMode.HEAT},
        blocking=True,
    )

    assert last_write(mock_api)["type"] == "SET_HEATING_MODE"
    assert last_write(mock_api)["value"] == 2
    assert hass.states.get(entity_id).state == HVACMode.HEAT


async def test_climate_leaving_the_preset(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_api: AiohttpClientMocker
) -> None:
    """Leaving the energy saving preset returns to normal heating."""
    await setup_integration(hass, config_entry)
    entity_id = entity_id_for(hass, "climate", "climate_SET_SETPOINT_TEMPERATURE[0]")

    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_PRESET_MODE,
        {ATTR_ENTITY_ID: entity_id, ATTR_PRESET_MODE: PRESET_NONE},
        blocking=True,
    )

    assert last_write(mock_api)["type"] == "SET_HEATING_MODE"
    assert hass.states.get(entity_id).attributes[ATTR_PRESET_MODE] == PRESET_NONE


async def test_climate_without_a_valve_reports_no_action(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """Without a valve reading it is unknown whether the device is heating."""
    mock_bluerange_api(
        aioclient_mock,
        sensors={
            "status": "0",
            "results": [
                entry
                for entry in SENSOR_RESPONSE["results"]
                if entry["type"] != "VALVE_POSITION"
            ],
        },
    )
    await setup_integration(hass, config_entry)

    state = hass.states.get(
        entity_id_for(hass, "climate", "climate_SET_SETPOINT_TEMPERATURE[0]")
    )

    assert state is not None
    assert state.attributes.get(ATTR_HVAC_ACTION) is None


async def test_set_actuator_service_rejects_a_foreign_device(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_api: AiohttpClientMocker
) -> None:
    """The service only writes to devices of this integration."""
    await setup_integration(hass, config_entry)

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            "set_actuator",
            {"device_id": "does-not-exist", "control_type": "SET_X", "value": 1},
            blocking=True,
        )


async def test_set_actuator_service_uses_the_known_module(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_api: AiohttpClientMocker
) -> None:
    """Without an explicit module the device metadata supplies it."""
    await setup_integration(hass, config_entry)
    device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, DEVICE_UUID)})
    assert device is not None

    await hass.services.async_call(
        DOMAIN,
        "set_actuator",
        {
            "device_id": device.id,
            "control_type": "SET_MAX_TEMPERATURE",
            "value": 26,
        },
        blocking=True,
    )

    assert last_write(mock_api) == {
        "deviceUuids": [DEVICE_UUID],
        "type": "SET_MAX_TEMPERATURE",
        "index": 0,
        "module": "core",
        "value": 26,
    }


async def test_mesh_node_diagnostics(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_api: AiohttpClientMocker
) -> None:
    """The radio and power figures of a mesh node become diagnostics."""
    await setup_integration(hass, config_entry)
    registry = er.async_get(hass)

    expected = {
        "battery_voltage": ("5.3", "V"),
        "transmit_power": ("4", "dBm"),
        "calibrated_rssi": ("-52", "dBm"),
    }
    for key, (value, unit) in expected.items():
        entity_id = entity_id_for(hass, "sensor", key)
        state = hass.states.get(entity_id)
        assert state is not None, key
        assert state.state == value, key
        assert state.attributes[ATTR_UNIT_OF_MEASUREMENT] == unit, key
        entry = registry.async_get(entity_id)
        assert entry is not None
        assert entry.entity_category == "diagnostic", key


async def test_devices_without_mesh_details_get_no_radio_sensors(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """A device that reports no radio figures should not show empty sensors."""
    devices = {
        "status": "0",
        "results": [
            {"uuid": DEVICE_UUID, "name": "Plain node", "status": "COMPLIANT"},
        ],
    }
    mock_bluerange_api(aioclient_mock, devices=devices)
    await setup_integration(hass, config_entry)

    keys = {
        entry.unique_id.removeprefix(f"{DEVICE_UUID}_")
        for entry in er.async_entries_for_config_entry(
            er.async_get(hass), config_entry.entry_id
        )
    }

    assert "device_status" in keys
    assert not {"battery_voltage", "transmit_power", "calibrated_rssi"} & keys


@pytest.fixture
def mock_blind(aioclient_mock: AiohttpClientMocker) -> AiohttpClientMocker:
    """Answer with a venetian blind instead of a thermostat."""
    mock_bluerange_api(aioclient_mock, sensors=BLIND_SENSORS, actuators=BLIND_ACTUATORS)
    return aioclient_mock


async def test_cover_state(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_blind: AiohttpClientMocker
) -> None:
    """How far the blind is driven down is reported as how far it is open."""
    await setup_integration(hass, config_entry)
    entity_id = entity_id_for(hass, "cover", "cover_0")

    state = hass.states.get(entity_id)

    assert state.state == STATE_OPEN
    assert state.attributes[ATTR_DEVICE_CLASS] == CoverDeviceClass.BLIND
    # Driven down 30 % leaves the blind open 70 %, while the slat angle is the
    # one BlueRange reports, because it is not an axis from closed to open.
    assert state.attributes[ATTR_CURRENT_POSITION] == 70
    assert state.attributes[ATTR_CURRENT_TILT_POSITION] == 80

    supported = CoverEntityFeature(state.attributes[ATTR_SUPPORTED_FEATURES])
    assert supported == (
        CoverEntityFeature.OPEN
        | CoverEntityFeature.CLOSE
        | CoverEntityFeature.STOP
        | CoverEntityFeature.SET_POSITION
        | CoverEntityFeature.SET_TILT_POSITION
    )


async def test_cover_removes_what_it_took_over(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_blind: AiohttpClientMocker,
) -> None:
    """Controls that became part of a cover leave no entity of their own behind."""
    config_entry.add_to_hass(hass)
    registry = er.async_get(hass)
    stale = {
        "number": "number_SET_SLAT_POSITION[0]",
        "button": "button_REQUEST_UP[0]",
        "sensor": "sensor_SLAT_ANGLE[0]",
    }
    for domain, key in stale.items():
        registry.async_get_or_create(
            domain,
            DOMAIN,
            f"{DEVICE_UUID}_{key}",
            config_entry=config_entry,
        )

    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    for domain, key in stale.items():
        assert registry.async_get_entity_id(domain, DOMAIN, f"{DEVICE_UUID}_{key}") is (
            None
        ), key
    # The cover that replaced them is there.
    assert entity_id_for(hass, "cover", "cover_0")


async def test_cover_keeps_entities_it_did_not_take_over(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_blind: AiohttpClientMocker,
) -> None:
    """Only the blind's own controls are cleaned up, on that channel alone."""
    config_entry.add_to_hass(hass)
    registry = er.async_get(hass)
    kept = ["number_SET_SLAT_POSITION[1]", "sensor_MOTOR_STATE[0]"]
    for key in kept:
        registry.async_get_or_create(
            key.split("_", 1)[0],
            DOMAIN,
            f"{DEVICE_UUID}_{key}",
            config_entry=config_entry,
        )

    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    for key in kept:
        assert (
            registry.async_get_entity_id(
                key.split("_", 1)[0], DOMAIN, f"{DEVICE_UUID}_{key}"
            )
            is not None
        ), key


async def test_cover_set_position(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_blind: AiohttpClientMocker
) -> None:
    """A height is written as how far the blind has to be driven down."""
    await setup_integration(hass, config_entry)
    entity_id = entity_id_for(hass, "cover", "cover_0")

    await hass.services.async_call(
        COVER_DOMAIN,
        SERVICE_SET_COVER_POSITION,
        {ATTR_ENTITY_ID: entity_id, ATTR_POSITION: 25},
        blocking=True,
    )

    assert last_write(mock_blind) == {
        "deviceUuids": [DEVICE_UUID],
        "type": "SET_SLAT_POSITION",
        "index": 0,
        "module": "blind",
        "value": 75,
    }
    assert hass.states.get(entity_id).attributes[ATTR_CURRENT_POSITION] == 25


async def test_cover_set_tilt_position(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_blind: AiohttpClientMocker
) -> None:
    """The slat angle is written as the angle BlueRange itself counts."""
    await setup_integration(hass, config_entry)
    entity_id = entity_id_for(hass, "cover", "cover_0")

    await hass.services.async_call(
        COVER_DOMAIN,
        SERVICE_SET_COVER_TILT_POSITION,
        {ATTR_ENTITY_ID: entity_id, ATTR_TILT_POSITION: 10},
        blocking=True,
    )

    assert last_write(mock_blind)["type"] == "SET_SLAT_ANGLE"
    assert last_write(mock_blind)["value"] == 10
    assert hass.states.get(entity_id).attributes[ATTR_CURRENT_TILT_POSITION] == 10


async def test_cover_open_and_close_use_the_motor_commands(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_blind: AiohttpClientMocker
) -> None:
    """Opening and closing send a command rather than write an end position."""
    await setup_integration(hass, config_entry)
    entity_id = entity_id_for(hass, "cover", "cover_0")

    await hass.services.async_call(
        COVER_DOMAIN,
        SERVICE_CLOSE_COVER,
        {ATTR_ENTITY_ID: entity_id},
        blocking=True,
    )

    assert last_write(mock_blind)["type"] == "REQUEST_DOWN"
    assert last_write(mock_blind)["value"] == 3
    assert hass.states.get(entity_id).state == STATE_CLOSED

    await hass.services.async_call(
        COVER_DOMAIN, SERVICE_OPEN_COVER, {ATTR_ENTITY_ID: entity_id}, blocking=True
    )

    assert last_write(mock_blind)["type"] == "REQUEST_UP"
    assert last_write(mock_blind)["value"] == 2
    state = hass.states.get(entity_id)
    assert state.state == STATE_OPEN
    assert state.attributes[ATTR_CURRENT_POSITION] == 100


async def test_knx_cover_drives_with_the_direction_value(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """A blind over KNX takes the direction as the value of one command."""
    mock_bluerange_api(
        aioclient_mock,
        sensors=BLIND_SENSORS,
        actuators={
            "status": "0",
            "results": [
                *BLIND_ACTUATORS["results"][:2],
                {
                    "type": "DRIVE_UP_DOWN",
                    "index": 0,
                    "module": ["knx"],
                    "unit": "ON_OFF",
                    "min": 0,
                    "max": 1,
                },
            ],
        },
    )
    await setup_integration(hass, config_entry)
    entity_id = entity_id_for(hass, "cover", "cover_0")

    await hass.services.async_call(
        COVER_DOMAIN, SERVICE_CLOSE_COVER, {ATTR_ENTITY_ID: entity_id}, blocking=True
    )

    assert last_write(aioclient_mock)["type"] == "DRIVE_UP_DOWN"
    assert last_write(aioclient_mock)["value"] == 1

    await hass.services.async_call(
        COVER_DOMAIN, SERVICE_OPEN_COVER, {ATTR_ENTITY_ID: entity_id}, blocking=True
    )

    assert last_write(aioclient_mock)["value"] == 0


async def test_cover_stop_waits_for_the_reported_position(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_blind: AiohttpClientMocker
) -> None:
    """Stopping a moving blind gives up on the position that was aimed for."""
    await setup_integration(hass, config_entry)
    entity_id = entity_id_for(hass, "cover", "cover_0")

    await hass.services.async_call(
        COVER_DOMAIN, SERVICE_CLOSE_COVER, {ATTR_ENTITY_ID: entity_id}, blocking=True
    )
    assert hass.states.get(entity_id).attributes[ATTR_CURRENT_POSITION] == 0

    await hass.services.async_call(
        COVER_DOMAIN, SERVICE_STOP_COVER, {ATTR_ENTITY_ID: entity_id}, blocking=True
    )

    assert last_write(mock_blind)["type"] == "REQUEST_STOP"
    assert last_write(mock_blind)["value"] == 1
    # What the blind reports is shown again instead of where it was told to go.
    assert hass.states.get(entity_id).attributes[ATTR_CURRENT_POSITION] == 70


async def test_shutter_without_a_readback_is_assumed(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """A blind reporting nothing back has no position and no slats."""
    mock_bluerange_api(
        aioclient_mock,
        sensors={"status": "0", "results": []},
        actuators=SHUTTER_ACTUATORS,
    )
    await setup_integration(hass, config_entry)
    entity_id = entity_id_for(hass, "cover", "cover_0")

    state = hass.states.get(entity_id)

    assert state.attributes[ATTR_ASSUMED_STATE] is True
    assert state.attributes[ATTR_DEVICE_CLASS] == CoverDeviceClass.SHUTTER
    assert ATTR_CURRENT_POSITION not in state.attributes
    supported = CoverEntityFeature(state.attributes[ATTR_SUPPORTED_FEATURES])
    assert supported == CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE


async def test_curated_names_carry_the_channel(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """A control on a second channel keeps its curated name and the channel."""
    setpoint = {
        "type": "SET_MAX_TEMPERATURE",
        "module": ["core"],
        "unit": "CELSIUS",
        "min": 8,
        "max": 28,
    }
    mock_bluerange_api(
        aioclient_mock,
        sensors={"status": "0", "results": []},
        actuators={
            "status": "0",
            "results": [setpoint | {"index": 0}, setpoint | {"index": 1}],
        },
    )
    await setup_integration(hass, config_entry)

    names = [
        hass.states.get(
            entity_id_for(hass, "number", f"number_SET_MAX_TEMPERATURE[{index}]")
        ).attributes[ATTR_FRIENDLY_NAME]
        for index in (0, 1)
    ]

    assert names == [
        "Thermostat Office Maximum temperature",
        "Thermostat Office Maximum temperature 2",
    ]
