"""Sensor platform for OpenWrt client metrics and telemetry."""

from __future__ import annotations

import logging

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import SIGNAL_STRENGTH_DECIBELS_MILLIWATT, EntityCategory
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
    """Set up diagnostic sensors for OpenWrt clients."""
    coordinator: OpenWrtDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    added_macs: set[str] = set()

    @callback
    def add_client_sensors(macs: set[str] | None = None) -> None:
        """Add sensors for discovered clients."""
        target_macs = macs if macs is not None else set(coordinator.data.keys())
        new_entities: list[SensorEntity] = []

        for mac in target_macs:
            if mac in added_macs or mac not in coordinator.data:
                continue
            added_macs.add(mac)
            new_entities.extend(
                [
                    OpenWrtClientIpSensor(coordinator, entry, mac),
                    OpenWrtClientSignalSensor(coordinator, entry, mac),
                ]
            )

        if new_entities:
            async_add_entities(new_entities)

    if coordinator.data:
        add_client_sensors()

    entry.async_on_unload(
        async_dispatcher_connect(
            hass,
            f"{DOMAIN}_{entry.entry_id}_new_client",
            add_client_sensors,
        )
    )


class OpenWrtClientBaseSensor(
    CoordinatorEntity[OpenWrtDataUpdateCoordinator], SensorEntity
):
    """Base class for OpenWrt client diagnostic sensors."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: OpenWrtDataUpdateCoordinator,
        entry: ConfigEntry,
        mac: str,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._mac = mac
        self._entry = entry

    @property
    def client_data(self):
        """Get current client data from coordinator."""
        return self.coordinator.data.get(self._mac)

    @property
    def device_info(self) -> DeviceInfo:
        """Link this sensor to its client device, connected via the router."""
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


class OpenWrtClientIpSensor(OpenWrtClientBaseSensor):
    """Sensor for client IP address."""

    _attr_name = "IP Address"

    def __init__(
        self,
        coordinator: OpenWrtDataUpdateCoordinator,
        entry: ConfigEntry,
        mac: str,
    ) -> None:
        """Initialize IP sensor."""
        super().__init__(coordinator, entry, mac)
        self._attr_unique_id = f"{mac}_ip_address"

    @property
    def native_value(self) -> str | None:
        """Return current IP address."""
        client = self.client_data
        return client.ip if client else None


class OpenWrtClientSignalSensor(OpenWrtClientBaseSensor):
    """Sensor for wireless client signal strength (RSSI dBm)."""

    _attr_name = "Signal Strength"
    _attr_device_class = SensorDeviceClass.SIGNAL_STRENGTH
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = SIGNAL_STRENGTH_DECIBELS_MILLIWATT

    def __init__(
        self,
        coordinator: OpenWrtDataUpdateCoordinator,
        entry: ConfigEntry,
        mac: str,
    ) -> None:
        """Initialize Signal sensor."""
        super().__init__(coordinator, entry, mac)
        self._attr_unique_id = f"{mac}_signal_strength"

    @property
    def native_value(self) -> int | None:
        """Return current RSSI signal level in dBm."""
        client = self.client_data
        return client.signal_dbm if client else None

    @property
    def available(self) -> bool:
        """Sensor only available for wireless clients with signal data."""
        client = self.client_data
        return bool(client and client.is_wireless and client.signal_dbm is not None)
