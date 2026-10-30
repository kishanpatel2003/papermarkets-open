"""Pagination helpers for each API's pagination style."""

from collections.abc import Iterator

from papermarkets.ingest.client import HttpClient


def paginate_dome(
    client: HttpClient,
    url: str,
    key_field: str = "pagination_key",
    items_field: str | None = None,
) -> Iterator[list]:
    """Yield pages of items from a Dome API endpoint using cursor-based pagination.

    The Dome API returns a `pagination` dict with `has_more` and `pagination_key`.
    `items_field` selects which top-level key holds the items list
    (e.g. "snapshots" for orderbooks, "orders" for trades).
    """
    next_key: str | None = None
    while True:
        page_url = url
        if next_key:
            sep = "&" if "?" in page_url else "?"
            page_url = f"{page_url}{sep}{key_field}={next_key}"

        data = client.get(page_url)

        if items_field:
            items = data.get(items_field, [])
        else:
            # Try common Dome fields
            for field in ("snapshots", "orders", "markets", "events", "activities"):
                if field in data:
                    items = data[field]
                    break
            else:
                items = []

        if not items:
            return

        yield items

        pagination = data.get("pagination", {})
        if not pagination.get("has_more"):
            return
        next_key = pagination.get(key_field)
        if not next_key:
            return


def paginate_gamma(
    client: HttpClient,
    base_url: str,
    limit: int = 100,
) -> Iterator[list]:
    """Yield pages of items from the Gamma API using offset-based pagination."""
    offset = 0
    while True:
        sep = "&" if "?" in base_url else "?"
        url = f"{base_url}{sep}limit={limit}&offset={offset}"
        data = client.get(url)

        # Gamma returns a plain JSON array of markets/events
        if not isinstance(data, list) or len(data) == 0:
            return

        yield data
        if len(data) < limit:
            return
        offset += limit
