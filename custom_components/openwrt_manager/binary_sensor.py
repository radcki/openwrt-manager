"""Binary sensor platform for OpenWrt connected clients."""

from __future__ import annotations

import logging

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
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
    """Set up binary sensors for OpenWrt router clients."""
    coordinator: OpenWrtDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    added_macs: set[str] = set()

    @callback
    def add_client_entities(macs: set[str] | None = None) -> None:
        """Add binary sensors for discovered clients."""
        target_macs = macs if macs is not None else set(coordinator.data.keys())
        new_entities: list[OpenWrtClientConnectivitySensor] = []

        for mac in target_macs:
            if mac in added_macs or mac not in coordinator.data:
                continue
            added_macs.add(mac)
            new_entities.append(
                OpenWrtClientConnectivitySensor(coordinator, entry, mac)
            )

        if new_entities:
            async_add_entities(new_entities)

    # Add existing clients
    if coordinator.data:
        add_client_entities()

    # Listen for newly discovered clients
    entry.async_on_unload(
        async_dispatcher_connect(
            hass,
            f"{DOMAIN}_{entry.entry_id}_new_client",
            add_client_entities,
        )
    )


class OpenWrtClientConnectivitySensor(
    CoordinatorEntity[OpenWrtDataUpdateCoordinator], BinarySensorEntity
):
    """Binary sensor indicating if an OpenWrt client is online."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_has_entity_name = True
    _attr_name = "Connection"

    def __init__(
        self,
        coordinator: OpenWrtDataUpdateCoordinator,
        entry: ConfigEntry,
        mac: str,
    ) -> None:
        """Initialize the client connectivity sensor."""
        super().__init__(coordinator)
        self._mac = mac
        self._entry = entry
        self._attr_unique_id = f"{mac}_connectivity"

    @property
    def client_data(self):
        """Get current client data from coordinator."""
        return self.coordinator.data.get(self._mac)

    @property
    def is_on(self) -> bool:
        """Return true if the client is currently online."""
        client = self.client_data
        return client.is_online if client else False

    @property
    def device_info(self) -> DeviceInfo:
        """Link this entity to its client device, connected via the router."""
        return client_device_info(self.client_data, self._mac, self._entry.entry_id)

    @property
    def extra_state_attributes(self) -> dict[str, str | int | None]:
        """Return additional connection details."""
        client = self.client_data
        if not client:
            return {}
        return {
            "mac_address": client.mac,
            "ip_address": client.ip,
            "interface": client.interface,
            "is_wireless": client.is_wireless,
            "signal_dbm": client.signal_dbm,
        }
