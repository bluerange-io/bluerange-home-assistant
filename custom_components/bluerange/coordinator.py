"""Keeps the BlueRange device inventory and its sensor values in sync."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    BlueRangeAuthError,
    BlueRangeClient,
    BlueRangeDevice,
    BlueRangeError,
    ControlDefinition,
    DeviceControls,
    SensorReading,
)
from .const import (
    DEVICE_REFRESH_INTERVAL,
    DOMAIN,
    MAX_PARALLEL_REQUESTS,
    MQTT_FALLBACK_SCAN_INTERVAL,
    PUSH_FLUSH_DELAY,
)
from .controls import DeviceLayout, build_layout

_LOGGER = logging.getLogger(__name__)

type BlueRangeConfigEntry = ConfigEntry[BlueRangeDataUpdateCoordinator]


@dataclass(slots=True, kw_only=True)
class BlueRangeData:
    """The snapshot every entity reads its state from."""

    devices: dict[str, BlueRangeDevice] = field(default_factory=dict)
    controls: dict[str, DeviceControls] = field(default_factory=dict)
    layouts: dict[str, DeviceLayout] = field(default_factory=dict)


class BlueRangeDataUpdateCoordinator(DataUpdateCoordinator[BlueRangeData]):
    """Polls one BlueRange server for all devices it exposes.

    The server answers metadata queries per device, so one update cycle issues
    one request per device.  The inventory itself - which devices exist and which
    actuators they offer - barely ever changes and is therefore only refreshed
    every :data:`~custom_components.bluerange.const.DEVICE_REFRESH_INTERVAL`.
    """

    config_entry: BlueRangeConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: BlueRangeConfigEntry,
        client: BlueRangeClient,
        scan_interval: int,
    ) -> None:
        """Initialise the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(seconds=scan_interval),
        )
        self.client = client
        self._poll_interval = timedelta(seconds=scan_interval)
        self._push_available = False
        # Bursts of readings would otherwise write entity states one by one.
        self._push_debouncer = Debouncer(
            hass,
            _LOGGER,
            cooldown=PUSH_FLUSH_DELAY,
            immediate=True,
            function=self._async_notify_listeners,
        )
        self._devices: dict[str, BlueRangeDevice] = {}
        self._actuators: dict[str, dict[str, ControlDefinition]] = {}
        self._controls: dict[str, DeviceControls] = {}
        self._layouts: dict[str, DeviceLayout] = {}
        self._inventory_refreshed: datetime | None = None

    @property
    def push_available(self) -> bool:
        """Return whether readings currently arrive over MQTT."""
        return self._push_available

    @callback
    def async_apply_reading(
        self, device_uuid: str, control_type: str, index: int, reading: SensorReading
    ) -> None:
        """Take one reading that arrived over MQTT into the current snapshot.

        Readings for controls that are not part of the metadata are dropped: no
        entity exists for them, and the next inventory refresh is what picks up a
        device or control that appeared since.
        """
        controls = self._controls.get(device_uuid)
        if controls is None:
            return
        key = f"{control_type}[{index}]"
        if key not in controls.sensors:
            return
        # The snapshot handed to the entities shares this object, so updating it
        # is enough and only the listeners have to be told.
        controls.readings[key] = reading
        self._push_debouncer.async_schedule_call()

    @callback
    def async_set_push_available(self, available: bool) -> None:
        """Switch between live updates and polling as the source of truth."""
        if available == self._push_available:
            return
        self._push_available = available

        if available:
            # Poll no more often than configured, but at least as a safety net.
            self.update_interval = max(
                self._poll_interval, timedelta(seconds=MQTT_FALLBACK_SCAN_INTERVAL)
            )
            _LOGGER.debug("Live updates active, polling every %s", self.update_interval)
            return

        self.update_interval = self._poll_interval
        _LOGGER.debug("Live updates lost, polling every %s", self.update_interval)
        # The interval setter does not reschedule, so without this the values
        # would stay stale until the long fallback interval elapses.
        self.hass.async_create_task(self.async_request_refresh())

    async def async_shutdown(self) -> None:
        """Stop the coordinator and anything it scheduled."""
        self._push_debouncer.async_shutdown()
        await super().async_shutdown()

    async def _async_notify_listeners(self) -> None:
        """Write the states of every entity after a batch of live readings."""
        self.async_update_listeners()

    async def _async_setup(self) -> None:
        """Discover the devices and their actuators once, before the first poll."""
        await self._async_refresh_inventory()

    async def _async_update_data(self) -> BlueRangeData:
        """Fetch the current sensor values of every known device."""
        now = dt_util.utcnow()
        if (
            self._inventory_refreshed is None
            or now - self._inventory_refreshed >= DEVICE_REFRESH_INTERVAL
        ):
            await self._async_refresh_inventory()

        await self._async_refresh_readings()

        return BlueRangeData(
            devices=dict(self._devices),
            controls=dict(self._controls),
            layouts=dict(self._layouts),
        )

    async def async_send_actuator(
        self, device_uuid: str, definition: ControlDefinition, value: Any
    ) -> None:
        """Write one actuator value and schedule a read back."""
        try:
            await self.client.async_set_actuator(device_uuid, definition, value)
        except BlueRangeAuthError as err:
            # DataUpdateCoordinator starts reauth automatically on polling
            # failures, but write calls run outside the update cycle, so the
            # repair prompt has to be triggered here.
            self.config_entry.async_start_reauth(self.hass)
            raise ConfigEntryAuthFailed(str(err)) from err
        except BlueRangeError as err:
            raise HomeAssistantError(
                f"Could not set {definition.control_type} on {device_uuid}: {err}"
            ) from err

    async def _async_refresh_inventory(self) -> None:
        """Reload the device list together with the actuators of every device."""
        try:
            devices = await self.client.async_get_devices()
        except BlueRangeAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except BlueRangeError as err:
            raise UpdateFailed(f"Could not load the device list: {err}") from err

        active = {device.uuid: device for device in devices if device.is_active}

        actuators: dict[str, dict[str, ControlDefinition]] = {}

        async def _load(uuid: str) -> None:
            actuators[uuid] = await self.client.async_get_actuators(uuid)

        failures = await self._async_run_limited(
            [(uuid, _load(uuid)) for uuid in active], "actuators"
        )
        if active and failures == len(active):
            raise UpdateFailed("Could not load the actuators of any device")

        for uuid in active:
            # Keep what is known for devices whose query failed this round.
            if uuid not in actuators and uuid in self._actuators:
                actuators[uuid] = self._actuators[uuid]

        self._devices = active
        self._actuators = actuators
        self._controls = {
            uuid: controls
            for uuid, controls in self._controls.items()
            if uuid in active
        }
        self._layouts = {
            uuid: layout for uuid, layout in self._layouts.items() if uuid in active
        }
        self._inventory_refreshed = dt_util.utcnow()
        _LOGGER.debug("Refreshed inventory: %d device(s)", len(active))

    async def _async_refresh_readings(self) -> None:
        """Reload the sensor metadata and last values of every known device."""
        results: dict[
            str, tuple[dict[str, ControlDefinition], dict[str, SensorReading]]
        ] = {}

        async def _load(uuid: str) -> None:
            results[uuid] = await self.client.async_get_sensors(uuid)

        failures = await self._async_run_limited(
            [(uuid, _load(uuid)) for uuid in self._devices], "sensors"
        )
        if self._devices and failures == len(self._devices):
            raise UpdateFailed("Could not load the sensor values of any device")

        for uuid in self._devices:
            if uuid not in results:
                continue
            sensors, readings = results[uuid]
            controls = DeviceControls(
                sensors=sensors,
                actuators=self._actuators.get(uuid, {}),
                readings=readings,
            )
            # The layout is derived before the cache is replaced, so that it can
            # be reused as long as the set of controls is unchanged.
            self._layouts[uuid] = self._layout_for(uuid, controls)
            self._controls[uuid] = controls

    def _layout_for(self, uuid: str, controls: DeviceControls) -> DeviceLayout:
        """Return the entity layout of a device, reclassifying only on change."""
        previous = self._layouts.get(uuid)
        cached = self._controls.get(uuid)
        if (
            previous is not None
            and cached is not None
            and cached.sensors.keys() == controls.sensors.keys()
            and cached.actuators.keys() == controls.actuators.keys()
        ):
            return previous
        return build_layout(controls)

    async def _async_run_limited(
        self, jobs: list[tuple[str, Awaitable[None]]], what: str
    ) -> int:
        """Run per device requests with a cap on concurrency.

        Returns the number of devices whose request failed.  A single failing
        device must not take the whole integration down, so its previous data is
        kept and only logged.
        """
        semaphore = asyncio.Semaphore(MAX_PARALLEL_REQUESTS)

        async def _guarded(uuid: str, job: Awaitable[None]) -> str | None:
            async with semaphore:
                try:
                    await job
                except BlueRangeAuthError:
                    raise
                except BlueRangeError as err:
                    _LOGGER.debug("Could not load %s of %s: %s", what, uuid, err)
                    return uuid
            return None

        outcomes = await asyncio.gather(
            *(_guarded(uuid, job) for uuid, job in jobs), return_exceptions=True
        )

        failures = 0
        for outcome in outcomes:
            if isinstance(outcome, BlueRangeAuthError):
                raise ConfigEntryAuthFailed(str(outcome))
            if isinstance(outcome, BaseException):
                raise outcome
            if outcome is not None:
                failures += 1
        return failures
