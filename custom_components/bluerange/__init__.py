"""The BlueRange integration."""

from __future__ import annotations

import logging

from homeassistant.const import (
    CONF_ACCESS_TOKEN,
    CONF_SCAN_INTERVAL,
    CONF_URL,
    CONF_VERIFY_SSL,
    Platform,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryError
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .api import BlueRangeClient, BlueRangeError
from .const import (
    CONF_ORGANIZATION,
    CONF_USE_MQTT,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_USE_MQTT,
    DOMAIN,
    MANUFACTURER,
)
from .coordinator import BlueRangeConfigEntry, BlueRangeDataUpdateCoordinator
from .entity import hub_identifier
from .mqtt import BlueRangeMqttListener
from .services import async_setup_services

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.CLIMATE,
    Platform.COVER,
    Platform.LIGHT,
    Platform.NUMBER,
    Platform.SENSOR,
    Platform.SWITCH,
]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the integration wide services."""
    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: BlueRangeConfigEntry) -> bool:
    """Set up BlueRange from a config entry."""
    session = async_get_clientsession(
        hass, verify_ssl=entry.data.get(CONF_VERIFY_SSL, True)
    )
    try:
        client = BlueRangeClient(
            session,
            entry.data[CONF_URL],
            entry.data[CONF_ACCESS_TOKEN],
            entry.data.get(CONF_ORGANIZATION),
        )
    except BlueRangeError as err:
        # A stored address that cannot be parsed will not fix itself by retrying.
        raise ConfigEntryError(str(err)) from err

    coordinator = BlueRangeDataUpdateCoordinator(
        hass,
        entry,
        client,
        entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
    )
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    if entry.options.get(CONF_USE_MQTT, DEFAULT_USE_MQTT):
        await _async_start_live_updates(hass, entry, coordinator)

    # Has to exist before the platforms point their devices at it.
    _async_register_hub(hass, entry, client)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: BlueRangeConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


@callback
def _async_register_hub(
    hass: HomeAssistant, entry: BlueRangeConfigEntry, client: BlueRangeClient
) -> None:
    """Register the organisation as the device every device is reached through."""
    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={hub_identifier(entry.entry_id)},
        entry_type=dr.DeviceEntryType.SERVICE,
        manufacturer=MANUFACTURER,
        name=entry.title,
        configuration_url=client.base_url,
    )


async def _async_start_live_updates(
    hass: HomeAssistant,
    entry: BlueRangeConfigEntry,
    coordinator: BlueRangeDataUpdateCoordinator,
) -> None:
    """Subscribe to the broker, falling back to polling if that is not possible.

    Live updates are an addition, not a replacement: the broker publishes without
    the retain flag, so the state fetched during the first refresh above is what
    fills the entities until the next reading of each sensor arrives.  Anything
    that keeps the subscription from happening therefore only costs timeliness,
    and is logged rather than raised.
    """
    try:
        user = await coordinator.client.async_get_current_user()
        user_uuid = user.get("uuid")
        if not user_uuid:
            # The broker derives the account from the client id, which is built
            # from this UUID, so without it there is nothing to connect as.
            _LOGGER.debug("The server did not name the account, skipping MQTT")
            return
        parameters = await coordinator.client.async_get_mqtt_parameters(
            user_uuid,
            coordinator.client.organization_uuid or user.get("organizationUuid"),
        )
    except BlueRangeError as err:
        _LOGGER.warning("Could not ask the server for MQTT access: %s", err)
        return

    if parameters is None:
        _LOGGER.info(
            "This BlueRange server offers no MQTT access, polling every %s",
            coordinator.update_interval,
        )
        return

    listener = BlueRangeMqttListener(
        hass,
        parameters,
        coordinator.async_apply_reading,
        coordinator.async_set_push_available,
    )
    if not await listener.async_start():
        return

    entry.async_on_unload(listener.async_stop)


async def _async_reload_entry(hass: HomeAssistant, entry: BlueRangeConfigEntry) -> None:
    """Reload the entry so changed options take effect."""
    await hass.config_entries.async_reload(entry.entry_id)
