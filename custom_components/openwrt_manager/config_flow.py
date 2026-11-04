"""Config flow for OpenWrt Router integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_SCAN_INTERVAL,
    CONF_USERNAME,
)
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult

from .const import (
    ACTION_DROP,
    ACTION_REJECT,
    CONF_FIREWALL_ACTION,
    CONF_FIREWALL_RULE_NAME,
    DEFAULT_FIREWALL_ACTION,
    DEFAULT_FIREWALL_RULE_NAME,
    DEFAULT_PORT_SSH,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)
from .openwrt_client.openwrt_client import (
    CannotConnect,
    EndpointNotUbus,
    InvalidAuth,
    OpenWrtClient,
)
from .openwrt_client.openwrt_manager import OpenWrtManager

_LOGGER = logging.getLogger(__name__)


class OpenWrtConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):  # type: ignore[call-arg]
    """Handle a config flow for OpenWrt Router."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial setup step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            raw_port = user_input.get(CONF_PORT, DEFAULT_PORT_SSH)

            port = DEFAULT_PORT_SSH if raw_port is None or raw_port <= 0 else raw_port

            user_input[CONF_PORT] = port
            host = user_input[CONF_HOST].strip()
            username = user_input.get(CONF_USERNAME, "root")
            password = user_input.get(CONF_PASSWORD, "")

            firewall_rule_name = user_input.get(
                CONF_FIREWALL_RULE_NAME, DEFAULT_FIREWALL_RULE_NAME
            )
            firewall_action = user_input.get(
                CONF_FIREWALL_ACTION, DEFAULT_FIREWALL_ACTION
            )

            openwrt_client = OpenWrtClient(
                host=host,
                port=port,
                username=username,
                password=password,
            )

            openwrt_manager = OpenWrtManager(
                openwrt_client=openwrt_client,
                firewall_rule_name=firewall_rule_name,
                firewall_action=firewall_action,
            )

            try:
                await openwrt_client.async_login()
                router_info = await openwrt_manager.async_get_router_info()
            except EndpointNotUbus:
                errors["base"] = "ubus_not_found"
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except Exception as ex:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception connecting to OpenWrt: %s", ex)
                errors["base"] = "unknown"
            finally:
                await openwrt_client.close()

            if not errors:
                unique_id = (
                    router_info.mac
                    if router_info.mac != "00:00:00:00:00:00"
                    else f"{host}:{port}"
                )
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_configured()

                title = (
                    router_info.hostname
                    if router_info.hostname
                    else f"OpenWrt ({host})"
                )
                return self.async_create_entry(title=title, data=user_input)

        schema = vol.Schema(
            {
                vol.Required(CONF_HOST, default="192.168.1.1"): str,
                vol.Required(CONF_PORT, default=DEFAULT_PORT_SSH): int,
                vol.Required(CONF_USERNAME, default="root"): str,
                vol.Required(CONF_PASSWORD): str,
                vol.Required(
                    CONF_FIREWALL_RULE_NAME, default=DEFAULT_FIREWALL_RULE_NAME
                ): str,
                vol.Optional(
                    CONF_FIREWALL_ACTION, default=DEFAULT_FIREWALL_ACTION
                ): vol.In([ACTION_REJECT, ACTION_DROP]),
                vol.Optional(
                    CONF_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL
                ): vol.All(int, vol.Range(min=5, max=300)),
            }
        )

        return self.async_show_form(
            step_id="user",
            data_schema=schema,
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> OpenWrtOptionsFlow:
        """Return the options flow handler."""
        return OpenWrtOptionsFlow(config_entry)


class OpenWrtOptionsFlow(config_entries.OptionsFlow):
    """Handle OpenWrt options."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""
        self.config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage options for OpenWrt router."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current_interval = self.config_entry.options.get(
            CONF_SCAN_INTERVAL,
            self.config_entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
        )
        current_action = self.config_entry.options.get(
            CONF_FIREWALL_ACTION,
            self.config_entry.data.get(CONF_FIREWALL_ACTION, DEFAULT_FIREWALL_ACTION),
        )

        options_schema = vol.Schema(
            {
                vol.Optional(CONF_SCAN_INTERVAL, default=current_interval): vol.All(
                    int, vol.Range(min=5, max=300)
                ),
                vol.Optional(CONF_FIREWALL_ACTION, default=current_action): vol.In(
                    [ACTION_REJECT, ACTION_DROP]
                ),
            }
        )

        return self.async_show_form(step_id="init", data_schema=options_schema)
