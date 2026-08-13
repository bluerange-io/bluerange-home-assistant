"""Sensor platform of the BlueRange integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    EntityCategory,
    UnitOfElectricPotential,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType

from .api import BlueRangeDevice, ControlDefinition, coerce_float
from .controls import DeviceLayout
from .coordinator import BlueRangeConfigEntry, BlueRangeDataUpdateCoordinator
from .entity import BlueRangeControlEntity, BlueRangeEntity, async_add_device_entities
from .mapping import SENSOR_TRANSLATION_KEYS, entity_category, humanize, sensor_metadata

PARALLEL_UPDATES = 0

#: The enrollment states a device may report, as Home Assistant enum options.
DEVICE_STATUS_OPTIONS = [
    "compliant",
    "noncompliant",
    "inactive",
    "enrollment_pending",
    "withdraw_pending",
    "withdrawn",
    "deletion_pending",
    "deleted",
]


@dataclass(frozen=True, kw_only=True)
class BlueRangeDeviceSensorDescription(SensorEntityDescription):
    """Describes a sensor that reads a property of the device itself."""

    value_fn: Callable[[BlueRangeDevice], StateType | datetime]


def _status(device: BlueRangeDevice) -> str | None:
    """Return the enrollment state as a Home Assistant enum option."""
    if device.status is None:
        return None
    status = device.status.lower()
    return status if status in DEVICE_STATUS_OPTIONS else None


#: Properties of the device record itself, rather than of one of its controls.
#: The radio and power figures are only reported by mesh nodes, so an entity is
#: created for them once a device actually carries the value.
DEVICE_SENSORS: tuple[BlueRangeDeviceSensorDescription, ...] = (
    BlueRangeDeviceSensorDescription(
        key="device_status",
        translation_key="device_status",
        device_class=SensorDeviceClass.ENUM,
        options=DEVICE_STATUS_OPTIONS,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_status,
    ),
    BlueRangeDeviceSensorDescription(
        key="last_connection",
        translation_key="last_connection",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda device: device.last_connection,
    ),
    BlueRangeDeviceSensorDescription(
        key="battery_voltage",
        translation_key="battery_voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda device: device.battery_voltage,
    ),
    # The two radio figures are how the node is set up rather than something it
    # measures, so they get no state class and would only clutter statistics.
    BlueRangeDeviceSensorDescription(
        key="transmit_power",
        translation_key="transmit_power",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda device: device.transmit_power,
    ),
    BlueRangeDeviceSensorDescription(
        key="calibrated_rssi",
        translation_key="calibrated_rssi",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda device: device.calibrated_rssi,
    ),
)

#: Sensors that describe the device record and are always worth creating.
_ALWAYS_CREATED = frozenset({"device_status", "last_connection"})


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BlueRangeConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the BlueRange sensors."""
    coordinator = entry.runtime_data

    def _build(device_uuid: str, layout: DeviceLayout) -> list[BlueRangeEntity]:
        entities: list[BlueRangeEntity] = []
        device = coordinator.data.devices.get(device_uuid) if coordinator.data else None
        for description in DEVICE_SENSORS:
            reported = device is not None and description.value_fn(device) is not None
            if description.key in _ALWAYS_CREATED or reported:
                entities.append(
                    BlueRangeDeviceSensor(coordinator, device_uuid, description)
                )
        entities.extend(
            BlueRangeSensor(coordinator, device_uuid, definition)
            for definition in layout.sensors
        )
        return entities

    async_add_device_entities(coordinator, async_add_entities, _build)


class BlueRangeSensor(BlueRangeControlEntity, SensorEntity):
    """One readable value of a BlueRange device."""

    def __init__(
        self,
        coordinator: BlueRangeDataUpdateCoordinator,
        device_uuid: str,
        definition: ControlDefinition,
    ) -> None:
        """Initialise the sensor from its BlueRange metadata."""
        super().__init__(
            coordinator,
            device_uuid,
            definition,
            kind="sensor",
            translation_keys=SENSOR_TRANSLATION_KEYS,
            label=humanize,
        )
        meta = sensor_metadata(definition.control_type, definition.unit)

        # Anything with a unit is a measurement; without one, a declared bound is
        # the only hint that the value is numeric rather than a status code.  One
        # bound is enough - a battery voltage is published with a lower bound and
        # no upper one, and is no less a measurement for it.
        bounded = definition.minimum is not None or definition.maximum is not None
        self._numeric = meta.unit is not None or bounded
        state_class = meta.state_class
        if state_class is None and meta.unit is None and bounded:
            state_class = SensorStateClass.MEASUREMENT

        self._attr_native_unit_of_measurement = meta.unit
        self._attr_device_class = meta.device_class
        self._attr_state_class = state_class
        self._attr_suggested_display_precision = meta.precision
        self._attr_entity_category = entity_category(definition.control_type)

    @property
    def native_value(self) -> StateType:
        """Return the value last reported for this sensor."""
        reading = self.reading(self._definition)
        if reading is None:
            return None
        if self._numeric:
            return coerce_float(reading.value)
        value = reading.value
        if value is None or isinstance(value, (str, int, float)):
            return value
        return str(value)


class BlueRangeDeviceSensor(BlueRangeEntity, SensorEntity):
    """One property of the device record itself."""

    entity_description: BlueRangeDeviceSensorDescription

    def __init__(
        self,
        coordinator: BlueRangeDataUpdateCoordinator,
        device_uuid: str,
        description: BlueRangeDeviceSensorDescription,
    ) -> None:
        """Initialise the sensor."""
        super().__init__(coordinator, device_uuid, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> StateType | datetime:
        """Return the property this sensor reads."""
        device = self.bluerange_device
        return None if device is None else self.entity_description.value_fn(device)
