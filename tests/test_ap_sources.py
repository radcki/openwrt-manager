"""Read-only adapter contracts, multiple instances and source failure isolation."""

import asyncio
from unittest.mock import AsyncMock, Mock

from aiohttp import ClientSession
from aiounifi.interfaces.api_handlers import ItemEvent

from custom_components.openwrt_manager.sources.factory import create_source
from custom_components.openwrt_manager.sources.manager import AccessPointSourceManager
from custom_components.openwrt_manager.sources.openwrt import (
    OpenWrtAccessPointSource,
    parse_station_counters,
)
from custom_components.openwrt_manager.sources.unifi import UniFiSource

MAC = "AA:BB:CC:DD:EE:01"
STATIONS = f"""Station {MAC.lower()} (on phy0-ap0)
    rx bytes: 123
    tx bytes: 456
    connected time: 30 seconds
"""


def test_station_counters_preserve_missing_values():
    stations = parse_station_counters(
        STATIONS + "Station aa:bb:cc:dd:ee:02 (on phy0-ap0)\n"
    )
    assert stations[MAC].rx_bytes == 123
    assert stations[MAC].tx_bytes == 456
    assert stations[MAC].connected_seconds == 30
    assert stations["AA:BB:CC:DD:EE:02"].rx_bytes is None


async def test_openwrt_reads_only_wireless_data_and_maps_client_perspective():
    client = Mock()
    client.async_ubus_call = AsyncMock(
        side_effect=[
            {"devices": ["phy0-ap0"]},
            {
                "bssid": "00:11:22:33:44:55",
                "ssid": "Home",
                "frequency": 5180,
                "channel": 36,
            },
            {
                "results": [
                    {
                        "mac": MAC.lower(),
                        "signal": -45,
                        "rx": {"rate": 120000},
                        "tx": {"rate": 240000},
                    }
                ]
            },
        ]
    )
    client.async_run_ssh_command = AsyncMock(return_value=STATIONS)
    source = OpenWrtAccessPointSource("ap1", "Living room", client)
    sample = (await source.async_refresh())[MAC]
    assert sample.access_point.identifier == "ap1"
    assert (sample.rx_rate, sample.tx_rate) == (240, 120)
    assert (sample.rx_bytes, sample.tx_bytes) == (456, 123)
    assert sample.band == "5 GHz"
    assert all(
        call.args[0] == "iwinfo" for call in client.async_ubus_call.call_args_list
    )


def unifi_config(source_id="unifi1"):
    return {
        "id": source_id,
        "type": "unifi",
        "name": "Home",
        "host": "192.168.1.3",
        "username": "local",
        "password": "secret",
    }


async def test_unifi_authoritative_refresh_removes_retained_library_clients():
    async with ClientSession() as session:
        source = UniFiSource("unifi1", unifi_config(), session)
        api = source.api
        api.login = AsyncMock()
        api.devices.update = AsyncMock()
        api.request = AsyncMock(
            return_value={
                "data": [
                    {
                        "mac": MAC.lower(),
                        "ap_mac": "00:11:22:33:44:55",
                        "tx_bytes": 456,
                        "rx_bytes": 123,
                        "tx_rate": 240000,
                        "rx_rate": 120000,
                        "signal": -50,
                    }
                ]
            }
        )
        source.api = api
        sample = (await source.async_refresh())[MAC]
        assert (sample.rx_bytes, sample.tx_bytes) == (456, 123)
        assert (sample.rx_rate, sample.tx_rate) == (240, 120)
        api.request.return_value = {"data": []}
        assert await source.async_refresh() == {}
        assert MAC.lower() in api.clients
        await source.close()


async def test_unifi_push_roaming_and_deletion_publish_new_snapshot():
    async with ClientSession() as session:
        source = UniFiSource("unifi1", unifi_config(), session)
        updated = Mock()
        source._updated = updated
        unsubscribe = source.api.clients.subscribe(source._client_updated)
        source.api.clients.process_raw(
            [{"mac": MAC.lower(), "ap_mac": "00:11:22:33:44:55"}]
        )
        first = updated.call_args.args[0][MAC]
        source.api.clients.process_raw(
            [{"mac": MAC.lower(), "ap_mac": "00:11:22:33:44:66"}]
        )
        assert updated.call_args.args[0][MAC].access_point != first.access_point
        source._client_updated(ItemEvent.DELETED, MAC.lower())
        assert updated.call_args.args[0] == {}
        unsubscribe()
        await source.close()


async def test_same_type_sources_have_independent_sessions():
    first = await create_source(unifi_config("one"))
    second = await create_source(unifi_config("two"))
    try:
        assert first is not second
        assert (
            first.api.connectivity.config.session
            is not second.api.connectivity.config.session
        )
        assert first.source_id != second.source_id
    finally:
        await first.close()
        await second.close()


async def test_openwrt_instances_do_not_share_ssh_connections():
    config = {
        "type": "openwrt",
        "name": "AP",
        "host": "192.168.1.4",
        "password": "secret",
    }
    first = await create_source({**config, "id": "one"})
    second = await create_source({**config, "id": "two"})
    try:
        assert first.client is not second.client
        assert first.source_id != second.source_id
    finally:
        await first.close()
        await second.close()


async def test_slow_source_does_not_delay_other_sources_and_is_cancelled_on_close():
    healthy_refreshed_twice = asyncio.Event()
    blocked_cancelled = asyncio.Event()
    refreshes = 0

    async def blocked_refresh():
        try:
            await asyncio.Event().wait()
        finally:
            blocked_cancelled.set()

    async def healthy_refresh():
        nonlocal refreshes
        refreshes += 1
        if refreshes >= 2:
            healthy_refreshed_twice.set()
        return {}

    blocked = Mock(
        async_refresh=AsyncMock(side_effect=blocked_refresh), close=AsyncMock()
    )
    healthy = Mock(
        async_refresh=AsyncMock(side_effect=healthy_refresh), close=AsyncMock()
    )
    manager = AccessPointSourceManager({"one": blocked, "two": healthy}, 0.01)
    manager.start(Mock())
    try:
        async with asyncio.timeout(2):
            await healthy_refreshed_twice.wait()
        blocked.async_refresh.assert_awaited_once()
    finally:
        await manager.close()
    assert blocked_cancelled.is_set()


async def test_source_failure_isolated_and_retries_back_off():
    failed = Mock(async_refresh=AsyncMock(side_effect=TimeoutError), close=AsyncMock())
    healthy = Mock(async_refresh=AsyncMock(return_value={}), close=AsyncMock())
    manager = AccessPointSourceManager({"ap1": failed, "ap2": healthy}, 15)
    await manager.async_refresh()
    assert not manager.statuses["ap1"].available
    assert manager.statuses["ap2"].available
    await manager.async_refresh()
    assert failed.async_refresh.await_count == 1
    assert healthy.async_refresh.await_count == 2
    await manager.close()
    failed.close.assert_awaited_once()
    healthy.close.assert_awaited_once()


async def test_manager_push_is_bound_to_instance_and_shutdown_cancels_polling():
    first = Mock(async_refresh=AsyncMock(return_value={}), close=AsyncMock())
    second = Mock(async_refresh=AsyncMock(return_value={}), close=AsyncMock())
    manager = AccessPointSourceManager({"one": first, "two": second}, 100)
    updated = Mock()
    manager.start(updated)
    first.start.call_args.args[0]({"first": Mock()})
    second.start.call_args.args[0]({"second": Mock()})
    assert set(manager.snapshots["one"]) == {"first"}
    assert set(manager.snapshots["two"]) == {"second"}
    await manager.close()
    assert manager._task.done()
    assert not [
        task
        for task in asyncio.all_tasks()
        if task is manager._task and not task.done()
    ]
