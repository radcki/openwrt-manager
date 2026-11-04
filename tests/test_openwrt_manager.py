"""Tests for parsing helpers and firewall logic of the OpenWrt manager."""

from __future__ import annotations

from typing import Any

import pytest

from custom_components.openwrt_manager.openwrt_client.openwrt_client import (
    OpenWrtClient,
)
from custom_components.openwrt_manager.openwrt_client.openwrt_manager import (
    OpenWrtManager,
    normalize_mac,
    parse_arp_table,
    parse_wireless_clients,
)

ARP = """IP address       HW type     Flags       HW address            Mask     Device
192.168.1.10     0x1         0x2         aa:bb:cc:dd:ee:01     *        br-lan
192.168.1.11     0x1         0x0         00:00:00:00:00:00     *        br-lan
"""


def test_normalize_mac():
    assert normalize_mac("aa-bb-cc-dd-ee-01") == "AA:BB:CC:DD:EE:01"


def test_parse_arp_table():
    entries = parse_arp_table(ARP)
    assert [e["mac"] for e in entries] == [
        "aa:bb:cc:dd:ee:01",
        "00:00:00:00:00:00",
    ]
    assert entries[0]["ip"] == "192.168.1.10"
    assert entries[1]["flags"] == "0x0"


def test_parse_wireless_clients_accepts_fractional_rates():
    raw = (
        "AA:BB:CC:DD:EE:01 -52 dBm / -95 dBm (SNR 43) 120 ms ago "
        "RX: 866.7 MBit/s, 100 Pkts. TX: 433.3 MBit/s, 90 Pkts.\n"
        "garbage line\n"
    )
    assert parse_wireless_clients(raw) == [("AA:BB:CC:DD:EE:01", -52, 866, 433)]


class FakeClient:
    """In-memory stand-in for OpenWrtClient holding a single firewall rule."""

    def __init__(self) -> None:
        self.rule: dict[str, Any] | None = None
        self.commands: list[str] = []

    async def async_ubus_call(self, target, method, params=None):
        assert target == "uci"
        if method == "get":
            if self.rule is None:
                return {"values": {}}
            return {"values": {"cfg1": {".type": "rule", ".index": 3, **self.rule}}}
        if method in ("add", "set"):
            self.rule = dict(params["values"])
        return {}

    async def async_run_ssh_command(self, cmd):
        self.commands.append(cmd)
        return ""


async def test_block_and_unblock_client_uses_configured_action():
    client = FakeClient()
    manager = OpenWrtManager(client, "HA_BLOCK", firewall_action="DROP")

    await manager.async_block_client("aa:bb:cc:dd:ee:01")
    assert client.rule["target"] == "DROP"
    assert client.rule["src_mac"] == ["AA:BB:CC:DD:EE:01"]
    assert client.rule["enabled"] == "1"

    await manager.async_unblock_client("AA:BB:CC:DD:EE:01")
    assert client.rule["src_mac"] == ""
    assert client.rule["enabled"] == "0"
    assert any("uci commit firewall" in c for c in client.commands)


async def test_ubus_call_quotes_arguments(monkeypatch: pytest.MonkeyPatch):
    client = OpenWrtClient("router", 22)
    seen: list[str] = []

    async def fake_run(cmd: str) -> str:
        seen.append(cmd)
        return '{"ok": true}'

    monkeypatch.setattr(client, "async_run_ssh_command", fake_run)
    result = await client.async_ubus_call(
        "uci", "set; reboot", {"name": "it's; reboot"}
    )

    assert result == {"ok": True}
    assert seen[0].startswith("ubus call uci 'set; reboot' ")
    assert "'\"'\"'" in seen[0]
