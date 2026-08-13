"""Climate platform of the BlueRange integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.climate import (
    PRESET_ECO,
    PRESET_NONE,
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import ControlDefinition, coerce_bool, coerce_float
from .const import DOMAIN
from .controls import ClimateSpec, DeviceLayout
from .coordinator import BlueRangeConfigEntry, BlueRangeDataUpdateCoordinator
from .entity import (
    BlueRangeEntity,
    OptimisticValue,
    async_add_device_entities,
    command_value,
)

PARALLEL_UPDATES = 0

#: Fallback range used when the catalog does not publish setpoint limits.
_DEFAULT_MIN_TEMP = 5.0
_DEFAULT_MAX_TEMP = 30.0
_TARGET_STEP = 0.5


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BlueRangeConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the BlueRange thermostats."""
    coordinator = entry.runtime_data

    def _build(device_uuid: str, layout: DeviceLayout) -> list[BlueRangeEntity]:
        return [
            BlueRangeClimate(coordinator, device_uuid, spec) for spec in layout.climates
        ]

    async_add_device_entities(coordinator, async_add_entities, _build)


class BlueRangeClimate(BlueRangeEntity, ClimateEntity):
    """A temperature setpoint of a BlueRange device.

    Heating is switched by two separate commands - one selecting the heating mode
    and one turning heating off - each accepting a single fixed value, which is
    how the BlueRange device catalog models mode changes.
    """

    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_target_temperature_step = _TARGET_STEP

    def __init__(
        self,
        coordinator: BlueRangeDataUpdateCoordinator,
        device_uuid: str,
        spec: ClimateSpec,
    ) -> None:
        """Initialise the thermostat from its BlueRange metadata."""
        super().__init__(coordinator, device_uuid, f"climate_{spec.setpoint.key}")
        self._spec = spec
        self._optimistic_target = OptimisticValue()
        self._optimistic_off = OptimisticValue()
        self._optimistic_eco = OptimisticValue()

        if spec.index:
            self._attr_translation_key = "channel"
            self._attr_translation_placeholders = {"index": str(spec.index + 1)}
        else:
            # A thermostat is what the device is, so it carries the device name.
            self._attr_name = None

        self._attr_min_temp = (
            spec.setpoint.minimum
            if spec.setpoint.minimum is not None
            else _DEFAULT_MIN_TEMP
        )
        self._attr_max_temp = (
            spec.setpoint.maximum
            if spec.setpoint.maximum is not None
            else _DEFAULT_MAX_TEMP
        )

        features = ClimateEntityFeature.TARGET_TEMPERATURE
        if spec.supports_on_off:
            self._attr_hvac_modes = [HVACMode.HEAT, HVACMode.OFF]
            features |= ClimateEntityFeature.TURN_ON | ClimateEntityFeature.TURN_OFF
        else:
            self._attr_hvac_modes = [HVACMode.HEAT]
        if spec.supports_eco:
            self._attr_preset_modes = [PRESET_NONE, PRESET_ECO]
            features |= ClimateEntityFeature.PRESET_MODE
        self._attr_supported_features = features

        # Without a read back of the setpoint the shown target is whatever Home
        # Assistant last wrote.
        self._attr_assumed_state = spec.setpoint_state is None

    @property
    def current_temperature(self) -> float | None:
        """Return the temperature the device measures."""
        return self._value(self._spec.current)

    @property
    def target_temperature(self) -> float | None:
        """Return the setpoint the device is working towards."""
        reported = self._value(self._spec.setpoint_state)
        value = self._optimistic_target.apply(reported)
        return None if value is None else float(value)

    @property
    def hvac_mode(self) -> HVACMode:
        """Return whether heating is currently enabled."""
        if not self._spec.supports_on_off:
            return HVACMode.HEAT
        reported = self._flag(self._spec.heating_off_state)
        value = self._optimistic_off.apply(reported)
        return HVACMode.OFF if value else HVACMode.HEAT

    @property
    def hvac_action(self) -> HVACAction | None:
        """Return whether the valve is currently letting heat through."""
        if self.hvac_mode is HVACMode.OFF:
            return HVACAction.OFF
        valve = self._value(self._spec.valve)
        if valve is None:
            return None
        return HVACAction.HEATING if valve > 0 else HVACAction.IDLE

    @property
    def preset_mode(self) -> str | None:
        """Return whether the energy saving preset is active."""
        if not self._spec.supports_eco:
            return None
        reported = self._flag(self._spec.eco_state)
        value = self._optimistic_eco.apply(reported)
        return PRESET_ECO if value else PRESET_NONE

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Write a new setpoint."""
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            return
        await self.coordinator.async_send_actuator(
            self._device_uuid, self._spec.setpoint, float(temperature)
        )
        self._optimistic_target.set(float(temperature))
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Switch heating on or off."""
        if hvac_mode is HVACMode.OFF:
            await self.async_turn_off()
        elif hvac_mode is HVACMode.HEAT:
            await self.async_turn_on()
        else:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="unsupported_hvac_mode",
                translation_placeholders={"hvac_mode": str(hvac_mode)},
            )

    async def async_turn_on(self) -> None:
        """Select the heating mode."""
        if self._spec.heat is None:
            return
        await self._async_command(self._spec.heat)
        self._optimistic_off.set(False)
        self._optimistic_eco.set(False)
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self) -> None:
        """Stop heating."""
        if self._spec.heating_off is None:
            return
        await self._async_command(self._spec.heating_off)
        self._optimistic_off.set(True)
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Enable or leave the energy saving preset."""
        if preset_mode == PRESET_ECO and self._spec.eco is not None:
            await self._async_command(self._spec.eco)
            self._optimistic_eco.set(True)
            self._optimistic_off.set(False)
        elif preset_mode == PRESET_NONE and self._spec.heat is not None:
            await self._async_command(self._spec.heat)
            self._optimistic_eco.set(False)
            self._optimistic_off.set(False)
        else:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="unsupported_preset_mode",
                translation_placeholders={"preset_mode": preset_mode},
            )
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()

    async def _async_command(self, actuator: ControlDefinition) -> None:
        """Send a mode command, which carries the one value it accepts."""
        await self.coordinator.async_send_actuator(
            self._device_uuid, actuator, command_value(actuator)
        )

    def _value(self, definition: ControlDefinition | None) -> float | None:
        """Return the numeric value of one of this thermostat's sensors."""
        reading = self.reading(definition)
        return None if reading is None else coerce_float(reading.value)

    def _flag(self, definition: ControlDefinition | None) -> bool | None:
        """Return the on/off value of one of this thermostat's sensors."""
        reading = self.reading(definition)
        return None if reading is None else coerce_bool(reading.value)
