"""UniFi controller telemetry using the same library as Home Assistant UniFi."""

import asyncio
import contextlib
import logging
import ssl
import time
from collections.abc import Callable
from typing import Any, Literal

from aiohttp import ClientError, ClientSession
from aiounifi.controller import Controller
from aiounifi.errors import AiounifiException
from aiounifi.interfaces.api_handlers import ItemEvent
from aiounifi.models.client import ClientListRequest
from aiounifi.models.configuration import Configuration

from ..models import AccessPointInfo, ClientObservation
from ..openwrt_client.openwrt_manager import normalize_mac

_LOGGER = logging.getLogger(__name__)


class UniFiSource:
    """Own an isolated controller session and publish read-only association data."""

    def __init__(
        self,
        source_id: str,
        config: dict[str, Any],
        session: ClientSession,
        ssl_context: ssl.SSLContext | Literal[False] | None = None,
    ) -> None:
        self.source_id = source_id
        self.name = config["name"]
        self.api = Controller(
            Configuration(
                session,
                config["host"],
                username=config["username"],
                password=config["password"],
                port=config.get("port", 443),
                site=config.get("site", "default"),
                ssl_context=ssl_context
                if ssl_context is not None
                else (
                    ssl.create_default_context()
                    if config.get("verify_ssl", True)
                    else False
                ),
            )
        )
        self._session = session
        self._logged_in = False
        self._observations: dict[str, ClientObservation] = {}
        self._updated: Callable[[dict[str, ClientObservation]], None] | None = None
        self._websocket_task: asyncio.Task[None] | None = None
        self._unsubscribe: Callable[[], None] | None = None
        self._refreshing = False
        self._refresh_lock = asyncio.Lock()

    async def async_sites(self) -> dict[str, str]:
        """Validate credentials and list selectable sites."""
        await self.api.login()
        self._logged_in = True
        await self.api.sites.update()
        return {
            site.name: site.description or site.name for site in self.api.sites.values()
        }

    def start(self, updated: Callable[[dict[str, ClientObservation]], None]) -> None:
        """Subscribe once and start a reconnecting websocket task."""
        self._updated = updated
        self._unsubscribe = self.api.clients.subscribe(self._client_updated)
        self._websocket_task = asyncio.create_task(self._run_websocket())

    def _client_updated(self, event: ItemEvent, mac: str) -> None:
        if self._refreshing:
            return
        normalized = normalize_mac(mac)
        if event == ItemEvent.DELETED:
            self._observations.pop(normalized, None)
        elif (client := self.api.clients.get(mac)) is not None:
            observation = self._observation(dict(client.raw), time.time())
            if observation is not None:
                self._observations[normalized] = observation
            else:
                self._observations.pop(normalized, None)
        if self._updated:
            self._updated(dict(self._observations))

    async def _run_websocket(self) -> None:
        delay = 5
        while True:
            try:
                if not self._logged_in:
                    observations = await self.async_refresh()
                    if self._updated:
                        self._updated(observations)
                await self.api.start_websocket()
            except (AiounifiException, ClientError, TimeoutError):
                _LOGGER.debug(
                    "UniFi websocket interrupted for source %s", self.source_id
                )
            self._logged_in = False
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60)

    async def async_refresh(self) -> dict[str, ClientObservation]:
        """Replace active clients rather than trusting the library's retained cache."""
        async with self._refresh_lock:
            return await self._async_refresh()

    async def _async_refresh(self) -> dict[str, ClientObservation]:
        """Serialize login and cache reconciliation with websocket recovery."""
        self._refreshing = True
        try:
            if not self._logged_in:
                await self.api.login()
                self._logged_in = True
            await self.api.devices.update()
            response = await self.api.request(ClientListRequest.create())
            now = time.time()
            observations = {}
            for raw in response.get("data", []):
                observation = self._observation(raw, now)
                if observation is not None:
                    observations[observation.mac] = observation
            self.api.clients.process_raw(response.get("data", []))
            self._observations = observations
            return dict(observations)
        except (AiounifiException, ClientError, TimeoutError):
            self._logged_in = False
            raise
        finally:
            self._refreshing = False

    def _observation(self, raw: dict[str, Any], now: float) -> ClientObservation | None:
        if raw.get("is_wired") or not raw.get("ap_mac") or not raw.get("mac"):
            return None
        mac = normalize_mac(raw["mac"])
        ap_mac = normalize_mac(raw["ap_mac"])
        device = next(
            (
                device
                for device in self.api.devices.values()
                if normalize_mac(device.mac) == ap_mac
            ),
            None,
        )
        name = device.name if device is not None else ap_mac
        radio = raw.get("radio", "")
        band = {"ng": "2.4 GHz", "na": "5 GHz", "6e": "6 GHz"}.get(radio)
        return ClientObservation(
            mac=mac,
            source_id=self.source_id,
            observed_at=now,
            access_point=AccessPointInfo(f"{self.source_id}:{ap_mac}", ap_mac, name),
            association_time=raw.get("assoc_time"),
            connected_seconds=raw.get("uptime"),
            ip=raw.get("ip"),
            hostname=raw.get("hostname"),
            ssid=raw.get("essid"),
            band=band,
            channel=raw.get("channel"),
            signal_dbm=raw.get("signal"),
            rx_rate=raw["tx_rate"] / 1000 if raw.get("tx_rate") is not None else None,
            tx_rate=raw["rx_rate"] / 1000 if raw.get("rx_rate") is not None else None,
            rx_bytes=raw.get("tx_bytes"),
            tx_bytes=raw.get("rx_bytes"),
        )

    async def close(self) -> None:
        """Stop callbacks before closing the independently owned HTTP session."""
        if self._unsubscribe:
            self._unsubscribe()
            self._unsubscribe = None
        if self._websocket_task:
            self._websocket_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._websocket_task
            self._websocket_task = None
        await self._session.close()
