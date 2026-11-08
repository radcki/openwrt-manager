"""Shared Home Assistant device identity for every client entity platform."""

from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity import DeviceInfo

from .const import DOMAIN
from .models import ClientDevice


def client_device_info(
    client: ClientDevice | None, mac: str, entry_id: str
) -> DeviceInfo:
    """Keep one client identity while its radio association changes."""
    return DeviceInfo(
        identifiers={(DOMAIN, mac)},
        connections={(dr.CONNECTION_NETWORK_MAC, mac)},
        name=(client.alias or client.hostname if client else None) or f"Client {mac}",
        manufacturer="OpenWrt Client",
        model="Wireless Client" if client and client.is_wireless else "Wired Client",
        via_device=(DOMAIN, entry_id),
    )
