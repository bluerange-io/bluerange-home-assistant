"""Tests for the BlueRange API client."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.bluerange.api import (
    BlueRangeAuthError,
    BlueRangeClient,
    BlueRangeConnectionError,
    BlueRangeDevice,
    BlueRangeError,
    BlueRangeOrganization,
    BlueRangeResponseError,
    ControlDefinition,
    coerce_bool,
    coerce_float,
    format_firmware_version,
    normalize_base_url,
    normalize_write_value,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .conftest import BASE_URL, DEVICE_UUID, TOKEN, mock_bluerange_api


def client(hass: HomeAssistant, base_url: str = BASE_URL) -> BlueRangeClient:
    """Return a client talking to the mocked server."""
    return BlueRangeClient(async_get_clientsession(hass), base_url, TOKEN)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("portal.example.com", "https://portal.example.com"),
        ("https://portal.example.com/", "https://portal.example.com"),
        ("http://10.0.0.5:8080/#/iot/devices", "http://10.0.0.5:8080"),
        ("  https://portal.example.com  ", "https://portal.example.com"),
    ],
)
def test_normalize_base_url(raw: str, expected: str) -> None:
    """Only the origin of the entered address is kept."""
    assert str(normalize_base_url(raw)) == expected


@pytest.mark.parametrize(
    "raw",
    ["", "   ", "ftp://portal.example.com", "https://", "not a url", "https://a b.com"],
)
def test_normalize_base_url_rejects_garbage(raw: str) -> None:
    """An address that cannot host an API is refused."""
    with pytest.raises(BlueRangeError):
        normalize_base_url(raw)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (260010030, "26.1.30"),
        (10000000, "1.0.0"),
        (None, None),
        (17, "17"),
        ("1.2.3", "1.2.3"),
    ],
)
def test_format_firmware_version(raw: object, expected: str | None) -> None:
    """FruityMesh packs its version into a single integer."""
    assert format_firmware_version(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(1, 1.0), ("2.5", 2.5), (True, 1.0), ("nope", None), (None, None), ([], None)],
)
def test_coerce_float(raw: object, expected: float | None) -> None:
    """Sensor values arrive as untyped JSON."""
    assert coerce_float(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (1, True),
        (0, False),
        (0.0, False),
        ("on", True),
        ("FALSE", False),
        (True, True),
        ("maybe", None),
        (None, None),
    ],
)
def test_coerce_bool(raw: object, expected: bool | None) -> None:
    """On/off values may be numbers, booleans or strings."""
    assert coerce_bool(raw) is expected


@pytest.mark.parametrize(
    ("raw", "expected"), [(True, 1), (False, 0), (21.0, 21), (21.5, 21.5), ("x", "x")]
)
def test_normalize_write_value(raw: object, expected: object) -> None:
    """Whole numbers are written as integers."""
    assert normalize_write_value(raw) == expected


def test_device_from_json() -> None:
    """A device record is mapped onto the fields Home Assistant needs."""
    device = BlueRangeDevice.from_json(
        {
            "uuid": "u-1",
            "deviceId": "FM-1",
            "name": "Thermostat",
            "manufacturer": {"name": "BlueRange"},
            "model": "BlueRange Thermostat",
            "fwVersion": 260010030,
            "nodeId": 7,
            "status": "COMPLIANT",
            "lastConnectionDate": 1767225600000,
            "room": {"name": "Office"},
            "floor": {"name": "Ground floor"},
        }
    )

    assert device is not None
    assert device.name == "Thermostat"
    assert device.manufacturer == "BlueRange"
    assert device.firmware_version == "26.1.30"
    assert device.suggested_area == "Office"
    assert device.is_active
    assert device.last_connection == datetime(2026, 1, 1, tzinfo=UTC)


def test_device_reads_the_mesh_node_details() -> None:
    """Mesh nodes report their radio and power figures in the details object."""
    device = BlueRangeDevice.from_json(
        {
            "uuid": "u-1",
            "details": {
                "platform": "MESH_NODE",
                "macAddress": "AA:BB:CC:DD:EE:FF",
                # Reported in tenths of a volt.
                "batteryInfo": 53,
                "dBmTX": 4,
                "calibratedRssi": -52,
            },
        }
    )

    assert device is not None
    assert device.mac_address == "AA:BB:CC:DD:EE:FF"
    assert device.battery_voltage == 5.3
    assert device.transmit_power == 4
    assert device.calibrated_rssi == -52


def test_device_without_details_reports_nothing() -> None:
    """Other kinds of device carry other fields, or none of these."""
    device = BlueRangeDevice.from_json({"uuid": "u-1", "details": {"platform": "IOS"}})

    assert device is not None
    assert device.mac_address is None
    assert device.battery_voltage is None
    assert device.transmit_power is None
    assert device.calibrated_rssi is None


def test_unmeasured_battery_is_not_zero_volts() -> None:
    """A node that does not measure its battery reports zero, not 0.0 V."""
    device = BlueRangeDevice.from_json({"uuid": "u-1", "details": {"batteryInfo": 0}})

    assert device is not None
    assert device.battery_voltage is None


def test_broken_details_are_ignored() -> None:
    """The details object is platform specific, so nothing in it is guaranteed."""
    device = BlueRangeDevice.from_json({"uuid": "u-1", "details": "not an object"})

    assert device is not None
    assert device.mac_address is None


def test_device_model_is_the_catalog_entry() -> None:
    """The module a device runs on says nothing about what the device is."""
    device = BlueRangeDevice.from_json(
        {
            "uuid": "u-1",
            "model": "nRF52",
            "deviceRepositoryEntry": {"name": "BlueRange Blind"},
        }
    )

    assert device is not None
    assert device.model == "BlueRange Blind"
    assert device.hardware == "nRF52"


def test_device_without_a_catalog_entry_keeps_its_model() -> None:
    """Without a catalog entry the module is the best model there is."""
    device = BlueRangeDevice.from_json({"uuid": "u-1", "model": "nRF52"})

    assert device is not None
    assert device.model == "nRF52"
    # Not repeated as hardware, which would show the same value twice.
    assert device.hardware is None


def test_device_falls_back_for_its_name() -> None:
    """A device without a name is still identifiable."""
    device = BlueRangeDevice.from_json({"uuid": "u-1", "deviceId": "FM-1"})

    assert device is not None
    assert device.name == "FM-1"
    assert device.suggested_area is None


def test_device_without_uuid_is_skipped() -> None:
    """A record that cannot be addressed is unusable."""
    assert BlueRangeDevice.from_json({"name": "nameless"}) is None


def test_withdrawn_device_is_inactive() -> None:
    """Devices on their way out of the inventory are not mirrored."""
    device = BlueRangeDevice.from_json({"uuid": "u-1", "status": "WITHDRAWN"})

    assert device is not None
    assert not device.is_active


def test_control_definition_from_json() -> None:
    """Control metadata is addressed by type and index."""
    definition = ControlDefinition.from_json(
        {
            "type": "SET_DIMMING",
            "index": 2,
            "module": ["t4l"],
            "unit": "PERCENT",
            "min": 0,
            "max": 100,
        }
    )

    assert definition is not None
    assert definition.key == "SET_DIMMING[2]"
    assert definition.module == "t4l"
    assert not definition.is_trigger


def test_control_definition_merged_over_modules_has_no_module() -> None:
    """An ambiguous module cannot be used to address a write."""
    definition = ControlDefinition.from_json(
        {"type": "SET_DIMMING", "module": ["t4l", "vs"]}
    )

    assert definition is not None
    assert definition.module is None
    assert definition.index == 0


def test_pinned_control_is_a_trigger() -> None:
    """A single accepted value marks a momentary command."""
    definition = ControlDefinition.from_json({"type": "RESET_HOST", "min": 1, "max": 1})

    assert definition is not None
    assert definition.is_trigger


async def test_get_devices(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The device inventory is read from the v2 endpoint."""
    mock_bluerange_api(aioclient_mock)

    devices = await client(hass).async_get_devices()

    assert [device.uuid for device in devices] == [DEVICE_UUID]
    assert devices[0].firmware_version == "26.1.30"


async def test_get_sensors_returns_metadata_and_values(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Sensor metadata and the last value come from the same query."""
    mock_bluerange_api(aioclient_mock)

    definitions, readings = await client(hass).async_get_sensors(DEVICE_UUID)

    assert definitions["ACTUAL_TEMPERATURE[0]"].unit == "CELSIUS"
    assert readings["ACTUAL_TEMPERATURE[0]"].value == 20.5
    assert readings["ACTUAL_TEMPERATURE[0]"].timestamp is not None
    # A record without a reading yields metadata only.
    assert readings["SETPOINT_TEMPERATURE[0]"].timestamp is None


async def test_get_sensors_restricts_the_query_to_one_device(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The server merges metadata across devices, so one is queried at a time."""
    mock_bluerange_api(aioclient_mock)

    await client(hass).async_get_sensors(DEVICE_UUID)

    body = aioclient_mock.mock_calls[-1][2]
    assert body["deviceUuids"] == [DEVICE_UUID]
    assert "lastSensorData" in body["fields"]


async def test_set_actuator_sends_the_address_and_value(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A write carries the device, the control address and the value."""
    mock_bluerange_api(aioclient_mock)
    definition = ControlDefinition(
        control_type="SET_SETPOINT_TEMPERATURE", index=0, module="euro"
    )

    await client(hass).async_set_actuator(DEVICE_UUID, definition, 21.5)

    method, url, body, _ = aioclient_mock.mock_calls[-1]
    assert method == "POST"
    assert url.path == "/api/v1/iot/actuator/actuatorData/action"
    assert body == {
        "deviceUuids": [DEVICE_UUID],
        "type": "SET_SETPOINT_TEMPERATURE",
        "index": 0,
        "value": 21.5,
        "module": "euro",
    }


async def test_set_actuator_omits_an_unknown_module(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Without an unambiguous module the server picks one itself."""
    mock_bluerange_api(aioclient_mock)

    await client(hass).async_set_actuator(
        DEVICE_UUID, ControlDefinition(control_type="TURN_ON"), True
    )

    body = aioclient_mock.mock_calls[-1][2]
    assert "module" not in body
    assert body["value"] == 1


async def test_rejected_token_raises_auth_error(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A 401 tells Home Assistant to ask for new credentials."""
    aioclient_mock.get(
        f"{BASE_URL}/api/v1/security/currentAuthorization/user", status=401
    )

    with pytest.raises(BlueRangeAuthError):
        await client(hass).async_get_current_user()


async def test_missing_permission_raises_response_error(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A 403 is not fixed by a new token, so it is not an auth failure."""
    aioclient_mock.post(f"{BASE_URL}/api/v1/iot/sensor/sensorInfo/query", status=403)

    with pytest.raises(BlueRangeResponseError):
        await client(hass).async_get_sensors(DEVICE_UUID)


async def test_actuator_permissions_missing_reports_writable_permission(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A 422 with ``ACTUATOR_PERMISSIONS_MISSING`` is translated to a clear message."""
    aioclient_mock.post(
        f"{BASE_URL}/api/v1/iot/actuator/actuatorData/action",
        status=422,
        json={
            "className": "io.relution.common.exception.RelutionException",
            "errorCode": "ACTUATOR_PERMISSIONS_MISSING",
        },
    )

    definition = ControlDefinition(control_type="SET_EXTERNAL_TEMPERATURE", index=0)
    with pytest.raises(BlueRangeResponseError, match="write actuators"):
        await client(hass).async_set_actuator(DEVICE_UUID, definition, 21.5)


async def test_login_page_instead_of_json_raises_auth_error(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """An unauthenticated request may be answered with the portal itself."""
    aioclient_mock.get(
        f"{BASE_URL}/api/v1/security/currentAuthorization/user",
        text="<html>login</html>",
        headers={"Content-Type": "text/html"},
    )

    with pytest.raises(BlueRangeAuthError):
        await client(hass).async_get_current_user()


async def test_error_status_in_payload_raises(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The wrapper reports failures without using an HTTP error."""
    aioclient_mock.post(
        f"{BASE_URL}/api/v1/iot/actuator/actuatorInfo/query",
        json={"status": "42", "message": "nope"},
    )

    with pytest.raises(BlueRangeResponseError, match="nope"):
        await client(hass).async_get_actuators(DEVICE_UUID)


async def test_unreachable_server_raises_connection_error(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A transport failure is reported as a connection problem."""
    aioclient_mock.get(
        f"{BASE_URL}/api/v1/security/currentAuthorization/user",
        exc=TimeoutError,
    )

    with pytest.raises(BlueRangeConnectionError):
        await client(hass).async_get_current_user()


async def test_every_request_is_scoped_to_the_organisation(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Without the parameter the server picks an organisation of its own."""
    mock_bluerange_api(aioclient_mock)
    scoped = BlueRangeClient(async_get_clientsession(hass), BASE_URL, TOKEN, "org-7")

    await scoped.async_get_devices()
    await scoped.async_get_sensors(DEVICE_UUID)
    await scoped.async_get_actuators(DEVICE_UUID)
    await scoped.async_set_actuator(
        DEVICE_UUID, ControlDefinition(control_type="SET_X"), 1
    )
    await scoped.async_get_mqtt_parameters("user-1", "org-7")

    assert aioclient_mock.mock_calls
    for _, url, _, _ in aioclient_mock.mock_calls:
        assert url.query["tenantOrganizationUuid"] == "org-7", url


async def test_the_account_lookup_is_not_scoped(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The user record lives in its home organisation.

    Scoping its lookup to another tenant organisation makes the server answer
    with 404, which would take the whole setup down.
    """
    mock_bluerange_api(aioclient_mock)
    scoped = BlueRangeClient(async_get_clientsession(hass), BASE_URL, TOKEN, "org-7")

    await scoped.async_get_current_user()
    await scoped.async_get_organizations()

    for _, url, _, _ in aioclient_mock.mock_calls:
        assert "tenantOrganizationUuid" not in url.query, url


async def test_requests_carry_no_organisation_until_one_is_chosen(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The config flow lists organisations before it can scope to one."""
    mock_bluerange_api(aioclient_mock)

    organizations = await client(hass).async_get_organizations()

    assert [organization.uuid for organization in organizations] == ["org-1"]
    assert "tenantOrganizationUuid" not in aioclient_mock.mock_calls[-1][1].query


async def test_organisation_label_qualifies_duplicates() -> None:
    """Two organisations may share a display name."""
    plain = BlueRangeOrganization.from_json({"uuid": "o1", "name": "Acme"})
    duplicate = BlueRangeOrganization.from_json(
        {"uuid": "o2", "name": "Acme", "uniqueName": "acme-eu", "duplicateName": True}
    )

    assert plain is not None
    assert duplicate is not None
    assert plain.label == "Acme"
    assert duplicate.label == "Acme (acme-eu)"


def test_organisation_without_uuid_is_skipped() -> None:
    """An organisation that cannot be addressed is unusable."""
    assert BlueRangeOrganization.from_json({"name": "nameless"}) is None
