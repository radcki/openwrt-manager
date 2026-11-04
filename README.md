# Home Assistant OpenWrt Router Integration

[![HACS Badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/default)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

A custom Home Assistant integration that connects to an **OpenWrt router** via SSH, automatically discovers connected clients, exposes them as distinct Home Assistant devices with online/offline connectivity tracking, and provides parental/firewall control switches to block or allow internet access per device by managing OpenWrt firewall traffic rule.

Tested on GL-iNet Flint 2

---

## "Why?"

I wanted to easily block and unblock individual IoT devices from internet access.

---

## Features

- **Automatic Client Discovery**: Scans DHCP leases, ARP table, and wireless interfaces (`hostapd`) to discover all connected and known devices.
- **Dedicated Device per Client**: Each network client is registered as its own device in Home Assistant (linked via the OpenWrt router).
- **Online / Connectivity Status**: Real-time `binary_sensor` and `device_tracker` entities indicating whether each client is active on the network.
- **Firewall Internet Access Control**: A dedicated `switch` entity for each device that allows blocking or restoring internet access:
  - Dynamically creates/modifies OpenWrt UCI firewall traffic rule (`/etc/config/firewall`). Rule is enabled if there is at least one blocked MAC.
  - Blocks WAN egress for the specific MAC while keeping local LAN access operational.
  - Automatically reloads the firewall service (`fw4` on nftables or `fw3` on iptables).
- **Telemetry**: Provides diagnostic sensors for IP address, MAC address, connection type (Ethernet, 2.4 GHz, 5 GHz), WiFi RSSI signal strength, and transfer rates.

---

## Prerequisites on OpenWrt

1. Enable SSH with password authentication (default port 22). The user must be allowed to run `uci`, `ubus` and `/etc/init.d/firewall reload` (normally `root`).
2. Install via HACS (custom repository, category _Integration_), restart Home Assistant, then add **OpenWrt Manager** under _Settings → Devices & services_.

> **Security:** the router's SSH host key is trusted on first connection and pinned in the config entry. If the key later changes (e.g. after a firmware reset), the integration refuses to connect; remove and re-add the integration to pin the new key.

> **Notice!** Firewall rule will stay on router after you stop using integration - **you will need to remove it manually**.

---

## Development & Local Testing

### 1. Environment Setup with `uv`

```bash
uv sync --all-extras
```

### 2. Linting, Formatting & Tests

```bash
# Run ruff check
uv run ruff check .

# Run ruff format check
uv run ruff format --check .

# Run type checks
uv run mypy custom_components/openwrt_manager

# Run tests
uv run pytest
```

### 3. Running Home Assistant via Docker Dev Environment

```bash
docker compose up -d
```

Access Home Assistant at `http://localhost:8123`.

---

## Architecture Overview

For each router's client device following entities are created:

- Device Tracker - Can be assigned to person to indicate presence
- Binary Sensor - Connectivity (Is online?)
- Switch - Internet Access (Is not blocked by manager firewall rule?)
- Sensor - IP Address
- Sensor - Signal Strenght
