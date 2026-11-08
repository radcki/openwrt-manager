"""Sensor platform for OpenWrt client metrics and telemetry."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import SIGNAL_STRENGTH_DECIBELS_MILLIWATT, EntityCategory
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
    """Set up diagnostic sensors for OpenWrt clients."""
    coordinator: OpenWrtDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    added_macs: set[str] = set()
    async_add_entities(
        [
            AccessPointSourceStatusSensor(coordinator, entry, source)
            for source in entry.options.get("ap_sources", [])
        ]
    )

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
                    *(
                        OpenWrtClientMetricSensor(coordinator, entry, mac, metric)
                        for metric in CLIENT_METRICS
                    ),
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
        return client_device_info(self.client_data, self._mac, self._entry.entry_id)


class OpenWrtClientIpSensor(OpenWrtClientBaseSensor):
    """Expose the router-authoritative client address with its existing identity."""

    _attr_name = "IP Address"

    def __init__(
        self, coordinator: OpenWrtDataUpdateCoordinator, entry: ConfigEntry, mac: str
    ) -> None:
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
        return super().available and bool(
            client and client.is_wireless and client.signal_dbm is not None
        )


@dataclass(frozen=True)
class ClientMetric:
    """Describe one merged client field and its Home Assistant presentation."""

    key: str
    name: str
    unit: str | None = None
    state_class: SensorStateClass | None = None


CLIENT_METRICS = (
    ClientMetric("connection_type", "Connection Type"),
    ClientMetric("access_point", "Access Point"),
    ClientMetric("ssid", "SSID"),
    ClientMetric("band", "Wi-Fi Band"),
    ClientMetric("channel", "Wi-Fi Channel"),
    ClientMetric(
        "rx_rate", "Receive Link Rate", "Mbit/s", SensorStateClass.MEASUREMENT
    ),
    ClientMetric(
        "tx_rate", "Transmit Link Rate", "Mbit/s", SensorStateClass.MEASUREMENT
    ),
    ClientMetric("download_rate", "Download Rate", "B/s", SensorStateClass.MEASUREMENT),
    ClientMetric("upload_rate", "Upload Rate", "B/s", SensorStateClass.MEASUREMENT),
    ClientMetric("rx_bytes", "Received Data", "B"),
    ClientMetric("tx_bytes", "Transmitted Data", "B"),
)


class OpenWrtClientMetricSensor(OpenWrtClientBaseSensor):
    """Present normalized AP telemetry without transport-specific entity logic."""

    def __init__(
        self,
        coordinator: OpenWrtDataUpdateCoordinator,
        entry: ConfigEntry,
        mac: str,
        metric: ClientMetric,
    ) -> None:
        super().__init__(coordinator, entry, mac)
        self.metric = metric
        self._attr_unique_id = f"{mac}_{metric.key}"
        self._attr_translation_key = metric.key
        self._attr_name = metric.name
        self._attr_native_unit_of_measurement = metric.unit
        self._attr_state_class = metric.state_class
        if metric.unit == "B":
            self._attr_device_class = SensorDeviceClass.DATA_SIZE
        elif metric.unit in ("B/s", "Mbit/s"):
            self._attr_device_class = SensorDeviceClass.DATA_RATE
        if metric.key in ("rx_bytes", "tx_bytes"):
            self._attr_entity_registry_enabled_default = False

    @property
    def native_value(self) -> str | int | float | None:
        client = self.client_data
        if client is None:
            return None
        if self.metric.key == "connection_type":
            return "Wi-Fi" if client.is_wireless else "Ethernet"
        if self.metric.key == "access_point":
            return self.access_point_name
        return getattr(client, self.metric.key)

    @property
    def access_point_name(self) -> str | None:
        """Use the configured source label while retaining AP identity in attributes."""
        client = self.client_data
        if client is None or client.access_point is None:
            return None
        for source in self._entry.options.get("ap_sources", []):
            if source["id"] == client.source_id:
                return source["name"]
        return client.access_point.name

    @property
    def available(self) -> bool:
        return super().available and self.native_value is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        client = self.client_data
        if self.metric.key == "access_point" and client and client.access_point:
            return {
                "name": self.access_point_name,
                "access_point_name": client.access_point.name,
                "access_point_id": client.access_point.identifier,
                "mac_address": client.access_point.mac,
                "source_id": client.source_id,
            }
        return {}


class AccessPointSourceStatusSensor(
    CoordinatorEntity[OpenWrtDataUpdateCoordinator], SensorEntity
):
    """Expose failures independently of client and router availability."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "ap_source_status"

    def __init__(
        self,
        coordinator: OpenWrtDataUpdateCoordinator,
        entry: ConfigEntry,
        source: dict[str, Any],
    ) -> None:
        super().__init__(coordinator)
        self.source = source
        self._attr_unique_id = f"{entry.entry_id}_source_{source['id']}_status"
        self._attr_name = f"AP source {source['name']}"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, entry.entry_id)})

    @property
    def available(self) -> bool:
        return True

    @property
    def native_value(self) -> str:
        if not self.source.get("enabled", True):
            return "disabled"
        manager = self.coordinator.source_manager
        status = manager.statuses.get(self.source["id"]) if manager else None
        return "connected" if status and status.available else "unavailable"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        manager = self.coordinator.source_manager
        status = manager.statuses.get(self.source["id"]) if manager else None
        return {
            "source_id": self.source["id"],
            "source_type": self.source["type"],
            "last_success": status.last_success if status else None,
            "error": status.error if status else None,
        }
