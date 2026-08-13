"""Light platform of the BlueRange integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.light import ATTR_BRIGHTNESS, ColorMode, LightEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import ControlDefinition, coerce_float
from .controls import DeviceLayout, LightSpec
from .coordinator import BlueRangeConfigEntry, BlueRangeDataUpdateCoordinator
from .entity import (
    BlueRangeEntity,
    OptimisticValue,
    async_add_device_entities,
    command_value,
)

PARALLEL_UPDATES = 0

#: Brightness level a light is switched to when no level was requested.
_FULL_BRIGHTNESS_PERCENT = 100.0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BlueRangeConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the BlueRange lights."""
    coordinator = entry.runtime_data

    def _build(device_uuid: str, layout: DeviceLayout) -> list[BlueRangeEntity]:
        return [
            BlueRangeLight(coordinator, device_uuid, spec) for spec in layout.lights
        ]

    async_add_device_entities(coordinator, async_add_entities, _build)


class BlueRangeLight(BlueRangeEntity, LightEntity):
    """A dimmable channel of a BlueRange luminaire.

    BlueRange works in percent while Home Assistant works in levels from 0 to
    255, so the value is scaled in both directions.
    """

    _attr_color_mode = ColorMode.BRIGHTNESS
    _attr_supported_color_modes = {ColorMode.BRIGHTNESS}

    def __init__(
        self,
        coordinator: BlueRangeDataUpdateCoordinator,
        device_uuid: str,
        spec: LightSpec,
    ) -> None:
        """Initialise the light from its BlueRange metadata."""
        super().__init__(coordinator, device_uuid, f"light_{spec.dimming.key}")
        self._spec = spec
        self._optimistic = OptimisticValue()
        if spec.index:
            self._attr_translation_key = "channel"
            self._attr_translation_placeholders = {"index": str(spec.index + 1)}
        else:
            # The luminaire is what the device is, so the light carries its name.
            self._attr_name = None
        self._attr_assumed_state = spec.brightness is None

    @property
    def is_on(self) -> bool | None:
        """Return whether the light currently emits any light."""
        percent = self._brightness_percent
        return None if percent is None else percent > 0

    @property
    def brightness(self) -> int | None:
        """Return the brightness on the Home Assistant scale."""
        percent = self._brightness_percent
        if percent is None:
            return None
        return round(max(0.0, min(100.0, percent)) * 255 / 100)

    @property
    def _brightness_percent(self) -> float | None:
        """Return the brightness in percent as BlueRange reports it."""
        reading = self.reading(self._spec.brightness)
        reported = None if reading is None else coerce_float(reading.value)
        value = self._optimistic.apply(reported)
        return None if value is None else float(value)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Switch the light on, optionally at a given brightness."""
        brightness = kwargs.get(ATTR_BRIGHTNESS)
        if brightness is not None:
            percent = round(int(brightness) * 100 / 255)
            await self._async_set_percent(float(percent))
            return

        if self._spec.turn_on is not None:
            await self._async_send(
                self._spec.turn_on, command_value(self._spec.turn_on)
            )
        elif self._spec.on_off is not None:
            await self._async_send(
                self._spec.on_off,
                self._spec.on_off.maximum
                if self._spec.on_off.maximum is not None
                else 1,
            )
        else:
            await self._async_set_percent(_FULL_BRIGHTNESS_PERCENT)
            return

        # Without a dimming write there is no percentage to be optimistic about,
        # so full output is assumed until the device reports back.
        self._optimistic.set(_FULL_BRIGHTNESS_PERCENT)
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Switch the light off."""
        if self._spec.turn_off is not None:
            await self._async_send(
                self._spec.turn_off, command_value(self._spec.turn_off)
            )
        elif self._spec.on_off is not None:
            await self._async_send(
                self._spec.on_off,
                self._spec.on_off.minimum
                if self._spec.on_off.minimum is not None
                else 0,
            )
        else:
            await self._async_set_percent(0.0)
            return

        self._optimistic.set(0.0)
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()

    async def _async_set_percent(self, percent: float) -> None:
        """Dim the light to a percentage and show it until confirmed."""
        await self._async_send(self._spec.dimming, percent)
        self._optimistic.set(percent)
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()

    async def _async_send(self, definition: ControlDefinition, value: Any) -> None:
        """Write one actuator of this light."""
        await self.coordinator.async_send_actuator(self._device_uuid, definition, value)
