"""Construct an isolated transport for each persisted source configuration."""

import asyncio
import ssl
from typing import Any, Literal

from aiohttp import ClientSession, ClientTimeout, CookieJar

from ..openwrt_client.openwrt_client import OpenWrtClient
from . import AccessPointSource
from .openwrt import OpenWrtAccessPointSource
from .unifi import UniFiSource


async def create_source(config: dict[str, Any]) -> AccessPointSource:
    """Create one source; UniFi sessions never share controller login cookies."""
    if config["type"] == "unifi":
        ssl_context: ssl.SSLContext | Literal[False] = (
            await asyncio.to_thread(ssl.create_default_context)
            if config.get("verify_ssl", True)
            else False
        )
        return UniFiSource(
            config["id"],
            config,
            ClientSession(
                cookie_jar=CookieJar(unsafe=True), timeout=ClientTimeout(total=25)
            ),
            ssl_context=ssl_context,
        )
    if config["type"] == "openwrt":
        return OpenWrtAccessPointSource(
            config["id"],
            config["name"],
            OpenWrtClient(
                host=config["host"],
                port=config.get("port", 22),
                username=config.get("username", "root"),
                password=config["password"],
                host_key=config.get("host_key"),
            ),
        )
    raise ValueError(f"Unknown AP source type: {config['type']}")
