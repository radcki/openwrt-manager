"""DataUpdateCoordinator for OpenWrt Router integration."""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN
from .openwrt_client.OpenWrtManager import ClientDevice, OpenWrtManager, RouterInfo

_LOGGER = logging.getLogger(__name__)


class OpenWrtDataUpdateCoordinator(DataUpdateCoordinator[dict[str, ClientDevice]]):
    """Class to manage fetching OpenWrt router client data."""

    entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        openwrt_manager: OpenWrtManager,
        entry: ConfigEntry,
        update_interval: timedelta,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{entry.entry_id}",
            update_interval=update_interval,
        )
        self.openwrt_manager = openwrt_manager
        self.entry = entry
        self.router_info: RouterInfo = RouterInfo()
        self.router_device_id: str | None = None
        self.known_macs: set[str] = set()

    async def _async_update_data(self) -> dict[str, ClientDevice]:
        """Fetch latest client data and firewall states from OpenWrt."""
        try:
            clients = await self.openwrt_manager.async_get_clients()
        except Exception as err:
            raise UpdateFailed(
                f"Error communicating with OpenWrt router: {err}"
            ) from err

        # Discover newly connected devices and dispatch signal for entity creation
        new_macs = set(clients.keys()) - self.known_macs
        if new_macs:
            _LOGGER.debug("Discovered %d new client(s): %s", len(new_macs), new_macs)
            self.known_macs.update(new_macs)
            async_dispatcher_send(
                self.hass,
                f"{DOMAIN}_{self.entry.entry_id}_new_client",
                new_macs,
            )

        return clients

    async def async_block_client(self, mac: str) -> None:
        """Block internet access for a client device and refresh data."""
        await self.openwrt_manager.async_block_client(mac)
        await self.async_request_refresh()

    async def async_unblock_client(self, mac: str) -> None:
        """Restore internet access for a client device and refresh data."""
        await self.openwrt_manager.async_unblock_client(mac)
        await self.async_request_refresh()
