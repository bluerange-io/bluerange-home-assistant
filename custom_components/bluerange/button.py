"""Button platform of the BlueRange integration."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .controls import ButtonSpec, DeviceLayout
from .coordinator import BlueRangeConfigEntry, BlueRangeDataUpdateCoordinator
from .entity import BlueRangeControlEntity, BlueRangeEntity, async_add_device_entities
from .mapping import BUTTON_TRANSLATION_KEYS, actuator_label, config_entity_category

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BlueRangeConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the BlueRange buttons."""
    coordinator = entry.runtime_data

    def _build(device_uuid: str, layout: DeviceLayout) -> list[BlueRangeEntity]:
        return [
            BlueRangeButton(coordinator, device_uuid, spec) for spec in layout.buttons
        ]

    async_add_device_entities(coordinator, async_add_entities, _build)


class BlueRangeButton(BlueRangeControlEntity, ButtonEntity):
    """A momentary command of a BlueRange device.

    The catalog models such commands as an actuator whose minimum and maximum are
    the same single value, for example a valve adaptation run or a host reset.
    """

    def __init__(
        self,
        coordinator: BlueRangeDataUpdateCoordinator,
        device_uuid: str,
        spec: ButtonSpec,
    ) -> None:
        """Initialise the button from its BlueRange metadata."""
        super().__init__(
            coordinator,
            device_uuid,
            spec.actuator,
            kind="button",
            translation_keys=BUTTON_TRANSLATION_KEYS,
            label=actuator_label,
        )
        self._spec = spec
        self._attr_entity_category = config_entity_category(spec.actuator.control_type)

    async def async_press(self) -> None:
        """Send the command to the device."""
        await self.coordinator.async_send_actuator(
            self._device_uuid, self._spec.actuator, self._spec.value
        )
        await self.coordinator.async_request_refresh()
