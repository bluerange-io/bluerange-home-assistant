"""Binary sensor platform of the BlueRange integration."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import ControlDefinition, coerce_bool
from .controls import DeviceLayout
from .coordinator import BlueRangeConfigEntry, BlueRangeDataUpdateCoordinator
from .entity import BlueRangeControlEntity, BlueRangeEntity, async_add_device_entities
from .mapping import (
    BINARY_SENSOR_DEVICE_CLASSES,
    BINARY_SENSOR_TRANSLATION_KEYS,
    entity_category,
    humanize,
)

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BlueRangeConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the BlueRange binary sensors."""
    coordinator = entry.runtime_data

    def _build(device_uuid: str, layout: DeviceLayout) -> list[BlueRangeEntity]:
        return [
            BlueRangeBinarySensor(coordinator, device_uuid, definition)
            for definition in layout.binary_sensors
        ]

    async_add_device_entities(coordinator, async_add_entities, _build)


class BlueRangeBinarySensor(BlueRangeControlEntity, BinarySensorEntity):
    """One on/off value of a BlueRange device."""

    def __init__(
        self,
        coordinator: BlueRangeDataUpdateCoordinator,
        device_uuid: str,
        definition: ControlDefinition,
    ) -> None:
        """Initialise the binary sensor from its BlueRange metadata."""
        super().__init__(
            coordinator,
            device_uuid,
            definition,
            kind="binary_sensor",
            translation_keys=BINARY_SENSOR_TRANSLATION_KEYS,
            label=humanize,
        )
        self._attr_device_class = BINARY_SENSOR_DEVICE_CLASSES.get(
            definition.control_type
        )
        self._attr_entity_category = entity_category(definition.control_type)

    @property
    def is_on(self) -> bool | None:
        """Return whether the sensor currently reports an active state."""
        reading = self.reading(self._definition)
        return None if reading is None else coerce_bool(reading.value)
