"""Constants for the OpenWrt Router integration."""

from homeassistant.const import Platform

DOMAIN = "openwrt_manager"
DEFAULT_NAME = "OpenWrt Router"

# Configuration keys
CONF_FIREWALL_ACTION = "firewall_action"
CONF_FIREWALL_RULE_NAME = "firewall_rule_name"
CONF_HOST_KEY = "host_key"

DEFAULT_PORT_SSH = 22
DEFAULT_SCAN_INTERVAL = 15
DEFAULT_FIREWALL_ACTION = "REJECT"
DEFAULT_FIREWALL_RULE_NAME = "HA_BLOCK_INTERNET"

# Firewall actions
ACTION_REJECT = "REJECT"
ACTION_DROP = "DROP"
FIREWALL_ACTIONS = [ACTION_REJECT, ACTION_DROP]

# Supported platforms
PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.DEVICE_TRACKER,
    Platform.SWITCH,
    Platform.SENSOR,
]
