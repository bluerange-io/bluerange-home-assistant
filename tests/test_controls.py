"""Tests for the classification of BlueRange controls."""

from __future__ import annotations

from custom_components.bluerange.api import ControlDefinition, DeviceControls
from custom_components.bluerange.controls import build_layout


def controls(
    sensors: list[ControlDefinition] | None = None,
    actuators: list[ControlDefinition] | None = None,
) -> DeviceControls:
    """Build a DeviceControls from plain lists."""
    return DeviceControls(
        sensors={item.key: item for item in sensors or []},
        actuators={item.key: item for item in actuators or []},
        readings={},
    )


def sensor(
    control_type: str, unit: str | None = None, index: int = 0, **kwargs: float
) -> ControlDefinition:
    """Build a sensor definition."""
    return ControlDefinition(
        control_type=control_type, index=index, unit=unit, **kwargs
    )


def actuator(
    control_type: str,
    unit: str | None = None,
    index: int = 0,
    minimum: float | None = None,
    maximum: float | None = None,
) -> ControlDefinition:
    """Build an actuator definition."""
    return ControlDefinition(
        control_type=control_type,
        index=index,
        unit=unit,
        minimum=minimum,
        maximum=maximum,
    )


def test_pinned_actuator_becomes_a_button() -> None:
    """An actuator accepting a single value is a momentary command."""
    layout = build_layout(
        controls(actuators=[actuator("RESET_HOST", "ON_OFF", minimum=1, maximum=1)])
    )

    assert [spec.actuator.control_type for spec in layout.buttons] == ["RESET_HOST"]
    assert layout.switches == []
    assert layout.buttons[0].value == 1


def test_on_off_actuator_becomes_a_switch_with_its_state() -> None:
    """A switch reads its state back from the matching sensor."""
    layout = build_layout(
        controls(
            sensors=[sensor("CHILD_PROTECTION", "ON_OFF")],
            actuators=[
                actuator("SET_CHILD_PROTECTION", "ON_OFF", minimum=0, maximum=1)
            ],
        )
    )

    assert len(layout.switches) == 1
    spec = layout.switches[0]
    assert spec.state is not None
    assert spec.state.control_type == "CHILD_PROTECTION"
    assert (spec.off_value, spec.on_value) == (0, 1)
    # The state is exposed through the switch, so it is not a sensor as well.
    assert layout.binary_sensors == []


def test_on_off_actuator_without_state_is_assumed() -> None:
    """Without a matching sensor the switch has nothing to read back."""
    layout = build_layout(
        controls(actuators=[actuator("SET_RELAY", "ON_OFF", minimum=0, maximum=1)])
    )

    assert layout.switches[0].state is None


def test_ranged_actuator_becomes_a_number() -> None:
    """An actuator with a range and a readback is a number."""
    layout = build_layout(
        controls(
            sensors=[sensor("MAX_TEMPERATURE", "CELSIUS")],
            actuators=[
                actuator("SET_MAX_TEMPERATURE", "CELSIUS", minimum=8, maximum=28)
            ],
        )
    )

    assert len(layout.numbers) == 1
    assert layout.numbers[0].state is not None
    assert layout.sensors == []


def test_actuator_without_unit_range_or_state_becomes_a_button() -> None:
    """Nothing to dial in and nothing to read back means a command."""
    layout = build_layout(controls(actuators=[actuator("SET_SOMETHING")]))

    assert [spec.actuator.control_type for spec in layout.buttons] == ["SET_SOMETHING"]
    assert layout.buttons[0].value == 1


def test_sensors_split_by_unit() -> None:
    """On/off sensors become binary sensors, everything else a sensor."""
    layout = build_layout(
        controls(
            sensors=[
                sensor("TEMPERATURE", "CELSIUS"),
                sensor("PRESENCE", "ON_OFF"),
                sensor("ERROR_FLAGS"),
            ]
        )
    )

    assert [item.control_type for item in layout.sensors] == [
        "ERROR_FLAGS",
        "TEMPERATURE",
    ]
    assert [item.control_type for item in layout.binary_sensors] == ["PRESENCE"]


def test_thermostat_layout() -> None:
    """A thermostat gathers its setpoint and mode commands into one entity."""
    layout = build_layout(
        controls(
            sensors=[
                sensor("ACTUAL_TEMPERATURE", "CELSIUS"),
                sensor("SETPOINT_TEMPERATURE", "CELSIUS"),
                sensor("HEATING_OFF", "ON_OFF"),
                sensor("ENERGY_SAVING_MODE", "ON_OFF"),
                sensor("VALVE_POSITION", "PERCENT"),
                sensor("BATTERY", "PERCENT"),
                sensor("CHILD_PROTECTION", "ON_OFF"),
            ],
            actuators=[
                actuator("SET_SETPOINT_TEMPERATURE", "CELSIUS", minimum=8, maximum=28),
                actuator("SET_HEATING_MODE", "ON_OFF", minimum=2, maximum=2),
                actuator("SET_HEATING_OFF", "ON_OFF", minimum=8, maximum=8),
                actuator("SET_ENERGY_SAVING_MODE", "ON_OFF", minimum=4, maximum=4),
                actuator("SET_CHILD_PROTECTION", "ON_OFF", minimum=0, maximum=1),
                actuator("TRIGGER_ADAPTATION", "ON_OFF", minimum=1, maximum=1),
            ],
        )
    )

    assert len(layout.climates) == 1
    climate = layout.climates[0]
    assert climate.supports_on_off
    assert climate.supports_eco
    assert climate.current is not None
    assert climate.current.control_type == "ACTUAL_TEMPERATURE"
    assert climate.valve is not None

    # The mode commands belong to the thermostat, the child lock does not.
    assert [spec.actuator.control_type for spec in layout.buttons] == [
        "TRIGGER_ADAPTATION"
    ]
    assert [spec.actuator.control_type for spec in layout.switches] == [
        "SET_CHILD_PROTECTION"
    ]

    # Measurements survive, control mirrors do not.
    assert [item.control_type for item in layout.sensors] == [
        "ACTUAL_TEMPERATURE",
        "BATTERY",
        "VALVE_POSITION",
    ]
    assert layout.binary_sensors == []


def test_pinned_setpoint_is_not_a_thermostat() -> None:
    """A temperature command with a fixed value cannot drive a thermostat."""
    layout = build_layout(
        controls(
            actuators=[
                actuator(
                    "RESET_EXTERNAL_TEMPERATURE", "CELSIUS", minimum=255, maximum=255
                )
            ]
        )
    )

    assert layout.climates == []
    assert len(layout.buttons) == 1


def test_luminaire_layout() -> None:
    """A dimmable channel gathers its on and off commands into one light."""
    layout = build_layout(
        controls(
            sensors=[
                sensor("BRIGHTNESS", "PERCENT", minimum=0, maximum=100),
                sensor("TASK_TUNING", "PERCENT", minimum=0, maximum=100),
                sensor("CHANNEL_1", "PERCENT", minimum=0, maximum=100),
            ],
            actuators=[
                actuator("SET_DIMMING", "PERCENT", minimum=0, maximum=100),
                actuator("TURN_ON", minimum=1, maximum=1),
                actuator("TURN_OFF", minimum=1, maximum=1),
                actuator("SET_AUTO", minimum=1, maximum=1),
            ],
        )
    )

    assert len(layout.lights) == 1
    light = layout.lights[0]
    assert light.turn_on is not None
    assert light.turn_off is not None
    assert light.brightness is not None
    assert light.brightness.control_type == "BRIGHTNESS"

    # Only the automatic mode is left over as a command of its own.
    assert [spec.actuator.control_type for spec in layout.buttons] == ["SET_AUTO"]
    # BRIGHTNESS is the light's state, the other levels stay sensors.
    assert [item.control_type for item in layout.sensors] == [
        "CHANNEL_1",
        "TASK_TUNING",
    ]


def test_multi_channel_luminaires_become_separate_lights() -> None:
    """Every channel of a multi head luminaire becomes its own light."""
    layout = build_layout(
        controls(
            actuators=[
                actuator("SET_DIMMING", "PERCENT", index=0, minimum=0, maximum=100),
                actuator("TURN_ON_OFF", "ON_OFF", index=0, minimum=0, maximum=1),
                actuator("SET_DIMMING", "PERCENT", index=2, minimum=0, maximum=100),
                actuator("TURN_ON_OFF", "ON_OFF", index=2, minimum=0, maximum=1),
            ]
        )
    )

    assert [light.index for light in layout.lights] == [0, 2]
    assert all(light.on_off is not None for light in layout.lights)
    # The on/off commands were consumed by their light.
    assert layout.switches == []


def test_dimming_alias_falls_back_to_channel() -> None:
    """Without a brightness sensor the first channel level is used."""
    layout = build_layout(
        controls(
            sensors=[sensor("CHANNEL_1", "PERCENT", minimum=0, maximum=100)],
            actuators=[actuator("SET_DIMMING", "PERCENT", minimum=0, maximum=100)],
        )
    )

    light = layout.lights[0]
    assert light.brightness is not None
    assert light.brightness.control_type == "CHANNEL_1"
    assert layout.sensors == []


def test_blind_module_layout() -> None:
    """A venetian blind gathers its height, angle and commands into one cover."""
    layout = build_layout(
        controls(
            sensors=[
                sensor("SLAT_POSITION", "PERCENT", minimum=0, maximum=100),
                sensor("SLAT_ANGLE", "PERCENT", minimum=0, maximum=100),
                sensor("MOTOR_STATE", minimum=0, maximum=10),
                sensor("CONFIG_TRAVEL_TIME_UP", "SECOND", minimum=1, maximum=600),
            ],
            actuators=[
                actuator("SET_SLAT_POSITION", "PERCENT", minimum=0, maximum=100),
                actuator("SET_SLAT_ANGLE", "PERCENT", minimum=0, maximum=100),
                actuator("REQUEST_UP", "ON_OFF", minimum=2, maximum=2),
                actuator("REQUEST_DOWN", "ON_OFF", minimum=3, maximum=3),
                actuator("REQUEST_STOP", "ON_OFF", minimum=1, maximum=1),
                actuator("REQUEST_STEP_UP", "ON_OFF", minimum=4, maximum=4),
                actuator("REQUEST_STEP_DOWN", "ON_OFF", minimum=5, maximum=5),
                actuator("SET_CONFIG_TRAVEL_TIME_UP", "SECOND", minimum=1, maximum=600),
            ],
        )
    )

    assert len(layout.covers) == 1
    cover = layout.covers[0]
    assert cover.supports_tilt
    assert cover.can_open
    assert cover.can_close
    assert cover.position is not None
    assert cover.position_state is not None
    assert cover.tilt_state is not None
    assert cover.stop is not None
    assert cover.stop.control_type == "REQUEST_STOP"

    # Everything that drives the blind belongs to the cover, while stepping the
    # slats has no counterpart on a Home Assistant cover and stays a command.
    assert [spec.actuator.control_type for spec in layout.buttons] == [
        "REQUEST_STEP_DOWN",
        "REQUEST_STEP_UP",
    ]
    assert layout.switches == []
    # The travel times are the blind's configuration, not its state.
    assert [spec.actuator.control_type for spec in layout.numbers] == [
        "SET_CONFIG_TRAVEL_TIME_UP"
    ]
    # What the blind reports besides its height and angle stays a sensor.
    assert [item.control_type for item in layout.sensors] == ["MOTOR_STATE"]
    assert layout.binary_sensors == []


def test_knx_blind_layout() -> None:
    """A blind reached over KNX is driven by one command carrying the direction."""
    layout = build_layout(
        controls(
            sensors=[sensor("SLAT_POSITION", "PERCENT", minimum=0, maximum=100)],
            actuators=[
                actuator("SET_SLAT_POSITION", "PERCENT", minimum=0, maximum=100),
                actuator("DRIVE_UP_DOWN", "ON_OFF", minimum=0, maximum=1),
                actuator("ANGLE_STEP_STOP", "ON_OFF", minimum=0, maximum=1),
            ],
        )
    )

    assert len(layout.covers) == 1
    cover = layout.covers[0]
    assert cover.drive is not None
    assert cover.can_open
    assert cover.can_close
    assert cover.stop is not None
    assert cover.stop.control_type == "ANGLE_STEP_STOP"
    # Left alone both would have looked like plain on/off actuators.
    assert layout.switches == []


def test_shutter_without_a_position_is_still_a_cover() -> None:
    """A shutter that only knows up and down can still be opened and closed."""
    layout = build_layout(
        controls(
            actuators=[
                actuator("REQUEST_UP", "ON_OFF", minimum=2, maximum=2),
                actuator("REQUEST_DOWN", "ON_OFF", minimum=3, maximum=3),
            ]
        )
    )

    assert len(layout.covers) == 1
    cover = layout.covers[0]
    assert cover.position is None
    assert not cover.supports_tilt
    assert cover.can_open
    assert cover.can_close
    # Left alone the two commands would have been buttons of their own.
    assert layout.buttons == []


def test_blind_channels_are_separate_covers() -> None:
    """Every channel of a multi channel blind actuator becomes its own cover."""
    layout = build_layout(
        controls(
            actuators=[
                actuator(
                    "SET_SLAT_POSITION", "PERCENT", index=0, minimum=0, maximum=100
                ),
                actuator(
                    "SET_SLAT_POSITION", "PERCENT", index=1, minimum=0, maximum=100
                ),
                actuator("SET_SLAT_ANGLE", "PERCENT", index=1, minimum=0, maximum=100),
            ]
        )
    )

    assert [cover.index for cover in layout.covers] == [0, 1]
    assert [cover.supports_tilt for cover in layout.covers] == [False, True]


def test_slat_angle_alone_is_not_a_cover() -> None:
    """Without a way to drive the blind there is nothing to open or close."""
    layout = build_layout(
        controls(
            sensors=[sensor("SLAT_ANGLE", "PERCENT", minimum=0, maximum=100)],
            actuators=[actuator("SET_SLAT_ANGLE", "PERCENT", minimum=0, maximum=100)],
        )
    )

    assert layout.covers == []
    assert [spec.actuator.control_type for spec in layout.numbers] == ["SET_SLAT_ANGLE"]


def test_pinned_drive_command_is_not_a_cover() -> None:
    """A drive command with one fixed value cannot pick a direction."""
    layout = build_layout(
        controls(actuators=[actuator("DRIVE_UP_DOWN", "ON_OFF", minimum=1, maximum=1)])
    )

    assert layout.covers == []
    assert [spec.actuator.control_type for spec in layout.buttons] == ["DRIVE_UP_DOWN"]


def test_layout_is_deterministic() -> None:
    """The same controls always produce the same entities in the same order."""
    definitions = controls(
        sensors=[sensor("HUMIDITY", "PERCENT"), sensor("TEMPERATURE", "CELSIUS")],
        actuators=[
            actuator("SET_B", "PERCENT", minimum=0, maximum=100),
            actuator("SET_A", "PERCENT", minimum=0, maximum=100),
        ],
    )

    first = build_layout(definitions)
    second = build_layout(definitions)

    assert [item.key for item in first.sensors] == [item.key for item in second.sensors]
    assert [spec.actuator.key for spec in first.numbers] == ["SET_A[0]", "SET_B[0]"]


def test_on_off_actuator_reads_back_through_its_state_sensor() -> None:
    """Devices report the relay state separately from the command sent to it."""
    layout = build_layout(
        controls(
            sensors=[
                sensor("TURN_ON_OFF_STATE", "ON_OFF"),
                sensor("TURN_ON_OFF_COMMAND", "ON_OFF"),
            ],
            actuators=[actuator("TURN_ON_OFF", "ON_OFF", minimum=0, maximum=1)],
        )
    )

    assert len(layout.switches) == 1
    spec = layout.switches[0]
    assert spec.state is not None
    # The state the relay is in, not the command it was given.
    assert spec.state.control_type == "TURN_ON_OFF_STATE"
    # The command stays visible on its own.
    assert [item.control_type for item in layout.binary_sensors] == [
        "TURN_ON_OFF_COMMAND"
    ]


def test_multi_channel_relays_pair_up_by_index() -> None:
    """A two channel relay reports one state per channel."""
    layout = build_layout(
        controls(
            sensors=[
                sensor("TURN_ON_OFF_STATE", "ON_OFF", index=0),
                sensor("TURN_ON_OFF_STATE", "ON_OFF", index=1),
            ],
            actuators=[
                actuator("TURN_ON_OFF", "ON_OFF", index=0, minimum=0, maximum=1),
                actuator("TURN_ON_OFF", "ON_OFF", index=1, minimum=0, maximum=1),
            ],
        )
    )

    assert len(layout.switches) == 2
    assert all(spec.state is not None for spec in layout.switches)
    assert [spec.state.index for spec in layout.switches] == [0, 1]
    assert layout.binary_sensors == []
