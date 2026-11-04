#!/usr/bin/env python3
"""Mock OpenWrt ubus JSON-RPC HTTP server for local testing."""

from __future__ import annotations

import argparse
import logging
from typing import Any

from aiohttp import web

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)
_LOGGER = logging.getLogger("mock_openwrt")


class MockOpenWrtState:
    """In-memory state of simulated OpenWrt router."""

    def __init__(self) -> None:
        self.session_id = "mock_session_abcdef1234567890"
        self.hostname = "OpenWrt-Lab"
        self.model = "Simulated OpenWrt Router"
        self.release = "OpenWrt 23.05.3 r23809-234f1a2efa"
        self.mac = "02:ba:de:00:00:01"
        self.uptime = 86400

        # Sample connected devices
        self.dhcp_leases = [
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

        self.wireless_clients = {
            "aa:bb:cc:11:22:01": {
                "signal": -48,
                "rate": {"rx": 866700, "tx": 866700},
            },
            "aa:bb:cc:11:22:02": {
                "signal": -65,
                "rate": {"rx": 433300, "tx": 300000},
            },
        }

        self.arp_table = (
            "IP address       HW type     Flags       HW address            Mask     Device\n"
            "192.168.1.101    0x1         0x2         aa:bb:cc:11:22:01     *        br-lan\n"
            "192.168.1.102    0x1         0x2         aa:bb:cc:11:22:02     *        br-lan\n"
            "192.168.1.103    0x1         0x2         aa:bb:cc:11:22:03     *        br-lan\n"
        )

        # UCI Firewall sections
        self.firewall_config: dict[str, Any] = {
            "defaults": {
                ".type": "defaults",
                "syn_flood": "1",
                "input": "ACCEPT",
                "output": "ACCEPT",
                "forward": "REJECT",
            },
            "zone_lan": {
                ".type": "zone",
                "name": "lan",
                "network": ["lan"],
                "input": "ACCEPT",
                "output": "ACCEPT",
                "forward": "ACCEPT",
            },
            "zone_wan": {
                ".type": "zone",
                "name": "wan",
                "network": ["wan", "wan6"],
                "input": "REJECT",
                "output": "ACCEPT",
                "forward": "REJECT",
                "masq": "1",
                "mtu_fix": "1",
            },
        }


state = MockOpenWrtState()


async def handle_ubus(request: web.Request) -> web.Response:
    """Handle ubus JSON-RPC requests."""
    try:
        data = await request.json()
    except Exception as err:
        return web.json_response(
            {"jsonrpc": "2.0", "error": {"code": -32700, "message": str(err)}}
        )

    req_id = data.get("id", 1)
    method = data.get("method")
    params = data.get("params", [])

    if method == "list":
        # Return registered objects matching pattern
        return web.json_response(
            {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "hostapd.wlan0": {},
                    "hostapd.wlan1": {},
                    "system": {},
                    "session": {},
                    "luci-rpc": {},
                    "uci": {},
                    "file": {},
                },
            }
        )

    if method != "call":
        return web.json_response(
            {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32601, "message": "Method not found"},
            }
        )

    if len(params) < 3:
        return web.json_response(
            {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32602, "message": "Invalid params"},
            }
        )

    _session, target, action = params[0], params[1], params[2]
    args = params[3] if len(params) > 3 else {}

    _LOGGER.info("ubus call: target=%s, action=%s, args=%s", target, action, args)

    # 1. Session Login
    if target == "session" and action == "login":
        username = args.get("username")
        password = args.get("password")
        if username == "root" and password is not None:
            return web.json_response(
                {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": [
                        0,
                        {
                            "ubus_rpc_session": state.session_id,
                            "timeout": 300,
                            "expires": 300,
                            "data": {"username": username},
                        },
                    ],
                }
            )
        return web.json_response({"jsonrpc": "2.0", "id": req_id, "result": [6, {}]})

    # 2. System Board & Info
    if target == "system" and action == "board":
        return web.json_response(
            {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": [
                    0,
                    {
                        "hostname": state.hostname,
                        "model": {"name": state.model},
                        "release": {"description": state.release},
                    },
                ],
            }
        )

    if target == "system" and action == "info":
        return web.json_response(
            {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": [0, {"uptime": state.uptime}],
            }
        )

    # 3. Network Device Status
    if target == "network.device" and action == "status":
        return web.json_response(
            {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": [0, {"macaddr": state.mac, "up": True}],
            }
        )

    # 4. DHCP Leases
    if target == "luci-rpc" and action == "getDHCPLeases":
        return web.json_response(
            {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": [0, {"dhcp_leases": state.dhcp_leases, "dhcp6_leases": []}],
            }
        )

    # 5. Hostapd Wireless Stations
    if target.startswith("hostapd.") and action == "get_clients":
        return web.json_response(
            {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": [0, {"clients": state.wireless_clients}],
            }
        )

    # 6. File read for ARP cache
    if target == "file" and action == "read":
        path = args.get("path")
        if path == "/proc/net/arp":
            return web.json_response(
                {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": [0, {"data": state.arp_table}],
                }
            )

    # 7. UCI Firewall Config Get / Set / Commit
    if target == "uci" and action == "get":
        config = args.get("config")
        if config == "firewall":
            return web.json_response(
                {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": [0, {"values": state.firewall_config}],
                }
            )

    if target == "uci" and action == "set":
        config = args.get("config")
        section = args.get("section")
        values = args.get("values", {})
        sec_type = args.get("type", "rule")

        if config == "firewall" and section:
            if section not in state.firewall_config:
                state.firewall_config[section] = {".type": sec_type}
            state.firewall_config[section].update(values)
            _LOGGER.info(
                "Updated firewall section '%s': %s",
                section,
                state.firewall_config[section],
            )
            return web.json_response(
                {"jsonrpc": "2.0", "id": req_id, "result": [0, {}]}
            )

    if target == "uci" and action == "commit":
        _LOGGER.info("Committed UCI firewall configuration")
        return web.json_response({"jsonrpc": "2.0", "id": req_id, "result": [0, {}]})

    # 8. Reload Firewall
    if (target == "file" and action == "exec") or (
        target == "luci" and action == "reload_config"
    ):
        _LOGGER.info("Firewall reload requested and executed successfully")
        return web.json_response({"jsonrpc": "2.0", "id": req_id, "result": [0, {}]})

    # Fallback
    return web.json_response({"jsonrpc": "2.0", "id": req_id, "result": [0, {}]})


def main() -> None:
    """Run the mock server."""
    parser = argparse.ArgumentParser(description="Mock OpenWrt ubus server")
    parser.add_argument("--host", default="127.0.0.1", help="Host interface to bind")
    parser.add_argument(
        "--port", type=int, default=8080, help="Port to listen on (default: 8080)"
    )
    args = parser.parse_args()

    app = web.Application()
    app.router.add_post("/ubus", handle_ubus)

    _LOGGER.info(
        "Starting Mock OpenWrt Server on http://%s:%d/ubus", args.host, args.port
    )
    web.run_app(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
