"""Switch platform of the BlueRange integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import coerce_bool
from .controls import DeviceLayout, SwitchSpec
from .coordinator import BlueRangeConfigEntry, BlueRangeDataUpdateCoordinator
from .entity import (
    BlueRangeControlEntity,
    BlueRangeEntity,
    OptimisticValue,
    async_add_device_entities,
)
from .mapping import SWITCH_TRANSLATION_KEYS, actuator_label, config_entity_category

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BlueRangeConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the BlueRange switches."""
    coordinator = entry.runtime_data

    def _build(device_uuid: str, layout: DeviceLayout) -> list[BlueRangeEntity]:
        return [
            BlueRangeSwitch(coordinator, device_uuid, spec) for spec in layout.switches
        ]

    async_add_device_entities(coordinator, async_add_entities, _build)


class BlueRangeSwitch(BlueRangeControlEntity, SwitchEntity):
    """An on/off actuator of a BlueRange device."""

    def __init__(
        self,
        coordinator: BlueRangeDataUpdateCoordinator,
        device_uuid: str,
        spec: SwitchSpec,
    ) -> None:
        """Initialise the switch from its BlueRange metadata."""
        super().__init__(
            coordinator,
            device_uuid,
            spec.actuator,
            kind="switch",
            translation_keys=SWITCH_TRANSLATION_KEYS,
            label=actuator_label,
        )
        self._spec = spec
        self._optimistic = OptimisticValue()
        # Without a sensor reporting the state back there is nothing to confirm a
        # write against, so Home Assistant offers both commands separately.
        self._attr_assumed_state = spec.state is None
        self._attr_entity_category = config_entity_category(spec.actuator.control_type)

    @property
    def is_on(self) -> bool | None:
        """Return whether the actuator is currently switched on."""
        reading = self.reading(self._spec.state)
        reported = None if reading is None else coerce_bool(reading.value)
        value = self._optimistic.apply(reported)
        return None if value is None else bool(value)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Switch the actuator on."""
        await self._async_write(True, self._spec.on_value)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Switch the actuator off."""
        await self._async_write(False, self._spec.off_value)

    async def _async_write(self, state: bool, value: Any) -> None:
        """Send a value and show it until the server confirms it."""
        await self.coordinator.async_send_actuator(
            self._device_uuid, self._spec.actuator, value
        )
        self._optimistic.set(state)
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()
