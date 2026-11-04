"""Unit tests for OpenWrt config flow."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.openwrt_router.config_flow import OpenWrtConfigFlow
from custom_components.openwrt_router.openwrt_client.OpenWrtManager import (
    CannotConnect,
    EndpointNotUbus,
    InvalidAuth,
    RouterInfo,
)


@pytest.mark.asyncio
async def test_config_flow_ubus_html_returns_ubus_not_found() -> None:
    """Test that OpenWrt returning HTML triggers ubus_not_found error in flow."""
    flow = OpenWrtConfigFlow()
    flow.hass = AsyncMock()

    user_input = {
        "host": "192.168.1.1",
        "protocol": "ubus_http",
        "port": 80,
        "username": "root",
        "password": "wrong_password",
    }

    with patch(
        "custom_components.openwrt_router.config_flow.OpenWrtClient.async_login",
        side_effect=EndpointNotUbus("HTML returned"),
    ):
        result = await flow.async_step_user(user_input)
        assert result["type"] == "form"
        assert result["errors"] == {"base": "ubus_not_found"}


@pytest.mark.asyncio
async def test_config_flow_invalid_auth() -> None:
    """Test that invalid credentials triggers invalid_auth error in flow."""
    flow = OpenWrtConfigFlow()
    flow.hass = AsyncMock()

    user_input = {
        "host": "192.168.1.1",
        "protocol": "ssh",
        "port": 22,
        "username": "root",
        "password": "badpassword",
    }

    with patch(
        "custom_components.openwrt_router.config_flow.OpenWrtClient.async_login",
        side_effect=InvalidAuth("Invalid credentials"),
    ):
        result = await flow.async_step_user(user_input)
        assert result["type"] == "form"
        assert result["errors"] == {"base": "invalid_auth"}


@pytest.mark.asyncio
async def test_config_flow_cannot_connect() -> None:
    """Test that connection error triggers cannot_connect error in flow."""
    flow = OpenWrtConfigFlow()
    flow.hass = AsyncMock()

    user_input = {
        "host": "192.168.1.99",
        "protocol": "ssh",
        "username": "root",
        "password": "password",
    }

    with patch(
        "custom_components.openwrt_router.config_flow.OpenWrtClient.async_login",
        side_effect=CannotConnect("Timeout"),
    ):
        result = await flow.async_step_user(user_input)
        assert result["type"] == "form"
        assert result["errors"] == {"base": "cannot_connect"}


@pytest.mark.asyncio
async def test_config_flow_ssh_success_auto_corrects_port_80() -> None:
    """Test that SSH flow auto-corrects port 80 to 22 and successfully creates entry."""
    flow = OpenWrtConfigFlow()
    flow.hass = AsyncMock()
    flow.async_set_unique_id = AsyncMock()
    flow._abort_if_unique_id_configured = MagicMock()

    user_input = {
        "host": "192.168.1.1",
        "protocol": "ssh",
        "port": 80,  # User left port 80 when changing dropdown to SSH
        "username": "root",
        "password": "validpassword",
    }

    mock_info = RouterInfo(
        hostname="My-OpenWrt-Router",
        model="GL-AXT1800",
        release="23.05.3",
        mac="00:11:22:33:44:55",
    )

    with (
        patch(
            "custom_components.openwrt_router.config_flow.OpenWrtClient.async_login",
            AsyncMock(return_value="ssh_authenticated"),
        ),
        patch(
            "custom_components.openwrt_router.config_flow.OpenWrtClient.async_get_router_info",
            AsyncMock(return_value=mock_info),
        ),
    ):
        result = await flow.async_step_user(user_input)
        assert result["type"] == "create_entry"
        assert result["title"] == "My-OpenWrt-Router"
        # Confirm port was auto-corrected to 22 in saved config data!
        assert result["data"]["port"] == 22
