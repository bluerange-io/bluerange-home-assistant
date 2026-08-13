"""Config flow for the BlueRange integration."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    SOURCE_RECONFIGURE,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import (
    CONF_ACCESS_TOKEN,
    CONF_SCAN_INTERVAL,
    CONF_URL,
    CONF_VERIFY_SSL,
)
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import (
    BlueRangeAuthError,
    BlueRangeClient,
    BlueRangeConnectionError,
    BlueRangeError,
    BlueRangeOrganization,
    normalize_base_url,
)
from .const import (
    CONF_ORGANIZATION,
    CONF_USE_MQTT,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_USE_MQTT,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
)
from .coordinator import BlueRangeConfigEntry

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_URL): TextSelector(
            TextSelectorConfig(type=TextSelectorType.URL, autocomplete="url")
        ),
        vol.Required(CONF_ACCESS_TOKEN): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        ),
        vol.Optional(CONF_VERIFY_SSL, default=True): BooleanSelector(),
    }
)

STEP_REAUTH_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_ACCESS_TOKEN): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        ),
    }
)


class BlueRangeConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the configuration of a BlueRange server.

    An API token may reach several organisations, and which one the server picks
    when it is not told is not something to rely on, so the organisation is part
    of the configuration.  One config entry covers one organisation, and a second
    entry can be added for another organisation of the same server.
    """

    VERSION = 1

    def __init__(self) -> None:
        """Start out with nothing entered."""
        self._connection: dict[str, Any] = {}
        self._organizations: list[BlueRangeOrganization] = []

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the server URL and an API token."""
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = await self._async_validate(user_input)
            if not errors:
                return await self.async_step_organization()

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                STEP_USER_SCHEMA, user_input
            ),
            errors=errors,
        )

    async def async_step_organization(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask which organisation to mirror, unless there is only one."""
        if user_input is not None:
            chosen = self._organization(user_input[CONF_ORGANIZATION])
            if chosen is not None:
                return await self._async_finish(chosen)

        if len(self._organizations) == 1:
            return await self._async_finish(self._organizations[0])

        return self.async_show_form(
            step_id="organization",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ORGANIZATION): SelectSelector(
                        SelectSelectorConfig(
                            options=[
                                SelectOptionDict(
                                    value=organization.uuid, label=organization.label
                                )
                                for organization in self._organizations
                            ],
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    )
                }
            ),
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Handle an expired or revoked API token."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for a fresh API token for an existing entry."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = await self._async_validate({**entry.data, **user_input})
            if not errors:
                organization = entry.data.get(CONF_ORGANIZATION)
                if organization and self._organization(organization) is None:
                    # A token of another tenant would swap out every device.
                    return self.async_abort(reason="wrong_account")
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={CONF_ACCESS_TOKEN: user_input[CONF_ACCESS_TOKEN]},
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=STEP_REAUTH_SCHEMA,
            description_placeholders={"url": entry.data[CONF_URL]},
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Let the user point an existing entry at a different server."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = await self._async_validate(user_input)
            if not errors:
                return await self.async_step_organization()

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                STEP_USER_SCHEMA, user_input or dict(entry.data)
            ),
            errors=errors,
        )

    async def _async_validate(self, user_input: Mapping[str, Any]) -> dict[str, str]:
        """Check the credentials and remember what they give access to."""
        try:
            base_url = normalize_base_url(user_input[CONF_URL])
        except BlueRangeError:
            return {CONF_URL: "invalid_url"}

        session = async_get_clientsession(
            self.hass, verify_ssl=user_input.get(CONF_VERIFY_SSL, True)
        )
        client = BlueRangeClient(session, str(base_url), user_input[CONF_ACCESS_TOKEN])
        try:
            user = await client.async_get_current_user()
            organizations = await client.async_get_organizations()
        except BlueRangeAuthError:
            return {"base": "invalid_auth"}
        except BlueRangeConnectionError:
            return {"base": "cannot_connect"}
        except BlueRangeError:
            _LOGGER.exception("Unexpected error while validating the BlueRange account")
            return {"base": "unknown"}

        if not organizations:
            # A token that may not enumerate them still belongs to one.
            home = user.get("organizationUuid")
            if not home:
                return {"base": "no_organizations"}
            organizations = [
                BlueRangeOrganization(
                    uuid=home, name=user.get("organizationName") or home
                )
            ]

        self._connection = {
            CONF_URL: str(base_url),
            CONF_ACCESS_TOKEN: user_input[CONF_ACCESS_TOKEN],
            CONF_VERIFY_SSL: user_input.get(CONF_VERIFY_SSL, True),
        }
        self._organizations = organizations
        return {}

    async def _async_finish(
        self, organization: BlueRangeOrganization
    ) -> ConfigFlowResult:
        """Create or update the entry for the chosen organisation."""
        host = normalize_base_url(self._connection[CONF_URL]).host
        data = {**self._connection, CONF_ORGANIZATION: organization.uuid}
        title = f"{organization.label} ({host})"

        await self.async_set_unique_id(f"{host}:{organization.uuid}")
        if self.source == SOURCE_RECONFIGURE:
            self._abort_if_unique_id_mismatch(reason="wrong_account")
            return self.async_update_reload_and_abort(
                self._get_reconfigure_entry(), title=title, data_updates=data
            )

        self._abort_if_unique_id_configured()
        return self.async_create_entry(title=title, data=data)

    @callback
    def _organization(self, uuid: str) -> BlueRangeOrganization | None:
        """Return the validated organisation with this UUID, if there is one."""
        return next(
            (
                organization
                for organization in self._organizations
                if organization.uuid == uuid
            ),
            None,
        )

    @staticmethod
    @callback
    def async_get_options_flow(entry: BlueRangeConfigEntry) -> OptionsFlow:
        """Return the options flow handler."""
        return BlueRangeOptionsFlow()


class BlueRangeOptionsFlow(OptionsFlow):
    """Handle the options of a BlueRange entry."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Let the user choose live updates and how often the server is polled."""
        if user_input is not None:
            return self.async_create_entry(
                data={
                    CONF_USE_MQTT: user_input[CONF_USE_MQTT],
                    CONF_SCAN_INTERVAL: int(user_input[CONF_SCAN_INTERVAL]),
                }
            )

        options = self.config_entry.options
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_USE_MQTT,
                        default=options.get(CONF_USE_MQTT, DEFAULT_USE_MQTT),
                    ): BooleanSelector(),
                    vol.Required(
                        CONF_SCAN_INTERVAL,
                        default=options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=MIN_SCAN_INTERVAL,
                            max=MAX_SCAN_INTERVAL,
                            step=5,
                            mode=NumberSelectorMode.BOX,
                            unit_of_measurement="s",
                        )
                    ),
                }
            ),
        )
