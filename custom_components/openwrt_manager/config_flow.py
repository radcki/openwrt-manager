"""Config flow for OpenWrt Router integration."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from typing import Any
from uuid import uuid4

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
    CONF_HOST_KEY,
    DEFAULT_FIREWALL_ACTION,
    DEFAULT_FIREWALL_RULE_NAME,
    DEFAULT_PORT_SSH,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)
from .openwrt_client.openwrt_client import (
    CannotConnect,
    InvalidAuth,
    OpenWrtClient,
)
from .openwrt_client.openwrt_manager import OpenWrtManager
from .sources.factory import create_source
from .sources.openwrt import OpenWrtAccessPointSource
from .sources.unifi import UniFiSource

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
                user_input[CONF_HOST_KEY] = openwrt_client.host_key
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
        return OpenWrtOptionsFlow()

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> FlowResult:
        """Start re-authentication after credentials were rejected."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Ask for a new password."""
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()

        if user_input is not None:
            client = OpenWrtClient(
                host=entry.data[CONF_HOST],
                port=entry.data.get(CONF_PORT, DEFAULT_PORT_SSH),
                username=entry.data.get(CONF_USERNAME, "root"),
                password=user_input[CONF_PASSWORD],
                host_key=entry.data.get(CONF_HOST_KEY),
            )
            try:
                await client.async_login()
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except CannotConnect:
                errors["base"] = "cannot_connect"
            finally:
                await client.close()

            if not errors:
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={CONF_PASSWORD: user_input[CONF_PASSWORD]},
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): str}),
            errors=errors,
        )


class OpenWrtOptionsFlow(config_entries.OptionsFlow):
    """Handle OpenWrt options."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Offer router options and any number of additional AP sources."""
        return self.async_show_menu(
            step_id="init",
            menu_options=[
                "router",
                "add_unifi",
                "add_openwrt",
                "manage_sources",
                "remove_ap_source",
            ],
        )

    def _save_source(self, source: dict[str, Any] | None) -> FlowResult:
        sources = list(self.config_entry.options.get("ap_sources", []))
        source_id = self._source_config["id"]
        sources = [existing for existing in sources if existing["id"] != source_id]
        if source is not None:
            original = list(self.config_entry.options.get("ap_sources", []))
            index = next(
                (
                    index
                    for index, item in enumerate(original)
                    if item["id"] == source_id
                ),
                len(sources),
            )
            sources.insert(index, source)
        return self.async_create_entry(
            title="", data={**self.config_entry.options, "ap_sources": sources}
        )

    async def async_step_add_unifi(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Configure an independent UniFi controller/site connection."""
        self._source_config = {"id": uuid4().hex, "type": "unifi", "enabled": True}
        return await self.async_step_source()

    async def async_step_add_openwrt(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Configure an independent OpenWrt AP connection."""
        self._source_config = {"id": uuid4().hex, "type": "openwrt", "enabled": True}
        return await self.async_step_source()

    async def async_step_manage_sources(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Select an instance by ID rather than by source type."""
        sources = self.config_entry.options.get("ap_sources", [])
        if not sources:
            return self.async_abort(reason="no_sources")
        if user_input is not None:
            self._source_config = dict(
                next(
                    source
                    for source in sources
                    if source["id"] == user_input["source_id"]
                )
            )
            return await self.async_step_source_action()
        return self.async_show_form(
            step_id="manage_sources",
            data_schema=self._source_selection_schema(),
        )

    async def async_step_source_action(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Present the selected source's actions as a registered HA flow step."""
        return self.async_show_menu(
            step_id="source_action",
            menu_options=["source", "toggle_source", "remove_source"],
        )

    def _source_selection_schema(self) -> vol.Schema:
        """Label instances by name, type and host while persisting their IDs."""
        return vol.Schema(
            {
                vol.Required("source_id"): vol.In(
                    {
                        source[
                            "id"
                        ]: f"{source['name']} ({source['type']}, {source['host']})"
                        for source in self.config_entry.options.get("ap_sources", [])
                    }
                )
            }
        )

    async def async_step_remove_ap_source(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Offer source removal directly from the main options menu."""
        sources = self.config_entry.options.get("ap_sources", [])
        if not sources:
            return self.async_abort(reason="no_sources")
        if user_input is not None:
            self._source_config = dict(
                next(
                    source
                    for source in sources
                    if source["id"] == user_input["source_id"]
                )
            )
            return self._save_source(None)
        return self.async_show_form(
            step_id="remove_ap_source", data_schema=self._source_selection_schema()
        )

    async def async_step_toggle_source(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Enable or disable only the selected instance."""
        self._source_config["enabled"] = not self._source_config.get("enabled", True)
        return self._save_source(self._source_config)

    async def async_step_remove_source(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Remove a source without removing client entities or firewall rules."""
        return self._save_source(None)

    async def async_step_source(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Validate credentials before persisting source configuration."""
        errors: dict[str, str] = {}
        config = self._source_config
        if user_input is not None:
            config = {**config, **user_input, "host": user_input["host"].strip()}
            source = await create_source(config)
            try:
                async with asyncio.timeout(30):
                    if isinstance(source, UniFiSource):
                        self._sites = await source.async_sites()
                        if not self._sites:
                            raise ValueError("No accessible sites")
                    else:
                        await source.async_refresh()
                        if isinstance(source, OpenWrtAccessPointSource):
                            config["host_key"] = source.client.host_key
            except Exception as error:
                _LOGGER.warning(
                    "AP source validation failed (%s)", type(error).__name__
                )
                errors["base"] = "source_connection_failed"
            finally:
                await source.close()
            if not errors:
                self._source_config = config
                if config["type"] == "unifi":
                    return await self.async_step_site()
                return self._save_source(config)
        fields: dict[Any, Any] = {
            vol.Required("name", default=config.get("name", "")): vol.All(
                str, vol.Length(min=1)
            ),
            vol.Required(CONF_HOST, default=config.get(CONF_HOST, "")): vol.All(
                str, vol.Length(min=1)
            ),
            vol.Required(
                CONF_PORT,
                default=config.get(CONF_PORT, 443 if config["type"] == "unifi" else 22),
            ): vol.All(int, vol.Range(min=1, max=65535)),
            vol.Required(
                CONF_USERNAME,
                default=config.get(
                    CONF_USERNAME, "" if config["type"] == "unifi" else "root"
                ),
            ): str,
            vol.Required(CONF_PASSWORD, default=config.get(CONF_PASSWORD, "")): str,
        }
        if config["type"] == "unifi":
            fields[
                vol.Required("verify_ssl", default=config.get("verify_ssl", True))
            ] = bool
        return self.async_show_form(
            step_id="source", data_schema=vol.Schema(fields), errors=errors
        )

    async def async_step_site(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Choose and validate the controller site used for telemetry."""
        errors: dict[str, str] = {}
        if user_input is not None:
            config = {**self._source_config, "site": user_input["site"]}
            source = await create_source(config)
            try:
                async with asyncio.timeout(30):
                    await source.async_refresh()
            except Exception as error:
                _LOGGER.warning("AP site validation failed (%s)", type(error).__name__)
                errors["base"] = "source_connection_failed"
            finally:
                await source.close()
            if not errors:
                return self._save_source(config)
        return self.async_show_form(
            step_id="site",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        "site",
                        default=self._source_config.get(
                            "site", next(iter(self._sites))
                        ),
                    ): vol.In(self._sites)
                }
            ),
            errors=errors,
        )

    async def async_step_router(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage options for OpenWrt router."""
        if user_input is not None:
            return self.async_create_entry(
                title="", data={**self.config_entry.options, **user_input}
            )

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

        return self.async_show_form(step_id="router", data_schema=options_schema)
