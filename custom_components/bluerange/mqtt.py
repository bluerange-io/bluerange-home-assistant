"""Live sensor updates from the BlueRange MQTT broker.

The gateways publish every sensor reading to the broker the server also listens
on, so subscribing to it gives the same values without polling for them:

``rltn-iot/{organizationUuid}/{siteUuid}/{deviceUuid}/sensor/{type}/{index}/sensorData``

Two properties of that stream shape this module:

* messages are published with QoS 0 and **without** the retain flag, so a client
  that just connected sees nothing until the next reading arrives.  The current
  state therefore still has to be fetched over REST once, and polling stays on as
  a slow fallback.
* the broker grants a token read access to ``rltn-iot/<organisation>/#``, so one
  subscription covers every device and the site of a device need not be known.

paho runs its own network thread, so every message is handed to the event loop
with :meth:`~asyncio.loop.call_soon_threadsafe`.
"""

from __future__ import annotations

from collections.abc import Callable
import logging
import ssl
from typing import Any, Final
from urllib.parse import urlparse

from paho.mqtt import MQTTException
import paho.mqtt.client as paho

from homeassistant.core import HomeAssistant, callback
from homeassistant.util.json import JSON_DECODE_EXCEPTIONS, json_loads

from .api import MqttParameters, SensorReading

_LOGGER = logging.getLogger(__name__)

# Position of the parts of a sensor topic once split on ``/``:
# rltn-iot / organisation / site / device / sensor / type / index / sensorData
_TOPIC_PREFIX: Final = "rltn-iot"
_TOPIC_LENGTH: Final = 8
_TOPIC_DEVICE_UUID: Final = 3
_TOPIC_KIND: Final = 4
_TOPIC_CONTROL_TYPE: Final = 5
_TOPIC_CONTROL_INDEX: Final = 6
_TOPIC_KIND_SENSOR: Final = "sensor"
_TOPIC_SUFFIX_SENSOR_DATA: Final = "sensorData"

#: Broker URI schemes, in the Java client spelling the server hands out.
_TLS_SCHEMES: Final = ("ssl", "tls", "mqtts", "wss", "https")
_WEBSOCKET_SCHEMES: Final = ("ws", "wss")

_DEFAULT_PORT_PLAIN: Final = 1883
_DEFAULT_PORT_TLS: Final = 8883
_KEEPALIVE: Final = 60

type ReadingCallback = Callable[[str, str, int, SensorReading], None]


def parse_sensor_topic(topic: str) -> tuple[str, str, int] | None:
    """Return device UUID, control type and index of a sensor data topic.

    The topic is the authoritative source for the address, because a payload may
    leave the fields out.  Anything that is not a sensor reading - actuator
    echoes, device operations, diagnostics - yields ``None``.
    """
    parts = topic.split("/")
    if len(parts) != _TOPIC_LENGTH or parts[0] != _TOPIC_PREFIX:
        return None
    if parts[_TOPIC_KIND] != _TOPIC_KIND_SENSOR:
        return None
    if parts[-1] != _TOPIC_SUFFIX_SENSOR_DATA:
        return None

    device_uuid = parts[_TOPIC_DEVICE_UUID]
    control_type = parts[_TOPIC_CONTROL_TYPE]
    if not device_uuid or not control_type:
        return None
    try:
        index = int(parts[_TOPIC_CONTROL_INDEX])
    except ValueError:
        return None
    return device_uuid, control_type, index


class BlueRangeMqttListener:
    """Keeps one subscription to the BlueRange broker alive."""

    def __init__(
        self,
        hass: HomeAssistant,
        parameters: MqttParameters,
        on_reading: ReadingCallback,
        on_availability: Callable[[bool], None],
    ) -> None:
        """Initialise the listener."""
        self._hass = hass
        self._parameters = parameters
        self._on_reading = on_reading
        self._on_availability = on_availability
        self._client: paho.Client | None = None

    @property
    def topic(self) -> str:
        """Return the topic filter this listener subscribes to."""
        return self._parameters.subscribe_topic

    async def async_start(self) -> bool:
        """Connect and subscribe, returning whether the attempt was accepted.

        Only the initial handshake is awaited; paho reconnects on its own
        afterwards, so a broker that goes away later does not need handling here.
        """
        uri = _first_supported_uri(self._parameters.server_uris)
        if uri is None:
            _LOGGER.warning(
                "None of the MQTT brokers the server named can be used: %s",
                ", ".join(self._parameters.server_uris),
            )
            return False

        # Setting up TLS reads the certificate store from disk and connecting
        # blocks, so the whole client is built in an executor.
        def _connect() -> paho.Client:
            client = paho.Client(
                paho.CallbackAPIVersion.VERSION2,
                client_id=self._parameters.client_id,
                transport="websockets" if uri.websocket else "tcp",
            )
            client.username_pw_set(self._parameters.username, self._parameters.password)
            if uri.tls:
                client.tls_set(cert_reqs=ssl.CERT_REQUIRED)
            client.on_connect = self._on_connect
            client.on_disconnect = self._on_disconnect
            client.on_message = self._on_message
            client.reconnect_delay_set(min_delay=1, max_delay=300)
            client.connect(uri.host, uri.port, _KEEPALIVE)
            return client

        try:
            client = await self._hass.async_add_executor_job(_connect)
        except (OSError, MQTTException) as err:
            _LOGGER.warning("Could not reach the MQTT broker at %s: %s", uri, err)
            return False

        client.loop_start()
        self._client = client
        _LOGGER.debug("Listening for live updates on %s", self.topic)
        return True

    async def async_stop(self) -> None:
        """Unsubscribe and disconnect."""
        client = self._client
        if client is None:
            return
        self._client = None
        client.disconnect()
        await self._hass.async_add_executor_job(client.loop_stop)

    def _on_connect(
        self,
        client: paho.Client,
        userdata: Any,
        flags: Any,
        reason_code: Any,
        properties: Any = None,
    ) -> None:
        """Subscribe once the broker accepted the connection (paho thread)."""
        if reason_code != 0:
            _LOGGER.warning("The MQTT broker refused the connection: %s", reason_code)
            self._hass.loop.call_soon_threadsafe(self._on_availability, False)
            return
        client.subscribe(self.topic, qos=0)
        self._hass.loop.call_soon_threadsafe(self._on_availability, True)

    def _on_disconnect(
        self,
        client: paho.Client,
        userdata: Any,
        flags: Any,
        reason_code: Any = None,
        properties: Any = None,
    ) -> None:
        """Fall back to polling while the broker is away (paho thread)."""
        _LOGGER.debug("Disconnected from the MQTT broker: %s", reason_code)
        self._hass.loop.call_soon_threadsafe(self._on_availability, False)

    def _on_message(
        self, client: paho.Client, userdata: Any, message: paho.MQTTMessage
    ) -> None:
        """Hand one reading over to the event loop (paho thread)."""
        address = parse_sensor_topic(message.topic)
        if address is None:
            return
        try:
            payload = json_loads(message.payload)
        except JSON_DECODE_EXCEPTIONS:
            _LOGGER.debug("Ignoring a malformed payload on %s", message.topic)
            return
        if not isinstance(payload, dict):
            return

        device_uuid, control_type, index = address
        reading = SensorReading.from_json(payload)
        self._hass.loop.call_soon_threadsafe(
            self._deliver, device_uuid, control_type, index, reading
        )

    @callback
    def _deliver(
        self, device_uuid: str, control_type: str, index: int, reading: SensorReading
    ) -> None:
        """Pass a reading on, keeping paho's thread out of Home Assistant."""
        self._on_reading(device_uuid, control_type, index, reading)


class _BrokerUri:
    """One usable broker address, normalised for paho."""

    def __init__(self, host: str, port: int, *, tls: bool, websocket: bool) -> None:
        """Initialise the address."""
        self.host = host
        self.port = port
        self.tls = tls
        self.websocket = websocket

    def __str__(self) -> str:
        """Return the address for log messages."""
        scheme = (
            "wss"
            if self.websocket and self.tls
            else "ws"
            if self.websocket
            else ("ssl" if self.tls else "tcp")
        )
        return f"{scheme}://{self.host}:{self.port}"


def _first_supported_uri(server_uris: tuple[str, ...]) -> _BrokerUri | None:
    """Return the first broker address that can be used, preferring plain MQTT."""
    candidates = [parsed for uri in server_uris if (parsed := _parse_uri(uri))]
    if not candidates:
        return None
    # A native connection is cheaper than tunnelling through websockets.
    candidates.sort(key=lambda candidate: candidate.websocket)
    return candidates[0]


def _parse_uri(server_uri: str) -> _BrokerUri | None:
    """Turn one broker URI into host, port and transport."""
    parsed = urlparse(server_uri)
    if not parsed.hostname:
        return None
    scheme = (parsed.scheme or "").lower()
    tls = scheme in _TLS_SCHEMES
    websocket = scheme in _WEBSOCKET_SCHEMES
    port = parsed.port or (_DEFAULT_PORT_TLS if tls else _DEFAULT_PORT_PLAIN)
    return _BrokerUri(parsed.hostname, port, tls=tls, websocket=websocket)
