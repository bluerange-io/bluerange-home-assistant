"""Shared entity plumbing for the BlueRange integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from homeassistant.core import callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .api import BlueRangeDevice, ControlDefinition, DeviceControls, SensorReading
from .const import DOMAIN, MANUFACTURER
from .controls import DeviceLayout
from .coordinator import BlueRangeDataUpdateCoordinator

#: How long a value written from Home Assistant is shown before the read back
#: from the server takes over.  Setpoints travel to the device over the mesh, so
#: the value reported back can lag behind by several seconds.
OPTIMISTIC_TIMEOUT = timedelta(seconds=90)

#: Tolerance when comparing a written setpoint with the value reported back.
_FLOAT_TOLERANCE = 1e-6

#: Name placeholder every curated entity name of a control ends in, so that a
#: control on a channel other than the first can carry the channel number
#: without giving up its translated name.  It is empty for the first channel.
CHANNEL_PLACEHOLDER = "channel"


def hub_identifier(entry_id: str) -> tuple[str, str]:
    """Return the registry identifier of the organisation behind an entry.

    Every device of an entry is reached through its BlueRange organisation, which
    is registered as a service device so that Home Assistant can show it as the
    device they are connected through.
    """
    return (DOMAIN, entry_id)


class BlueRangeEntity(CoordinatorEntity[BlueRangeDataUpdateCoordinator]):
    """Base class for every entity backed by a BlueRange device."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: BlueRangeDataUpdateCoordinator,
        device_uuid: str,
        unique_key: str,
    ) -> None:
        """Initialise the entity."""
        super().__init__(coordinator)
        self._device_uuid = device_uuid
        self._attr_unique_id = f"{device_uuid}_{unique_key}"

    @property
    def bluerange_device(self) -> BlueRangeDevice | None:
        """Return the device this entity belongs to."""
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.devices.get(self._device_uuid)

    @property
    def controls(self) -> DeviceControls | None:
        """Return the controls currently known for this device."""
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.controls.get(self._device_uuid)

    @property
    def device_info(self) -> DeviceInfo | None:
        """Return the Home Assistant device this entity is attached to."""
        device = self.bluerange_device
        if device is None:
            return None
        info = DeviceInfo(
            identifiers={(DOMAIN, device.uuid)},
            name=device.name,
            manufacturer=device.manufacturer or MANUFACTURER,
            model=device.model,
            hw_version=device.hardware,
            sw_version=device.firmware_version,
            serial_number=device.device_id,
            suggested_area=device.suggested_area,
            configuration_url=self.coordinator.client.base_url,
            via_device=hub_identifier(self.coordinator.config_entry.entry_id),
        )
        if device.mac_address:
            # Lets Home Assistant tie this device to the same one seen over
            # Bluetooth, for example by the bluetooth integration.
            info["connections"] = {
                (dr.CONNECTION_BLUETOOTH, dr.format_mac(device.mac_address))
            }
        return info

    @property
    def available(self) -> bool:
        """Return whether the device is still part of the inventory."""
        return super().available and self.bluerange_device is not None

    def reading(self, definition: ControlDefinition | None) -> SensorReading | None:
        """Return the last value reported for one of this device's sensors."""
        controls = self.controls
        if controls is None:
            return None
        return controls.reading(definition)


class BlueRangeControlEntity(BlueRangeEntity):
    """Base class for an entity that represents one sensor or actuator."""

    def __init__(
        self,
        coordinator: BlueRangeDataUpdateCoordinator,
        device_uuid: str,
        definition: ControlDefinition,
        *,
        kind: str,
        translation_keys: dict[str, str],
        label: Callable[[str], str],
    ) -> None:
        """Initialise the entity and derive its name."""
        super().__init__(coordinator, device_uuid, f"{kind}_{definition.key}")
        self._definition = definition
        resolved = resolve_name(definition, translation_keys, label)
        if resolved.translation_key is not None:
            self._attr_translation_key = resolved.translation_key
            self._attr_translation_placeholders = resolved.placeholders
        else:
            self._attr_name = resolved.name

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose the BlueRange addressing of this control."""
        attributes: dict[str, Any] = {"control_type": self._definition.control_type}
        if self._definition.index:
            attributes["index"] = self._definition.index
        if self._definition.module:
            attributes["module"] = self._definition.module
        return attributes


class OptimisticValue:
    """Shows a locally written value until the server confirms it.

    BlueRange forwards a setpoint to the device asynchronously, so the value read
    back right after a write is usually still the old one.  Until the read back
    matches - or the timeout expires - the written value is reported instead.
    """

    def __init__(self) -> None:
        """Start out with nothing pending."""
        self._value: Any = None
        self._until: datetime | None = None

    def set(self, value: Any) -> None:
        """Remember a value that was just written."""
        self._value = value
        self._until = dt_util.utcnow() + OPTIMISTIC_TIMEOUT

    def clear(self) -> None:
        """Forget a previously written value."""
        self._value = None
        self._until = None

    def apply(self, reported: Any) -> Any:
        """Return either the pending written value or the reported one."""
        if self._until is None:
            return reported
        if dt_util.utcnow() >= self._until:
            self.clear()
            return reported
        if reported is not None and _equivalent(reported, self._value):
            self.clear()
            return reported
        return self._value


@dataclass(frozen=True, slots=True, kw_only=True)
class ControlName:
    """How one control is named in Home Assistant.

    Either a translation key together with the placeholders it takes, or a
    literal name for a control the integration has no curated name for.
    """

    translation_key: str | None = None
    placeholders: dict[str, str] = field(default_factory=dict)
    name: str | None = None


def resolve_name(
    definition: ControlDefinition,
    translation_keys: dict[str, str],
    label: Callable[[str], str],
) -> ControlName:
    """Return how a control is named in Home Assistant.

    Curated types get a translated name, anything else one derived from the
    BlueRange type.  Either way a control on a channel other than the first has
    the channel number appended, because a device exposing the same type several
    times would otherwise end up with several identically named entities.
    """
    channel = f" {definition.index + 1}" if definition.index else ""
    if key := translation_keys.get(definition.control_type):
        return ControlName(
            translation_key=key, placeholders={CHANNEL_PLACEHOLDER: channel}
        )
    return ControlName(name=f"{label(definition.control_type)}{channel}")


def command_value(actuator: ControlDefinition) -> Any:
    """Return the single value a momentary command accepts.

    The catalog pins ``min`` and ``max`` to that value; a command published
    without a range is triggered with a plain one.
    """
    return actuator.minimum if actuator.minimum is not None else 1


@callback
def async_add_device_entities(
    coordinator: BlueRangeDataUpdateCoordinator,
    async_add_entities: AddConfigEntryEntitiesCallback,
    build: Callable[[str, DeviceLayout], list[BlueRangeEntity]],
) -> None:
    """Add entities for all known devices and keep watching for new ones.

    Devices may be enrolled while Home Assistant is running, so the builder is
    re-run after every update cycle and anything not seen before is added.  The
    coordinator hands out the very same layout object as long as a device's set of
    controls is unchanged, which is what keeps the steady state cheap.
    """
    known: set[str] = set()
    processed: dict[str, DeviceLayout] = {}

    @callback
    def _discover() -> None:
        data = coordinator.data
        if data is None:
            return
        new: list[BlueRangeEntity] = []
        for device_uuid, layout in data.layouts.items():
            if processed.get(device_uuid) is layout:
                continue
            processed[device_uuid] = layout
            for entity in build(device_uuid, layout):
                unique_id = entity.unique_id
                if unique_id is None or unique_id in known:
                    continue
                known.add(unique_id)
                new.append(entity)
        if new:
            async_add_entities(new)

    coordinator.config_entry.async_on_unload(coordinator.async_add_listener(_discover))
    _discover()


def _equivalent(left: Any, right: Any) -> bool:
    """Return whether two control values mean the same thing."""
    if isinstance(left, bool) or isinstance(right, bool):
        return bool(left) == bool(right)
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return abs(float(left) - float(right)) <= _FLOAT_TOLERANCE
    return left == right
