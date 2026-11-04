"""Test fixtures, mocks, and environment setup for OpenWrt tests."""

from __future__ import annotations

import sys
from types import ModuleType
from unittest.mock import MagicMock

# Lightweight Home Assistant test shim if Home Assistant Core is not installed locally
if "homeassistant" not in sys.modules:
    try:
        import homeassistant  # noqa: F401
    except ImportError:
        # Create mock module hierarchy for homeassistant
        ha_mock = ModuleType("homeassistant")
        ha_const = ModuleType("homeassistant.const")
        ha_core = ModuleType("homeassistant.core")
        ha_config_entries = ModuleType("homeassistant.config_entries")
        ha_data_entry_flow = ModuleType("homeassistant.data_entry_flow")
        ha_helpers = ModuleType("homeassistant.helpers")
        ha_helpers_dr = ModuleType("homeassistant.helpers.device_registry")
        ha_helpers_aiohttp = ModuleType("homeassistant.helpers.aiohttp_client")
        ha_helpers_update_coord = ModuleType("homeassistant.helpers.update_coordinator")
        ha_helpers_dispatcher = ModuleType("homeassistant.helpers.dispatcher")
        ha_helpers_entity = ModuleType("homeassistant.helpers.entity")
        ha_helpers_entity_platform = ModuleType("homeassistant.helpers.entity_platform")
        ha_components = ModuleType("homeassistant.components")
        ha_comp_binary_sensor = ModuleType("homeassistant.components.binary_sensor")
        ha_comp_switch = ModuleType("homeassistant.components.switch")
        ha_comp_device_tracker = ModuleType("homeassistant.components.device_tracker")
        ha_comp_sensor = ModuleType("homeassistant.components.sensor")

        from enum import StrEnum

        class Platform(StrEnum):
            BINARY_SENSOR = "binary_sensor"
            SWITCH = "switch"
            DEVICE_TRACKER = "device_tracker"
            SENSOR = "sensor"

        class SourceType(StrEnum):
            ROUTER = "router"

        class BinarySensorDeviceClass(StrEnum):
            CONNECTIVITY = "connectivity"

        class SensorDeviceClass(StrEnum):
            SIGNAL_STRENGTH = "signal_strength"

        class SensorStateClass(StrEnum):
            MEASUREMENT = "measurement"

        class EntityCategory(StrEnum):
            DIAGNOSTIC = "diagnostic"
            CONFIG = "config"

        ha_const.Platform = Platform
        ha_const.CONF_HOST = "host"
        ha_const.CONF_PORT = "port"
        ha_const.CONF_USERNAME = "username"
        ha_const.CONF_PASSWORD = "password"
        ha_const.CONF_SCAN_INTERVAL = "scan_interval"
        ha_const.SIGNAL_STRENGTH_DECIBELS_MILLIWATT = "dBm"
        ha_const.EntityCategory = EntityCategory

        def callback(func):
            return func

        ha_core.callback = callback
        ha_core.HomeAssistant = MagicMock

        class ConfigFlow:
            VERSION = 1

            def __init_subclass__(cls, domain=None, **kwargs):
                super().__init_subclass__(**kwargs)
                cls.domain = domain

            def async_show_form(self, step_id, data_schema, errors=None):
                return {
                    "type": "form",
                    "step_id": step_id,
                    "data_schema": data_schema,
                    "errors": errors or {},
                }

            def async_create_entry(self, title, data):
                return {"type": "create_entry", "title": title, "data": data}

        class OptionsFlow:
            pass

        class ConfigEntry:
            entry_id = "test_entry_id"
            data: dict = {}
            options: dict = {}

            def add_update_listener(self, listener):
                return MagicMock()

            def async_on_unload(self, listener):
                pass

        ha_config_entries.ConfigFlow = ConfigFlow
        ha_config_entries.OptionsFlow = OptionsFlow
        ha_config_entries.ConfigEntry = ConfigEntry
        ha_data_entry_flow.FlowResult = dict

        class UpdateFailed(Exception):
            pass

        class DataUpdateCoordinator[T]:
            def __init__(self, hass, logger, name, update_interval):
                self.hass = hass
                self.logger = logger
                self.name = name
                self.update_interval = update_interval
                self.data = {}

            async def async_config_entry_first_refresh(self):
                pass

            async def async_request_refresh(self):
                pass

        class CoordinatorEntity[T]:
            def __init__(self, coordinator):
                self.coordinator = coordinator

        ha_helpers_update_coord.DataUpdateCoordinator = DataUpdateCoordinator
        ha_helpers_update_coord.UpdateFailed = UpdateFailed
        ha_helpers_update_coord.CoordinatorEntity = CoordinatorEntity

        ha_helpers_dr.CONNECTION_NETWORK_MAC = "mac"
        ha_helpers_dr.async_get = MagicMock()
        ha_helpers_aiohttp.async_get_clientsession = MagicMock()
        ha_helpers_dispatcher.async_dispatcher_send = MagicMock()
        ha_helpers_dispatcher.async_dispatcher_connect = MagicMock()
        ha_helpers_entity.DeviceInfo = dict
        ha_helpers_entity.EntityCategory = EntityCategory
        ha_helpers_entity_platform.AddEntitiesCallback = MagicMock

        class Entity:
            @property
            def unique_id(self) -> str | None:
                return getattr(self, "_attr_unique_id", None)

        class BinarySensorEntity(Entity):
            pass

        class SwitchEntity(Entity):
            pass

        class ScannerEntity(Entity):
            pass

        class SensorEntity(Entity):
            pass

        ha_comp_binary_sensor.BinarySensorEntity = BinarySensorEntity
        ha_comp_binary_sensor.BinarySensorDeviceClass = BinarySensorDeviceClass
        ha_comp_switch.SwitchEntity = SwitchEntity
        ha_comp_device_tracker.ScannerEntity = ScannerEntity
        ha_comp_device_tracker.SourceType = SourceType
        ha_comp_sensor.SensorEntity = SensorEntity
        ha_comp_sensor.SensorDeviceClass = SensorDeviceClass
        ha_comp_sensor.SensorStateClass = SensorStateClass
        ha_comp_sensor.SensorEntityDescription = MagicMock

        sys.modules["homeassistant"] = ha_mock
        sys.modules["homeassistant.const"] = ha_const
        sys.modules["homeassistant.core"] = ha_core
        sys.modules["homeassistant.config_entries"] = ha_config_entries
        sys.modules["homeassistant.data_entry_flow"] = ha_data_entry_flow
        sys.modules["homeassistant.helpers"] = ha_helpers
        sys.modules["homeassistant.helpers.device_registry"] = ha_helpers_dr
        sys.modules["homeassistant.helpers.aiohttp_client"] = ha_helpers_aiohttp
        sys.modules["homeassistant.helpers.update_coordinator"] = (
            ha_helpers_update_coord
        )
        sys.modules["homeassistant.helpers.dispatcher"] = ha_helpers_dispatcher
        sys.modules["homeassistant.helpers.entity"] = ha_helpers_entity
        sys.modules["homeassistant.helpers.entity_platform"] = (
            ha_helpers_entity_platform
        )
        sys.modules["homeassistant.components"] = ha_components
        sys.modules["homeassistant.components.binary_sensor"] = ha_comp_binary_sensor
        sys.modules["homeassistant.components.switch"] = ha_comp_switch
        sys.modules["homeassistant.components.device_tracker"] = ha_comp_device_tracker
        sys.modules["homeassistant.components.sensor"] = ha_comp_sensor

import pytest

from custom_components.openwrt_router.openwrt_client.OpenWrtManager import (
    OpenWrtManager,
)


@pytest.fixture
def mock_openwrt_client() -> OpenWrtManager:
    """Create an OpenWrtClient instance configured for local testing."""
    return OpenWrtManager(
        host="127.0.0.1",
        port=8080,
        username="root",
        password="testpassword",
        protocol="ubus_http",
    )


@pytest.fixture
def sample_dhcp_leases() -> list[dict[str, str]]:
    """Sample DHCP lease list."""
    return [
        {
            "macaddr": "aa:bb:cc:11:22:01",
            "ipaddr": "192.168.1.101",
            "hostname": "Laptop-Alice",
        },
        {
            "macaddr": "aa:bb:cc:11:22:02",
            "ipaddr": "192.168.1.102",
            "hostname": "Phone-Bob",
        },
        {
            "macaddr": "aa:bb:cc:11:22:03",
            "ipaddr": "192.168.1.103",
            "hostname": "Smart-TV",
        },
    ]


@pytest.fixture
def sample_wireless_clients() -> dict[str, dict]:
    """Sample hostapd get_clients response."""
    return {
        "aa:bb:cc:11:22:01": {
            "signal": -50,
            "rate": {"rx": 866700, "tx": 866700},
        },
        "aa:bb:cc:11:22:02": {
            "signal": -68,
            "rate": {"rx": 400000, "tx": 300000},
        },
    }


@pytest.fixture
def sample_arp_table() -> str:
    """Sample /proc/net/arp contents."""
    return (
        "IP address       HW type     Flags       HW address            Mask     Device\n"
        "192.168.1.101    0x1         0x2         aa:bb:cc:11:22:01     *        br-lan\n"
        "192.168.1.102    0x1         0x2         aa:bb:cc:11:22:02     *        br-lan\n"
        "192.168.1.103    0x1         0x2         aa:bb:cc:11:22:03     *        br-lan\n"
    )
