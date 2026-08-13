"""Tests for the BlueRange metadata mapping and its translations."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from custom_components.bluerange import mapping
from custom_components.bluerange.entity import CHANNEL_PLACEHOLDER
from custom_components.bluerange.sensor import DEVICE_SENSORS
from homeassistant.components.sensor.const import (
    DEVICE_CLASS_STATE_CLASSES,
    DEVICE_CLASS_UNITS,
)
from homeassistant.const import EntityCategory

COMPONENT_PATH = Path(mapping.__file__).parent

#: The translation key tables of the integration, per Home Assistant platform.
TRANSLATION_KEY_TABLES = {
    "sensor": mapping.SENSOR_TRANSLATION_KEYS,
    "binary_sensor": mapping.BINARY_SENSOR_TRANSLATION_KEYS,
    "switch": mapping.SWITCH_TRANSLATION_KEYS,
    "number": mapping.NUMBER_TRANSLATION_KEYS,
    "button": mapping.BUTTON_TRANSLATION_KEYS,
}

#: Keys that come from an entity description or are set on the class itself,
#: derived from the code so that adding one cannot slip past this check.
EXTRA_TRANSLATION_KEYS = {
    "sensor": {
        description.translation_key
        for description in DEVICE_SENSORS
        if description.translation_key
    },
    "light": {"channel"},
    "climate": {"channel"},
    "cover": {"channel"},
}


def load_json(name: str) -> dict[str, Any]:
    """Return one of the integration's JSON files."""
    return json.loads((COMPONENT_PATH / name).read_text(encoding="utf-8"))


def leaf_paths(value: Any, prefix: str = "") -> set[str]:
    """Return the dotted paths of every leaf in a nested mapping."""
    if not isinstance(value, dict):
        return {prefix}
    return {
        path
        for key, child in value.items()
        for path in leaf_paths(child, f"{prefix}.{key}" if prefix else key)
    }


def test_every_translation_key_is_declared() -> None:
    """A key without an entry would leave the entity nameless."""
    entities = load_json("strings.json")["entity"]
    missing = []
    for platform, table in TRANSLATION_KEY_TABLES.items():
        expected = set(table.values()) | EXTRA_TRANSLATION_KEYS.get(platform, set())
        for key in sorted(expected):
            if "name" not in entities.get(platform, {}).get(key, {}):
                missing.append(f"{platform}.{key}")
    for platform, keys in EXTRA_TRANSLATION_KEYS.items():
        for key in sorted(keys):
            if "name" not in entities.get(platform, {}).get(key, {}):
                missing.append(f"{platform}.{key}")

    assert missing == []


def test_no_unused_translation_keys() -> None:
    """Every declared entity name is actually reachable from the code."""
    entities = load_json("strings.json")["entity"]
    used = {
        platform: set(table.values()) | EXTRA_TRANSLATION_KEYS.get(platform, set())
        for platform, table in TRANSLATION_KEY_TABLES.items()
    }
    for platform, keys in EXTRA_TRANSLATION_KEYS.items():
        used.setdefault(platform, set()).update(keys)

    unused = [
        f"{platform}.{key}"
        for platform, keys in entities.items()
        for key in keys
        if key not in used.get(platform, set())
    ]

    assert unused == []


@pytest.mark.parametrize("file", ["strings.json", "translations/de.json"])
def test_curated_control_names_take_the_channel(file: str) -> None:
    """A curated name has to have room for the channel of the control."""
    entities = load_json(file)["entity"]

    without = [
        f"{platform}.{key}"
        for platform, table in TRANSLATION_KEY_TABLES.items()
        for key in sorted(set(table.values()))
        if not entities[platform][key]["name"].endswith(f"{{{CHANNEL_PLACEHOLDER}}}")
    ]

    assert without == []


@pytest.mark.parametrize("file", ["strings.json", "translations/de.json"])
def test_device_names_take_no_placeholder(file: str) -> None:
    """A name is shown verbatim, so a placeholder nothing fills would show too."""
    entities = load_json(file)["entity"]["sensor"]

    for description in DEVICE_SENSORS:
        assert "{" not in entities[description.translation_key]["name"]


def test_english_translations_match_strings() -> None:
    """translations/en.json is the shipped copy of strings.json."""
    assert load_json("strings.json") == load_json("translations/en.json")


def test_german_translations_are_complete() -> None:
    """Every English string has a German counterpart and vice versa."""
    english = leaf_paths(load_json("strings.json"))
    german = leaf_paths(load_json("translations/de.json"))

    assert sorted(english - german) == []
    assert sorted(german - english) == []


def test_icons_reference_declared_entities() -> None:
    """An icon for an unknown key would never be applied."""
    entities = load_json("strings.json")["entity"]
    icons = load_json("icons.json")["entity"]

    unknown = [
        f"{platform}.{key}"
        for platform, keys in icons.items()
        for key in keys
        if key not in entities.get(platform, {})
    ]

    assert unknown == []


@pytest.mark.parametrize("unit", sorted(mapping.UNIT_MAP))
def test_unit_metadata_is_accepted_by_home_assistant(unit: str) -> None:
    """Home Assistant rejects a device class combined with the wrong unit."""
    meta = mapping.UNIT_MAP[unit]
    if meta.device_class is None:
        return

    allowed_units = DEVICE_CLASS_UNITS.get(meta.device_class)
    assert allowed_units is None or meta.unit in allowed_units

    allowed_states = DEVICE_CLASS_STATE_CLASSES.get(meta.device_class)
    assert not allowed_states or meta.state_class in allowed_states


@pytest.mark.parametrize("control_type", sorted(mapping.SENSOR_OVERRIDES))
def test_overrides_stay_consistent_for_every_unit(control_type: str) -> None:
    """A refinement must not force a combination Home Assistant refuses.

    The unit is reported by the server, so a control type may turn up with a unit
    its override was not written for.
    """
    for unit in [*mapping.UNIT_MAP, None, "SOMETHING_NEW"]:
        meta = mapping.sensor_metadata(control_type, unit)
        if meta.device_class is None:
            continue

        allowed_units = DEVICE_CLASS_UNITS.get(meta.device_class)
        assert allowed_units is None or meta.unit in allowed_units, (
            f"{control_type} with unit {unit}"
        )

        allowed_states = DEVICE_CLASS_STATE_CLASSES.get(meta.device_class)
        assert not allowed_states or meta.state_class in allowed_states, (
            f"{control_type} with unit {unit}"
        )


def test_override_applies_only_to_its_units() -> None:
    """A humidity reading in degrees Celsius is not a humidity."""
    assert mapping.sensor_metadata("HUMIDITY", "PERCENT").device_class is not None
    assert mapping.sensor_metadata("HUMIDITY", "CELSIUS").device_class.value == (
        "temperature"
    )


def test_unknown_unit_yields_no_state_class() -> None:
    """A value of unknown shape must not be recorded as a measurement."""
    meta = mapping.sensor_metadata("SOME_VENDOR_FLAG", None)

    assert meta.unit is None
    assert meta.state_class is None


@pytest.mark.parametrize(
    ("control_type", "expected"),
    [
        ("VENDOR_TEST_DEBUG", EntityCategory.DIAGNOSTIC),
        ("FLAG_0", EntityCategory.DIAGNOSTIC),
        ("VENDOR_ERROR_BITFIELD", EntityCategory.DIAGNOSTIC),
        ("MESH_ACCESS_DURATION", EntityCategory.DIAGNOSTIC),
        ("TEMPERATURE", None),
        ("VALVE_POSITION", None),
    ],
)
def test_entity_category(control_type: str, expected: EntityCategory | None) -> None:
    """Controls describing the device itself are diagnostics."""
    assert mapping.entity_category(control_type) == expected


def test_writable_diagnostics_are_configuration() -> None:
    """A debug control that can be changed belongs to the configuration."""
    assert mapping.config_entity_category("SET_VENDOR_TEST_DEBUG") is (
        EntityCategory.CONFIG
    )
    assert mapping.config_entity_category("SET_CHILD_PROTECTION") is None


@pytest.mark.parametrize(
    ("unit", "minimum", "maximum", "expected"),
    [
        # The unit knows better than the range: a setpoint in whole degrees is
        # still adjustable in halves.
        ("CELSIUS", 8.0, 28.0, 0.5),
        ("KELVIN", 2700.0, 6500.0, 50),
        # A range published with decimals is meant to be set that finely.
        ("SECOND", 0.1, 10.0, 0.1),
        ("SECOND", 0.01, 10.0, 0.01),
        ("SECOND", 1.0, 600.0, 1.0),
        ("PERCENT", 0.0, 100.0, 1.0),
        (None, None, None, 1.0),
        # Not something a device resolves, so the step stops at a thousandth.
        ("SECOND", 0.000001, 1.0, 0.001),
    ],
)
def test_number_step(
    unit: str | None, minimum: float | None, maximum: float | None, expected: float
) -> None:
    """A setpoint has to be adjustable in the increments its range implies."""
    assert mapping.number_step(unit, minimum, maximum) == expected


@pytest.mark.parametrize(
    ("control_type", "expected"),
    [
        ("PRESENT_AMBIENT_LIGHT_LEVEL", "Present ambient light level"),
        ("CO2", "Co2"),
        ("LH2|CONF1|follow_up_pir_center", "Follow up pir center"),
    ],
)
def test_humanize(control_type: str, expected: str) -> None:
    """Unknown control types still get a readable name."""
    assert mapping.humanize(control_type) == expected


@pytest.mark.parametrize(
    ("control_type", "expected"),
    [
        ("SET_CHILD_PROTECTION", "Child protection"),
        ("TRIGGER_ADAPTATION", "Trigger adaptation"),
        ("SET_", "Set"),
    ],
)
def test_actuator_label(control_type: str, expected: str) -> None:
    """An actuator is named after what it controls, not after the command."""
    assert mapping.actuator_label(control_type) == expected
