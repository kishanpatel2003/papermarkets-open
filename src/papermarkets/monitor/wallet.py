"""Wallet profile lookup via Dome API with in-memory caching."""

import logging
from datetime import UTC, datetime

from papermarkets.ingest.client import HttpClient, dome_client
from papermarkets.ingest.config import DOME_BASE_URL
from papermarkets.ingest.exceptions import SkipMarket
from papermarkets.monitor.state import WalletProfile

logger = logging.getLogger(__name__)


class WalletCache:
    """Fetch and cache wallet profiles from Dome's /polymarket/wallet endpoint."""

    def __init__(self, client: HttpClient | None = None):
        self._client = client or dome_client
        self._cache: dict[str, WalletProfile | None] = {}

    def get(self, address: str) -> WalletProfile | None:
        """Look up a wallet by proxy address. Returns None on 404."""
        if address in self._cache:
            return self._cache[address]

        url = f"{DOME_BASE_URL}/polymarket/wallet?proxy={address}"
        try:
            data = self._client.get(url)
        except SkipMarket:
            # 404 — wallet not found
            self._cache[address] = None
            return None

        metrics = data.get("wallet_metrics", {})
        profile = WalletProfile(
            eoa=data.get("eoa", ""),
            proxy=data.get("proxy", address),
            handle=data.get("handle", ""),
            pseudonym=data.get("pseudonym", ""),
            total_volume=metrics.get("total_volume", 0),
            total_trades=metrics.get("total_trades", 0),
            total_markets=metrics.get("total_markets", 0),
            fetched_at=datetime.now(tz=UTC).isoformat(),
        )
        self._cache[address] = profile
        return profile

    def preload(self, profiles: dict[str, WalletProfile]) -> None:
        """Seed cache from previously saved state."""
        for addr, profile in profiles.items():
            if addr not in self._cache:
                self._cache[addr] = profile

    @property
    def size(self) -> int:
        return len(self._cache)
