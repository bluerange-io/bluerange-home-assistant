"""Minimal async client for the BlueRange IoT REST API.

The BlueRange server exposes its IoT domain under ``/api/v1/iot/...`` and
``/api/v2/iot/...``.  Only the handful of endpoints needed to mirror devices into
Home Assistant are implemented here:

======================================================  =============================
``POST /api/v2/iot/devices/baseInfo/query``             device inventory
``POST /api/v1/iot/sensor/sensorInfo/query``            sensor metadata + last values
``POST /api/v1/iot/actuator/actuatorInfo/query``        actuator metadata
``POST /api/v1/iot/actuator/actuatorData/action``       write a setpoint
``GET  /api/v1/iot/mqtt``                               broker access for live updates
``GET  /api/v1/security/currentAuthorization/user``     credential check
======================================================  =============================

Authentication uses a long lived API token that is created in the BlueRange
portal on the user profile page and is passed as ``X-User-Access-Token``.  That
header name, and the ``rltn-`` prefix of the MQTT topics, still carry the
product's former name Relution.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
import logging
from typing import Any, Final, Self

from aiohttp import ClientError, ClientResponse, ClientSession
from yarl import URL

from homeassistant.util.json import JSON_DECODE_EXCEPTIONS, json_loads

_LOGGER = logging.getLogger(__name__)

TOKEN_HEADER: Final = "X-User-Access-Token"
REQUEST_TIMEOUT: Final = 30

_EP_CURRENT_USER: Final = "api/v1/security/currentAuthorization/user"
_EP_DEVICES: Final = "api/v2/iot/devices/baseInfo/query"
_EP_SENSOR_INFO: Final = "api/v1/iot/sensor/sensorInfo/query"
_EP_ACTUATOR_INFO: Final = "api/v1/iot/actuator/actuatorInfo/query"
_EP_ACTUATOR_ACTION: Final = "api/v1/iot/actuator/actuatorData/action"
_EP_MQTT: Final = "api/v1/iot/mqtt"
_EP_TENANT_ORGANIZATIONS: Final = "api/v1/security/tenantOrganizations"

# Selects which of the organisations a token may reach a request applies to.
# Without it the server falls back to the home organisation of the token.
_PARAM_TENANT_ORGANIZATION: Final = "tenantOrganizationUuid"

# The account and the organisations it may reach are not part of any single
# organisation: the user record lives in its home organisation, so scoping its
# lookup to another tenant organisation makes the server report it as missing.
_UNSCOPED_ENDPOINTS: Final = frozenset({_EP_CURRENT_USER, _EP_TENANT_ORGANIZATIONS})

_PAGE_SIZE: Final = 200
_MAX_DEVICES: Final = 5000

# The client id has to start with ``Token-<user uuid>-`` for the server to hand
# out native MQTT access to an API token; the rest of it is free.
MQTT_CLIENT_SUFFIX: Final = "homeassistant"

# ``fields`` tells the server which optional parts of the metadata to compute.
# ``lastSensorData`` is the part that actually carries the current value.
_SENSOR_FIELDS: Final = ["lastSensorData", "deviceUuids", "unit", "min", "max"]

# FruityMesh encodes its firmware version as MAJOR * 10^7 + MINOR * 10^4 + PATCH.
_FW_VERSION_MAJOR_FACTOR: Final = 10_000_000
_FW_VERSION_MINOR_FACTOR: Final = 10_000


class BlueRangeError(Exception):
    """Base class for all errors raised by the client."""


class BlueRangeConnectionError(BlueRangeError):
    """The server could not be reached."""


class BlueRangeAuthError(BlueRangeError):
    """The API token was rejected."""


class BlueRangeResponseError(BlueRangeError):
    """The server answered with an unexpected status or payload."""


def coerce_float(value: Any) -> float | None:
    """Return ``value`` as a float, or ``None`` if it is not numeric."""
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def coerce_bool(value: Any) -> bool | None:
    """Return ``value`` as a bool, or ``None`` if it carries no on/off meaning."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("1", "true", "on", "yes"):
            return True
        if lowered in ("0", "false", "off", "no"):
            return False
    return None


def normalize_write_value(value: Any) -> Any:
    """Return a setpoint in the plainest JSON form that carries its meaning.

    Whole numbers are sent as integers because the consumers on the device side
    may be stricter about the payload than JSON itself is.
    """
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def format_firmware_version(raw: Any) -> str | None:
    """Format the numeric FruityMesh firmware version as ``MAJOR.MINOR.PATCH``."""
    if raw is None:
        return None
    if not isinstance(raw, int) or isinstance(raw, bool):
        return str(raw)
    if raw < _FW_VERSION_MAJOR_FACTOR:
        # Not a FruityMesh style version - show it unchanged rather than guess.
        return str(raw)
    major, rest = divmod(raw, _FW_VERSION_MAJOR_FACTOR)
    minor, patch = divmod(rest, _FW_VERSION_MINOR_FACTOR)
    return f"{major}.{minor}.{patch}"


def _named(raw: Any) -> str | None:
    """Extract the ``name`` of an embedded BlueRange reference object."""
    if isinstance(raw, dict):
        name = raw.get("displayName") or raw.get("name")
        if isinstance(name, str) and name:
            return name
    return None


def _details(raw: dict[str, Any]) -> dict[str, Any]:
    """Return the platform specific details of a device record.

    Mesh nodes report their radio and power figures in here.  The object is
    polymorphic on ``platform``, so other kinds of device carry other fields and
    everything read from it has to be optional.
    """
    details = raw.get("details")
    return details if isinstance(details, dict) else {}


def _coerce_int(value: Any) -> int | None:
    """Return ``value`` as an int, or ``None`` if it is not a whole number."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    number = coerce_float(value)
    return None if number is None else round(number)


def _single_module(raw: Any) -> str | None:
    """Return the module name if a control belongs to exactly one module.

    Metadata results carry ``module`` as a set because they may be merged over
    several devices.  Only an unambiguous value can be used for writes.
    """
    if isinstance(raw, str):
        return raw or None
    if isinstance(raw, list) and len(raw) == 1 and isinstance(raw[0], str):
        return raw[0] or None
    return None


@dataclass(frozen=True, slots=True, kw_only=True)
class BlueRangeDevice:
    """A device as listed by the BlueRange device inventory."""

    uuid: str
    device_id: str | None = None
    name: str
    description: str | None = None
    manufacturer: str | None = None
    model: str | None = None
    hardware: str | None = None
    firmware_version: str | None = None
    gateway_version: str | None = None
    node_id: int | None = None
    mac_address: str | None = None
    battery_voltage: float | None = None
    transmit_power: int | None = None
    calibrated_rssi: int | None = None
    status: str | None = None
    platform: str | None = None
    site: str | None = None
    building: str | None = None
    floor: str | None = None
    room: str | None = None
    zone: str | None = None
    last_connection: datetime | None = None

    @property
    def suggested_area(self) -> str | None:
        """Return the most specific location known for this device."""
        return self.room or self.zone or self.floor or self.building or self.site

    @property
    def is_active(self) -> bool:
        """Return whether the device is still enrolled and in service."""
        return self.status not in (
            "WITHDRAW_PENDING",
            "WITHDRAWN",
            "DELETION_PENDING",
            "DELETED",
        )

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> Self | None:
        """Build a device from one ``baseInfo`` record, or ``None`` if unusable."""
        uuid = raw.get("uuid")
        if not isinstance(uuid, str) or not uuid:
            return None

        device_id = raw.get("deviceId")
        # ``model`` names the module a device is built around, such as nRF52,
        # which says nothing about what the device is.  The device catalog entry
        # is the actual product, so that is what belongs in the model field and
        # the module is reported as the hardware it runs on.
        hardware = raw.get("model")
        model = _named(raw.get("deviceRepositoryEntry")) or hardware
        if model == hardware:
            hardware = None
        name = (
            raw.get("displayName")
            or raw.get("name")
            or raw.get("description")
            or model
            or device_id
            or uuid
        )

        last_connection: datetime | None = None
        raw_last_connection = raw.get("lastConnectionDate")
        if isinstance(raw_last_connection, (int, float)) and raw_last_connection > 0:
            last_connection = datetime.fromtimestamp(raw_last_connection / 1000, tz=UTC)

        node_id = raw.get("nodeId")

        details = _details(raw)
        mac_address = details.get("macAddress")
        # Reported in tenths of a volt, and 0 by nodes that do not measure it.
        raw_battery = _coerce_int(details.get("batteryInfo"))
        battery_voltage = raw_battery / 10 if raw_battery else None

        return cls(
            uuid=uuid,
            device_id=device_id if isinstance(device_id, str) else None,
            name=str(name),
            description=raw.get("description"),
            manufacturer=_named(raw.get("manufacturer")),
            model=model,
            hardware=hardware,
            firmware_version=format_firmware_version(raw.get("fwVersion")),
            gateway_version=raw.get("gwVersion"),
            node_id=node_id if isinstance(node_id, int) else None,
            mac_address=mac_address if isinstance(mac_address, str) else None,
            battery_voltage=battery_voltage,
            transmit_power=_coerce_int(details.get("dBmTX")),
            calibrated_rssi=_coerce_int(details.get("calibratedRssi")),
            status=raw.get("status"),
            platform=raw.get("platform"),
            site=_named(raw.get("site")),
            building=_named(raw.get("building")),
            floor=_named(raw.get("floor")),
            room=_named(raw.get("room")),
            zone=_named(raw.get("zone")),
            last_connection=last_connection,
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class ControlDefinition:
    """Static metadata of a single sensor or actuator of a device.

    A control is addressed by its type together with its index, because devices
    such as multi channel luminaires expose the same type several times.
    """

    control_type: str
    index: int = 0
    module: str | None = None
    unit: str | None = None
    minimum: float | None = None
    maximum: float | None = None

    @property
    def key(self) -> str:
        """Return the identifier used to look this control up on its device."""
        return f"{self.control_type}[{self.index}]"

    @property
    def is_trigger(self) -> bool:
        """Return whether this actuator only accepts one fixed value.

        The catalog models momentary commands - resets, mode switches, boosts -
        by pinning ``min`` and ``max`` to the single value that may be sent.
        """
        return (
            self.minimum is not None
            and self.maximum is not None
            and self.minimum == self.maximum
        )

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> Self | None:
        """Build a control from one metadata record, or ``None`` if unusable."""
        control_type = raw.get("type")
        if not isinstance(control_type, str) or not control_type:
            return None
        index = raw.get("index")
        unit = raw.get("unit")
        return cls(
            control_type=control_type,
            index=index
            if isinstance(index, int) and not isinstance(index, bool)
            else 0,
            module=_single_module(raw.get("module")),
            unit=unit if isinstance(unit, str) and unit else None,
            minimum=coerce_float(raw.get("min")),
            maximum=coerce_float(raw.get("max")),
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class SensorReading:
    """The most recent value reported for a sensor."""

    value: Any
    timestamp: datetime | None = None

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> Self:
        """Build a reading from an embedded ``lastSensorData`` record."""
        timestamp: datetime | None = None
        raw_timestamp = raw.get("timestamp")
        if isinstance(raw_timestamp, (int, float)) and raw_timestamp > 0:
            timestamp = datetime.fromtimestamp(raw_timestamp / 1000, tz=UTC)
        return cls(value=raw.get("value"), timestamp=timestamp)


@dataclass(frozen=True, slots=True, kw_only=True)
class BlueRangeOrganization:
    """One organisation the API token has access to."""

    uuid: str
    name: str
    unique_name: str | None = None
    duplicate_name: bool = False

    @property
    def label(self) -> str:
        """Return a name that tells this organisation apart from the others."""
        if self.duplicate_name and self.unique_name:
            return f"{self.name} ({self.unique_name})"
        return self.name

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> Self | None:
        """Build an organisation from one record, or ``None`` if unusable."""
        uuid = raw.get("uuid")
        if not isinstance(uuid, str) or not uuid:
            return None
        name = raw.get("name") or raw.get("uniqueName") or uuid
        return cls(
            uuid=uuid,
            name=str(name),
            unique_name=raw.get("uniqueName"),
            duplicate_name=bool(raw.get("duplicateName")),
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class MqttParameters:
    """How to reach the MQTT broker the server publishes sensor data through."""

    server_uris: tuple[str, ...]
    client_id: str
    username: str
    password: str
    organization_uuid: str | None = None

    @property
    def subscribe_topic(self) -> str:
        """Return the one topic filter that covers the whole organisation.

        The broker grants ``rltn-iot/<organisation>/#`` to a token of that
        organisation, so a single subscription is enough and the site a device
        belongs to does not have to be known up front.
        """
        return f"rltn-iot/{self.organization_uuid}/#"


@dataclass(slots=True)
class DeviceControls:
    """Everything known about the controls of a single device."""

    sensors: dict[str, ControlDefinition]
    actuators: dict[str, ControlDefinition]
    readings: dict[str, SensorReading]

    def reading(self, definition: ControlDefinition | None) -> SensorReading | None:
        """Return the last reading of ``definition``, if there is one."""
        if definition is None:
            return None
        return self.readings.get(definition.key)


class BlueRangeClient:
    """Talks to one BlueRange server on behalf of one API token."""

    def __init__(
        self,
        session: ClientSession,
        base_url: str,
        token: str,
        organization_uuid: str | None = None,
    ) -> None:
        """Initialise the client.

        Every request carries ``organization_uuid`` so that all of them resolve
        in the same organisation.  A token may reach several, and which one the
        server picks by itself is not something to rely on.
        """
        self._session = session
        self._base_url = normalize_base_url(base_url)
        self._token = token
        self._organization_uuid = organization_uuid

    @property
    def base_url(self) -> str:
        """Return the normalised server URL."""
        return str(self._base_url)

    @property
    def host(self) -> str:
        """Return the host of the server, as used to identify an entry."""
        return self._base_url.host or str(self._base_url)

    @property
    def organization_uuid(self) -> str | None:
        """Return the organisation every request is scoped to."""
        return self._organization_uuid

    async def async_get_current_user(self) -> dict[str, Any]:
        """Return the user the API token belongs to.

        Used to validate credentials while configuring the integration.
        """
        result = await self._request("GET", _EP_CURRENT_USER)
        if not isinstance(result, dict):
            raise BlueRangeResponseError("Unexpected response for the current user")
        return result

    async def async_get_organizations(self) -> list[BlueRangeOrganization]:
        """Return the organisations the API token has access to."""
        payload = await self._request("GET", _EP_TENANT_ORGANIZATIONS)
        organizations = []
        for raw in _results(payload):
            if isinstance(raw, dict) and (
                organization := BlueRangeOrganization.from_json(raw)
            ):
                organizations.append(organization)
        return organizations

    async def async_get_devices(self) -> list[BlueRangeDevice]:
        """Return all devices of the organisation the token belongs to."""
        devices: list[BlueRangeDevice] = []
        offset = 0
        while offset < _MAX_DEVICES:
            payload = await self._request(
                "POST",
                _EP_DEVICES,
                json={
                    "limit": _PAGE_SIZE,
                    "offset": offset,
                    "getItems": True,
                    "getNonpagedCount": False,
                },
            )
            page = _results(payload)
            for raw in page:
                if isinstance(raw, dict) and (device := BlueRangeDevice.from_json(raw)):
                    devices.append(device)
            if len(page) < _PAGE_SIZE:
                break
            offset += _PAGE_SIZE
        return devices

    async def async_get_sensors(
        self, device_uuid: str
    ) -> tuple[dict[str, ControlDefinition], dict[str, SensorReading]]:
        """Return the sensor metadata and last values of one device.

        The query is restricted to a single device on purpose: the server merges
        metadata of every matched device into one record per type and index, so a
        broader query could not be attributed back to individual devices.
        """
        payload = await self._request(
            "POST",
            _EP_SENSOR_INFO,
            json={"deviceUuids": [device_uuid], "fields": _SENSOR_FIELDS},
        )
        definitions: dict[str, ControlDefinition] = {}
        readings: dict[str, SensorReading] = {}
        for raw in _results(payload):
            if not isinstance(raw, dict):
                continue
            definition = ControlDefinition.from_json(raw)
            if definition is None:
                continue
            definitions[definition.key] = definition
            last = raw.get("lastSensorData")
            if isinstance(last, dict):
                readings[definition.key] = SensorReading.from_json(last)
        return definitions, readings

    async def async_get_actuators(
        self, device_uuid: str
    ) -> dict[str, ControlDefinition]:
        """Return the actuator metadata of one device."""
        payload = await self._request(
            "POST",
            _EP_ACTUATOR_INFO,
            json={"deviceUuids": [device_uuid]},
        )
        definitions: dict[str, ControlDefinition] = {}
        for raw in _results(payload):
            if not isinstance(raw, dict):
                continue
            if definition := ControlDefinition.from_json(raw):
                definitions[definition.key] = definition
        return definitions

    async def async_set_actuator(
        self, device_uuid: str, definition: ControlDefinition, value: Any
    ) -> None:
        """Send a setpoint to one actuator of one device."""
        body: dict[str, Any] = {
            "deviceUuids": [device_uuid],
            "type": definition.control_type,
            "index": definition.index,
            "value": normalize_write_value(value),
        }
        if definition.module:
            body["module"] = definition.module
        await self._request("POST", _EP_ACTUATOR_ACTION, json=body)

    async def async_get_mqtt_parameters(
        self, user_uuid: str, organization_uuid: str | None
    ) -> MqttParameters | None:
        """Return broker access for live updates, or ``None`` if there is none.

        Native MQTT is only handed out to clients whose id marks them as API
        token based; anything else is told to enroll as a device first.  For such
        a client the server deliberately leaves the password empty, because it is
        the API token the client already holds.

        A server without MQTT configured answers with an error rather than with
        parameters, and the endpoint refuses plain HTTP, so both cases are
        reported as "no live updates available" instead of as a failure.
        """
        client_id = f"Token-{user_uuid}-{MQTT_CLIENT_SUFFIX}"
        try:
            payload = await self._request(
                "GET",
                _EP_MQTT,
                params={"clientId": client_id, "protocol": "MQTT"},
            )
        except BlueRangeResponseError as err:
            _LOGGER.debug("The server offers no MQTT access: %s", err)
            return None

        if not isinstance(payload, dict):
            return None
        server_uris = payload.get("serverURIs")
        if not isinstance(server_uris, list) or not server_uris:
            _LOGGER.debug("The server did not name an MQTT broker")
            return None

        variables = payload.get("variables")
        organization = organization_uuid
        if isinstance(variables, dict) and variables.get("organizationUuid"):
            organization = variables["organizationUuid"]
        if not organization:
            _LOGGER.debug("The organisation is unknown, cannot subscribe")
            return None

        return MqttParameters(
            server_uris=tuple(uri for uri in server_uris if isinstance(uri, str)),
            client_id=payload.get("clientId") or client_id,
            # A deployment may configure one shared broker account, in which case
            # the server returns it instead of the token based one.
            username=payload.get("username") or client_id,
            password=payload.get("password") or self._token,
            organization_uuid=organization,
        )

    async def _request(
        self,
        method: str,
        endpoint: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
    ) -> Any:
        """Perform one API request and return the decoded payload."""
        url = self._base_url / endpoint
        query = dict(params or {})
        if self._organization_uuid and endpoint not in _UNSCOPED_ENDPOINTS:
            query[_PARAM_TENANT_ORGANIZATION] = self._organization_uuid
        if query:
            url = url.with_query(query)
        headers = {
            TOKEN_HEADER: self._token,
            "Accept": "application/json",
        }
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT):
                response = await self._session.request(
                    method, url, json=json, headers=headers
                )
                return await _decode(response)
        except TimeoutError as err:
            raise BlueRangeConnectionError(f"Timeout while calling {endpoint}") from err
        except ClientError as err:
            raise BlueRangeConnectionError(
                f"Error while calling {endpoint}: {err}"
            ) from err


def normalize_base_url(base_url: str) -> URL:
    """Return ``base_url`` as a URL without path, defaulting to HTTPS."""
    candidate = base_url.strip()
    if not candidate:
        raise BlueRangeError("Empty server URL")
    if "://" not in candidate:
        candidate = f"https://{candidate}"
    try:
        url = URL(candidate)
        if url.scheme not in ("http", "https"):
            raise BlueRangeError(f"Unsupported scheme in server URL: {base_url}")
        # yarl happily accepts a host containing spaces, which is what typing
        # something that is not an address at all ends up as.
        if not url.host or any(char.isspace() for char in url.host):
            raise BlueRangeError(f"Invalid server URL: {base_url}")
        # A pasted portal URL may carry a path or fragment - only the origin
        # is used, so that endpoints are appended to the bare server address.
        return url.origin()
    except ValueError as err:
        raise BlueRangeError(f"Invalid server URL: {base_url}") from err


def _extract_error_code(body: str) -> str | None:
    """Return the ``errorCode`` of a JSON error body, if any."""
    if not body:
        return None
    try:
        payload = json_loads(body)
    except JSON_DECODE_EXCEPTIONS:
        return None
    if isinstance(payload, dict):
        code = payload.get("errorCode")
        if isinstance(code, str):
            return code
    return None


async def _decode(response: ClientResponse) -> Any:
    """Turn an API response into a payload, mapping failures onto exceptions."""
    if response.status == 401:
        raise BlueRangeAuthError("The API token was rejected by the server")
    if response.status == 403:
        raise BlueRangeResponseError(
            "The API token lacks the permissions required to read IoT data"
        )
    if response.status >= 400:
        body = await response.text()
        if _extract_error_code(body) == "ACTUATOR_PERMISSIONS_MISSING":
            raise BlueRangeResponseError(
                "The API token lacks the permissions required to write actuators"
            )
        raise BlueRangeResponseError(
            f"HTTP {response.status} from server: {body[:200]}"
        )

    if response.status == 204:
        return None

    # Writes answer with an empty body, which is not JSON but not an error.
    body = (await response.text()).strip()
    if not body:
        return None

    try:
        return json_loads(body)
    except JSON_DECODE_EXCEPTIONS as err:
        if body.lower().startswith(("<!doctype", "<html")):
            # Requests without a valid token can be answered with the portal
            # login page instead of with an API error.
            raise BlueRangeAuthError(
                "The server answered with a web page instead of API data, "
                "which usually means the API token was not accepted"
            ) from err
        raise BlueRangeResponseError(
            "The server sent a malformed JSON payload"
        ) from err


def _results(payload: Any) -> list[Any]:
    """Extract the ``results`` list of a BlueRange response wrapper."""
    if payload is None:
        return []
    if not isinstance(payload, dict):
        raise BlueRangeResponseError("Expected a result wrapper object")
    status = payload.get("status")
    if status is not None and status != "0":
        message = payload.get("message") or payload.get("errors") or status
        raise BlueRangeResponseError(f"The server reported an error: {message}")
    results = payload.get("results")
    if results is None:
        return []
    if not isinstance(results, list):
        raise BlueRangeResponseError("Expected 'results' to be a list")
    return results
