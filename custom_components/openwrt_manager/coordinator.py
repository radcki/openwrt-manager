"""DataUpdateCoordinator for OpenWrt Router integration."""

from __future__ import annotations

import logging
import time
from dataclasses import replace
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .client_data import ClientDataMerger
from .const import DOMAIN
from .models import AccessPointInfo
from .openwrt_client.openwrt_client import OpenWrtClientError
from .openwrt_client.openwrt_manager import ClientDevice, OpenWrtManager, RouterInfo
from .sources.manager import AccessPointSourceManager

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
            config_entry=entry,
        )
        self.openwrt_manager = openwrt_manager
        self.entry = entry
        self.router_info: RouterInfo = RouterInfo()
        self.known_macs: set[str] = set()
        self.source_manager: AccessPointSourceManager | None = None
        self.router_clients: dict[str, ClientDevice] = {}
        self.merger = ClientDataMerger()

    def merged_clients(self) -> dict[str, ClientDevice]:
        """Build entity data without another router or firewall request."""
        manager = self.source_manager
        return self.merger.merge(
            self.router_clients,
            list(manager.snapshots.values()) if manager else [],
            time.time(),
            manager.interval * 2 if manager else 0,
        )

    def source_updated(self) -> None:
        """Publish source status independently; enrich only valid router data."""
        if self.last_update_success and self.router_clients:
            self.data = self.merged_clients()
        self.async_update_listeners()

    async def _async_update_data(self) -> dict[str, ClientDevice]:
        """Fetch latest client data and firewall states from OpenWrt."""
        try:
            clients = await self.openwrt_manager.async_get_clients()
        except OpenWrtClientError as err:
            raise UpdateFailed(
                f"Error communicating with OpenWrt router: {err}"
            ) from err

        router_access_point = AccessPointInfo(
            self.entry.entry_id, self.router_info.mac, self.entry.title
        )
        self.router_clients = {
            mac: replace(
                client, access_point=router_access_point, source_id=self.entry.entry_id
            )
            if client.is_wireless and client.is_online
            else client
            for mac, client in clients.items()
        }
        clients = self.merged_clients()
        return clients

    def discover_clients(self) -> None:
        """Notify platforms about router-discovered clients."""
        new_macs = set(self.data or {}) - self.known_macs
        if new_macs:
            _LOGGER.debug("Discovered %d new client(s): %s", len(new_macs), new_macs)
            self.known_macs.update(new_macs)
            async_dispatcher_send(
                self.hass,
                f"{DOMAIN}_{self.entry.entry_id}_new_client",
                new_macs,
            )

    async def async_block_client(self, mac: str) -> None:
        """Block internet access for a client device and refresh data."""
        try:
            await self.openwrt_manager.async_block_client(mac)
        except OpenWrtClientError as err:
            raise HomeAssistantError(f"Failed to block {mac}: {err}") from err
        await self.async_request_refresh()

    async def async_unblock_client(self, mac: str) -> None:
        """Restore internet access for a client device and refresh data."""
        try:
            await self.openwrt_manager.async_unblock_client(mac)
        except OpenWrtClientError as err:
            raise HomeAssistantError(f"Failed to unblock {mac}: {err}") from err
        await self.async_request_refresh()
