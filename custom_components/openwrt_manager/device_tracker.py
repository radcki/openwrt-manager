"""Device tracker platform for OpenWrt connected clients."""

from __future__ import annotations

import logging

from homeassistant.components.device_tracker import ScannerEntity, SourceType
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import OpenWrtDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up device tracker entities for OpenWrt clients."""
    coordinator: OpenWrtDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    added_macs: set[str] = set()

    @callback
    def add_client_trackers(macs: set[str] | None = None) -> None:
        """Add tracker entities for discovered clients."""
        target_macs = macs if macs is not None else set(coordinator.data.keys())
        new_entities: list[OpenWrtClientTracker] = []

        for mac in target_macs:
            if mac in added_macs or mac not in coordinator.data:
                continue
            added_macs.add(mac)
            new_entities.append(OpenWrtClientTracker(coordinator, entry, mac))

        if new_entities:
            async_add_entities(new_entities)

    # Add existing clients
    if coordinator.data:
        add_client_trackers()

    # Listen for newly discovered clients
    entry.async_on_unload(
        async_dispatcher_connect(
            hass,
            f"{DOMAIN}_{entry.entry_id}_new_client",
            add_client_trackers,
        )
    )


class OpenWrtClientTracker(
    CoordinatorEntity[OpenWrtDataUpdateCoordinator], ScannerEntity
):
    """Device tracker entity for OpenWrt connected client."""

    _attr_has_entity_name = True
    _attr_name = "Tracker"

    def __init__(
        self,
        coordinator: OpenWrtDataUpdateCoordinator,
        entry: ConfigEntry,
        mac: str,
    ) -> None:
        """Initialize tracker."""
        super().__init__(coordinator)
        self._mac = mac
        self._entry = entry
        self._attr_unique_id = f"{mac}_tracker"

    @property
    def client_data(self):
        """Get current client data from coordinator."""
        return self.coordinator.data.get(self._mac)

    @property
    def source_type(self) -> SourceType:
        """Return the source type of the device tracker."""
        return SourceType.ROUTER

    @property
    def is_connected(self) -> bool:
        """Return true if the device is connected to the network."""
        client = self.client_data
        return client.is_online if client else False

    @property
    def mac_address(self) -> str:
        """Return the mac address of the device."""
        return self._mac

    @property
    def alias(self) -> str | None:
        """Return the hostname of the device."""
        client = self.client_data
        return client.alias if client else None

    @property
    def hostname(self) -> str | None:
        """Return the hostname of the device."""
        client = self.client_data
        return client.hostname if client else None

    @property
    def ip_address(self) -> str | None:
        """Return the IP address of the device."""
        client = self.client_data
        return client.ip if client else None

    @property
    def device_info(self) -> DeviceInfo:
        """Link this tracker to its client device, connected via the router."""
        client = self.client_data
        device_name = f"Client {self._mac}"

        if client and client.alias:
            device_name = client.alias
        elif client and client.hostname:
            device_name = client.hostname

        return DeviceInfo(
            identifiers={(DOMAIN, self._mac)},
            connections={(dr.CONNECTION_NETWORK_MAC, self._mac)},
            name=device_name,
            manufacturer="OpenWrt Client",
            model="Wireless Client"
            if (client and client.is_wireless)
            else "Wired Client",
            via_device_id=self.coordinator.router_device_id,
        )
