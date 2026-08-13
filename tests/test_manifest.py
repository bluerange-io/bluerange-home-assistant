"""Tests for the integration metadata that Home Assistant validates."""

from __future__ import annotations

import json
from pathlib import Path

import voluptuous as vol
import yaml

from custom_components.bluerange import PLATFORMS, const
from custom_components.bluerange.services import SET_ACTUATOR_SCHEMA
import homeassistant.components.mqtt
from homeassistant.const import ATTR_DEVICE_ID

COMPONENT_PATH = Path(const.__file__).parent

#: Keys hassfest requires from a custom integration.
REQUIRED_MANIFEST_KEYS = {
    "domain",
    "name",
    "codeowners",
    "documentation",
    "integration_type",
    "iot_class",
    "requirements",
    "version",
}

VALID_IOT_CLASSES = {
    "assumed_state",
    "calculated",
    "cloud_polling",
    "cloud_push",
    "local_polling",
    "local_push",
}

VALID_INTEGRATION_TYPES = {
    "device",
    "entity",
    "hardware",
    "helper",
    "hub",
    "service",
    "system",
    "virtual",
}


def manifest() -> dict:
    """Return the parsed manifest."""
    return json.loads((COMPONENT_PATH / "manifest.json").read_text(encoding="utf-8"))


def test_manifest_is_complete() -> None:
    """A missing key stops the integration from loading."""
    assert set(manifest()) >= REQUIRED_MANIFEST_KEYS


def test_manifest_values() -> None:
    """The declared classification has to be one Home Assistant knows."""
    data = manifest()

    assert data["domain"] == const.DOMAIN
    assert data["config_flow"] is True
    assert data["iot_class"] in VALID_IOT_CLASSES
    assert data["integration_type"] in VALID_INTEGRATION_TYPES


def test_paho_pin_matches_home_assistant() -> None:
    """A different pin than Home Assistant's own would fight over the package."""
    ours = manifest()["requirements"]
    theirs = json.loads(
        (
            Path(homeassistant.components.mqtt.__file__).parent / "manifest.json"
        ).read_text(encoding="utf-8")
    )["requirements"]

    assert ours == [
        requirement for requirement in theirs if requirement.startswith("paho-mqtt")
    ]


def test_version_follows_the_home_assistant_scheme() -> None:
    """Releases are dated like Home Assistant's own: YYYY.M.PATCH."""
    year, month, patch = manifest()["version"].split(".")

    assert 2020 <= int(year) <= 2100
    assert 1 <= int(month) <= 12
    assert month == str(int(month)), "the month is not zero padded"
    assert patch.isdigit()


def test_manifest_domain_matches_the_directory() -> None:
    """Home Assistant looks the integration up by its directory name."""
    assert manifest()["domain"] == COMPONENT_PATH.name


def test_every_platform_module_exists() -> None:
    """A forwarded platform without a module would fail at setup."""
    for platform in PLATFORMS:
        assert (COMPONENT_PATH / f"{platform.value}.py").is_file()


def test_services_yaml_matches_the_schema() -> None:
    """A field only reaches the service if both sides declare it."""
    services = yaml.safe_load(
        (COMPONENT_PATH / "services.yaml").read_text(encoding="utf-8")
    )

    assert set(services) == {const.SERVICE_SET_ACTUATOR}

    documented = set(services[const.SERVICE_SET_ACTUATOR]["fields"])
    accepted = {
        str(key.schema if isinstance(key, vol.Marker) else key)
        for key in SET_ACTUATOR_SCHEMA.schema
    }
    assert documented == accepted


def test_services_are_described() -> None:
    """An undocumented service shows up without a name in the UI."""
    strings = json.loads((COMPONENT_PATH / "strings.json").read_text(encoding="utf-8"))
    described = strings["services"][const.SERVICE_SET_ACTUATOR]

    assert described["name"]
    assert described["description"]
    assert ATTR_DEVICE_ID in described["fields"]
