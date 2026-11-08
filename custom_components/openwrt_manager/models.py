"""Framework-independent client and access point telemetry contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class AccessPointInfo:
    """An access point identified independently of its display name."""

    identifier: str
    mac: str
    name: str


@dataclass(frozen=True)
class ClientObservation:
    """A wireless association observed at a source; rates use the client perspective."""

    mac: str
    source_id: str
    observed_at: float
    access_point: AccessPointInfo
    connected: bool = True
    association_time: float | None = None
    connected_seconds: float | None = None
    ip: str | None = None
    hostname: str | None = None
    ssid: str | None = None
    band: str | None = None
    channel: int | None = None
    signal_dbm: int | None = None
    rx_rate: float | None = None
    tx_rate: float | None = None
    rx_bytes: int | None = None
    tx_bytes: int | None = None


@dataclass
class SourceStatus:
    """Operational state without credentials or raw remote error messages."""

    available: bool = False
    last_success: float | None = None
    error: str | None = None


@dataclass
class ClientDevice:
    """Merged router identity, firewall state and optional wireless telemetry."""

    mac: str
    ip: str | None = None
    alias: str | None = None
    hostname: str | None = None
    is_online: bool = False
    is_wireless: bool = False
    interface: str | None = None
    signal_dbm: int | None = None
    rx_rate: float | None = None
    tx_rate: float | None = None
    internet_blocked: bool = False
    firewall_rule_id: str | None = None
    extra_attributes: dict[str, Any] = field(default_factory=dict)
    access_point: AccessPointInfo | None = None
    source_id: str | None = None
    ssid: str | None = None
    band: str | None = None
    channel: int | None = None
    rx_bytes: int | None = None
    tx_bytes: int | None = None
    download_rate: float | None = None
    upload_rate: float | None = None
