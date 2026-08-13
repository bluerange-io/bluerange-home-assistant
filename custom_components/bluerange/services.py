"""Services of the BlueRange integration."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.const import ATTR_DEVICE_ID
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv, device_registry as dr

from .api import ControlDefinition
from .const import (
    ATTR_CONTROL_TYPE,
    ATTR_INDEX,
    ATTR_MODULE,
    ATTR_VALUE,
    DOMAIN,
    SERVICE_SET_ACTUATOR,
)
from .coordinator import BlueRangeDataUpdateCoordinator

SET_ACTUATOR_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_DEVICE_ID): vol.All(cv.ensure_list, [cv.string]),
        vol.Required(ATTR_CONTROL_TYPE): cv.string,
        vol.Optional(ATTR_INDEX, default=0): vol.Coerce(int),
        vol.Optional(ATTR_MODULE): cv.string,
        vol.Required(ATTR_VALUE): vol.Any(vol.Coerce(float), cv.boolean, cv.string),
    }
)


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the services of the integration."""
    if hass.services.has_service(DOMAIN, SERVICE_SET_ACTUATOR):
        return

    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_ACTUATOR,
        _async_set_actuator,
        schema=SET_ACTUATOR_SCHEMA,
    )


async def _async_set_actuator(call: ServiceCall) -> None:
    """Write a value to an actuator that has no entity of its own.

    BlueRange devices can expose vendor specific actuators that do not map onto
    any Home Assistant entity type.  This service addresses them directly by
    their BlueRange type and index.
    """
    control_type: str = call.data[ATTR_CONTROL_TYPE]
    index: int = call.data[ATTR_INDEX]
    module: str | None = call.data.get(ATTR_MODULE)
    value: Any = call.data[ATTR_VALUE]

    for device_id in call.data[ATTR_DEVICE_ID]:
        coordinator, device_uuid = _async_resolve_device(call.hass, device_id)
        definition = _async_resolve_actuator(
            coordinator, device_uuid, control_type, index, module
        )
        await coordinator.async_send_actuator(device_uuid, definition, value)
        await coordinator.async_request_refresh()


@callback
def _async_resolve_device(
    hass: HomeAssistant, device_id: str
) -> tuple[BlueRangeDataUpdateCoordinator, str]:
    """Return the coordinator and BlueRange UUID behind a Home Assistant device."""
    device = dr.async_get(hass).async_get(device_id)
    if device is None:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="device_not_found",
            translation_placeholders={"device_id": device_id},
        )

    device_uuid = next(
        (identifier[1] for identifier in device.identifiers if identifier[0] == DOMAIN),
        None,
    )
    for entry_id in device.config_entries:
        entry = hass.config_entries.async_get_entry(entry_id)
        if entry is None or entry.domain != DOMAIN:
            continue
        coordinator = getattr(entry, "runtime_data", None)
        if device_uuid is not None and isinstance(
            coordinator, BlueRangeDataUpdateCoordinator
        ):
            return coordinator, device_uuid

    raise ServiceValidationError(
        translation_domain=DOMAIN,
        translation_key="not_a_bluerange_device",
        translation_placeholders={"device_id": device_id},
    )


@callback
def _async_resolve_actuator(
    coordinator: BlueRangeDataUpdateCoordinator,
    device_uuid: str,
    control_type: str,
    index: int,
    module: str | None,
) -> ControlDefinition:
    """Return the actuator to write to, falling back to explicit addressing."""
    known: ControlDefinition | None = None
    if coordinator.data is not None and (
        controls := coordinator.data.controls.get(device_uuid)
    ):
        known = controls.actuators.get(f"{control_type}[{index}]")

    if known is None:
        # The device may expose actuators the metadata query did not return; the
        # server validates the address, so pass it through as given.
        return ControlDefinition(control_type=control_type, index=index, module=module)
    if module is not None and module != known.module:
        return ControlDefinition(
            control_type=known.control_type,
            index=known.index,
            module=module,
            unit=known.unit,
            minimum=known.minimum,
            maximum=known.maximum,
        )
    return known
