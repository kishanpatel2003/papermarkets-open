"""Custom exceptions for the ingestion pipeline."""


class SkipMarket(Exception):
    """Raised on 404 — caught by ingesters to skip the current market."""


class RateLimitExceeded(Exception):
    """Raised on 429 when retries are exhausted."""
