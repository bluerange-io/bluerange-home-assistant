"""A stand-in BlueRange server for testing the integration end to end.

Serves the five endpoints the integration uses, with one thermostat, one
dimmable luminaire and one venetian blind, and denies MQTT so that the polling
fallback is exercised.  Sensor values drift on every query so that successive
polls can be told apart.

Run inside the Home Assistant container:

    python3 /config/fake_bluerange.py
"""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import sys
import time

PORT = 9099
TOKEN = "dev-token"

# Set to True to hand out broker parameters instead of denying MQTT.
MQTT_ENABLED = True

THERMOSTAT = "device-thermostat"
LUMINAIRE = "device-luminaire"
BLIND = "device-blind"

REQUESTS: list[str] = []

# Actuator writes land here so the effect of a service call can be checked.
WRITES: list[dict] = []

# Sensor values, mutated by writes and drifting on every poll.
STATE = {
    THERMOSTAT: {
        "ACTUAL_TEMPERATURE[0]": 20.5,
        "SETPOINT_TEMPERATURE[0]": 21.0,
        "BATTERY[0]": 87,
        "VALVE_POSITION[0]": 35,
        "CHILD_PROTECTION[0]": 0,
        "HEATING_OFF[0]": 0,
        "ENERGY_SAVING_MODE[0]": 0,
        "MAX_TEMPERATURE[0]": 24.0,
        "MESH_ACCESS_DURATION[0]": 120,
        "ERROR_FLAGS[0]": 0,
    },
    LUMINAIRE: {
        "BRIGHTNESS[0]": 40,
        "TASK_TUNING[0]": 100,
        "CHANNEL_1[0]": 40,
        "PRESENCE[0]": 1,
        "TEMPERATURE[0]": 22.4,
        "HUMIDITY[0]": 41,
        "CO2[0]": 620,
        "PRESENT_AMBIENT_LIGHT_LEVEL[0]": 310,
        "TOTAL_DEVICE_ENERGY_USE[0]": 14200,
        "PRESENT_DEVICE_INPUT_POWER[0]": 18.5,
    },
    # Counted the way BlueRange counts a blind: 0 is driven all the way up.
    BLIND: {
        "SLAT_POSITION[0]": 30,
        "SLAT_ANGLE[0]": 80,
        "CONFIG_TRAVEL_TIME_UP[0]": 42,
        "MOTOR_STATE[0]": 0,
    },
}

UNITS = {
    "ACTUAL_TEMPERATURE": "CELSIUS",
    "SETPOINT_TEMPERATURE": "CELSIUS",
    "MAX_TEMPERATURE": "CELSIUS",
    "TEMPERATURE": "CELSIUS",
    "BATTERY": "PERCENT",
    "VALVE_POSITION": "PERCENT",
    "BRIGHTNESS": "PERCENT",
    "TASK_TUNING": "PERCENT",
    "CHANNEL_1": "PERCENT",
    "HUMIDITY": "PERCENT",
    "CHILD_PROTECTION": "ON_OFF",
    "HEATING_OFF": "ON_OFF",
    "ENERGY_SAVING_MODE": "ON_OFF",
    "PRESENCE": "ON_OFF",
    "CO2": "PPM",
    "PRESENT_AMBIENT_LIGHT_LEVEL": "LUX",
    "TOTAL_DEVICE_ENERGY_USE": "WATT_HOUR",
    "PRESENT_DEVICE_INPUT_POWER": "WATT",
    "MESH_ACCESS_DURATION": "SECOND",
    "SLAT_POSITION": "PERCENT",
    "SLAT_ANGLE": "PERCENT",
    "CONFIG_TRAVEL_TIME_UP": "SECOND",
}

MODULES = {THERMOSTAT: "euro", LUMINAIRE: "vs", BLIND: "blind"}

ACTUATORS = {
    THERMOSTAT: [
        ("SET_SETPOINT_TEMPERATURE", "CELSIUS", 8, 28),
        ("SET_HEATING_MODE", "ON_OFF", 2, 2),
        ("SET_HEATING_OFF", "ON_OFF", 8, 8),
        ("SET_ENERGY_SAVING_MODE", "ON_OFF", 4, 4),
        ("SET_CHILD_PROTECTION", "ON_OFF", 0, 1),
        ("SET_MAX_TEMPERATURE", "CELSIUS", 8, 28),
        ("TRIGGER_ADAPTATION", "ON_OFF", 1, 1),
        ("RESET_HOST", "ON_OFF", 1, 1),
    ],
    LUMINAIRE: [
        ("SET_DIMMING", "PERCENT", 0, 100),
        ("TURN_ON", None, 1, 1),
        ("TURN_OFF", None, 1, 1),
        ("SET_AUTO", None, 1, 1),
        ("SET_VENDOR_TEST_DEBUG", "ON_OFF", 0, 1),
    ],
    BLIND: [
        ("SET_SLAT_POSITION", "PERCENT", 0, 100),
        ("SET_SLAT_ANGLE", "PERCENT", 0, 100),
        # The motor commands are pinned to the value that triggers them.
        ("REQUEST_UP", "ON_OFF", 2, 2),
        ("REQUEST_DOWN", "ON_OFF", 3, 3),
        ("REQUEST_STOP", "ON_OFF", 1, 1),
        ("REQUEST_STEP_UP", "ON_OFF", 4, 4),
        ("REQUEST_STEP_DOWN", "ON_OFF", 5, 5),
        ("SET_CONFIG_TRAVEL_TIME_UP", "SECOND", 1, 600),
    ],
}

DEVICES = [
    {
        "uuid": THERMOSTAT,
        "deviceId": "FM-0001",
        "name": "Thermostat Office",
        "status": "COMPLIANT",
        "platform": "MESH_NODE",
        "manufacturer": {"uuid": "m-1", "name": "BlueRange"},
        "model": "BlueRange Thermostat",
        "fwVersion": 260010030,
        "nodeId": 42,
        "lastConnectionDate": 1767225600000,
        "site": {"uuid": "s-1", "name": "HQ"},
        "building": {"uuid": "b-1", "name": "Main"},
        "floor": {"uuid": "f-1", "name": "Ground floor"},
        "room": {"uuid": "r-1", "name": "Office"},
    },
    {
        "uuid": LUMINAIRE,
        "deviceId": "FM-0002",
        "name": "Luminaire Hallway",
        "status": "COMPLIANT",
        "platform": "MESH_NODE",
        "manufacturer": {"uuid": "m-2", "name": "BlueRange"},
        "model": "BlueRange Luminaire",
        "fwVersion": 260010030,
        "nodeId": 43,
        "lastConnectionDate": 1767225600000,
        "site": {"uuid": "s-1", "name": "HQ"},
        "building": {"uuid": "b-1", "name": "Main"},
        "floor": {"uuid": "f-1", "name": "Ground floor"},
        "room": {"uuid": "r-2", "name": "Hallway"},
    },
    {
        "uuid": BLIND,
        "deviceId": "FM-0003",
        "name": "Blind Office",
        "status": "COMPLIANT",
        "platform": "MESH_NODE",
        "manufacturer": {"uuid": "m-3", "name": "BlueRange"},
        "model": "BlueRange Blind",
        "fwVersion": 260010030,
        "nodeId": 44,
        "lastConnectionDate": 1767225600000,
        "site": {"uuid": "s-1", "name": "HQ"},
        "building": {"uuid": "b-1", "name": "Main"},
        "floor": {"uuid": "f-1", "name": "Ground floor"},
        "room": {"uuid": "r-1", "name": "Office"},
    },
    # Must not show up in Home Assistant.
    {"uuid": "device-gone", "name": "Old node", "status": "WITHDRAWN"},
]

# Which sensor a write is reflected in, and how the written value maps onto it.
WRITE_EFFECTS = {
    "SET_SETPOINT_TEMPERATURE": ("SETPOINT_TEMPERATURE", lambda v, _: v),
    "SET_MAX_TEMPERATURE": ("MAX_TEMPERATURE", lambda v, _: v),
    "SET_CHILD_PROTECTION": ("CHILD_PROTECTION", lambda v, _: v),
    "SET_HEATING_OFF": ("HEATING_OFF", lambda v, _: 1),
    "SET_HEATING_MODE": ("HEATING_OFF", lambda v, _: 0),
    "SET_ENERGY_SAVING_MODE": ("ENERGY_SAVING_MODE", lambda v, _: 1),
    "SET_DIMMING": ("BRIGHTNESS", lambda v, _: v),
    "TURN_ON": ("BRIGHTNESS", lambda v, old: 100),
    "TURN_OFF": ("BRIGHTNESS", lambda v, old: 0),
    "SET_SLAT_POSITION": ("SLAT_POSITION", lambda v, _: v),
    "SET_SLAT_ANGLE": ("SLAT_ANGLE", lambda v, _: v),
    # A real blind would take its time to get to the end position.
    "REQUEST_UP": ("SLAT_POSITION", lambda v, _: 0),
    "REQUEST_DOWN": ("SLAT_POSITION", lambda v, _: 100),
    "SET_CONFIG_TRAVEL_TIME_UP": ("CONFIG_TRAVEL_TIME_UP", lambda v, _: v),
}


def sensor_results(device_uuid: str) -> list[dict]:
    """Return sensorInfo records for one device, with its current values."""
    now = int(time.time() * 1000)
    results = []
    for key, value in STATE.get(device_uuid, {}).items():
        control_type, index = key.rstrip("]").split("[")
        results.append(
            {
                "type": control_type,
                "index": int(index),
                "module": [MODULES[device_uuid]],
                "unit": UNITS.get(control_type),
                "lastSensorData": {
                    "deviceUuid": device_uuid,
                    "type": control_type,
                    "index": int(index),
                    "value": value,
                    "timestamp": now,
                },
            }
        )
    return results


def actuator_results(device_uuid: str) -> list[dict]:
    """Return actuatorInfo records for one device."""
    return [
        {
            "type": control_type,
            "index": 0,
            "module": [MODULES[device_uuid]],
            "unit": unit,
            "min": minimum,
            "max": maximum,
        }
        for control_type, unit, minimum, maximum in ACTUATORS.get(device_uuid, [])
    ]


def drift() -> None:
    """Move the free running measurements a little on every poll."""
    thermostat = STATE[THERMOSTAT]
    thermostat["ACTUAL_TEMPERATURE[0]"] = round(
        thermostat["ACTUAL_TEMPERATURE[0]"] + 0.1, 1
    )
    luminaire = STATE[LUMINAIRE]
    luminaire["CO2[0]"] += 5
    luminaire["TOTAL_DEVICE_ENERGY_USE[0]"] += 3


def apply_write(body: dict) -> None:
    """Reflect an actuator write in the sensor it reads back through."""
    WRITES.append(body)
    control_type = body.get("type")
    effect = WRITE_EFFECTS.get(control_type)
    if effect is None:
        return
    sensor, convert = effect
    for device_uuid in body.get("deviceUuids", []):
        values = STATE.get(device_uuid)
        key = f"{sensor}[{body.get('index', 0)}]"
        if values is not None and key in values:
            values[key] = convert(body.get("value"), values[key])


class Handler(BaseHTTPRequestHandler):
    """Answers the endpoints the integration calls."""

    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: object) -> None:
        """Keep the console readable."""

    def _authorized(self) -> bool:
        return self.headers.get("X-User-Access-Token") == TOKEN

    def _organization(self) -> str | None:
        """Return the organisation the request selected, if any."""
        if "?" not in self.path:
            return None
        for pair in self.path.split("?", 1)[1].split("&"):
            key, _, value = pair.partition("=")
            if key == "tenantOrganizationUuid":
                return value
        return None

    def _send(self, status: int, payload: object) -> None:
        body = json.dumps(payload).encode() if payload is not None else b""
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _wrap(self, results: list) -> None:
        self._send(200, {"status": "0", "results": results})

    def do_GET(self) -> None:
        """Serve the credential check, the MQTT denial and the debug dumps."""
        path = self.path.split("?")[0]
        REQUESTS.append(f"GET {self.path}")

        if path == "/debug":
            self._send(200, {"requests": REQUESTS, "writes": WRITES, "state": STATE})
            return
        if not self._authorized():
            self._send(401, {"status": "1", "message": "bad token"})
            return
        if path == "/api/v1/security/currentAuthorization/user":
            self._send(
                200,
                {
                    "uuid": "user-1",
                    "name": "dev",
                    "organizationUuid": "org-1",
                    "organizationName": "Acme",
                },
            )
            return
        if path == "/api/v1/security/tenantOrganizations":
            self._wrap(
                [
                    {"uuid": "org-1", "name": "Acme", "uniqueName": "acme"},
                    {
                        "uuid": "org-2",
                        "name": "Acme",
                        "uniqueName": "acme-eu",
                        "duplicateName": True,
                    },
                ]
            )
            return
        if path == "/api/v1/iot/mqtt":
            if not MQTT_ENABLED:
                # Stands for a server without a broker configured.
                self._send(501, {"status": "1", "message": "MQTT is not configured"})
                return
            # A TLS broker nothing listens on, to exercise the connect path.
            self._send(
                200,
                {
                    "serverURIs": ["ssl://127.0.0.1:18883"],
                    "clientId": "Token-user-1-homeassistant",
                    "variables": {"organizationUuid": self._organization() or "org-1"},
                },
            )
            return
        self._send(404, {"status": "1", "message": "unknown"})

    def do_POST(self) -> None:
        """Serve the queries and the actuator writes."""
        path = self.path.split("?")[0]
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        REQUESTS.append(f"POST {self.path} {json.dumps(body, sort_keys=True)}")

        if not self._authorized():
            self._send(401, {"status": "1", "message": "bad token"})
            return

        if path == "/api/v2/iot/devices/baseInfo/query":
            if self._organization() != "org-1":
                self._wrap([])
                return
            self._wrap(DEVICES if body.get("offset", 0) == 0 else [])
            return
        if path == "/api/v1/iot/sensor/sensorInfo/query":
            uuids = body.get("deviceUuids") or []
            drift()
            self._wrap([r for u in uuids for r in sensor_results(u)])
            return
        if path == "/api/v1/iot/actuator/actuatorInfo/query":
            uuids = body.get("deviceUuids") or []
            self._wrap([r for u in uuids for r in actuator_results(u)])
            return
        if path == "/api/v1/iot/actuator/actuatorData/action":
            apply_write(body)
            self._send(204, None)
            return
        self._send(404, {"status": "1", "message": "unknown"})


if __name__ == "__main__":
    with open("/config/fake_bluerange.pid", "w") as handle:
        handle.write(str(os.getpid()))
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"fake BlueRange on http://127.0.0.1:{PORT}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        sys.exit(0)
