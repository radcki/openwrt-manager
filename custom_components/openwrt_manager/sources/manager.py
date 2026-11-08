"""Coordinate independent read-only sources without coupling them to HA."""

import asyncio
import logging
import time
from collections.abc import Callable
from functools import partial

from ..models import ClientObservation, SourceStatus
from . import AccessPointSource

_LOGGER = logging.getLogger(__name__)


class AccessPointSourceManager:
    """Keep source snapshots isolated and bound retries and read durations."""

    def __init__(self, sources: dict[str, AccessPointSource], interval: float) -> None:
        self.sources = sources
        self.interval = interval
        self.snapshots: dict[str, dict[str, ClientObservation]] = {
            key: {} for key in sources
        }
        self.statuses = {key: SourceStatus() for key in sources}
        self._failures = dict.fromkeys(sources, 0)
        self._retry_at = dict.fromkeys(sources, 0.0)
        self._updated: Callable[[], None] | None = None
        self._task: asyncio.Task[None] | None = None
        self._refresh_tasks: dict[str, asyncio.Task[None]] = {}

    def start(self, updated: Callable[[], None]) -> None:
        """Start each instance; identical source types do not share state."""
        self._updated = updated
        for source_id, source in self.sources.items():
            source.start(partial(self._publish, source_id))
        self._task = asyncio.create_task(self._poll())

    def _publish(
        self, source_id: str, observations: dict[str, ClientObservation]
    ) -> None:
        self.snapshots[source_id] = observations
        self.statuses[source_id] = SourceStatus(True, time.time())
        self._failures[source_id] = 0
        self._retry_at[source_id] = 0
        if self._updated:
            self._updated()

    async def _refresh(self, source_id: str, source: AccessPointSource) -> None:
        if time.monotonic() < self._retry_at[source_id]:
            return
        try:
            async with asyncio.timeout(30):
                observations = await source.async_refresh()
        except Exception as error:
            status = self.statuses[source_id]
            status.available = False
            status.error = type(error).__name__
            self._failures[source_id] += 1
            self._retry_at[source_id] = time.monotonic() + min(
                self.interval * 2 ** min(self._failures[source_id] - 1, 5), 300
            )
            _LOGGER.warning("AP source %s refresh failed (%s)", source_id, status.error)
            if self._updated:
                self._updated()
        else:
            self._publish(source_id, observations)

    async def async_refresh(self) -> None:
        """Refresh all configured instances concurrently, isolating failures."""
        await asyncio.gather(
            *(self._refresh(key, source) for key, source in self.sources.items())
        )

    async def _poll(self) -> None:
        while True:
            await asyncio.sleep(self.interval)
            for source_id, source in self.sources.items():
                task = self._refresh_tasks.get(source_id)
                if task is None or task.done():
                    self._refresh_tasks[source_id] = asyncio.create_task(
                        self._refresh(source_id, source)
                    )
            if self._updated:
                self._updated()

    async def close(self) -> None:
        """Stop polling and close every source even when one close fails."""
        self._updated = None
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        for task in self._refresh_tasks.values():
            task.cancel()
        await asyncio.gather(*self._refresh_tasks.values(), return_exceptions=True)
        results = await asyncio.gather(
            *(source.close() for source in self.sources.values()),
            return_exceptions=True,
        )
        for result in results:
            if isinstance(result, BaseException):
                _LOGGER.error("AP source shutdown failed (%s)", type(result).__name__)
