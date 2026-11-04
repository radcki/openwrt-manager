"""Integration test for mock OpenWrt server logic."""

from __future__ import annotations

import pytest

from scripts.mock_openwrt_server import handle_ubus, state


class MockWebRequest:
    """Mock aiohttp.web.Request."""

    def __init__(self, data: dict) -> None:
        self._data = data

    async def json(self) -> dict:
        return self._data


@pytest.mark.asyncio
async def test_mock_server_ubus_flow() -> None:
    """Test mock server login, client discovery, and firewall manipulation."""
    # 1. Login
    login_req = MockWebRequest(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "call",
            "params": [
                "00000000000000000000000000000000",
                "session",
                "login",
                {"username": "root", "password": "password"},
            ],
        }
    )
    resp = await handle_ubus(login_req)
    assert resp.status == 200
    import json

    data = json.loads(resp.text)
    assert data["result"][0] == 0
    session_id = data["result"][1]["ubus_rpc_session"]
    assert session_id == state.session_id

    # 2. Query DHCP leases
    dhcp_req = MockWebRequest(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "call",
            "params": [session_id, "luci-rpc", "getDHCPLeases", {}],
        }
    )
    resp = await handle_ubus(dhcp_req)
    data = json.loads(resp.text)
    assert len(data["result"][1]["dhcp_leases"]) == 3

    # 3. Add Firewall Block Rule
    set_fw_req = MockWebRequest(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "call",
            "params": [
                session_id,
                "uci",
                "set",
                {
                    "config": "firewall",
                    "section": "ha_block_test_device",
                    "type": "rule",
                    "values": {
                        "name": "HA_Block_Test",
                        "src": "lan",
                        "dest": "wan",
                        "src_mac": "aa:bb:cc:dd:ee:99",
                        "target": "REJECT",
                        "enabled": "1",
                    },
                },
            ],
        }
    )
    resp = await handle_ubus(set_fw_req)
    data = json.loads(resp.text)
    assert data["result"][0] == 0
    assert "ha_block_test_device" in state.firewall_config
    assert state.firewall_config["ha_block_test_device"]["enabled"] == "1"

    # 4. Reload Firewall
    reload_req = MockWebRequest(
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "call",
            "params": [
                session_id,
                "file",
                "exec",
                {"command": "/etc/init.d/firewall", "params": ["reload"]},
            ],
        }
    )
    resp = await handle_ubus(reload_req)
    data = json.loads(resp.text)
    assert data["result"][0] == 0
