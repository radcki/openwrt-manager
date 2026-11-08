"""Merge wireless observations without changing router authority."""

from dataclasses import replace

from .models import ClientDevice, ClientObservation


class ClientTrafficCalculator:
    """Calculate bytes per second only between comparable association samples."""

    def __init__(self) -> None:
        self._previous: dict[str, ClientObservation] = {}
        self._rates: dict[str, tuple[float | None, float | None]] = {}

    def reset(self, mac: str) -> None:
        """Discard the baseline when association evidence expires."""
        self._previous.pop(mac, None)
        self._rates.pop(mac, None)

    def calculate(self, sample: ClientObservation) -> tuple[float | None, float | None]:
        """Return download/upload throughput; repeated snapshots keep their rate."""
        previous = self._previous.get(sample.mac)
        if previous == sample:
            return self._rates.get(sample.mac, (None, None))
        rates: tuple[float | None, float | None] = (None, None)
        if (
            previous is not None
            and previous.source_id == sample.source_id
            and previous.access_point.identifier == sample.access_point.identifier
            and previous.association_time == sample.association_time
            and (
                sample.connected_seconds is None
                or previous.connected_seconds is None
                or sample.connected_seconds >= previous.connected_seconds
            )
            and sample.observed_at - previous.observed_at > 0
        ):
            elapsed = sample.observed_at - previous.observed_at
            rates = (
                self._rate(sample.rx_bytes, previous.rx_bytes, elapsed),
                self._rate(sample.tx_bytes, previous.tx_bytes, elapsed),
            )
        self._previous[sample.mac] = sample
        self._rates[sample.mac] = rates
        return rates

    @staticmethod
    def _rate(
        current: int | None, previous: int | None, elapsed: float
    ) -> float | None:
        if current is None or previous is None or current < previous:
            return None
        return (current - previous) / elapsed


class ClientDataMerger:
    """Enrich known router clients using fresh observations and stable tie ordering."""

    def __init__(self) -> None:
        self.traffic = ClientTrafficCalculator()
        self._wireless_macs: set[str] = set()

    def merge(
        self,
        router_clients: dict[str, ClientDevice],
        snapshots: list[dict[str, ClientObservation]],
        now: float,
        maximum_age: float,
    ) -> dict[str, ClientDevice]:
        """Preserve router fields; a source cannot introduce or block clients."""
        result = {mac: replace(client) for mac, client in router_clients.items()}
        for mac, client in result.items():
            observations = [
                snapshot[mac]
                for snapshot in snapshots
                if mac in snapshot and now - snapshot[mac].observed_at <= maximum_age
            ]
            connected = [
                observation for observation in observations if observation.connected
            ]
            if not connected:
                self.traffic.reset(mac)
                if mac in self._wireless_macs:
                    client.is_wireless = True
                continue
            observation = max(connected, key=lambda item: item.observed_at)
            self._wireless_macs.add(mac)
            client.is_wireless = True
            client.is_online = True
            client.ip = client.ip or observation.ip
            client.hostname = client.hostname or observation.hostname
            client.access_point = observation.access_point
            client.source_id = observation.source_id
            for attribute in (
                "ssid",
                "band",
                "channel",
                "signal_dbm",
                "rx_rate",
                "tx_rate",
                "rx_bytes",
                "tx_bytes",
            ):
                setattr(client, attribute, getattr(observation, attribute))
            client.download_rate, client.upload_rate = self.traffic.calculate(
                observation
            )
        return result
