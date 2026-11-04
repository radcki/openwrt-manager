"""Unit tests for OpenWrt Home Assistant entities and coordinator."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.openwrt_router.binary_sensor import (
    OpenWrtClientConnectivitySensor,
)
from custom_components.openwrt_router.const import DOMAIN
from custom_components.openwrt_router.coordinator import OpenWrtDataUpdateCoordinator
from custom_components.openwrt_router.device_tracker import OpenWrtClientTracker
from custom_components.openwrt_router.openwrt_client.OpenWrtManager import (
    ClientDevice,
    OpenWrtManager,
)
from custom_components.openwrt_router.sensor import (
    OpenWrtClientIpSensor,
    OpenWrtClientSignalSensor,
)
from custom_components.openwrt_router.switch import OpenWrtInternetAccessSwitch


@pytest.fixture
def sample_client() -> ClientDevice:
    """Create a sample ClientDevice."""
    return ClientDevice(
        mac="aa:bb:cc:11:22:33",
        ip="192.168.1.50",
        hostname="Tablet-User",
        is_online=True,
        is_wireless=True,
        interface="wlan0",
        signal_dbm=-55,
        internet_blocked=False,
    )


@pytest.fixture
def mock_coordinator(sample_client: ClientDevice) -> OpenWrtDataUpdateCoordinator:
    """Create a mock OpenWrtDataUpdateCoordinator."""
    hass = MagicMock()
    entry = MagicMock()
    entry.entry_id = "test_entry_id"

    client = MagicMock(spec=OpenWrtManager)
    client.firewall_action = "REJECT"
    client.async_block_client = AsyncMock()
    client.async_unblock_client = AsyncMock()

    coordinator = OpenWrtDataUpdateCoordinator(
        hass=hass,
        openwrt_manager=client,
        entry=entry,
        update_interval=None,  # type: ignore[arg-type]
    )
    coordinator.data = {sample_client.mac: sample_client}
    coordinator.async_request_refresh = AsyncMock()  # type: ignore[method-assign]
    return coordinator


def test_connectivity_binary_sensor(
    mock_coordinator: OpenWrtDataUpdateCoordinator, sample_client: ClientDevice
) -> None:
    """Test connectivity binary sensor properties."""
    entry = mock_coordinator.entry
    sensor = OpenWrtClientConnectivitySensor(mock_coordinator, entry, sample_client.mac)

    assert sensor.is_on is True
    assert sensor.unique_id == f"{sample_client.mac}_connectivity"
    assert sensor.device_info["identifiers"] == {(DOMAIN, sample_client.mac)}
    assert sensor.device_info["via_device"] == (DOMAIN, entry.entry_id)

    # Test attributes
    attrs = sensor.extra_state_attributes
    assert attrs["ip_address"] == "192.168.1.50"
    assert attrs["signal_dbm"] == -55
    assert attrs["is_wireless"] is True


@pytest.mark.asyncio
async def test_internet_access_switch(
    mock_coordinator: OpenWrtDataUpdateCoordinator, sample_client: ClientDevice
) -> None:
    """Test internet access switch state and toggle actions."""
    entry = mock_coordinator.entry
    switch = OpenWrtInternetAccessSwitch(mock_coordinator, entry, sample_client.mac)

    # When internet is not blocked, switch is ON
    assert switch.is_on is True
    assert switch.icon == "mdi:web"

    # Turn off -> blocks internet
    await switch.async_turn_off()
    mock_coordinator.openwrt_manager.async_block_client.assert_awaited_once_with(
        sample_client.mac, hostname=sample_client.hostname
    )

    # Change client to blocked
    sample_client.internet_blocked = True
    assert switch.is_on is False
    assert switch.icon == "mdi:web-off"

    # Turn on -> unblocks internet
    await switch.async_turn_on()
    mock_coordinator.openwrt_manager.async_unblock_client.assert_awaited_once_with(
        sample_client.mac
    )


def test_device_tracker_entity(
    mock_coordinator: OpenWrtDataUpdateCoordinator, sample_client: ClientDevice
) -> None:
    """Test scanner device tracker entity."""
    entry = mock_coordinator.entry
    tracker = OpenWrtClientTracker(mock_coordinator, entry, sample_client.mac)

    assert tracker.is_connected is True
    assert tracker.mac_address == sample_client.mac
    assert tracker.hostname == "Tablet-User"
    assert tracker.ip_address == "192.168.1.50"
    assert tracker.source_type == "router"


def test_diagnostic_sensors(
    mock_coordinator: OpenWrtDataUpdateCoordinator, sample_client: ClientDevice
) -> None:
    """Test IP and WiFi signal diagnostic sensors."""
    entry = mock_coordinator.entry

    ip_sensor = OpenWrtClientIpSensor(mock_coordinator, entry, sample_client.mac)
    assert ip_sensor.native_value == "192.168.1.50"

    signal_sensor = OpenWrtClientSignalSensor(
        mock_coordinator, entry, sample_client.mac
    )
    assert signal_sensor.native_value == -55
    assert signal_sensor.available is True

    # Wired client should mark signal sensor unavailable
    sample_client.is_wireless = False
    assert signal_sensor.available is False
