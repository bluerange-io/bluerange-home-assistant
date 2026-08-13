"""Translates BlueRange units and control types into Home Assistant metadata.

BlueRange units follow the Prometheus base unit convention, which maps cleanly
onto Home Assistant device classes for most of them.  A device class is only
assigned where the unit is one Home Assistant actually accepts for it, because a
mismatch would make Home Assistant convert or reject the value.  ``KELVIN`` is
the notable exception: BlueRange uses it for colour temperature, so treating it
as a temperature would have Home Assistant display it in degrees Celsius.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Final

from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.components.number import NumberDeviceClass
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import (
    DEGREE,
    LIGHT_LUX,
    PERCENTAGE,
    EntityCategory,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfFrequency,
    UnitOfLength,
    UnitOfPower,
    UnitOfPressure,
    UnitOfRatio,
    UnitOfSoundPressure,
    UnitOfSpeed,
    UnitOfTemperature,
    UnitOfTime,
    UnitOfVolume,
    UnitOfVolumeFlowRate,
)


@dataclass(frozen=True, slots=True, kw_only=True)
class UnitMeta:
    """How one BlueRange unit is represented in Home Assistant."""

    unit: str | None = None
    device_class: SensorDeviceClass | None = None
    state_class: SensorStateClass | None = SensorStateClass.MEASUREMENT
    precision: int | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class SensorMeta:
    """Per control type refinements on top of the plain unit mapping.

    ``units`` guards the refinement: a control type only means what the override
    says as long as it is reported in one of the expected units.  Without that
    guard a mislabelled control could end up with a state class its device class
    does not allow, which Home Assistant rejects.
    """

    device_class: SensorDeviceClass | None = None
    state_class: SensorStateClass | None = None
    precision: int | None = None
    units: tuple[str, ...] | None = None


#: Home Assistant metadata per BlueRange ``Unit`` enum value.
UNIT_MAP: Final[dict[str, UnitMeta]] = {
    "CELSIUS": UnitMeta(
        unit=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        precision=1,
    ),
    # Only ever used for colour temperature, which is not a temperature reading.
    "KELVIN": UnitMeta(unit=UnitOfTemperature.KELVIN, precision=0),
    "PASCAL": UnitMeta(
        unit=UnitOfPressure.PA, device_class=SensorDeviceClass.PRESSURE, precision=0
    ),
    "HPA": UnitMeta(
        unit=UnitOfPressure.HPA, device_class=SensorDeviceClass.PRESSURE, precision=1
    ),
    "VOLT": UnitMeta(
        unit=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        precision=2,
    ),
    "LUX": UnitMeta(
        unit=LIGHT_LUX, device_class=SensorDeviceClass.ILLUMINANCE, precision=0
    ),
    "LUMEN": UnitMeta(unit="lm", precision=0),
    "METER": UnitMeta(
        unit=UnitOfLength.METERS, device_class=SensorDeviceClass.DISTANCE, precision=2
    ),
    "METER_PER_SECOND": UnitMeta(
        unit=UnitOfSpeed.METERS_PER_SECOND,
        device_class=SensorDeviceClass.SPEED,
        precision=2,
    ),
    "CUBIC_METERS": UnitMeta(
        unit=UnitOfVolume.CUBIC_METERS,
        device_class=SensorDeviceClass.VOLUME,
        state_class=SensorStateClass.TOTAL_INCREASING,
        precision=3,
    ),
    "CUBIC_METERS_PER_HOUR": UnitMeta(
        unit=UnitOfVolumeFlowRate.CUBIC_METERS_PER_HOUR,
        device_class=SensorDeviceClass.VOLUME_FLOW_RATE,
        precision=2,
    ),
    "DBA": UnitMeta(
        unit=UnitOfSoundPressure.WEIGHTED_DECIBEL_A,
        device_class=SensorDeviceClass.SOUND_PRESSURE,
        precision=0,
    ),
    "PPM": UnitMeta(unit=UnitOfRatio.PARTS_PER_MILLION, precision=0),
    "PPB": UnitMeta(
        unit=UnitOfRatio.PARTS_PER_BILLION,
        device_class=SensorDeviceClass.VOLATILE_ORGANIC_COMPOUNDS_PARTS,
        precision=0,
    ),
    "PERCENT": UnitMeta(unit=PERCENTAGE, precision=0),
    "AMPERE": UnitMeta(
        unit=UnitOfElectricCurrent.AMPERE,
        device_class=SensorDeviceClass.CURRENT,
        precision=2,
    ),
    "HERTZ": UnitMeta(
        unit=UnitOfFrequency.HERTZ,
        device_class=SensorDeviceClass.FREQUENCY,
        precision=1,
    ),
    "OHM": UnitMeta(unit="Ω", precision=1),
    "SECOND": UnitMeta(
        unit=UnitOfTime.SECONDS, device_class=SensorDeviceClass.DURATION, precision=0
    ),
    "WATT": UnitMeta(
        unit=UnitOfPower.WATT, device_class=SensorDeviceClass.POWER, precision=1
    ),
    "WATT_HOUR": UnitMeta(
        unit=UnitOfEnergy.WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        precision=0,
    ),
    "LITER": UnitMeta(
        unit=UnitOfVolume.LITERS,
        device_class=SensorDeviceClass.VOLUME,
        state_class=SensorStateClass.TOTAL_INCREASING,
        precision=1,
    ),
    "LITER_PER_HOUR": UnitMeta(
        unit=UnitOfVolumeFlowRate.LITERS_PER_HOUR,
        device_class=SensorDeviceClass.VOLUME_FLOW_RATE,
        precision=1,
    ),
    "AMPERE_HOUR": UnitMeta(
        unit="Ah", state_class=SensorStateClass.TOTAL_INCREASING, precision=3
    ),
    "DEGREE": UnitMeta(unit=DEGREE, precision=1),
    # Placeholders the server may report for controls without a real unit.
    "ENUM": UnitMeta(state_class=None),
    "UNKNOWN": UnitMeta(state_class=None),
}

#: Refinements that cannot be derived from the unit alone.
SENSOR_OVERRIDES: Final[dict[str, SensorMeta]] = {
    "HUMIDITY": SensorMeta(device_class=SensorDeviceClass.HUMIDITY, units=("PERCENT",)),
    "BATTERY": SensorMeta(device_class=SensorDeviceClass.BATTERY, units=("PERCENT",)),
    "CO2": SensorMeta(device_class=SensorDeviceClass.CO2, units=("PPM",)),
    "PRESENT_AMBIENT_CO2": SensorMeta(
        device_class=SensorDeviceClass.CO2, units=("PPM",)
    ),
    # Cumulative operating hours, which reset when the driver is replaced.
    "TOTAL_DEVICE_POWER_ON_TIME": SensorMeta(
        state_class=SensorStateClass.TOTAL_INCREASING, units=("SECOND",)
    ),
    "LIGHT_SOURCE_ON_TIME_NOT_RESETTABLE": SensorMeta(
        state_class=SensorStateClass.TOTAL_INCREASING, units=("SECOND",)
    ),
    "LIGHT_SOURCE_START_COUNTER_NOT_RESETTABLE": SensorMeta(
        state_class=SensorStateClass.TOTAL_INCREASING, units=("SECOND",)
    ),
}

#: Device classes for the on/off sensors Home Assistant has a meaning for.
BINARY_SENSOR_DEVICE_CLASSES: Final[dict[str, BinarySensorDeviceClass]] = {
    "PRESENCE": BinarySensorDeviceClass.OCCUPANCY,
    "OCCUPANCY": BinarySensorDeviceClass.OCCUPANCY,
    "MOTION": BinarySensorDeviceClass.MOTION,
    "WINDOW": BinarySensorDeviceClass.WINDOW,
    "WINDOW_OPEN": BinarySensorDeviceClass.WINDOW,
    "ERROR": BinarySensorDeviceClass.PROBLEM,
}

#: Substrings marking controls that describe the device rather than its function.
DIAGNOSTIC_MARKERS: Final[tuple[str, ...]] = (
    "_DEBUG",
    "_TEST",
    "TEST_",
    "FLAG",
    "BITFIELD",
    "COUNTER",
    "SERIAL",
    "MESH_ACCESS",
    "PERIODIC",
)

#: Numeric setpoints that Home Assistant can annotate with a device class.
NUMBER_DEVICE_CLASSES: Final[dict[str, NumberDeviceClass]] = {
    "CELSIUS": NumberDeviceClass.TEMPERATURE,
    "PASCAL": NumberDeviceClass.PRESSURE,
    "VOLT": NumberDeviceClass.VOLTAGE,
    "AMPERE": NumberDeviceClass.CURRENT,
    "WATT": NumberDeviceClass.POWER,
}

#: Step size of a setpoint, per BlueRange unit.
NUMBER_STEPS: Final[dict[str, float]] = {
    "CELSIUS": 0.5,
    "KELVIN": 50,
    "VOLT": 0.1,
    "AMPERE": 0.1,
    "CUBIC_METERS_PER_HOUR": 0.1,
    "LITER_PER_HOUR": 0.1,
}

#: Step size used when neither the unit nor the range says anything about the
#: resolution.
DEFAULT_NUMBER_STEP: Final = 1.0

#: Finest step derived from a published range.  Beyond this a setpoint would be
#: adjustable in increments no device resolves.
MAX_NUMBER_DECIMALS: Final = 3

#: Upper bound for setpoints the catalog publishes without a range.
UNBOUNDED_NUMBER_MAX: Final = 65535.0

#: Curated names, keyed by BlueRange sensor type, for the ``sensor`` platform.
SENSOR_TRANSLATION_KEYS: Final[dict[str, str]] = {
    "ACTUAL_FLOW": "actual_flow",
    "ACTUAL_TEMPERATURE": "actual_temperature",
    "BATTERY": "battery",
    "BRIGHTNESS": "brightness",
    "CHARGE_CONSUMPTION": "charge_consumption",
    "CO2": "co2",
    "ERROR_FLAGS": "error_flags",
    "EXTERNAL_TEMPERATURE": "external_temperature",
    "HUMIDITY": "humidity",
    "LIGHT_SOURCE_ON_TIME_NOT_RESETTABLE": "light_source_on_time",
    "MAX_TEMPERATURE": "max_temperature",
    "MESH_ACCESS_DURATION": "mesh_access_duration",
    "MIN_TEMPERATURE": "min_temperature",
    "MOTION": "motion_level",
    "PRESENT_AMBIENT_LIGHT_LEVEL": "ambient_light_level",
    "PRESENT_AMBIENT_NOISE": "ambient_noise",
    "PRESENT_AMBIENT_VOC_CONCENTRATION": "voc_concentration",
    "PRESENT_DEVICE_INPUT_POWER": "input_power",
    "SETPOINT_TEMPERATURE": "setpoint_temperature",
    "SETPOINT_TEMPERATURE_CALCULATED": "setpoint_temperature_calculated",
    "TASK_TUNING": "task_tuning",
    "TEMPERATURE": "temperature",
    "TOTAL_DEVICE_ENERGY_USE": "energy_use",
    "TOTAL_DEVICE_POWER_ON_TIME": "power_on_time",
    "VALVE_POSITION": "valve_position",
}

#: Curated names, keyed by BlueRange sensor type, for the ``binary_sensor`` platform.
BINARY_SENSOR_TRANSLATION_KEYS: Final[dict[str, str]] = {
    "BOOST_MODE": "boost_mode",
    "CHILD_PROTECTION": "child_protection",
    "ENERGY_SAVING_MODE": "energy_saving_mode",
    "HEATING_MODE": "heating_mode",
    "HEATING_OFF": "heating_off",
    "NIGHT_MODE": "night_mode",
    "OCCUPANCY": "occupancy",
    "PRESENCE": "presence",
    "USES_EXTERNAL_TEMPERATURE": "uses_external_temperature",
    "WINDOW_TIMER_ACTIVE": "window_timer_active",
}

#: Curated names, keyed by BlueRange actuator type, for the ``switch`` platform.
SWITCH_TRANSLATION_KEYS: Final[dict[str, str]] = {
    "SET_CHILD_PROTECTION": "child_protection",
    "SET_NIGHT_MODE": "night_mode",
    "TURN_ON_OFF": "power",
}

#: Curated names, keyed by BlueRange actuator type, for the ``number`` platform.
NUMBER_TRANSLATION_KEYS: Final[dict[str, str]] = {
    "SET_EXTERNAL_TEMPERATURE": "external_temperature",
    "SET_MAX_TEMPERATURE": "max_temperature",
    "SET_MIN_TEMPERATURE": "min_temperature",
    "SET_SETPOINT_TEMPERATURE_ADJUSTMENT": "setpoint_temperature_adjustment",
}

#: Curated names, keyed by BlueRange actuator type, for the ``button`` platform.
BUTTON_TRANSLATION_KEYS: Final[dict[str, str]] = {
    "RESET_EXTERNAL_TEMPERATURE": "reset_external_temperature",
    "RESET_HOST": "reset_host",
    "SET_AUTO": "automatic",
    "SET_ENERGY_SAVING_MODE": "energy_saving_mode",
    "SET_HEATING_MODE": "heating_mode",
    "SET_HEATING_OFF": "heating_off",
    "TRIGGER_ADAPTATION": "trigger_adaptation",
    "TURN_OFF": "turn_off",
    "TURN_ON": "turn_on",
}


def sensor_metadata(control_type: str, unit: str | None) -> UnitMeta:
    """Return the Home Assistant metadata for one sensor."""
    base = UNIT_MAP.get(unit or "", UnitMeta(state_class=None))
    override = SENSOR_OVERRIDES.get(control_type)
    if override is None or (
        override.units is not None and (unit or "") not in override.units
    ):
        return base
    return UnitMeta(
        unit=base.unit,
        device_class=override.device_class or base.device_class,
        state_class=override.state_class or base.state_class,
        precision=override.precision
        if override.precision is not None
        else base.precision,
    )


def number_step(
    unit: str | None, minimum: float | None, maximum: float | None
) -> float:
    """Return the increment a setpoint can be adjusted in.

    The unit is the better hint where there is one, because it says what the
    device does with the value rather than only how it was published.  Failing
    that, a range published with decimals - a slat tilt time of 0.1 to 10
    seconds - is meant to be set at that resolution, which a step of one whole
    unit could not reach.
    """
    if (step := NUMBER_STEPS.get(unit or "")) is not None:
        return step
    decimals = max(_decimals(minimum), _decimals(maximum))
    if decimals:
        return 10.0**-decimals
    return DEFAULT_NUMBER_STEP


def _decimals(value: float | None) -> int:
    """Return how many decimals a published bound carries.

    Normalising first, because a whole bound reaches this as ``10.0`` and would
    otherwise look like it carried one.
    """
    if value is None:
        return 0
    exponent = Decimal(str(value)).normalize().as_tuple().exponent
    if not isinstance(exponent, int) or exponent >= 0:
        return 0
    return min(-exponent, MAX_NUMBER_DECIMALS)


def entity_category(control_type: str) -> EntityCategory | None:
    """Return ``DIAGNOSTIC`` for controls that are not part of normal operation."""
    upper = control_type.upper()
    if any(marker in upper for marker in DIAGNOSTIC_MARKERS):
        return EntityCategory.DIAGNOSTIC
    return None


def config_entity_category(control_type: str) -> EntityCategory | None:
    """Return ``CONFIG`` for writable controls that only tune the device.

    Test and debug actuators are not part of normal operation, but unlike a
    reading they can be changed, so they belong to the configuration section
    rather than to diagnostics.
    """
    upper = control_type.upper()
    if any(marker in upper for marker in DIAGNOSTIC_MARKERS):
        return EntityCategory.CONFIG
    return None


def humanize(control_type: str) -> str:
    """Turn a BlueRange control type into a readable fallback name.

    ``PRESENT_AMBIENT_LIGHT_LEVEL`` becomes ``Present ambient light level``.
    Vendor specific types that already carry their own structure, such as
    ``LH2|CONF1|follow_up_pir_center``, keep their segments.
    """
    if "|" in control_type:
        control_type = control_type.rsplit("|", 1)[-1]
    words = control_type.replace("_", " ").strip()
    if not words:
        return control_type
    if words.isupper() or words.islower():
        return words.capitalize()
    return words


def actuator_label(control_type: str) -> str:
    """Return the readable fallback name of an actuator.

    The ``SET_`` prefix is dropped because the entity itself already conveys
    that it writes a value: ``SET_CHILD_PROTECTION`` becomes
    ``Child protection``.
    """
    return humanize(control_type.removeprefix("SET_") or control_type)
