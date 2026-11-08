"""Run with Python inside the HA image to verify real framework contracts.

This suite deliberately uses unittest so the production HA image needs no test
dependencies. Run: python tests/homeassistant_contracts.py (repository on PYTHONPATH).
"""

import unittest
from datetime import timedelta
from types import MappingProxyType, SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from aiohttp import ClientSession
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowManager

from custom_components.openwrt_manager.config_flow import OpenWrtOptionsFlow
from custom_components.openwrt_manager.coordinator import OpenWrtDataUpdateCoordinator
from custom_components.openwrt_manager.models import (
    AccessPointInfo,
    ClientDevice,
    ClientObservation,
)
from custom_components.openwrt_manager.sensor import (
    CLIENT_METRICS,
    AccessPointSourceStatusSensor,
    OpenWrtClientIpSensor,
    OpenWrtClientMetricSensor,
    OpenWrtClientSignalSensor,
)
from custom_components.openwrt_manager.sources.manager import AccessPointSourceManager
from custom_components.openwrt_manager.sources.unifi import UniFiSource

MAC = "AA:BB:CC:DD:EE:01"


class OptionsFlowUnderTest(OpenWrtOptionsFlow):
    """Supply an entry without starting HA's config-entry persistence machinery."""

    @property
    def config_entry(self):
        return self.test_entry


class OptionsFlowManagerUnderTest(FlowManager):
    """Use HA's routing and step validation without persisting test options."""

    def __init__(self, hass, flow):
        super().__init__(hass)
        self.flow = flow

    async def async_create_flow(self, handler_key, *, context=None, data=None):
        return self.flow

    async def async_finish_flow(self, flow, result):
        return result


class HomeAssistantContracts(unittest.IsolatedAsyncioTestCase):
    """Exercise flow, coordinator and entity contracts against Home Assistant."""

    async def asyncSetUp(self):
        self.hass = HomeAssistant("/tmp/openwrt-manager-contracts")
        self.entry = ConfigEntry(
            entry_id="router",
            domain="openwrt_manager",
            title="Router",
            version=1,
            minor_version=1,
            source="user",
            data={},
            options={},
            unique_id="router",
            discovery_keys=MappingProxyType({}),
            subentries_data=None,
        )

    def flow(self, sources=None):
        flow = OptionsFlowUnderTest()
        flow.hass = self.hass
        flow.test_entry = SimpleNamespace(
            options={"ap_sources": sources or [], "firewall_action": "DROP"}, data={}
        )
        return flow

    async def test_options_menu_and_router_options_preserve_multiple_sources(self):
        sources = [{"id": "one", "type": "openwrt"}, {"id": "two", "type": "openwrt"}]
        flow = self.flow(sources)
        result = await flow.async_step_init()
        self.assertEqual(result["type"], "menu")
        self.assertIn("remove_ap_source", result["menu_options"])
        result = await flow.async_step_router({"scan_interval": 20})
        self.assertEqual(result["data"]["ap_sources"], sources)
        self.assertEqual(result["data"]["firewall_action"], "DROP")

    async def test_edit_toggle_remove_keep_other_instances_and_order(self):
        sources = [
            {
                "id": identifier,
                "type": "openwrt",
                "name": identifier,
                "host": identifier,
            }
            for identifier in ("one", "two", "three")
        ]
        flow = self.flow(sources)
        result = await flow.async_step_manage_sources({"source_id": "two"})
        self.assertEqual(result["type"], "menu")
        result = await flow.async_step_toggle_source()
        self.assertEqual(
            [source["id"] for source in result["data"]["ap_sources"]],
            ["one", "two", "three"],
        )
        self.assertFalse(result["data"]["ap_sources"][1]["enabled"])
        self.assertNotIn("enabled", sources[1])
        result = await flow.async_step_remove_source()
        self.assertEqual(
            [source["id"] for source in result["data"]["ap_sources"]], ["one", "three"]
        )

    async def test_manage_source_actions_through_homeassistant_flow_manager(self):
        sources = [{"id": "one", "type": "unifi", "name": "Home", "host": "controller"}]
        for action in ("source", "toggle_source", "remove_source"):
            with self.subTest(action=action):
                manager = OptionsFlowManagerUnderTest(self.hass, self.flow(sources))
                result = await manager.async_init("openwrt_manager")
                flow_id = result["flow_id"]
                result = await manager.async_configure(
                    flow_id, {"next_step_id": "manage_sources"}
                )
                self.assertEqual(result["step_id"], "manage_sources")
                result = await manager.async_configure(flow_id, {"source_id": "one"})
                self.assertEqual(result["step_id"], "source_action")
                self.assertIn(action, result["menu_options"])
                result = await manager.async_configure(
                    flow_id, {"next_step_id": action}
                )
                if action == "source":
                    self.assertEqual(result["type"], "form")
                    self.assertEqual(result["step_id"], "source")
                else:
                    self.assertEqual(result["type"], "create_entry")
                    if action == "remove_source":
                        self.assertEqual(result["data"]["ap_sources"], [])
                    else:
                        self.assertFalse(result["data"]["ap_sources"][0]["enabled"])

    async def test_direct_source_removal_preserves_other_sources_and_router_options(
        self,
    ):
        sources = [
            {
                "id": identifier,
                "type": "unifi",
                "name": identifier,
                "host": "controller",
            }
            for identifier in ("one", "two")
        ]
        flow = self.flow(sources)
        form = await flow.async_step_remove_ap_source()
        self.assertEqual(form["step_id"], "remove_ap_source")
        result = await flow.async_step_remove_ap_source({"source_id": "two"})
        self.assertEqual(result["data"]["ap_sources"], [sources[0]])
        self.assertEqual(result["data"]["firewall_action"], "DROP")
        self.assertEqual(len(sources), 2)
        result = await self.flow().async_step_remove_ap_source()
        self.assertEqual(result["reason"], "no_sources")

    async def test_unifi_site_validation_and_failed_credentials_do_not_save(self):
        flow = self.flow()
        await flow.async_step_add_unifi()
        config = {
            "name": "Home",
            "host": "controller",
            "port": 443,
            "username": "local",
            "password": "secret",
            "verify_ssl": True,
        }
        source = UniFiSource("candidate", config, ClientSession())
        source.async_sites = AsyncMock(
            return_value={"default": "Home", "second": "Second site"}
        )
        source.async_refresh = AsyncMock(return_value={})
        with patch(
            "custom_components.openwrt_manager.config_flow.create_source",
            return_value=source,
        ):
            result = await flow.async_step_source(config)
            self.assertEqual(result["step_id"], "site")
            result = await flow.async_step_site({"site": "second"})
            self.assertEqual(result["data"]["ap_sources"][0]["site"], "second")
        failing = Mock(
            async_refresh=AsyncMock(side_effect=TimeoutError), close=AsyncMock()
        )
        await flow.async_step_add_openwrt()
        with patch(
            "custom_components.openwrt_manager.config_flow.create_source",
            return_value=failing,
        ):
            result = await flow.async_step_source(config)
            self.assertEqual(result["errors"]["base"], "source_connection_failed")

    async def test_client_sensors_keep_identity_and_expose_ap_telemetry(self):
        coordinator = OpenWrtDataUpdateCoordinator(
            self.hass, Mock(), self.entry, timedelta(seconds=15)
        )
        coordinator.data = {
            MAC: ClientDevice(
                MAC,
                ip="192.168.1.2",
                is_wireless=True,
                signal_dbm=-45,
                access_point=AccessPointInfo("ap", "00:11:22:33:44:55", "Living room"),
            )
        }
        ip = OpenWrtClientIpSensor(coordinator, self.entry, MAC)
        signal = OpenWrtClientSignalSensor(coordinator, self.entry, MAC)
        self.assertEqual(ip.unique_id, f"{MAC}_ip_address")
        self.assertEqual(signal.unique_id, f"{MAC}_signal_strength")
        self.assertEqual(
            ip.device_info["identifiers"], signal.device_info["identifiers"]
        )
        self.assertEqual(ip.device_info["via_device"], ("openwrt_manager", "router"))
        for metric in CLIENT_METRICS:
            sensor = OpenWrtClientMetricSensor(coordinator, self.entry, MAC, metric)
            if metric.key == "access_point":
                self.assertEqual(sensor.native_value, "Living room")
                self.assertEqual(sensor.extra_state_attributes["access_point_id"], "ap")
                self.assertEqual(sensor.extra_state_attributes["name"], "Living room")
            if metric.key == "download_rate":
                self.assertFalse(sensor.available)
        coordinator.last_update_success = False
        self.assertFalse(signal.available)

    async def test_access_point_sensor_uses_configured_source_name(self):
        coordinator = OpenWrtDataUpdateCoordinator(
            self.hass, Mock(), self.entry, timedelta(seconds=15)
        )
        coordinator.data = {
            MAC: ClientDevice(
                MAC,
                source_id="unifi-source",
                access_point=AccessPointInfo(
                    "unifi-source:ap-mac", "00:11:22:33:44:55", "Controller AP name"
                ),
            )
        }
        entry = SimpleNamespace(
            entry_id="router",
            options={"ap_sources": [{"id": "unifi-source", "name": "Salon"}]},
        )
        metric = next(
            metric for metric in CLIENT_METRICS if metric.key == "access_point"
        )
        sensor = OpenWrtClientMetricSensor(coordinator, entry, MAC, metric)
        self.assertEqual(sensor.native_value, "Salon")
        self.assertEqual(
            sensor.extra_state_attributes["access_point_id"], "unifi-source:ap-mac"
        )
        self.assertEqual(
            sensor.extra_state_attributes["access_point_name"], "Controller AP name"
        )

    async def test_main_router_wifi_uses_integration_name_without_assigning_wired_clients(
        self,
    ):
        wifi = ClientDevice(MAC, is_wireless=True, is_online=True)
        wired_mac = "AA:BB:CC:DD:EE:02"
        offline_mac = "AA:BB:CC:DD:EE:03"
        router = Mock(
            async_get_clients=AsyncMock(
                return_value={
                    MAC: wifi,
                    wired_mac: ClientDevice(wired_mac, is_online=True),
                    offline_mac: ClientDevice(offline_mac, is_wireless=True),
                }
            )
        )
        coordinator = OpenWrtDataUpdateCoordinator(
            self.hass, router, self.entry, timedelta(seconds=15)
        )
        coordinator.data = await coordinator._async_update_data()
        metric = next(
            metric for metric in CLIENT_METRICS if metric.key == "access_point"
        )
        sensor = OpenWrtClientMetricSensor(coordinator, self.entry, MAC, metric)
        self.assertEqual(sensor.native_value, "Router")
        self.assertTrue(sensor.available)
        self.assertIsNone(coordinator.data[wired_mac].access_point)
        self.assertIsNone(coordinator.data[offline_mac].access_point)
        self.assertIsNone(wifi.access_point)

    async def test_source_status_updates_without_healthy_router_clients(self):
        for router_available, clients in (
            (False, {MAC: ClientDevice(MAC)}),
            (True, {}),
        ):
            with self.subTest(router_available=router_available):
                coordinator = OpenWrtDataUpdateCoordinator(
                    self.hass, Mock(), self.entry, timedelta(seconds=15)
                )
                coordinator.router_clients = clients
                coordinator.data = clients
                coordinator.last_update_success = router_available
                manager = AccessPointSourceManager({"ap": Mock()}, 15)
                coordinator.source_manager = manager
                sensor = AccessPointSourceStatusSensor(
                    coordinator,
                    self.entry,
                    {"id": "ap", "name": "AP", "type": "openwrt"},
                )
                observed_states = []
                unsubscribe = coordinator.async_add_listener(
                    lambda states=observed_states, status_sensor=sensor: states.append(
                        status_sensor.native_value
                    )
                )
                try:
                    for available in (True, False, True):
                        manager.statuses["ap"].available = available
                        coordinator.source_updated()
                    self.assertEqual(
                        observed_states, ["connected", "unavailable", "connected"]
                    )
                    self.assertIs(coordinator.data, clients)
                    self.assertEqual(coordinator.last_update_success, router_available)
                finally:
                    unsubscribe()
                    await coordinator.async_shutdown()

    async def test_push_does_not_read_router_or_reset_its_poll_schedule(self):
        router = Mock(
            async_get_clients=AsyncMock(return_value={MAC: ClientDevice(MAC)})
        )
        coordinator = OpenWrtDataUpdateCoordinator(
            self.hass, router, self.entry, timedelta(seconds=15)
        )
        manager = AccessPointSourceManager({}, 15)
        coordinator.source_manager = manager
        coordinator.data = await coordinator._async_update_data()
        manager.snapshots["one"] = {
            MAC: ClientObservation(
                MAC,
                "one",
                __import__("time").time(),
                AccessPointInfo("ap", "00:11:22:33:44:55", "AP"),
            )
        }
        with patch.object(coordinator, "async_set_updated_data") as reschedule:
            coordinator.source_updated()
            reschedule.assert_not_called()
        self.assertTrue(coordinator.data[MAC].is_wireless)
        router.async_get_clients.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
