"""Unit tests for OpenWrt client logic."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.openwrt_router.openwrt_client.OpenWrtManager import (
    CannotConnect,
    EndpointNotUbus,
    InvalidAuth,
    OpenWrtManager,
    normalize_mac,
    sanitize_rule_name,
)


class MockPostResponse:
    """Mock aiohttp response context manager."""

    def __init__(
        self,
        data: dict | None = None,
        text_content: str | None = None,
        status: int = 200,
    ) -> None:
        self._data = data or {}
        self._text = (
            text_content
            if text_content is not None
            else json.dumps(data if data is not None else {})
        )
        self.status = status
        self.reason = "OK" if status == 200 else "Error"

    async def text(self) -> str:
        return self._text

    async def json(self, **kwargs) -> dict:
        return self._data

    async def __aenter__(self) -> MockPostResponse:
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        pass


def test_mac_normalization() -> None:
    """Test MAC address normalization."""
    assert normalize_mac("AA:BB:CC:DD:EE:FF") == "aa:bb:cc:dd:ee:ff"
    assert normalize_mac("aa-bb-cc-dd-ee-ff") == "aa:bb:cc:dd:ee:ff"
    assert normalize_mac("aabb.ccdd.eeff") == "aa:bb:cc:dd:ee:ff"
    assert normalize_mac("AABBCCDDEEFF") == "aa:bb:cc:dd:ee:ff"


def test_sanitize_rule_name() -> None:
    """Test UCI firewall section name generation."""
    assert sanitize_rule_name("aa:bb:cc:11:22:33") == "ha_block_aa_bb_cc_11_22_33"
    assert sanitize_rule_name("AA-BB-CC-11-22-33") == "ha_block_aa_bb_cc_11_22_33"


def test_port_auto_correction() -> None:
    """Test that SSH port 80 defaults to 22, and ubus_http port 22 defaults to 80."""
    # When user chooses SSH but port was left at 80
    ssh_client = OpenWrtManager(host="192.168.1.1", port=80, protocol="ssh")
    assert ssh_client.port == 22

    # When user chooses SSH with no port
    ssh_client_none = OpenWrtManager(host="192.168.1.1", port=None, protocol="ssh")
    assert ssh_client_none.port == 22

    # When user chooses ubus_http with no port
    http_client = OpenWrtManager(host="192.168.1.1", port=None, protocol="ubus_http")
    assert http_client.port == 80


@pytest.mark.asyncio
async def test_client_login_success(mock_openwrt_client: OpenWrtManager) -> None:
    """Test successful ubus login."""
    login_resp = {
        "jsonrpc": "2.0",
        "id": 1,
        "result": [
            0,
            {
                "ubus_rpc_session": "test_session_token_12345",
                "timeout": 300,
                "expires": 300,
            },
        ],
    }

    mock_session = MagicMock()
    mock_session.post.return_value = MockPostResponse(login_resp)

    with patch.object(
        mock_openwrt_client, "_get_session", AsyncMock(return_value=mock_session)
    ):
        session_id = await mock_openwrt_client.async_login()
        assert session_id == "test_session_token_12345"


@pytest.mark.asyncio
async def test_client_login_html_detection(mock_openwrt_client: OpenWrtManager) -> None:
    """Test that router returning LuCI HTML raises EndpointNotUbus."""
    luci_html = "<!DOCTYPE html><html><head><title>LuCI - OpenWrt</title></head><body>Login</body></html>"

    mock_session = MagicMock()
    mock_session.post.return_value = MockPostResponse(
        text_content=luci_html, status=200
    )

    with (
        patch.object(
            mock_openwrt_client, "_get_session", AsyncMock(return_value=mock_session)
        ),
        pytest.raises(EndpointNotUbus),
    ):
        await mock_openwrt_client.async_login()


@pytest.mark.asyncio
async def test_client_login_invalid_auth(mock_openwrt_client: OpenWrtManager) -> None:
    """Test login failure with invalid credentials."""
    fail_resp = {
        "jsonrpc": "2.0",
        "id": 1,
        "result": [6, {}],  # ubus error 6 = access denied / invalid auth
    }

    mock_session = MagicMock()
    mock_session.post.return_value = MockPostResponse(fail_resp)

    with (
        patch.object(
            mock_openwrt_client, "_get_session", AsyncMock(return_value=mock_session)
        ),
        pytest.raises(InvalidAuth),
    ):
        await mock_openwrt_client.async_login()


@pytest.mark.asyncio
async def test_client_login_connection_error(
    mock_openwrt_client: OpenWrtManager,
) -> None:
    """Test login failure when router returns HTTP error."""
    mock_session = MagicMock()
    mock_session.post.return_value = MockPostResponse({}, status=502)

    with (
        patch.object(
            mock_openwrt_client, "_get_session", AsyncMock(return_value=mock_session)
        ),
        pytest.raises(CannotConnect),
    ):
        await mock_openwrt_client.async_login()


@pytest.mark.asyncio
async def test_get_router_info(mock_openwrt_client: OpenWrtManager) -> None:
    """Test retrieving router hardware and software info."""

    async def mock_call(target: str, method: str, params=None, retry_on_auth=True):
        if target == "system" and method == "board":
            return {
                "hostname": "OpenWrt-Router",
                "model": {"name": "GL.iNet GL-MT3000"},
                "release": {"description": "OpenWrt 23.05.3"},
            }
        if target == "system" and method == "info":
            return {"uptime": 72000}
        if target == "network.device" and method == "status":
            return {"macaddr": "00:11:22:33:44:55"}
        return {}

    with patch.object(mock_openwrt_client, "async_ubus_call", side_effect=mock_call):
        info = await mock_openwrt_client.async_get_router_info()
        assert info.hostname == "OpenWrt-Router"
        assert info.model == "GL.iNet GL-MT3000"
        assert info.release == "OpenWrt 23.05.3"
        assert info.uptime == 72000
        assert info.mac == "00:11:22:33:44:55"


@pytest.mark.asyncio
async def test_get_clients_and_firewall_status(
    mock_openwrt_client: OpenWrtManager,
    sample_dhcp_leases: list[dict],
    sample_wireless_clients: dict,
    sample_arp_table: str,
) -> None:
    """Test fetching connected clients and checking firewall rules."""
    firewall_values = {
        "rule_block_tv": {
            ".type": "rule",
            "name": "HA_Block_Smart-TV",
            "src": "lan",
            "dest": "wan",
            "src_mac": "aa:bb:cc:11:22:03",
            "target": "REJECT",
            "enabled": "1",
        }
    }

    async def mock_call(target: str, method: str, params=None, retry_on_auth=True):
        if target == "luci-rpc" and method == "getDHCPLeases":
            return {"dhcp_leases": sample_dhcp_leases}
        if target.startswith("hostapd.") and method == "get_clients":
            return {"clients": sample_wireless_clients}
        if target == "file" and method == "read":
            return {"data": sample_arp_table}
        if target == "uci" and method == "get":
            return {"values": firewall_values}
        return {}

    with (
        patch.object(mock_openwrt_client, "async_ubus_call", side_effect=mock_call),
        patch.object(
            mock_openwrt_client,
            "_async_get_wireless_interfaces",
            return_value=["hostapd.wlan0"],
        ),
    ):
        clients = await mock_openwrt_client.async_get_clients()

        assert len(clients) == 3
        alice = clients["aa:bb:cc:11:22:01"]
        assert alice.hostname == "Laptop-Alice"
        assert alice.is_online is True
        assert alice.is_wireless is True
        assert alice.signal_dbm == -50
        assert alice.internet_blocked is False

        tv = clients["aa:bb:cc:11:22:03"]
        assert tv.hostname == "Smart-TV"
        assert tv.is_online is True
        assert tv.internet_blocked is True
        assert tv.firewall_rule_id == "rule_block_tv"


@pytest.mark.asyncio
async def test_block_and_unblock_client(mock_openwrt_client: OpenWrtManager) -> None:
    """Test blocking and unblocking client via UCI firewall calls."""
    calls = []

    async def mock_call(target: str, method: str, params=None, retry_on_auth=True):
        calls.append((target, method, params))
        if target == "uci" and method == "get":
            return {
                "values": {
                    "ha_block_aa_bb_cc_11_22_01": {
                        ".type": "rule",
                        "src_mac": "aa:bb:cc:11:22:01",
                        "enabled": "1",
                    }
                }
            }
        return {}

    with patch.object(mock_openwrt_client, "async_ubus_call", side_effect=mock_call):
        # Block client
        await mock_openwrt_client.async_block_client(
            "aa:bb:cc:11:22:01", hostname="Laptop-Alice"
        )

        # Verify UCI set was called for blocking
        set_calls = [c for c in calls if c[0] == "uci" and c[1] == "set"]
        assert len(set_calls) >= 1
        assert set_calls[0][2]["values"]["enabled"] == "1"

        # Verify commit and reload were called
        assert any(c[0] == "uci" and c[1] == "commit" for c in calls)

        # Unblock client
        calls.clear()
        await mock_openwrt_client.async_unblock_client("aa:bb:cc:11:22:01")

        unblock_set_calls = [c for c in calls if c[0] == "uci" and c[1] == "set"]
        assert len(unblock_set_calls) >= 1
        assert unblock_set_calls[0][2]["values"]["enabled"] == "0"
        assert any(c[0] == "uci" and c[1] == "commit" for c in calls)


@pytest.mark.asyncio
async def test_ssh_protocol_workflow() -> None:
    """Test full SSH workflow (login, commands, fallbacks) without making HTTP calls."""
    ssh_client = OpenWrtManager(
        host="192.168.1.1",
        port=22,
        username="root",
        password="secretpassword",
        protocol="ssh",
    )

    ssh_commands: list[str] = []

    async def mock_ssh_run(cmd: str) -> str:
        ssh_commands.append(cmd)
        if cmd == "echo ok":
            return "ok\n"
        if "ubus call system board" in cmd:
            return json.dumps(
                {
                    "hostname": "OpenWrt-SSH",
                    "model": {"name": "GL.iNet GL-AXT1800"},
                    "release": {"description": "OpenWrt 23.05.3"},
                }
            )
        if "ubus call system info" in cmd:
            return json.dumps({"uptime": 86400})
        if "ubus call network.device status" in cmd:
            return json.dumps({"macaddr": "aa:bb:cc:00:11:22"})
        if "cat /tmp/dhcp.leases" in cmd:
            return "1761937200 aa:bb:cc:11:22:33 192.168.1.120 Phone-SSH *\n"
        if "cat /proc/net/arp" in cmd:
            return (
                "IP address HW type Flags HW address Mask Device\n"
                "192.168.1.120 0x1 0x2 aa:bb:cc:11:22:33 * br-lan\n"
            )
        if "ubus list" in cmd:
            return "hostapd.wlan0\n"
        if "hostapd.wlan0 get_clients" in cmd:
            return json.dumps(
                {
                    "clients": {
                        "aa:bb:cc:11:22:33": {
                            "signal": -58,
                            "rate": {"rx": 433000, "tx": 433000},
                        }
                    }
                }
            )
        if "uci get firewall" in cmd or "ubus call uci get" in cmd:
            return json.dumps({"values": {}})
        if "uci commit firewall" in cmd:
            return ""
        return ""

    with (
        patch.object(ssh_client, "_get_ssh_conn", AsyncMock()),
        patch.object(ssh_client, "async_run_ssh_command", side_effect=mock_ssh_run),
        patch.object(ssh_client, "_get_session") as mock_http_session,
    ):
        # 1. Login via SSH
        session_id = await ssh_client.async_login()
        assert session_id == "ssh_authenticated"
        # Confirm no HTTP session was ever touched!
        mock_http_session.assert_not_called()

        # 2. Get router info via SSH
        info = await ssh_client.async_get_router_info()
        assert info.hostname == "OpenWrt-SSH"
        assert info.model == "GL.iNet GL-AXT1800"
        assert info.mac == "aa:bb:cc:00:11:22"

        # 3. Get clients via SSH
        clients = await ssh_client.async_get_clients()
        assert "aa:bb:cc:11:22:33" in clients
        assert clients["aa:bb:cc:11:22:33"].is_online is True
        assert clients["aa:bb:cc:11:22:33"].hostname == "Phone-SSH"
        assert clients["aa:bb:cc:11:22:33"].signal_dbm == -58

        # 4. Block client via SSH
        await ssh_client.async_block_client("aa:bb:cc:11:22:33")
        assert any(
            "uci commit firewall && /etc/init.d/firewall reload" in cmd
            for cmd in ssh_commands
        )

        # Confirm HTTP was never called
        mock_http_session.assert_not_called()

    await ssh_client.close()
