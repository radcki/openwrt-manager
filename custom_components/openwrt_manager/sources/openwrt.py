"""Read-only wireless telemetry from independent OpenWrt access points."""

import shlex
import time
from collections.abc import Callable
from dataclasses import dataclass

from ..models import AccessPointInfo, ClientObservation
from ..openwrt_client.openwrt_client import OpenWrtClient, OpenWrtClientError
from ..openwrt_client.openwrt_manager import normalize_mac


class OpenWrtAccessPointSource:
    """Read iwinfo JSON per radio without accessing router firewall configuration."""

    def __init__(self, source_id: str, name: str, client: OpenWrtClient) -> None:
        self.source_id = source_id
        self.name = name
        self.client = client

    def start(self, updated: Callable[[dict[str, ClientObservation]], None]) -> None:
        """Polling is scheduled by the source manager."""

    async def async_refresh(self) -> dict[str, ClientObservation]:
        """Read associations and metadata for every iwinfo interface."""
        devices = await self.client.async_ubus_call("iwinfo", "devices")
        if not isinstance(devices, dict) or "devices" not in devices:
            raise OpenWrtClientError("iwinfo devices unavailable")
        observations = {}
        for interface in devices["devices"]:
            info = await self.client.async_ubus_call(
                "iwinfo", "info", {"device": interface}
            )
            associations = await self.client.async_ubus_call(
                "iwinfo", "assoclist", {"device": interface}
            )
            if (
                not isinstance(info, dict)
                or not isinstance(associations, dict)
                or "results" not in associations
            ):
                raise OpenWrtClientError("iwinfo association data unavailable")
            raw = await self.client.async_run_ssh_command(
                f"iw dev {shlex.quote(interface)} station dump"
            )
            counters = parse_station_counters(raw)
            ap_mac = normalize_mac(info.get("bssid", ""))
            access_point = AccessPointInfo(self.source_id, ap_mac, self.name)
            frequency = info.get("frequency", 0)
            band = (
                "6 GHz"
                if frequency >= 5925
                else "5 GHz"
                if frequency >= 4900
                else "2.4 GHz"
                if frequency
                else None
            )
            for station in associations["results"]:
                mac = normalize_mac(station["mac"])
                station_counters = counters.get(mac, StationCounters())
                observations[mac] = ClientObservation(
                    mac=mac,
                    source_id=self.source_id,
                    observed_at=time.time(),
                    access_point=access_point,
                    ssid=info.get("ssid"),
                    band=band,
                    channel=info.get("channel"),
                    signal_dbm=station.get("signal"),
                    rx_rate=station.get("tx", {}).get("rate", 0) / 1000 or None,
                    tx_rate=station.get("rx", {}).get("rate", 0) / 1000 or None,
                    rx_bytes=station_counters.tx_bytes,
                    tx_bytes=station_counters.rx_bytes,
                    connected_seconds=station_counters.connected_seconds,
                )
        return observations

    async def close(self) -> None:
        """Close this AP's independent SSH connection."""
        await self.client.close()


@dataclass
class StationCounters:
    """AP-perspective counters and elapsed association time reported by iw."""

    rx_bytes: int | None = None
    tx_bytes: int | None = None
    connected_seconds: int | None = None


def parse_station_counters(raw: str) -> dict[str, StationCounters]:
    """Extract AP-perspective byte counters from Linux iw station dump."""
    result: dict[str, StationCounters] = {}
    counters: StationCounters | None = None
    for line in raw.splitlines():
        words = line.strip().split()
        if words[:1] == ["Station"]:
            counters = StationCounters()
            result[normalize_mac(words[1])] = counters
        elif counters is not None:
            if words[:2] == ["rx", "bytes:"]:
                counters.rx_bytes = int(words[2])
            elif words[:2] == ["tx", "bytes:"]:
                counters.tx_bytes = int(words[2])
            elif words[:2] == ["connected", "time:"]:
                counters.connected_seconds = int(words[2])
    return result
