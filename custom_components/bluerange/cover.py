"""Cover platform of the BlueRange integration."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.cover import (
    ATTR_POSITION,
    ATTR_TILT_POSITION,
    CoverDeviceClass,
    CoverEntity,
    CoverEntityFeature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import ControlDefinition, coerce_float
from .const import DOMAIN
from .controls import CoverSpec, DeviceLayout
from .coordinator import BlueRangeConfigEntry, BlueRangeDataUpdateCoordinator
from .entity import (
    BlueRangeEntity,
    OptimisticValue,
    async_add_device_entities,
    command_value,
)

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 0

#: The platforms an actuator of a blind was an entity of before the cover
#: existed, and the ones a reading of it was.  The entity kind is the first part
#: of the unique id, and happens to be the platform it belongs to.
_ACTUATOR_KINDS = ("button", "number", "switch")
_SENSOR_KINDS = ("binary_sensor", "sensor")

#: The two ends of the BlueRange height scale, which counts how far a blind is
#: driven down rather than how far it is open.
_DRIVEN_UP = 0.0
_DRIVEN_DOWN = 100.0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BlueRangeConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the BlueRange covers."""
    coordinator = entry.runtime_data

    def _build(device_uuid: str, layout: DeviceLayout) -> list[BlueRangeEntity]:
        for spec in layout.covers:
            _async_remove_superseded(hass, device_uuid, spec)
        return [
            BlueRangeCover(coordinator, device_uuid, spec) for spec in layout.covers
        ]

    async_add_device_entities(coordinator, async_add_entities, _build)


@callback
def _async_remove_superseded(
    hass: HomeAssistant, device_uuid: str, spec: CoverSpec
) -> None:
    """Remove what the controls of a blind were entities of on their own.

    Before the cover existed, every control of a blind was an entity of its own:
    the height and the angle as numbers, the motor commands as buttons.  Home
    Assistant keeps an entity that is no longer provided in its registry, where
    it would sit as unavailable forever, so the ones this cover took over are
    removed.  Only controls the cover actually claimed are looked for, and one
    that never had an entity of its own is simply not found.
    """
    registry = er.async_get(hass)
    superseded = [
        (kind, definition.key)
        for kinds, definitions in (
            (
                _ACTUATOR_KINDS,
                (
                    spec.position,
                    spec.tilt,
                    spec.open_command,
                    spec.close_command,
                    spec.drive,
                    spec.stop,
                ),
            ),
            (_SENSOR_KINDS, (spec.position_state, spec.tilt_state)),
        )
        for kind in kinds
        for definition in definitions
        if definition is not None
    ]
    for kind, key in superseded:
        if entity_id := registry.async_get_entity_id(
            kind, DOMAIN, f"{device_uuid}_{kind}_{key}"
        ):
            _LOGGER.debug("Removing %s, which is part of a cover now", entity_id)
            registry.async_remove(entity_id)


class BlueRangeCover(BlueRangeEntity, CoverEntity):
    """A blind of a BlueRange device.

    BlueRange counts how far a blind is driven down - 0 % is all the way up and
    100 % all the way down - while Home Assistant counts how far a cover is open,
    so the height is inverted in both directions.

    The slat angle is not inverted, because it is not an axis from closed to
    open: 0 % has the slats closed upwards, 50 % has them horizontal and 100 %
    has them closed downwards again.  It is therefore passed through as the tilt
    position, showing the same number Home Assistant and BlueRange both write,
    and the blind is not offered a tilt to open or close to, which would have to
    pick one of the two closed ends.
    """

    def __init__(
        self,
        coordinator: BlueRangeDataUpdateCoordinator,
        device_uuid: str,
        spec: CoverSpec,
    ) -> None:
        """Initialise the cover from its BlueRange metadata."""
        # Keyed by the channel rather than by one of the controls, because which
        # of them a blind offers may change without the blind becoming another.
        super().__init__(coordinator, device_uuid, f"cover_{spec.index}")
        self._spec = spec
        self._optimistic_position = OptimisticValue()
        self._optimistic_tilt = OptimisticValue()

        if spec.index:
            self._attr_translation_key = "channel"
            self._attr_translation_placeholders = {"index": str(spec.index + 1)}
        else:
            # The blind is what the device is, so it carries the device name.
            self._attr_name = None

        features = CoverEntityFeature(0)
        if spec.can_open:
            features |= CoverEntityFeature.OPEN
        if spec.can_close:
            features |= CoverEntityFeature.CLOSE
        if spec.position is not None:
            features |= CoverEntityFeature.SET_POSITION
        if spec.stop is not None:
            features |= CoverEntityFeature.STOP
        if spec.supports_tilt:
            features |= CoverEntityFeature.SET_TILT_POSITION
        self._attr_supported_features = features

        # Slats are what tells a venetian blind from a plain shutter.
        self._attr_device_class = (
            CoverDeviceClass.BLIND if spec.supports_tilt else CoverDeviceClass.SHUTTER
        )

        # Without a height reported back, where the blind ended up is whatever
        # Home Assistant last asked for.
        self._attr_assumed_state = spec.position_state is None

    @property
    def current_cover_position(self) -> int | None:
        """Return how far the blind is open, on the Home Assistant scale."""
        driven = self._reported(self._spec.position_state, self._optimistic_position)
        if driven is None:
            return None
        return round(_DRIVEN_DOWN - _clamped(driven))

    @property
    def is_closed(self) -> bool | None:
        """Return whether the blind is driven all the way down."""
        position = self.current_cover_position
        return None if position is None else position == 0

    @property
    def current_cover_tilt_position(self) -> int | None:
        """Return the angle of the slats, as BlueRange counts it."""
        turned = self._reported(self._spec.tilt_state, self._optimistic_tilt)
        return None if turned is None else round(_clamped(turned))

    async def async_open_cover(self, **kwargs: Any) -> None:
        """Drive the blind up."""
        await self._async_drive(_DRIVEN_UP)

    async def async_close_cover(self, **kwargs: Any) -> None:
        """Drive the blind down."""
        await self._async_drive(_DRIVEN_DOWN)

    async def async_set_cover_position(self, **kwargs: Any) -> None:
        """Drive the blind to a height."""
        position = kwargs.get(ATTR_POSITION)
        if position is None or self._spec.position is None:
            return
        driven = _DRIVEN_DOWN - _clamped(float(position))
        await self._async_send(self._spec.position, driven)
        self._optimistic_position.set(driven)
        await self._async_settle()

    async def async_set_cover_tilt_position(self, **kwargs: Any) -> None:
        """Turn the slats to an angle."""
        tilt = kwargs.get(ATTR_TILT_POSITION)
        if tilt is None or self._spec.tilt is None:
            return
        turned = _clamped(float(tilt))
        await self._async_send(self._spec.tilt, turned)
        self._optimistic_tilt.set(turned)
        await self._async_settle()

    async def async_stop_cover(self, **kwargs: Any) -> None:
        """Halt the blind wherever it currently is."""
        if self._spec.stop is None:
            return
        await self._async_send(self._spec.stop, command_value(self._spec.stop))
        # Where the blind came to rest is only known once it reports back, so
        # nothing is assumed in the meantime.
        self._optimistic_position.clear()
        self._optimistic_tilt.clear()
        await self._async_settle()

    async def _async_drive(self, percent: float) -> None:
        """Drive the blind to one of its end positions.

        A command for the direction is preferred over writing the end position,
        because it is what a blind without a position to drive to offers.
        """
        command = (
            self._spec.open_command
            if percent <= _DRIVEN_UP
            else self._spec.close_command
        )
        if command is not None:
            await self._async_send(command, command_value(command))
        elif self._spec.drive is not None:
            await self._async_send(
                self._spec.drive, _direction_value(self._spec.drive, percent)
            )
        elif self._spec.position is not None:
            await self._async_send(self._spec.position, percent)
        else:
            return
        # A blind takes seconds to travel, so the end position is shown until it
        # reports back, the same as for any other value written from here.
        self._optimistic_position.set(percent)
        await self._async_settle()

    async def _async_send(self, definition: ControlDefinition, value: Any) -> None:
        """Write one actuator of this blind."""
        await self.coordinator.async_send_actuator(self._device_uuid, definition, value)

    async def _async_settle(self) -> None:
        """Show what was written and ask the server for the real value."""
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()

    def _reported(
        self, definition: ControlDefinition | None, optimistic: OptimisticValue
    ) -> float | None:
        """Return one percentage of this blind, as BlueRange counts it."""
        reading = self.reading(definition)
        reported = None if reading is None else coerce_float(reading.value)
        value = optimistic.apply(reported)
        return None if value is None else float(value)


def _clamped(percent: float) -> float:
    """Return a percentage inside the range a blind is driven in."""
    return max(_DRIVEN_UP, min(_DRIVEN_DOWN, percent))


def _direction_value(drive: ControlDefinition, percent: float) -> Any:
    """Return the value the combined direction command takes to drive one way.

    The command names the two ends of its range, the lower one driving up.
    """
    if percent <= _DRIVEN_UP:
        return drive.minimum if drive.minimum is not None else 0
    return drive.maximum if drive.maximum is not None else 1
