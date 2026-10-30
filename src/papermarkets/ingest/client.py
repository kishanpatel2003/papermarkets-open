"""Rate-limited HTTP client with retry and error classification."""

import contextlib
import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from papermarkets.ingest.exceptions import RateLimitExceeded, SkipMarket

logger = logging.getLogger(__name__)

_DEFAULT_CONCURRENCY = 50


class HttpClient:
    """HTTP client with token-bucket rate limiting and exponential backoff retry."""

    def __init__(
        self,
        requests_per_second: float = 10,
        max_retries: int = 3,
        timeout: int = 30,
        default_headers: dict[str, str] | None = None,
        max_workers: int = _DEFAULT_CONCURRENCY,
    ):
        self.min_interval = 1.0 / requests_per_second
        self.max_retries = max_retries
        self.timeout = timeout
        self.default_headers = default_headers or {}
        self.max_workers = max_workers
        self._last_request_time = 0.0
        self._rate_lock = threading.Lock()

    def _wait_for_rate_limit(self) -> None:
        with self._rate_lock:
            elapsed = time.monotonic() - self._last_request_time
            if elapsed < self.min_interval:
                time.sleep(self.min_interval - elapsed)
            self._last_request_time = time.monotonic()

    def get(self, url: str, headers: dict[str, str] | None = None) -> dict:
        merged = {**self.default_headers, **(headers or {})}
        merged.setdefault("User-Agent", "PaperMarkets/0.1")

        last_exc: Exception | None = None
        for attempt in range(self.max_retries + 1):
            self._wait_for_rate_limit()
            try:
                req = Request(url, headers=merged)
                with urlopen(req, timeout=self.timeout) as resp:
                    return json.loads(resp.read().decode())
            except HTTPError as e:
                body = ""
                with contextlib.suppress(Exception):
                    body = e.read().decode()[:500]
                if e.code == 404:
                    logger.warning("404 for %s — skipping", url)
                    raise SkipMarket(f"404: {url}") from e
                if e.code == 400:
                    logger.warning("400 bad request for %s: %s — skipping", url, body)
                    raise SkipMarket(f"400: {url}: {body}") from e
                if e.code == 429:
                    backoff = min(60, (2**attempt))
                    logger.warning("429 rate limited on %s, backing off %ds", url, backoff)
                    time.sleep(backoff)
                    last_exc = e
                    continue
                if e.code >= 500:
                    backoff = min(60, (2**attempt))
                    logger.warning("%d server error on %s, retry in %ds", e.code, url, backoff)
                    time.sleep(backoff)
                    last_exc = e
                    continue
                logger.error("HTTP %d on %s: %s", e.code, url, body)
                raise
            except (URLError, TimeoutError, OSError) as e:
                backoff = min(60, (2**attempt))
                logger.warning("Network error on %s: %s, retry in %ds", url, e, backoff)
                time.sleep(backoff)
                last_exc = e
                continue

        if isinstance(last_exc, HTTPError) and last_exc.code == 429:
            raise RateLimitExceeded(f"429 after {self.max_retries} retries: {url}") from last_exc
        raise ConnectionError(f"Failed after {self.max_retries} retries: {url}") from last_exc

    def get_many(self, urls: list[str]) -> list[dict]:
        """Fetch multiple URLs concurrently. Returns results in the same order as input.

        Exceptions from individual requests propagate — if any URL fails with a
        non-retryable error, the entire call raises.
        """
        if not urls:
            return []
        if len(urls) == 1:
            return [self.get(urls[0])]

        results: list[dict | None] = [None] * len(urls)
        with ThreadPoolExecutor(max_workers=min(self.max_workers, len(urls))) as pool:
            future_to_idx = {pool.submit(self.get, url): i for i, url in enumerate(urls)}
            for future in as_completed(future_to_idx):
                idx = future_to_idx[future]
                results[idx] = future.result()  # propagates exceptions
        return results  # type: ignore[return-value]


_dome_client: HttpClient | None = None
_public_client: HttpClient | None = None


def get_dome_client() -> HttpClient:
    """Lazy singleton for the Dome API client. Created on first call."""
    global _dome_client
    if _dome_client is None:
        from papermarkets.ingest.config import DOME_API_KEY

        _dome_client = HttpClient(
            requests_per_second=200,
            default_headers={"X-Api-Key": DOME_API_KEY},
        )
    return _dome_client


def get_public_client() -> HttpClient:
    """Lazy singleton for the public (unauthenticated) client. Created on first call."""
    global _public_client
    if _public_client is None:
        _public_client = HttpClient(requests_per_second=50)
    return _public_client
