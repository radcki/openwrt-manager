from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from ..models import ClientDevice
from .openwrt_client import OpenWrtClient, OpenWrtClientError

_LOGGER = logging.getLogger(__name__)

WIRELESS_CLIENTS_CMD = (
    'iwinfo | grep "ESSID:" | cut -d" " -f1 | while read iface; do '
    "iwinfo \"$iface\" assoclist | xargs | sed 's/..:..:..:..:..:../\\n&/g' | "
    'grep -E "^([[:xdigit:]]{2}:){5}[[:xdigit:]]{2}"; done'
)
WIRELESS_CLIENT_PATTERN = re.compile(
    r"([0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5})\s+(-?\d+)\s+dBm"
    r".*?RX:\s+([\d.]+)\s+MBit/s.*?TX:\s+([\d.]+)\s+MBit/s"
)


@dataclass
class FirewallRule:
    id: str
    index: int
    name: str
    src: str
    dest: str
    target: str
    src_mac: list[str]
    enabled: bool

    def as_uci_value_dict(self) -> dict[str, Any]:
        enabled = len(self.src_mac) > 0
        src_mac = "" if len(self.src_mac) == 0 else self.src_mac
        return {
            "name": self.name,
            "src": self.src,
            "dest": self.dest,
            "target": self.target,
            "src_mac": src_mac,
            "enabled": "1" if enabled else "0",
        }


@dataclass
class RouterInfo:
    """Represents OpenWrt router hardware and software info."""

    hostname: str = "OpenWrt"
    model: str = "Generic OpenWrt Router"
    release: str = "Unknown"
    mac: str = "00:00:00:00:00:00"
    uptime: int = 0


def normalize_mac(mac: str) -> str:
    """Normalize MAC address to uppercase colon-separated format."""
    clean = re.sub(r"[^0-9a-fA-F]", "", mac).upper()
    return ":".join(clean[i : i + 2] for i in range(0, 12, 2))


def sanitize_rule_name(mac: str) -> str:
    """Generate a valid UCI section identifier from a MAC address."""
    return f"ha_block_{re.sub(r'[^0-9a-zA-Z]', '_', mac).lower()}"


def parse_wireless_clients(raw: str) -> list[tuple[str, int, int, int]]:
    """Parse association lines into (mac, signal_dbm, rx_mbit, tx_mbit) tuples."""
    result = []
    for line in raw.splitlines():
        match = WIRELESS_CLIENT_PATTERN.match(line)
        if not match:
            continue
        mac, signal, rx, tx = match.groups()
        result.append((mac, int(signal), int(float(rx)), int(float(tx))))
    return result


def parse_arp_table(raw: str) -> list[dict[str, str]]:
    """Parse the contents of /proc/net/arp."""
    entries: list[dict[str, str]] = []
    for line in raw.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 6:
            entries.append(
                {
                    "ip": parts[0],
                    "flags": parts[2],
                    "mac": parts[3],
                    "device": parts[5],
                }
            )
    return entries


class OpenWrtManager:
    """High-level operations on an OpenWrt router."""

    def __init__(
        self,
        openwrt_client: OpenWrtClient,
        firewall_rule_name: str,
        firewall_action: str = "REJECT",
    ) -> None:
        """Initialize the manager."""
        self.openwrt_client = openwrt_client
        self.firewall_action = firewall_action
        self.firewall_rule_name = firewall_rule_name

    async def async_get_router_info(self) -> RouterInfo:
        """Retrieve router model, hostname, release version, and primary MAC."""
        info = RouterInfo()
        board = await self.openwrt_client.async_ubus_call("system", "board")
        if isinstance(board, dict):
            info.hostname = board.get("hostname", "OpenWrt")
            model_info = board.get("model", {})
            info.model = (
                model_info.get("name", "OpenWrt Device")
                if isinstance(model_info, dict)
                else str(model_info or "OpenWrt Device")
            )
            release_info = board.get("release", {})
            info.release = (
                release_info.get("description", "OpenWrt")
                if isinstance(release_info, dict)
                else str(release_info or "OpenWrt")
            )

        sys_info = await self.openwrt_client.async_ubus_call("system", "info")
        if isinstance(sys_info, dict):
            info.uptime = sys_info.get("uptime", 0)

        # Get LAN MAC address for router unique identifier
        for iface in ("br-lan", "eth0", "lan", "eth1"):
            net_status = await self.openwrt_client.async_ubus_call(
                "network.device", "status", {"name": iface}
            )
            if isinstance(net_status, dict) and "macaddr" in net_status:
                info.mac = normalize_mac(net_status["macaddr"])
                break

        # fallback for primary MAC if network.device status did not return it
        if info.mac == "00:00:00:00:00:00":
            mac_out = await self.openwrt_client.async_run_ssh_command(
                "cat /sys/class/net/br-lan/address 2>/dev/null || cat /sys/class/net/eth0/address 2>/dev/null"
            )
            if mac_out.strip():
                info.mac = normalize_mac(mac_out.strip())

        return info

    async def async_get_glinet_aliases(self) -> dict[str, str]:
        """Fetch GL-iNet client name aliases from the gl-client UCI config.

        GL-iNet firmware stores user-assigned device names in /etc/config/gl-client.
        Each section looks like:
            config client 'aabbccddeeff'
                option mac 'aa:bb:cc:dd:ee:ff'
                option alias 'My Phone'

        Returns a dict mapping normalized MAC -> alias name.
        """
        aliases: dict[str, str] = {}

        try:
            gl_config = await self.openwrt_client.async_ubus_call(
                "uci", "get", {"config": "gl-client"}
            )
            if isinstance(gl_config, dict) and "values" in gl_config:
                for sec_val in gl_config["values"].values():
                    if not isinstance(sec_val, dict):
                        continue
                    mac = sec_val.get("mac")
                    name = sec_val.get("alias")
                    if mac and name and name.strip():
                        aliases[normalize_mac(mac)] = name.strip()
                if aliases:
                    _LOGGER.debug(
                        "Loaded %d GL-iNet client aliases via uci.get", len(aliases)
                    )
                    return aliases
        except OpenWrtClientError as err:
            _LOGGER.debug("uci.get gl-client failed: %s", err)

        # SSH fallback: parse `uci show gl-client` output
        try:
            raw = await self.openwrt_client.async_run_ssh_command(
                "uci show gl-client 2>/dev/null"
            )
            # Lines look like:
            #   gl-client.aabbccddeeff.mac='aa:bb:cc:dd:ee:ff'
            #   gl-client.aabbccddeeff.alias='My Phone'
            section_mac: dict[str, str] = {}
            section_name: dict[str, str] = {}
            for line in raw.splitlines():
                line = line.strip()
                if not line or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                val = val.strip("'\"")
                parts = key.split(".")
                if len(parts) < 3:
                    continue
                sec_id = parts[1]
                option = parts[2]
                if option == "mac":
                    section_mac[sec_id] = normalize_mac(val)
                elif option == "alias" and val.strip():
                    section_name[sec_id] = val.strip()

            for sec_id, name in section_name.items():
                mac = section_mac.get(sec_id)
                if mac:
                    aliases[mac] = name
                elif re.fullmatch(r"[0-9a-fA-F]{12}", sec_id):
                    # section name itself may be the MAC without colons
                    aliases[normalize_mac(sec_id)] = name

            if aliases:
                _LOGGER.debug(
                    "Loaded %d GL-iNet client aliases via SSH uci show",
                    len(aliases),
                )
        except OpenWrtClientError as err:
            _LOGGER.debug("SSH uci show gl-client failed: %s", err)

        return aliases

    async def async_get_clients(self) -> dict[str, ClientDevice]:
        """Fetch all connected clients from DHCP leases, wireless stations, and ARP."""
        clients: dict[str, ClientDevice] = {}

        # 1. DHCP leases
        leases_resp = await self.openwrt_client.async_ubus_call(
            "luci-rpc", "getDHCPLeases"
        )
        if isinstance(leases_resp, dict):
            for lease in leases_resp.get("dhcp_leases", []):
                mac = lease.get("macaddr")
                if not mac:
                    continue
                norm_mac = normalize_mac(mac)
                clients[norm_mac] = ClientDevice(
                    mac=norm_mac,
                    ip=lease.get("ipaddr"),
                    hostname=lease.get("hostname")
                    or f"Client-{norm_mac[-5:].replace(':', '')}",
                    is_online=False,
                )

        # 2. Wireless stations
        wireless_raw = await self.openwrt_client.async_run_ssh_command(
            WIRELESS_CLIENTS_CMD
        )
        for mac, signal, ap_rx_rate, ap_tx_rate in parse_wireless_clients(wireless_raw):
            norm_mac = normalize_mac(mac)
            client = clients.setdefault(norm_mac, ClientDevice(mac=norm_mac))
            client.is_online = True
            client.is_wireless = True
            client.signal_dbm = signal
            client.rx_rate = ap_tx_rate
            client.tx_rate = ap_rx_rate

        # 3. ARP table (marks active clients as online)
        arp_raw = await self.openwrt_client.async_run_ssh_command(
            "cat /proc/net/arp 2>/dev/null"
        )
        for entry in parse_arp_table(arp_raw):
            norm_mac = normalize_mac(entry["mac"])
            if norm_mac == "00:00:00:00:00:00":
                continue
            existing = clients.get(norm_mac)
            if existing is None:
                clients[norm_mac] = ClientDevice(
                    mac=norm_mac, ip=entry["ip"], is_online=True
                )
                continue
            if not existing.ip:
                existing.ip = entry["ip"]
            if entry["flags"] != "0x0":
                existing.is_online = True

        # 4. Firewall block status
        rule = await self._async_get_or_create_firewall_rule()
        for norm_mac, client in clients.items():
            blocked = rule is not None and norm_mac in rule.src_mac
            client.internet_blocked = blocked
            client.firewall_rule_id = rule.name if blocked and rule else None

        # 5. GL-iNet user-assigned aliases
        for norm_mac, alias in (await self.async_get_glinet_aliases()).items():
            if norm_mac in clients:
                clients[norm_mac].alias = alias

        return clients

    async def _async_add_firewall_rule(self) -> None:
        rule = FirewallRule(
            index=-1,
            id="",
            name=self.firewall_rule_name,
            src="lan",
            dest="wan",
            target=self.firewall_action,
            enabled=False,
            src_mac=[],
        )
        await self.openwrt_client.async_ubus_call(
            "uci",
            "add",
            {
                "config": "firewall",
                "name": self.firewall_rule_name,
                "type": "rule",
                "values": rule.as_uci_value_dict(),
            },
        )
        await self._async_commit_and_reload_firewall()

    async def _async_get_or_create_firewall_rule(self) -> FirewallRule | None:
        rule = await self._async_get_firewall_rule()
        if rule is None:
            _LOGGER.info(
                "Rule %s not found, adding new entry.", self.firewall_rule_name
            )
            await self._async_add_firewall_rule()
            rule = await self._async_get_firewall_rule()
        if rule is None:
            _LOGGER.error(
                "Rule %s not found after attempting creation, returning None.",
                self.firewall_rule_name,
            )
        return rule

    async def _async_get_firewall_rule(self) -> FirewallRule | None:
        fw_config = await self.openwrt_client.async_ubus_call(
            "uci", "get", {"config": "firewall"}
        )
        if not isinstance(fw_config, dict) or "values" not in fw_config:
            return None

        for key, firewall_rule in fw_config["values"].items():
            if not isinstance(firewall_rule, dict):
                continue
            if firewall_rule.get(".type") != "rule":
                continue
            name = firewall_rule.get("name")
            if name != self.firewall_rule_name:
                continue

            src_mac = firewall_rule.get("src_mac") or []
            if isinstance(src_mac, str):
                src_mac = [src_mac]
            return FirewallRule(
                index=int(firewall_rule.get(".index", 0)),
                id=key,
                name=name,
                src=firewall_rule.get("src", ""),
                dest=firewall_rule.get("dest", ""),
                target=firewall_rule.get("target", "").upper(),
                enabled=str(firewall_rule.get("enabled", "1")) == "1",
                src_mac=[normalize_mac(mac) for mac in src_mac],
            )
        return None

    async def async_block_client(self, mac: str) -> None:
        """Block internet access for a device by modifying OpenWrt firewall rules."""
        norm_mac = normalize_mac(mac)
        _LOGGER.info("Blocking internet access for client %s", norm_mac)
        current_rule = await self._async_get_or_create_firewall_rule()
        if current_rule is None:
            _LOGGER.warning("Rule %s not found", self.firewall_rule_name)
            return
        if norm_mac in current_rule.src_mac:
            _LOGGER.info("Client %s was already in rule, doing nothing", norm_mac)
            return

        current_rule.src_mac.append(norm_mac)
        await self._async_save_rule(current_rule)

    async def async_unblock_client(self, mac: str) -> None:
        """Unblock internet access for a device by removing it from the rule."""
        norm_mac = normalize_mac(mac)
        _LOGGER.info("Unblocking internet access for client %s", norm_mac)

        current_rule = await self._async_get_or_create_firewall_rule()
        if current_rule is None:
            _LOGGER.warning("Rule %s not found", self.firewall_rule_name)
            return
        if norm_mac not in current_rule.src_mac:
            _LOGGER.info("Client %s was already not in rule, doing nothing", norm_mac)
            return

        current_rule.src_mac.remove(norm_mac)
        await self._async_save_rule(current_rule)

    async def _async_save_rule(self, rule: FirewallRule) -> None:
        await self.openwrt_client.async_ubus_call(
            "uci",
            "set",
            {
                "config": "firewall",
                "section": rule.id,
                "type": "rule",
                "values": rule.as_uci_value_dict(),
            },
        )
        await self._async_commit_and_reload_firewall()

    async def _async_commit_and_reload_firewall(self) -> None:
        """Commit UCI changes and reload the OpenWrt firewall."""
        await self.openwrt_client.async_run_ssh_command(
            "uci commit firewall && /etc/init.d/firewall reload"
        )
