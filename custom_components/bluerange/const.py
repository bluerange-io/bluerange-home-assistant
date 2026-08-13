"""Constants for the BlueRange integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "bluerange"

MANUFACTURER: Final = "BlueRange"

# Configuration / options keys that have no counterpart in homeassistant.const.
CONF_ORGANIZATION: Final = "organization"

DEFAULT_SCAN_INTERVAL: Final = 60
MIN_SCAN_INTERVAL: Final = 15
MAX_SCAN_INTERVAL: Final = 3600

# The device inventory (and with it the sensor/actuator metadata of every device)
# changes rarely, so it is only refreshed every couple of update cycles.
DEVICE_REFRESH_INTERVAL: Final = timedelta(minutes=15)

# The server answers one request per device, so requests are throttled to keep
# larger installations from hammering the API.
MAX_PARALLEL_REQUESTS: Final = 6

CONF_USE_MQTT: Final = "use_mqtt"
DEFAULT_USE_MQTT: Final = True

# While live updates arrive over MQTT, polling is only a safety net. It is never
# made more frequent than what the user configured.
MQTT_FALLBACK_SCAN_INTERVAL: Final = 900

# Readings can arrive in bursts, so the resulting state writes are collapsed.
PUSH_FLUSH_DELAY: Final = 1.0

SERVICE_SET_ACTUATOR: Final = "set_actuator"

ATTR_CONTROL_TYPE: Final = "control_type"
ATTR_INDEX: Final = "index"
ATTR_MODULE: Final = "module"
ATTR_VALUE: Final = "value"
