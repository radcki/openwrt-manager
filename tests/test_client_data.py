"""Wireless enrichment and traffic behavior independent of Home Assistant."""

from dataclasses import replace

import pytest

from custom_components.openwrt_manager.client_data import (
    ClientDataMerger,
    ClientTrafficCalculator,
)
from custom_components.openwrt_manager.models import (
    AccessPointInfo,
    ClientDevice,
    ClientObservation,
)

MAC = "AA:BB:CC:DD:EE:01"
AP = AccessPointInfo("source:ap", "AA:BB:CC:00:00:01", "Living room")


def observation(**changes):
    return replace(
        ClientObservation(MAC, "source", 100, AP, rx_bytes=1000, tx_bytes=2000),
        **changes,
    )


def test_enrichment_preserves_router_authority_and_ignores_unknown_clients():
    router = ClientDevice(
        MAC,
        ip="192.168.1.2",
        alias="Phone",
        internet_blocked=True,
        firewall_rule_id="HA_BLOCK",
    )
    sample = observation(ip="192.168.9.2", signal_dbm=-42)
    result = ClientDataMerger().merge(
        {MAC: router}, [{MAC: sample, "unknown": observation(mac="unknown")}], 100, 30
    )
    assert set(result) == {MAC}
    client = result[MAC]
    assert client.ip == router.ip
    assert client.alias == "Phone"
    assert client.internet_blocked and client.firewall_rule_id == "HA_BLOCK"
    assert client.is_wireless and client.is_online
    assert client.access_point == AP and client.signal_dbm == -42
    assert not router.is_wireless


def test_roaming_uses_freshest_observation_and_configuration_order_for_ties():
    merger = ClientDataMerger()
    first = observation()
    second = observation(
        source_id="second", access_point=replace(AP, identifier="second:ap")
    )
    router = {MAC: ClientDevice(MAC)}
    assert (
        merger.merge(router, [{MAC: first}, {MAC: second}], 100, 30)[MAC].source_id
        == "source"
    )
    second = replace(second, observed_at=101)
    client = merger.merge(router, [{MAC: first}, {MAC: second}], 101, 30)[MAC]
    assert client.source_id == "second"
    assert client.download_rate is None


@pytest.mark.parametrize(
    "snapshots,now",
    [
        ([], 110),
        ([{MAC: observation()}], 131),
        ([{MAC: observation(connected=False)}], 110),
    ],
)
def test_disconnect_and_expiry_clear_telemetry_without_false_ethernet(snapshots, now):
    merger = ClientDataMerger()
    router = {MAC: ClientDevice(MAC, is_online=True)}
    merger.merge(router, [{MAC: observation()}], 100, 30)
    client = merger.merge(router, snapshots, now, 30)[MAC]
    assert client.is_wireless and client.is_online
    assert client.access_point is None
    assert client.signal_dbm is None and client.rx_bytes is None


def test_router_only_behavior_is_unchanged():
    router = {MAC: ClientDevice(MAC, is_wireless=True, signal_dbm=-55, rx_rate=50)}
    assert ClientDataMerger().merge(router, [], 100, 30) == router


def test_throughput_reuses_snapshot_and_resets_after_counter_reset():
    calculator = ClientTrafficCalculator()
    assert calculator.calculate(observation()) == (None, None)
    second = observation(observed_at=110, rx_bytes=2000, tx_bytes=4000)
    assert calculator.calculate(second) == (100, 200)
    assert calculator.calculate(second) == (100, 200)
    assert calculator.calculate(
        observation(observed_at=120, rx_bytes=10, tx_bytes=20)
    ) == (None, None)


@pytest.mark.parametrize(
    "changes",
    [
        {"source_id": "second"},
        {"access_point": replace(AP, identifier="other")},
        {"association_time": 99},
        {"connected_seconds": 1},
    ],
)
def test_new_association_does_not_produce_throughput_spike(changes):
    calculator = ClientTrafficCalculator()
    calculator.calculate(observation(connected_seconds=30))
    assert calculator.calculate(
        observation(observed_at=110, rx_bytes=100000, **changes)
    ) == (None, None)


def test_expired_association_starts_new_traffic_baseline():
    merger = ClientDataMerger()
    router = {MAC: ClientDevice(MAC)}
    merger.merge(router, [{MAC: observation()}], 100, 30)
    merger.merge(router, [], 140, 30)
    client = merger.merge(
        router, [{MAC: observation(observed_at=150, rx_bytes=5000)}], 150, 30
    )[MAC]
    assert client.download_rate is None
