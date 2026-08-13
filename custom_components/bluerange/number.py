"""Number platform of the BlueRange integration."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import coerce_float
from .controls import DeviceLayout, NumberSpec
from .coordinator import BlueRangeConfigEntry, BlueRangeDataUpdateCoordinator
from .entity import (
    BlueRangeControlEntity,
    BlueRangeEntity,
    OptimisticValue,
    async_add_device_entities,
)
from .mapping import (
    NUMBER_DEVICE_CLASSES,
    NUMBER_TRANSLATION_KEYS,
    UNBOUNDED_NUMBER_MAX,
    actuator_label,
    config_entity_category,
    number_step,
    sensor_metadata,
)

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BlueRangeConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the BlueRange numbers."""
    coordinator = entry.runtime_data

    def _build(device_uuid: str, layout: DeviceLayout) -> list[BlueRangeEntity]:
        return [
            BlueRangeNumber(coordinator, device_uuid, spec) for spec in layout.numbers
        ]

    async_add_device_entities(coordinator, async_add_entities, _build)


class BlueRangeNumber(BlueRangeControlEntity, NumberEntity):
    """An adjustable setpoint of a BlueRange device."""

    def __init__(
        self,
        coordinator: BlueRangeDataUpdateCoordinator,
        device_uuid: str,
        spec: NumberSpec,
    ) -> None:
        """Initialise the number from its BlueRange metadata."""
        actuator = spec.actuator
        super().__init__(
            coordinator,
            device_uuid,
            actuator,
            kind="number",
            translation_keys=NUMBER_TRANSLATION_KEYS,
            label=actuator_label,
        )
        self._spec = spec
        self._optimistic = OptimisticValue()

        meta = sensor_metadata(actuator.control_type, actuator.unit)
        self._attr_native_unit_of_measurement = meta.unit
        self._attr_device_class = NUMBER_DEVICE_CLASSES.get(actuator.unit or "")
        self._attr_native_step = number_step(
            actuator.unit, actuator.minimum, actuator.maximum
        )
        self._attr_entity_category = config_entity_category(actuator.control_type)

        if actuator.minimum is None or actuator.maximum is None:
            # Without a published range the server is the only authority on what
            # is acceptable, so free input is offered instead of a slider.
            self._attr_native_min_value = (
                actuator.minimum if actuator.minimum is not None else 0.0
            )
            self._attr_native_max_value = (
                actuator.maximum
                if actuator.maximum is not None
                else UNBOUNDED_NUMBER_MAX
            )
            self._attr_mode = NumberMode.BOX
        else:
            self._attr_native_min_value = actuator.minimum
            self._attr_native_max_value = actuator.maximum
            self._attr_mode = NumberMode.AUTO

        self._attr_assumed_state = spec.state is None

    @property
    def native_value(self) -> float | None:
        """Return the value the actuator is currently set to."""
        reading = self.reading(self._spec.state)
        reported = None if reading is None else coerce_float(reading.value)
        value = self._optimistic.apply(reported)
        return None if value is None else float(value)

    async def async_set_native_value(self, value: float) -> None:
        """Send a new setpoint and show it until the server confirms it."""
        await self.coordinator.async_send_actuator(
            self._device_uuid, self._spec.actuator, value
        )
        self._optimistic.set(value)
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()
