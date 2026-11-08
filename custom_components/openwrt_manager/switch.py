"""Switch platform to allow or block client internet access via OpenWrt firewall."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import OpenWrtDataUpdateCoordinator
from .entity import client_device_info

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up firewall internet access switches for OpenWrt clients."""
    coordinator: OpenWrtDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    added_macs: set[str] = set()

    @callback
    def add_client_switches(macs: set[str] | None = None) -> None:
        """Add internet control switches for clients."""
        target_macs = macs if macs is not None else set(coordinator.data.keys())
        new_entities: list[OpenWrtInternetAccessSwitch] = []

        for mac in target_macs:
            if mac in added_macs or mac not in coordinator.data:
                continue
            added_macs.add(mac)
            new_entities.append(OpenWrtInternetAccessSwitch(coordinator, entry, mac))

        if new_entities:
            async_add_entities(new_entities)

    # Add existing clients
    if coordinator.data:
        add_client_switches()

    # Listen for newly discovered clients
    entry.async_on_unload(
        async_dispatcher_connect(
            hass,
            f"{DOMAIN}_{entry.entry_id}_new_client",
            add_client_switches,
        )
    )


class OpenWrtInternetAccessSwitch(
    CoordinatorEntity[OpenWrtDataUpdateCoordinator], SwitchEntity
):
    """Switch representing internet access for an OpenWrt client."""

    _attr_has_entity_name = True
    _attr_name = "Internet Access"

    def __init__(
        self,
        coordinator: OpenWrtDataUpdateCoordinator,
        entry: ConfigEntry,
        mac: str,
    ) -> None:
        """Initialize the internet access switch."""
        super().__init__(coordinator)
        self._mac = mac
        self._entry = entry
        self._attr_unique_id = f"{mac}_internet_access"

    @property
    def client_data(self):
        """Get current client data from coordinator."""
        return self.coordinator.data.get(self._mac)

    @property
    def is_on(self) -> bool:
        """Return True if internet access is allowed (not blocked by firewall)."""
        client = self.client_data
        return not client.internet_blocked if client else True

    @property
    def icon(self) -> str:
        """Return icon depending on internet access status."""
        return "mdi:web" if self.is_on else "mdi:web-off"

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Allow internet access (remove/disable firewall block rule)."""
        _LOGGER.info("Enabling internet access for client %s", self._mac)
        await self.coordinator.async_unblock_client(self._mac)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Block internet access (add/enable firewall block rule)."""
        _LOGGER.info("Blocking internet access for client %s", self._mac)
        await self.coordinator.async_block_client(self._mac)

    @property
    def device_info(self) -> DeviceInfo:
        """Link this switch to its client device, connected via the router."""
        return client_device_info(self.client_data, self._mac, self._entry.entry_id)

    @property
    def extra_state_attributes(self) -> dict[str, str | None]:
        """Return state attributes describing firewall details."""
        client = self.client_data
        if not client:
            return {}
        return {
            "mac_address": client.mac,
            "ip_address": client.ip,
            "firewall_rule_id": client.firewall_rule_id,
            "firewall_target": self.coordinator.openwrt_manager.firewall_action,
        }
