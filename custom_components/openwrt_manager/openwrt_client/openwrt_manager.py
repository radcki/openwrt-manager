from __future__ import annotations

import contextlib
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from openwrt_manager.openwrt_client import OpenWrtClient

PROTOCOL_SSH = "ssh"

_LOGGER = logging.getLogger(__name__)


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


@dataclass
class ClientDevice:
    """Represents a client device connected to OpenWrt."""

    mac: str
    ip: str | None = None
    alias: str | None = None
    hostname: str | None = None
    is_online: bool = False
    is_wireless: bool = False
    interface: str | None = None
    signal_dbm: int | None = None
    rx_rate: int | None = None
    tx_rate: int | None = None
    internet_blocked: bool = False
    firewall_rule_id: str | None = None
    extra_attributes: dict[str, Any] = field(default_factory=dict)


def normalize_mac(mac: str) -> str:
    """Normalize MAC address to lowercase colon-separated format."""
    clean = re.sub(r"[^0-9a-fA-F]", "", mac).upper()
    return ":".join(clean[i : i + 2] for i in range(0, 12, 2))


def sanitize_rule_name(mac: str) -> str:
    """Generate a valid UCI section identifier from a MAC address."""
    return f"ha_block_{re.sub(r'[^0-9a-zA-Z]', '_', mac).lower()}"


class OpenWrtManager:
    """Asynchronous client for interacting with OpenWrt."""

    def __init__(
        self,
        openwrt_client: OpenWrtClient,
        firewall_rule_name: str,
        firewall_action: str = "REJECT",
    ) -> None:
        """Initialize the OpenWrt client."""
        self.openwrt_client = openwrt_client
        self.firewall_action = firewall_action
        self.firewall_rule_name = firewall_rule_name

    async def async_get_router_info(self) -> RouterInfo:
        """Retrieve router model, hostname, release version, and primary MAC."""
        info = RouterInfo()
        try:
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
        except Exception as err:
            _LOGGER.warning("Could not fetch system.board info: %s", err)

        try:
            sys_info = await self.openwrt_client.async_ubus_call("system", "info")
            if isinstance(sys_info, dict):
                info.uptime = sys_info.get("uptime", 0)
        except Exception as err:
            _LOGGER.warning("Could not fetch system.info: %s", err)

        # Get LAN MAC address for router unique identifier
        try:
            net_status = await self.openwrt_client.async_ubus_call(
                "network.device", "status", {"name": "br-lan"}
            )
            if isinstance(net_status, dict) and "macaddr" in net_status:
                info.mac = normalize_mac(net_status["macaddr"])
            else:
                for iface in ("eth0", "lan", "eth1"):
                    fallback = await self.openwrt_client.async_ubus_call(
                        "network.device", "status", {"name": iface}
                    )
                    if isinstance(fallback, dict) and "macaddr" in fallback:
                        info.mac = normalize_mac(fallback["macaddr"])
                        break
        except Exception:
            _LOGGER.debug("Using fallback MAC address for OpenWrt router")

        # fallback for primary MAC if network.device status did not return it
        if info.mac == "00:00:00:00:00:00":
            try:
                mac_out = await self.openwrt_client.async_run_ssh_command(
                    "cat /sys/class/net/br-lan/address 2>/dev/null || cat /sys/class/net/eth0/address 2>/dev/null"
                )
                if mac_out.strip():
                    info.mac = normalize_mac(mac_out.strip())
            except Exception:
                pass

        return info

    async def async_get_glinet_aliases(self) -> dict[str, str]:
        """Fetch GL-iNet client name aliases from the gl-client UCI config.

        GL-iNet firmware stores user-assigned device names in /etc/config/gl-client.
        Each section looks like:
            config client 'aabbccddeeff'
                option mac 'aa:bb:cc:dd:ee:ff'
                option name 'My Phone'

        Returns a dict mapping normalized MAC -> alias name.
        """
        aliases: dict[str, str] = {}

        try:
            gl_config = await self.openwrt_client.async_ubus_call(
                "uci", "get", {"config": "gl-client"}
            )
            if isinstance(gl_config, dict) and "values" in gl_config:
                for _sec, sec_val in gl_config["values"].items():
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
        except Exception as err:
            _LOGGER.debug("uci.get gl-client failed: %s", err)

        # SSH fallback: parse `uci show gl-client` output
        try:
            raw = await self.openwrt_client.async_run_ssh_command(
                "uci show gl-client 2>/dev/null"
            )
            # Lines look like:
            #   gl-client.aabbccddeeff.mac='aa:bb:cc:dd:ee:ff'
            #   gl-client.aabbccddeeff.name='My Phone'
            section_mac: dict[str, str] = {}  # section_id -> mac
            section_name: dict[str, str] = {}  # section_id -> name
            for line in raw.splitlines():
                line = line.strip()
                if not line or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                val = val.strip("'\"")
                parts = key.split(".")
                if len(parts) < 3:
                    continue
                # parts: ['gl-client', '<section>', '<option>']
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
                else:
                    # section name itself may be the MAC without colons
                    with contextlib.suppress(Exception):
                        aliases[sec_id] = name

            if aliases:
                _LOGGER.debug(
                    "Loaded %d GL-iNet client aliases via SSH uci show",
                    len(aliases),
                )
        except Exception as err:
            _LOGGER.debug("SSH uci show gl-client failed: %s", err)

        return aliases

    async def async_get_clients(self) -> dict[str, ClientDevice]:
        """Fetch all connected clients from DHCP leases, wireless stations, and ARP."""
        clients: dict[str, ClientDevice] = {}

        # 1. Fetch DHCP Leases via ubus
        try:
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
        except Exception as err:
            _LOGGER.warning("Could not fetch DHCP leases from OpenWrt ubus: %s", err)

        # 1b. SSH Fallback: Read /tmp/dhcp.leases directly if luci-rpc was not available
        # if self.protocol == PROTOCOL_SSH:
        #     try:
        #         raw_leases = await self.async_run_ssh_command(
        #             "cat /tmp/dhcp.leases 2>/dev/null"
        #         )
        #         for line in raw_leases.splitlines():
        #             parts = line.split()
        #             if len(parts) >= 4:
        #                 mac, ip, hostname = parts[1], parts[2], parts[3]
        #                 norm_mac = normalize_mac(mac)
        #                 if norm_mac not in clients:
        #                     clients[norm_mac] = ClientDevice(
        #                         mac=norm_mac,
        #                         ip=ip,
        #                         hostname=hostname
        #                         if hostname != "*"
        #                         else f"Client-{norm_mac[-5:].replace(':', '')}",
        #                         is_online=False,
        #                     )
        #                 elif not clients[norm_mac].ip:
        #                     clients[norm_mac].ip = ip
        #     except Exception as err:
        #         _LOGGER.debug("Could not read /tmp/dhcp.leases via SSH: %s", err)

        # 2. Fetch Wireless Station Clients
        try:
            wireless_clients_cmd = 'iwinfo | grep "ESSID:" | cut -d" " -f1 | while read iface; do iwinfo "$iface" assoclist | xargs | sed \'s/..:..:..:..:..:../\\n&/g\' | grep -E "^([[:xdigit:]]{2}:){5}[[:xdigit:]]{2}"; done'
            wireless_clients = await self.openwrt_client.async_run_ssh_command(
                wireless_clients_cmd
            )
            parse_pattern = r"([0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5})\s+(-?\d+)\s+dBm.*?RX:\s+([\d.]+)\s+MBit/s.*?TX:\s+([\d.]+)\s+MBit/s"
            for wireless_client in wireless_clients.splitlines():
                match = re.match(parse_pattern, wireless_client)
                if not match:
                    continue
                mac, signal, rx, tx = match.groups()
                norm_mac = normalize_mac(mac)
                if norm_mac not in clients:
                    clients[norm_mac] = ClientDevice(mac=norm_mac)

                client = clients[norm_mac]

                client.is_online = True
                client.is_wireless = True
                # client.interface = wireless_client.replace("hostapd.", "")
                client.signal_dbm = int(signal)
                client.rx_rate = int(rx)
                client.tx_rate = int(tx)

        except Exception as err:
            _LOGGER.warning("Could not fetch wireless station info: %s", err)

        # 3. Fetch ARP Table (marks active clients as online)
        try:
            arp_data = await self._async_get_arp_table()
            for entry in arp_data:
                mac = entry.get("mac")
                if not mac:
                    continue
                norm_mac = normalize_mac(mac)
                if norm_mac not in clients:
                    clients[norm_mac] = ClientDevice(
                        mac=norm_mac,
                        ip=entry.get("ip"),
                        is_online=True,
                    )
                else:
                    client = clients[norm_mac]
                    if not client.ip and entry.get("ip"):
                        client.ip = entry.get("ip")
                    if entry.get("flags") != "0x0":
                        client.is_online = True
        except Exception as err:
            _LOGGER.debug("Could not parse ARP cache: %s", err)

        # 4. Check Firewall Block Status for each client
        try:
            rule = await self._async_get_or_create_firewall_rule()
            if rule is not None:
                for norm_mac, client in clients.items():
                    if norm_mac in rule.src_mac:
                        client.internet_blocked = True
                        client.firewall_rule_id = rule.name
                    else:
                        client.internet_blocked = False
                        client.firewall_rule_id = None
        except Exception as err:
            _LOGGER.warning("Could not inspect firewall rules: %s", err)

        # 5. Overlay GL-iNet user-assigned aliases as device names
        try:
            aliases = await self.async_get_glinet_aliases()
            for norm_mac, alias in aliases.items():
                if norm_mac in clients:
                    clients[norm_mac].alias = alias
        except Exception as err:
            _LOGGER.debug("Could not fetch GL-iNet aliases: %s", err)

        return clients

    async def _async_get_arp_table(self) -> list[dict[str, str]]:
        """Read /proc/net/arp using SSH or ubus file read."""
        entries: list[dict[str, str]] = []
        raw_text: str | None = None

        # if self.protocol == PROTOCOL_SSH:
        #     with contextlib.suppress(Exception):
        #         raw_text = await self.openwrt_client.async_run_ssh_command(
        #             "cat /proc/net/arp 2>/dev/null"
        #         )
        # else:
        try:
            file_resp = await self.openwrt_client.async_ubus_call(
                "file", "read", {"path": "/proc/net/arp"}
            )
            if isinstance(file_resp, dict) and "data" in file_resp:
                raw_text = file_resp["data"]
        except Exception:
            pass

        if raw_text:
            lines = raw_text.splitlines()
            for line in lines[1:]:
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

    async def _async_add_firewall_rule(self) -> None:
        rule = FirewallRule(
            index=-1,
            id="",
            name=self.firewall_rule_name,
            src="lan",
            dest="wan",
            target="REJECT",
            enabled=False,
            src_mac=[],
        )
        rule_dict = rule.as_uci_value_dict()
        await self.openwrt_client.async_ubus_call(
            "uci",
            "add",
            {
                "config": "firewall",
                "name": self.firewall_rule_name,
                "type": "rule",
                "values": rule_dict,
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
        try:
            fw_config = await self.openwrt_client.async_ubus_call(
                "uci", "get", {"config": "firewall"}
            )
            if not isinstance(fw_config, dict) or "values" not in fw_config:
                return None

            sections = fw_config["values"]
            for key, firewall_rule in sections.items():
                if not isinstance(firewall_rule, dict):
                    continue
                if firewall_rule.get(".type") != "rule":
                    continue
                name = firewall_rule.get("name")

                if name != self.firewall_rule_name:
                    continue

                index = int(firewall_rule.get(".index", ""))
                src = firewall_rule.get("src", "")
                dest = firewall_rule.get("dest", "")
                target = firewall_rule.get("target", "").upper()
                enabled = str(firewall_rule.get("enabled", "1")) == "1"
                src_mac = firewall_rule.get("src_mac") or []
                if isinstance(src_mac, str):
                    src_mac = [src_mac]
                return FirewallRule(
                    index=index,
                    id=key,
                    name=name,
                    src=src,
                    dest=dest,
                    target=target,
                    enabled=enabled,
                    src_mac=src_mac,
                )
        except Exception as err:
            _LOGGER.warning("Failed to retrieve UCI firewall config: %s", err)
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
        """Unblock internet access for a device by disabling or removing its firewall rule."""
        norm_mac = normalize_mac(mac)
        _LOGGER.info("Unblocking internet access for client %s", norm_mac)

        current_rule = await self._async_get_or_create_firewall_rule()
        if current_rule is None:
            _LOGGER.warning("Rule %s not found", self.firewall_rule_name)
            return
        if norm_mac not in current_rule.src_mac:
            _LOGGER.info(
                "Client %s (%s) was already not in rule, doing nothing", norm_mac
            )
            return

        current_rule.src_mac.remove(norm_mac)
        await self._async_save_rule(current_rule)

    async def _async_save_rule(self, rule: FirewallRule) -> None:
        rule_dict = rule.as_uci_value_dict()
        await self.openwrt_client.async_ubus_call(
            "uci",
            "set",
            {
                "config": "firewall",
                "section": rule.id,
                "type": "rule",
                "values": rule_dict,
            },
        )
        await self._async_commit_and_reload_firewall()

    async def _async_commit_and_reload_firewall(self) -> None:
        """Commit UCI changes and reload the OpenWrt firewall."""
        try:
            await self.openwrt_client.async_run_ssh_command(
                "uci commit firewall && /etc/init.d/firewall reload"
            )
            return
        except Exception as err:
            _LOGGER.error(f"Failed to commit/reload firewall via SSH: {err}")
