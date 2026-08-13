"""Maps the controls of a BlueRange device onto Home Assistant entity kinds.

BlueRange describes a device as a flat list of sensors (readable) and actuators
(writable), each addressed by a type and an index.  Home Assistant instead wants
typed entities, so the two lists have to be interpreted:

* an actuator that only accepts a single value is a momentary command (button),
* an actuator with the ``ON_OFF`` unit is a switch,
* any other actuator with a range is a number,
* a dimmable channel is a light,
* a temperature setpoint together with its mode commands is a thermostat,
* a drivable blind together with its slat angle is a cover.

Whenever a writable entity fully mirrors a sensor - a switch and its state, a
setpoint and its readback - that sensor is *consumed* so the same information is
not exposed twice.  Physical measurements are never consumed, because they carry
long term statistics that a climate attribute could not.

This module deliberately contains no Home Assistant imports so the rules stay
straightforward to unit test.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Final

from .api import ControlDefinition, DeviceControls

UNIT_ON_OFF: Final = "ON_OFF"
UNIT_CELSIUS: Final = "CELSIUS"

# Actuator types that adjust a room temperature setpoint.
_SETPOINT_ACTUATORS: Final = ("SET_SETPOINT_TEMPERATURE", "SET_TEMPERATURE")

# Sensor types that may serve as the measured temperature of a thermostat, most
# specific first.
_CURRENT_TEMPERATURE_SENSORS: Final = (
    "ACTUAL_TEMPERATURE",
    "TEMPERATURE",
    "EXTERNAL_TEMPERATURE",
)

# Sensor types that may report the output level of a dimmable channel.
_BRIGHTNESS_SENSORS: Final = ("BRIGHTNESS", "CHANNEL_1")

_ACTUATOR_DIMMING: Final = "SET_DIMMING"
_ACTUATOR_TURN_ON: Final = "TURN_ON"
_ACTUATOR_TURN_OFF: Final = "TURN_OFF"
_ACTUATOR_ON_OFF: Final = "TURN_ON_OFF"
_ACTUATOR_HEAT: Final = "SET_HEATING_MODE"
_ACTUATOR_HEATING_OFF: Final = "SET_HEATING_OFF"
_ACTUATOR_ECO: Final = "SET_ENERGY_SAVING_MODE"

_SENSOR_SETPOINT: Final = "SETPOINT_TEMPERATURE"
_SENSOR_HEATING_OFF: Final = "HEATING_OFF"
_SENSOR_ECO: Final = "ENERGY_SAVING_MODE"
_SENSOR_VALVE_POSITION: Final = "VALVE_POSITION"

# The controls of a blind.  ``SLAT_POSITION`` is how far the blind itself is
# driven down, ``SLAT_ANGLE`` how far its slats are turned.
_ACTUATOR_SET_POSITION: Final = "SET_SLAT_POSITION"
_ACTUATOR_SET_TILT: Final = "SET_SLAT_ANGLE"

# The blind module drives the motor through three separate commands, each
# pinned to the one value that triggers it.
_ACTUATOR_OPEN: Final = "REQUEST_UP"
_ACTUATOR_CLOSE: Final = "REQUEST_DOWN"
_ACTUATOR_STOP: Final = "REQUEST_STOP"

# A blind reached over KNX instead offers one command carrying the direction,
# and is stopped through the object that also steps its slats.
_ACTUATOR_DRIVE: Final = "DRIVE_UP_DOWN"
_ACTUATOR_STEP_STOP: Final = "ANGLE_STEP_STOP"

_SENSOR_POSITION: Final = "SLAT_POSITION"
_SENSOR_TILT: Final = "SLAT_ANGLE"

# State sensors that cannot be derived by dropping the ``SET_`` prefix.  Without
# one a switch has nothing to confirm a write against and Home Assistant has to
# offer both commands separately instead of a toggle.
_STATE_SENSOR_ALIASES: Final[dict[str, tuple[str, ...]]] = {
    "SET_TEMPERATURE": (_SENSOR_SETPOINT,),
    _ACTUATOR_DIMMING: _BRIGHTNESS_SENSORS,
    # ``_STATE`` is what the relay actually does, ``_COMMAND`` only what it was
    # last told to do, so the state is preferred.
    _ACTUATOR_ON_OFF: ("TURN_ON_OFF_STATE", "TURN_ON_OFF_COMMAND"),
}

# Value written for an actuator that has no declared range, e.g. a plain trigger.
_DEFAULT_TRIGGER_VALUE: Final = 1


@dataclass(frozen=True, slots=True, kw_only=True)
class SwitchSpec:
    """An ``ON_OFF`` actuator and the sensor that reports its state."""

    actuator: ControlDefinition
    state: ControlDefinition | None = None
    on_value: Any = 1
    off_value: Any = 0


@dataclass(frozen=True, slots=True, kw_only=True)
class NumberSpec:
    """An adjustable actuator and the sensor that reports its current value."""

    actuator: ControlDefinition
    state: ControlDefinition | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class ButtonSpec:
    """A momentary actuator together with the single value it accepts."""

    actuator: ControlDefinition
    value: Any = _DEFAULT_TRIGGER_VALUE


@dataclass(frozen=True, slots=True, kw_only=True)
class LightSpec:
    """A dimmable channel and the commands that drive it."""

    index: int
    dimming: ControlDefinition
    turn_on: ControlDefinition | None = None
    turn_off: ControlDefinition | None = None
    on_off: ControlDefinition | None = None
    brightness: ControlDefinition | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class CoverSpec:
    """A blind and the controls that drive it."""

    index: int
    position: ControlDefinition | None = None
    position_state: ControlDefinition | None = None
    tilt: ControlDefinition | None = None
    tilt_state: ControlDefinition | None = None
    open_command: ControlDefinition | None = None
    close_command: ControlDefinition | None = None
    drive: ControlDefinition | None = None
    stop: ControlDefinition | None = None

    @property
    def supports_tilt(self) -> bool:
        """Return whether the slats of this blind can be turned."""
        return self.tilt is not None

    @property
    def can_open(self) -> bool:
        """Return whether the blind can be driven up."""
        return (
            self.open_command is not None
            or self.drive is not None
            or self.position is not None
        )

    @property
    def can_close(self) -> bool:
        """Return whether the blind can be driven down."""
        return (
            self.close_command is not None
            or self.drive is not None
            or self.position is not None
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class ClimateSpec:
    """A temperature setpoint and everything that belongs to it."""

    index: int
    setpoint: ControlDefinition
    setpoint_state: ControlDefinition | None = None
    current: ControlDefinition | None = None
    heat: ControlDefinition | None = None
    heating_off: ControlDefinition | None = None
    heating_off_state: ControlDefinition | None = None
    eco: ControlDefinition | None = None
    eco_state: ControlDefinition | None = None
    valve: ControlDefinition | None = None

    @property
    def supports_on_off(self) -> bool:
        """Return whether heating can be switched on and off."""
        return self.heat is not None and self.heating_off is not None

    @property
    def supports_eco(self) -> bool:
        """Return whether an energy saving preset can be selected."""
        return self.eco is not None and self.heat is not None


@dataclass(slots=True, kw_only=True)
class DeviceLayout:
    """The Home Assistant entities one BlueRange device maps to."""

    climates: list[ClimateSpec] = field(default_factory=list)
    lights: list[LightSpec] = field(default_factory=list)
    covers: list[CoverSpec] = field(default_factory=list)
    switches: list[SwitchSpec] = field(default_factory=list)
    numbers: list[NumberSpec] = field(default_factory=list)
    buttons: list[ButtonSpec] = field(default_factory=list)
    sensors: list[ControlDefinition] = field(default_factory=list)
    binary_sensors: list[ControlDefinition] = field(default_factory=list)


def build_layout(controls: DeviceControls) -> DeviceLayout:
    """Classify the controls of one device into Home Assistant entities."""
    consumed_actuators: set[str] = set()
    consumed_sensors: set[str] = set()

    climates = _build_climates(controls, consumed_actuators, consumed_sensors)
    lights = _build_lights(controls, consumed_actuators, consumed_sensors)
    covers = _build_covers(controls, consumed_actuators, consumed_sensors)

    switches: list[SwitchSpec] = []
    numbers: list[NumberSpec] = []
    buttons: list[ButtonSpec] = []

    for key, actuator in _sorted(controls.actuators):
        if key in consumed_actuators:
            continue

        if actuator.is_trigger:
            buttons.append(ButtonSpec(actuator=actuator, value=actuator.minimum))
            continue

        state = _state_sensor(controls, actuator, consumed_sensors)

        if actuator.unit == UNIT_ON_OFF:
            switches.append(
                SwitchSpec(
                    actuator=actuator,
                    state=state,
                    on_value=actuator.maximum if actuator.maximum is not None else 1,
                    off_value=actuator.minimum if actuator.minimum is not None else 0,
                )
            )
        elif _is_adjustable(actuator, state):
            numbers.append(NumberSpec(actuator=actuator, state=state))
        else:
            # No unit, no range and nothing to read back: treat it as a command.
            buttons.append(ButtonSpec(actuator=actuator))
            continue

        if state is not None:
            consumed_sensors.add(state.key)

    sensors: list[ControlDefinition] = []
    binary_sensors: list[ControlDefinition] = []
    for key, sensor in _sorted(controls.sensors):
        if key in consumed_sensors:
            continue
        if sensor.unit == UNIT_ON_OFF:
            binary_sensors.append(sensor)
        else:
            sensors.append(sensor)

    return DeviceLayout(
        climates=climates,
        lights=lights,
        covers=covers,
        switches=switches,
        numbers=numbers,
        buttons=buttons,
        sensors=sensors,
        binary_sensors=binary_sensors,
    )


def _build_climates(
    controls: DeviceControls,
    consumed_actuators: set[str],
    consumed_sensors: set[str],
) -> list[ClimateSpec]:
    """Build one thermostat per adjustable temperature setpoint."""
    specs: list[ClimateSpec] = []
    for _, setpoint in _sorted(controls.actuators):
        if setpoint.control_type not in _SETPOINT_ACTUATORS:
            continue
        if setpoint.unit != UNIT_CELSIUS or setpoint.is_trigger:
            # A pinned setpoint is a command, not something a thermostat can set.
            continue

        index = setpoint.index
        spec = ClimateSpec(
            index=index,
            setpoint=setpoint,
            setpoint_state=_sensor(controls, _SENSOR_SETPOINT, index),
            current=_first_sensor(controls, _CURRENT_TEMPERATURE_SENSORS, index),
            heat=_actuator(controls, _ACTUATOR_HEAT, index),
            heating_off=_actuator(controls, _ACTUATOR_HEATING_OFF, index),
            heating_off_state=_sensor(controls, _SENSOR_HEATING_OFF, index),
            eco=_actuator(controls, _ACTUATOR_ECO, index),
            eco_state=_sensor(controls, _SENSOR_ECO, index),
            # Referenced to derive whether the device is currently heating; it
            # stays a sensor of its own.
            valve=_sensor(controls, _SENSOR_VALVE_POSITION, index),
        )
        specs.append(spec)

        consumed_actuators.add(setpoint.key)
        for actuator in (spec.heat, spec.heating_off, spec.eco):
            if actuator is not None:
                consumed_actuators.add(actuator.key)
        # Only the pure control mirrors are consumed; the measured temperature
        # stays a sensor so that it keeps its long term statistics.
        for sensor in (spec.setpoint_state, spec.heating_off_state, spec.eco_state):
            if sensor is not None:
                consumed_sensors.add(sensor.key)

    return specs


def _build_lights(
    controls: DeviceControls,
    consumed_actuators: set[str],
    consumed_sensors: set[str],
) -> list[LightSpec]:
    """Build one light per dimmable channel."""
    specs: list[LightSpec] = []
    for _, dimming in _sorted(controls.actuators):
        if dimming.control_type != _ACTUATOR_DIMMING or dimming.is_trigger:
            continue

        index = dimming.index
        spec = LightSpec(
            index=index,
            dimming=dimming,
            turn_on=_actuator(controls, _ACTUATOR_TURN_ON, index),
            turn_off=_actuator(controls, _ACTUATOR_TURN_OFF, index),
            on_off=_actuator(controls, _ACTUATOR_ON_OFF, index),
            brightness=_first_sensor(controls, _BRIGHTNESS_SENSORS, index),
        )
        specs.append(spec)

        consumed_actuators.add(dimming.key)
        for actuator in (spec.turn_on, spec.turn_off, spec.on_off):
            if actuator is not None:
                consumed_actuators.add(actuator.key)
        if spec.brightness is not None:
            consumed_sensors.add(spec.brightness.key)

    return specs


def _build_covers(
    controls: DeviceControls,
    consumed_actuators: set[str],
    consumed_sensors: set[str],
) -> list[CoverSpec]:
    """Build one cover per blind channel.

    A blind counts as one as soon as it can be driven: to a height, in a
    direction, or both.  A roller shutter offers only some of these, a venetian
    blind adds the angle of its slats on top.
    """
    specs: list[CoverSpec] = []
    for index in _blind_channels(controls):
        spec = CoverSpec(
            index=index,
            position=_actuator(controls, _ACTUATOR_SET_POSITION, index),
            position_state=_sensor(controls, _SENSOR_POSITION, index),
            tilt=_actuator(controls, _ACTUATOR_SET_TILT, index),
            tilt_state=_sensor(controls, _SENSOR_TILT, index),
            open_command=_actuator(controls, _ACTUATOR_OPEN, index),
            close_command=_actuator(controls, _ACTUATOR_CLOSE, index),
            drive=_actuator(controls, _ACTUATOR_DRIVE, index),
            stop=_first_actuator(
                controls, (_ACTUATOR_STOP, _ACTUATOR_STEP_STOP), index
            ),
        )
        specs.append(spec)

        for actuator in (
            spec.position,
            spec.tilt,
            spec.open_command,
            spec.close_command,
            spec.drive,
            spec.stop,
        ):
            if actuator is not None:
                consumed_actuators.add(actuator.key)
        # Height and angle only report back what the blind was told to do, so
        # the cover is the only place they are shown.
        for sensor in (spec.position_state, spec.tilt_state):
            if sensor is not None:
                consumed_sensors.add(sensor.key)

    return specs


def _blind_channels(controls: DeviceControls) -> list[int]:
    """Return the channels that carry a blind, in a stable order."""
    channels: set[int] = set()
    for _, actuator in _sorted(controls.actuators):
        # A height or a direction range pinned to a single value is a command of
        # its own, not something a blind can be driven with.
        drives_to_a_height = (
            actuator.control_type == _ACTUATOR_SET_POSITION and not actuator.is_trigger
        )
        drives_in_a_direction = (
            actuator.control_type == _ACTUATOR_DRIVE and not actuator.is_trigger
        )
        # The separate up and down commands are pinned by design.
        commands_a_direction = actuator.control_type in (
            _ACTUATOR_OPEN,
            _ACTUATOR_CLOSE,
        )
        if drives_to_a_height or drives_in_a_direction or commands_a_direction:
            channels.add(actuator.index)
    return sorted(channels)


def _is_adjustable(
    actuator: ControlDefinition, state: ControlDefinition | None
) -> bool:
    """Return whether an actuator behaves like a value that can be dialled in."""
    has_unit = actuator.unit is not None
    has_range = actuator.minimum is not None or actuator.maximum is not None
    has_readback = state is not None
    return has_unit or has_range or has_readback


def _state_sensor(
    controls: DeviceControls,
    actuator: ControlDefinition,
    consumed_sensors: set[str],
) -> ControlDefinition | None:
    """Return the sensor that reports back what an actuator was set to.

    The catalog names the pair consistently - ``SET_CHILD_PROTECTION`` is read
    back through ``CHILD_PROTECTION`` - with a few aliases for the cases where
    the reading has a different name than the command.
    """
    candidates = list(_STATE_SENSOR_ALIASES.get(actuator.control_type, ()))
    if actuator.control_type.startswith("SET_"):
        candidates.append(actuator.control_type.removeprefix("SET_"))
    for candidate in candidates:
        sensor = _sensor(controls, candidate, actuator.index)
        if sensor is not None and sensor.key not in consumed_sensors:
            return sensor
    return None


def _sensor(
    controls: DeviceControls, control_type: str, index: int
) -> ControlDefinition | None:
    """Return one sensor of a device by type and index."""
    return controls.sensors.get(f"{control_type}[{index}]")


def _first_sensor(
    controls: DeviceControls, control_types: tuple[str, ...], index: int
) -> ControlDefinition | None:
    """Return the first sensor of a device matching any of ``control_types``."""
    for control_type in control_types:
        if sensor := _sensor(controls, control_type, index):
            return sensor
    return None


def _actuator(
    controls: DeviceControls, control_type: str, index: int
) -> ControlDefinition | None:
    """Return one actuator of a device by type and index."""
    return controls.actuators.get(f"{control_type}[{index}]")


def _first_actuator(
    controls: DeviceControls, control_types: tuple[str, ...], index: int
) -> ControlDefinition | None:
    """Return the first actuator of a device matching any of ``control_types``."""
    for control_type in control_types:
        if actuator := _actuator(controls, control_type, index):
            return actuator
    return None


def _sorted(
    definitions: dict[str, ControlDefinition],
) -> list[tuple[str, ControlDefinition]]:
    """Return controls in a stable order so entity creation is deterministic."""
    return sorted(
        definitions.items(), key=lambda item: (item[1].control_type, item[1].index)
    )
