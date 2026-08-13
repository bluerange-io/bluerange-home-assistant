"""Tests for the live updates over MQTT."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.bluerange.api import BlueRangeClient
from custom_components.bluerange.const import (
    CONF_USE_MQTT,
    DEFAULT_SCAN_INTERVAL,
    MQTT_FALLBACK_SCAN_INTERVAL,
)
from custom_components.bluerange.mqtt import parse_sensor_topic
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .conftest import (
    BASE_URL,
    DEVICE_UUID,
    NO_MQTT,
    TOKEN,
    entity_id_for,
    mock_bluerange_api,
    setup_integration,
)

SENSOR_TOPIC = (
    f"rltn-iot/org-1/site-1/{DEVICE_UUID}/sensor/ACTUAL_TEMPERATURE/0/sensorData"
)


@pytest.mark.parametrize(
    ("topic", "expected"),
    [
        (
            "rltn-iot/org/site/dev/sensor/TEMPERATURE/0/sensorData",
            ("dev", "TEMPERATURE", 0),
        ),
        (
            "rltn-iot/org/site/dev/sensor/SET_DIMMING/2/sensorData",
            ("dev", "SET_DIMMING", 2),
        ),
        # Actuator echoes, diagnostics and anything else are not readings.
        ("rltn-iot/org/site/dev/actuator/SET_DIMMING/0/actuatorData", None),
        ("rltn-mdm/org/dev/diag/client/error/some", None),
        ("rltn-iot/org/site/dev/sensor/TEMPERATURE/0/sensorRequest", None),
        ("rltn-iot/org/site/dev/sensor/TEMPERATURE/sensorData", None),
        ("rltn-iot/org/site/dev/sensor/TEMPERATURE/x/sensorData", None),
        ("rltn-iot/org/site//sensor/TEMPERATURE/0/sensorData", None),
        ("", None),
    ],
)
def test_parse_sensor_topic(topic: str, expected: tuple[str, str, int] | None) -> None:
    """The topic is what identifies the sensor a reading belongs to."""
    assert parse_sensor_topic(topic) == expected


async def test_mqtt_parameters(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Broker access is requested with a client id marking an API token."""
    mock_bluerange_api(aioclient_mock)
    client = BlueRangeClient(async_get_clientsession(hass), BASE_URL, TOKEN)

    parameters = await client.async_get_mqtt_parameters("user-1", "org-1")

    assert parameters is not None
    assert parameters.client_id == "Token-user-1-homeassistant"
    assert parameters.username == "Token-user-1-homeassistant"
    # The server leaves the password out because it is the API token.
    assert parameters.password == TOKEN
    assert parameters.subscribe_topic == "rltn-iot/org-1/#"

    url = aioclient_mock.mock_calls[-1][1]
    assert url.query["clientId"] == "Token-user-1-homeassistant"
    assert url.query["protocol"] == "MQTT"


async def test_mqtt_parameters_use_a_shared_broker_account(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A deployment may configure one broker account for every client."""
    mock_bluerange_api(
        aioclient_mock,
        mqtt={
            "serverURIs": ["tcp://mqtt.example.com:1883"],
            "username": "shared",
            "password": "secret",
            "variables": {"organizationUuid": "org-1"},
        },
    )
    client = BlueRangeClient(async_get_clientsession(hass), BASE_URL, TOKEN)

    parameters = await client.async_get_mqtt_parameters("user-1", "org-1")

    assert parameters is not None
    assert (parameters.username, parameters.password) == ("shared", "secret")


@pytest.mark.parametrize(
    "mqtt",
    [
        NO_MQTT,
        {"serverURIs": [], "variables": {"organizationUuid": "org-1"}},
        {"serverURIs": ["tcp://host:1883"]},
    ],
    ids=["not_configured", "no_broker_named", "organisation_unknown"],
)
async def test_mqtt_parameters_unavailable(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mqtt: Any
) -> None:
    """Anything short of usable parameters means there are no live updates."""
    mock_bluerange_api(aioclient_mock, mqtt=mqtt)
    client = BlueRangeClient(async_get_clientsession(hass), BASE_URL, TOKEN)

    assert await client.async_get_mqtt_parameters("user-1", None) is None


def connect(mock_paho: MagicMock) -> MagicMock:
    """Report a successful connection the way paho would, and return the client."""
    client = mock_paho.return_value
    client.on_connect(client, None, {}, 0)
    return client


def publish(mock_paho: MagicMock, topic: str, payload: dict[str, Any]) -> None:
    """Deliver one message the way paho would."""
    client = mock_paho.return_value
    message = MagicMock()
    message.topic = topic
    message.payload = json.dumps(payload).encode()
    client.on_message(client, None, message)


async def test_subscribes_to_the_organisation(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_api: AiohttpClientMocker,
    mock_paho: MagicMock,
) -> None:
    """One subscription covers every device of the organisation."""
    await setup_integration(hass, config_entry)

    client = mock_paho.return_value
    client.connect.assert_called_once()
    assert client.connect.call_args.args[:2] == ("mqtt.example.com", 8883)
    client.username_pw_set.assert_called_once_with("Token-user-1-homeassistant", TOKEN)
    client.tls_set.assert_called_once()

    connect(mock_paho)
    client.subscribe.assert_called_once_with("rltn-iot/org-1/#", qos=0)


async def test_a_reading_updates_the_entity(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_api: AiohttpClientMocker,
    mock_paho: MagicMock,
) -> None:
    """A published reading reaches the entity without another poll."""
    await setup_integration(hass, config_entry)
    entity_id = entity_id_for(hass, "sensor", "sensor_ACTUAL_TEMPERATURE[0]")
    assert hass.states.get(entity_id).state == "20.5"

    connect(mock_paho)
    calls_before = len(mock_api.mock_calls)
    publish(mock_paho, SENSOR_TOPIC, {"value": 23.5, "timestamp": 1767225600000})
    await hass.async_block_till_done()

    assert hass.states.get(entity_id).state == "23.5"
    assert len(mock_api.mock_calls) == calls_before


async def test_polling_backs_off_while_live_updates_arrive(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_api: AiohttpClientMocker,
    mock_paho: MagicMock,
) -> None:
    """Polling is only a safety net once the broker confirmed the subscription."""
    await setup_integration(hass, config_entry)
    coordinator = config_entry.runtime_data
    assert not coordinator.push_available
    assert coordinator.update_interval.total_seconds() == DEFAULT_SCAN_INTERVAL

    connect(mock_paho)
    await hass.async_block_till_done()

    assert coordinator.push_available
    assert coordinator.update_interval.total_seconds() == MQTT_FALLBACK_SCAN_INTERVAL


async def test_polling_resumes_when_the_broker_goes_away(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_api: AiohttpClientMocker,
    mock_paho: MagicMock,
) -> None:
    """Losing the broker has to bring the configured interval back at once."""
    await setup_integration(hass, config_entry)
    client = connect(mock_paho)
    await hass.async_block_till_done()

    client.on_disconnect(client, None, {}, 0)
    await hass.async_block_till_done()

    coordinator = config_entry.runtime_data
    assert not coordinator.push_available
    assert coordinator.update_interval.total_seconds() == DEFAULT_SCAN_INTERVAL


async def test_readings_for_unknown_controls_are_ignored(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_api: AiohttpClientMocker,
    mock_paho: MagicMock,
) -> None:
    """A reading without metadata has no entity to update."""
    await setup_integration(hass, config_entry)
    connect(mock_paho)

    publish(
        mock_paho,
        f"rltn-iot/org-1/site-1/{DEVICE_UUID}/sensor/NOT_IN_METADATA/0/sensorData",
        {"value": 1},
    )
    publish(
        mock_paho,
        "rltn-iot/org-1/site-1/other-device/sensor/ACTUAL_TEMPERATURE/0/sensorData",
        {"value": 99},
    )
    await hass.async_block_till_done()

    entity_id = entity_id_for(hass, "sensor", "sensor_ACTUAL_TEMPERATURE[0]")
    assert hass.states.get(entity_id).state == "20.5"


async def test_malformed_payloads_are_ignored(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_api: AiohttpClientMocker,
    mock_paho: MagicMock,
) -> None:
    """A broken message must not take the listener down."""
    await setup_integration(hass, config_entry)
    client = connect(mock_paho)

    message = MagicMock()
    message.topic = SENSOR_TOPIC
    message.payload = b"not json"
    client.on_message(client, None, message)
    await hass.async_block_till_done()

    entity_id = entity_id_for(hass, "sensor", "sensor_ACTUAL_TEMPERATURE[0]")
    assert hass.states.get(entity_id).state == "20.5"


async def test_setup_without_a_broker_keeps_polling(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    mock_paho: MagicMock,
) -> None:
    """A server without MQTT is not an error, it just keeps the poll interval."""
    mock_bluerange_api(aioclient_mock, mqtt=NO_MQTT)
    await setup_integration(hass, config_entry)

    coordinator = config_entry.runtime_data
    assert not coordinator.push_available
    assert coordinator.update_interval.total_seconds() == DEFAULT_SCAN_INTERVAL
    mock_paho.assert_not_called()
    assert (
        hass.states.get(entity_id_for(hass, "sensor", "sensor_BATTERY[0]")) is not None
    )


async def test_unreachable_broker_keeps_polling(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_api: AiohttpClientMocker,
    mock_paho: MagicMock,
) -> None:
    """A broker that cannot be reached must not stop the integration."""
    mock_paho.return_value.connect.side_effect = OSError("no route to host")

    await setup_integration(hass, config_entry)

    coordinator = config_entry.runtime_data
    assert not coordinator.push_available
    assert coordinator.update_interval.total_seconds() == DEFAULT_SCAN_INTERVAL


async def test_live_updates_can_be_turned_off(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_api: AiohttpClientMocker,
    mock_paho: MagicMock,
) -> None:
    """With the option off the broker is not even asked about."""
    config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(config_entry, options={CONF_USE_MQTT: False})
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    mock_paho.assert_not_called()
    assert not any(
        str(url).endswith("/api/v1/iot/mqtt") for _, url, _, _ in mock_api.mock_calls
    )
    assert config_entry.runtime_data.update_interval.total_seconds() == (
        DEFAULT_SCAN_INTERVAL
    )


async def test_websocket_broker_is_used_when_it_is_the_only_one(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    mock_paho: MagicMock,
) -> None:
    """Some deployments only expose the broker over websockets."""
    mock_bluerange_api(
        aioclient_mock,
        mqtt={
            "serverURIs": ["wss://mqtt.example.com/mqtt"],
            "variables": {"organizationUuid": "org-1"},
        },
    )
    await setup_integration(hass, config_entry)

    assert mock_paho.call_args.kwargs["transport"] == "websockets"
    # Without a port in the URI the TLS default applies.
    assert mock_paho.return_value.connect.call_args.args[:2] == (
        "mqtt.example.com",
        8883,
    )


async def test_native_broker_is_preferred(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    mock_paho: MagicMock,
) -> None:
    """A native connection is cheaper than tunnelling through websockets."""
    mock_bluerange_api(
        aioclient_mock,
        mqtt={
            "serverURIs": [
                "wss://mqtt.example.com/mqtt",
                "tcp://mqtt.example.com:1883",
            ],
            "variables": {"organizationUuid": "org-1"},
        },
    )
    await setup_integration(hass, config_entry)

    assert mock_paho.call_args.kwargs["transport"] == "tcp"
    assert mock_paho.return_value.connect.call_args.args[:2] == (
        "mqtt.example.com",
        1883,
    )
    mock_paho.return_value.tls_set.assert_not_called()


async def test_unload_disconnects(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_api: AiohttpClientMocker,
    mock_paho: MagicMock,
) -> None:
    """Unloading the entry has to close the subscription."""
    await setup_integration(hass, config_entry)
    connect(mock_paho)

    assert await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()

    mock_paho.return_value.disconnect.assert_called_once()
    mock_paho.return_value.loop_stop.assert_called_once()
