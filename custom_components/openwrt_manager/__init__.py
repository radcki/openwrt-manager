"""Home Assistant Integration for OpenWrt Routers."""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_SCAN_INTERVAL,
    CONF_USERNAME,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .const import (
    CONF_FIREWALL_ACTION,
    CONF_FIREWALL_RULE_NAME,
    DEFAULT_FIREWALL_ACTION,
    DEFAULT_FIREWALL_RULE_NAME,
    DEFAULT_PORT_SSH,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    PLATFORMS,
)
from .coordinator import OpenWrtDataUpdateCoordinator
from .openwrt_client.openwrt_client import OpenWrtClient
from .openwrt_client.openwrt_manager import OpenWrtManager

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up OpenWrt Router from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    host = entry.data[CONF_HOST]
    port = entry.data.get(CONF_PORT, DEFAULT_PORT_SSH)
    username = entry.data.get(CONF_USERNAME, "root")
    password = entry.data.get(CONF_PASSWORD, "")
    firewall_action = entry.options.get(
        CONF_FIREWALL_ACTION,
        entry.data.get(CONF_FIREWALL_ACTION, DEFAULT_FIREWALL_ACTION),
    )
    firewall_rule_name = entry.options.get(
        CONF_FIREWALL_RULE_NAME,
        entry.data.get(CONF_FIREWALL_RULE_NAME, DEFAULT_FIREWALL_RULE_NAME),
    )
    scan_interval_sec = entry.options.get(
        CONF_SCAN_INTERVAL,
        entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
    )

    openwrt_client = OpenWrtClient(
        host=host, port=port, username=username, password=password
    )

    openwrt_manager = OpenWrtManager(
        openwrt_client=openwrt_client,
        firewall_rule_name=firewall_rule_name,
        firewall_action=firewall_action,
    )

    coordinator = OpenWrtDataUpdateCoordinator(
        hass=hass,
        openwrt_manager=openwrt_manager,
        entry=entry,
        update_interval=timedelta(seconds=scan_interval_sec),
    )

    # Fetch router details and register router device in registry
    router_info = await openwrt_manager.async_get_router_info()
    coordinator.router_info = router_info

    device_registry = dr.async_get(hass)
    router_device = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        connections={(dr.CONNECTION_NETWORK_MAC, router_info.mac)}
        if router_info.mac
        else set(),
        manufacturer="OpenWrt",
        model=router_info.model,
        name=router_info.hostname or "OpenWrt Router",
        sw_version=router_info.release,
    )
    # Store the router's device registry ID so entity platforms can use via_device_id
    coordinator.router_device_id = router_device.id

    # Initial data load
    await coordinator.async_config_entry_first_refresh()

    hass.data[DOMAIN][entry.entry_id] = coordinator

    # Forward setup to entity platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload an OpenWrt Router config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        coordinator: OpenWrtDataUpdateCoordinator = hass.data[DOMAIN].pop(
            entry.entry_id
        )
        await coordinator.openwrt_manager.openwrt_client.close()

    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry when options change."""
    await hass.config_entries.async_reload(entry.entry_id)
