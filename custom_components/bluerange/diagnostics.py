"""Diagnostics support for the BlueRange integration."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_ACCESS_TOKEN
from homeassistant.core import HomeAssistant

from .coordinator import BlueRangeConfigEntry

TO_REDACT = {CONF_ACCESS_TOKEN}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: BlueRangeConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data
    data = coordinator.data

    devices: list[dict[str, Any]] = []
    if data is not None:
        for uuid, device in data.devices.items():
            controls = data.controls.get(uuid)
            layout = data.layouts.get(uuid)
            devices.append(
                {
                    "device": asdict(device),
                    "sensors": sorted(controls.sensors) if controls else [],
                    "actuators": sorted(controls.actuators) if controls else [],
                    "readings": (
                        {
                            key: reading.value
                            for key, reading in controls.readings.items()
                        }
                        if controls
                        else {}
                    ),
                    "entities": _describe_layout(layout) if layout else {},
                }
            )

    return {
        "entry": {
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": dict(entry.options),
        },
        "live_updates": coordinator.push_available,
        "update_interval": str(coordinator.update_interval),
        "devices": devices,
    }


def _describe_layout(layout: Any) -> dict[str, list[str]]:
    """Summarise which entities a device was classified into."""
    return {
        "climate": [spec.setpoint.key for spec in layout.climates],
        "light": [spec.dimming.key for spec in layout.lights],
        # A cover gathers several controls, so all of them are listed to show
        # which ones were recognised as belonging to the blind.
        "cover": [
            " + ".join(
                definition.key
                for definition in (
                    spec.position,
                    spec.tilt,
                    spec.open_command,
                    spec.close_command,
                    spec.drive,
                    spec.stop,
                )
                if definition is not None
            )
            for spec in layout.covers
        ],
        "switch": [spec.actuator.key for spec in layout.switches],
        "number": [spec.actuator.key for spec in layout.numbers],
        "button": [spec.actuator.key for spec in layout.buttons],
        "sensor": [definition.key for definition in layout.sensors],
        "binary_sensor": [definition.key for definition in layout.binary_sensors],
    }
