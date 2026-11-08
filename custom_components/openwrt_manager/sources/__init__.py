"""Read-only access point sources, each instantiated per configuration ID."""

from collections.abc import Callable
from typing import Protocol

from ..models import ClientObservation


class AccessPointSource(Protocol):
    """Transport contract; callbacks publish the source's current snapshot."""

    async def async_refresh(self) -> dict[str, ClientObservation]:
        """Fetch an authoritative wireless snapshot."""
        ...

    def start(self, updated: Callable[[dict[str, ClientObservation]], None]) -> None:
        """Register push updates after the initial refresh."""
        ...

    async def close(self) -> None:
        """Cancel subscriptions and close owned transports."""
        ...
