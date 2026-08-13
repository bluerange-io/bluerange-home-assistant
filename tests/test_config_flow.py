"""Tests for the BlueRange config flow."""

from __future__ import annotations

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.bluerange.const import (
    CONF_ORGANIZATION,
    CONF_USE_MQTT,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)
from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import (
    CONF_ACCESS_TOKEN,
    CONF_SCAN_INTERVAL,
    CONF_URL,
    CONF_VERIFY_SSL,
)
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from .conftest import BASE_URL, TOKEN, mock_bluerange_api, setup_integration

USER_INPUT = {
    CONF_URL: "portal.example.com",
    CONF_ACCESS_TOKEN: TOKEN,
    CONF_VERIFY_SSL: True,
}


async def start_user_flow(hass: HomeAssistant) -> dict:
    """Open the user step of the config flow."""
    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )


async def test_user_flow(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A valid token creates an entry for the organisation."""
    mock_bluerange_api(aioclient_mock)
    result = await start_user_flow(hass)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Acme (portal.example.com)"
    assert result["result"].unique_id == "portal.example.com:org-1"
    # The entered address is stored normalised, the organisation explicitly.
    assert result["data"] == {
        CONF_URL: BASE_URL,
        CONF_ACCESS_TOKEN: TOKEN,
        CONF_VERIFY_SSL: True,
        CONF_ORGANIZATION: "org-1",
    }


@pytest.mark.parametrize(
    ("status", "expected"),
    [(401, {"base": "invalid_auth"}), (500, {"base": "unknown"})],
)
async def test_user_flow_server_errors(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    status: int,
    expected: dict[str, str],
) -> None:
    """A rejected or broken server is reported on the form."""
    aioclient_mock.get(
        f"{BASE_URL}/api/v1/security/currentAuthorization/user", status=status
    )
    result = await start_user_flow(hass)

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == expected


async def test_user_flow_cannot_connect_then_recovers(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """After a failed attempt the form can be submitted again."""
    aioclient_mock.get(
        f"{BASE_URL}/api/v1/security/currentAuthorization/user", exc=TimeoutError
    )
    result = await start_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )

    assert result["errors"] == {"base": "cannot_connect"}

    aioclient_mock.clear_requests()
    mock_bluerange_api(aioclient_mock)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_user_flow_rejects_a_bad_url(hass: HomeAssistant) -> None:
    """An address that cannot host an API is reported on the field."""
    result = await start_user_flow(hass)

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**USER_INPUT, CONF_URL: "not a url"}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_URL: "invalid_url"}


async def test_duplicate_organisation_is_rejected(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """One entry per organisation is enough."""
    mock_bluerange_api(aioclient_mock)
    config_entry.add_to_hass(hass)
    result = await start_user_flow(hass)

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reauth_updates_the_token(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """A new token replaces the old one on the existing entry."""
    mock_bluerange_api(aioclient_mock)
    await setup_integration(hass, config_entry)

    result = await config_entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ACCESS_TOKEN: "token-new"}
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert config_entry.data[CONF_ACCESS_TOKEN] == "token-new"
    assert config_entry.data[CONF_URL] == BASE_URL


async def test_reauth_rejects_another_organisation(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """A token of a different tenant would silently swap all devices."""
    mock_bluerange_api(aioclient_mock)
    await setup_integration(hass, config_entry)

    aioclient_mock.clear_requests()
    mock_bluerange_api(
        aioclient_mock,
        user={"uuid": "user-2", "organizationUuid": "org-2"},
        organizations={
            "status": "0",
            "results": [{"uuid": "org-2", "name": "Other"}],
        },
    )

    result = await config_entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ACCESS_TOKEN: "token-other"}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_account"
    assert config_entry.data[CONF_ACCESS_TOKEN] == TOKEN


async def test_reconfigure_updates_the_connection(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """The server address of an existing entry can be changed."""
    mock_bluerange_api(aioclient_mock)
    await setup_integration(hass, config_entry)

    result = await config_entry.start_reconfigure_flow(hass)
    assert result["step_id"] == "reconfigure"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_URL: "portal.example.com",
            CONF_ACCESS_TOKEN: TOKEN,
            CONF_VERIFY_SSL: False,
        },
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert config_entry.data[CONF_VERIFY_SSL] is False


async def test_options_change_the_update_interval(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_api: AiohttpClientMocker
) -> None:
    """The polling interval is configurable for large installations."""
    await setup_integration(hass, config_entry)

    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    assert result["step_id"] == "init"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_SCAN_INTERVAL: 300, CONF_USE_MQTT: False}
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert config_entry.options == {CONF_SCAN_INTERVAL: 300, CONF_USE_MQTT: False}
    assert config_entry.runtime_data.update_interval.total_seconds() == 300


async def test_default_update_interval(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_api: AiohttpClientMocker
) -> None:
    """Without options the default interval is used."""
    await setup_integration(hass, config_entry)

    assert config_entry.runtime_data.update_interval.total_seconds() == (
        DEFAULT_SCAN_INTERVAL
    )


TWO_ORGANIZATIONS = {
    "status": "0",
    "results": [
        {"uuid": "org-1", "name": "Acme", "uniqueName": "acme"},
        {
            "uuid": "org-2",
            "name": "Acme",
            "uniqueName": "acme-eu",
            "duplicateName": True,
        },
    ],
}


async def test_user_flow_asks_which_organisation(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A token reaching several organisations has to be pinned down to one."""
    mock_bluerange_api(aioclient_mock, organizations=TWO_ORGANIZATIONS)
    result = await start_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "organization"
    options = result["data_schema"].schema[CONF_ORGANIZATION].config["options"]
    # A name shared by two organisations is qualified with its unique name.
    assert options == [
        {"value": "org-1", "label": "Acme"},
        {"value": "org-2", "label": "Acme (acme-eu)"},
    ]

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ORGANIZATION: "org-2"}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Acme (acme-eu) (portal.example.com)"
    assert result["data"][CONF_ORGANIZATION] == "org-2"
    assert result["result"].unique_id == "portal.example.com:org-2"


async def test_two_organisations_of_one_server_coexist(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """The second organisation of the same server is a separate entry."""
    mock_bluerange_api(aioclient_mock, organizations=TWO_ORGANIZATIONS)
    config_entry.add_to_hass(hass)

    result = await start_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ORGANIZATION: "org-2"}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert len(hass.config_entries.async_entries(DOMAIN)) == 2


async def test_the_same_organisation_is_still_rejected(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """Choosing an organisation that is already set up is a duplicate."""
    mock_bluerange_api(aioclient_mock, organizations=TWO_ORGANIZATIONS)
    config_entry.add_to_hass(hass)

    result = await start_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ORGANIZATION: "org-1"}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_organisation_falls_back_to_the_home_organisation(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A token that may not enumerate organisations still belongs to one."""
    mock_bluerange_api(aioclient_mock, organizations={"status": "0", "results": []})
    result = await start_user_flow(hass)

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_ORGANIZATION] == "org-1"


async def test_no_organisation_at_all_is_an_error(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Without an organisation there is nothing to mirror."""
    mock_bluerange_api(
        aioclient_mock,
        user={"uuid": "user-1"},
        organizations={"status": "0", "results": []},
    )
    result = await start_user_flow(hass)

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "no_organizations"}


async def test_reconfigure_can_switch_organisation(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """Switching to another organisation would replace every device."""
    mock_bluerange_api(aioclient_mock, organizations=TWO_ORGANIZATIONS)
    await setup_integration(hass, config_entry)

    result = await config_entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    assert result["step_id"] == "organization"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ORGANIZATION: "org-2"}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_account"
    assert config_entry.data[CONF_ORGANIZATION] == "org-1"
