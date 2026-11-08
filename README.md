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

## Additional access point telemetry

In the integration's **Configure** menu, choose **Add UniFi source** or
**Add OpenWrt AP**. Repeat for every controller/site or access point. Multiple
sources of the same type are supported, each with independent credentials,
connections and diagnostic status. **Manage AP sources** lets you edit,
disable, enable or remove a source. Router options preserve the source list.

- **UniFi:** enter the controller hostname, HTTPS port (normally 443 for UniFi
  OS, 8443 for standalone Network Application), local account credentials and
  select a site. TLS certificate verification is enabled by default; turn it off
  explicitly only if your controller uses an untrusted certificate. This uses
  `aiounifi`, not Home Assistant's UniFi entities. Local accounts without MFA are
  required for this authentication mode. Hosting in Proxmox/LXC needs no special
  integration, provided HA can reach the controller.
- **OpenWrt AP:** enter SSH credentials for each additional AP. The AP needs
  `ubus` with `iwinfo` support (`rpcd-mod-iwinfo`) for radio/association data and
  `iw` for byte counters. The SSH host key is pinned when saving configuration.
  A changed host key requires removing and adding that source again.

Only clients discovered by the main router are enriched. They retain their
existing device and entity IDs, and internet access switches still exclusively
modify the **main OpenWrt router's firewall**. AP sources never modify firewall
or UniFi configuration.

Client sensors expose the current AP using the configured source name (the
integration entry title for the main router's Wi-Fi). Stable AP ID, AP name
reported by the controller and MAC remain available as attributes, alongside
SSID, band, channel, RSSI, receive/transmit link rates, measured download/upload
rates and byte counters when available. All directions use the **client's
perspective**. Traffic includes local LAN traffic; it is not WAN-only usage.
Byte-counter entities are disabled by default and can be enabled per client.
Counters describe the current source/association, not a lifetime total across
APs. A first sample, roaming, expired association or counter reset produces no
throughput value until a comparable second sample exists.

UniFi uses websocket updates plus periodic reconciliation. OpenWrt APs are
polled at the integration scan interval (default 15 seconds). AP history uses
Home Assistant Recorder's normal retention and exclusions. Source failures are
isolated and retried with backoff; observations expire after two scan intervals.
Loss of AP telemetry clears current AP metrics rather than labelling a known
wireless client as Ethernet. Router observations continue to determine presence
when no current wireless association is available.

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

Framework contract tests can also run without adding test dependencies to the
Home Assistant image:

```bash
docker run --rm --network none --entrypoint python \
  -e PYTHONPATH=/workspace -v "$PWD:/workspace:ro" -w /workspace \
  ghcr.io/home-assistant/home-assistant:stable tests/homeassistant_contracts.py
```

### 3. Running Home Assistant via Docker Dev Environment

```bash
docker compose up -d
```

Access Home Assistant at `http://localhost:8123`.
