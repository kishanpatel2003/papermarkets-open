"""Ingest all Polymarket market metadata from the Gamma API into Parquet."""

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

from papermarkets.ingest.client import get_public_client
from papermarkets.ingest.config import GAMMA_BASE_URL, OUTPUT_DIR
from papermarkets.ingest.pagination import paginate_gamma
from papermarkets.ingest.writer import MARKETS_SCHEMA, ParquetWriter

logger = logging.getLogger(__name__)

# Only ingest markets created on or after this date (when Dome started recording orderbooks)
_DOME_EPOCH = datetime(2025, 10, 14, tzinfo=UTC)


def _parse_iso(value: str | None) -> datetime | None:
    """Parse an ISO 8601 timestamp string, returning None on failure."""
    if not value:
        return None
    try:
        # Handle various Gamma formats: with/without timezone, with/without fractional seconds
        cleaned = value.replace("+00", "+00:00").replace("Z", "+00:00")
        if "+" not in cleaned and "T" in cleaned:
            cleaned += "+00:00"
        return datetime.fromisoformat(cleaned)
    except (ValueError, TypeError):
        return None


def _parse_json_list(value: str | None) -> list:
    """Parse a JSON-encoded list string, returning empty list on failure."""
    if not value:
        return []
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


def _safe_float(value, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


def _normalize_market(raw: dict) -> dict:
    """Transform a raw Gamma market dict into our target schema."""
    clob_ids = _parse_json_list(raw.get("clobTokenIds"))
    events = raw.get("events") or []
    event_slug = events[0].get("slug") if events else None

    # Tags: could be a JSON string, a list, or None
    tags_raw = raw.get("tags")
    if isinstance(tags_raw, str):
        tags = tags_raw if tags_raw.startswith("[") else json.dumps([tags_raw])
    elif isinstance(tags_raw, list):
        tags = json.dumps(tags_raw)
    else:
        tags = "[]"

    resolved_at = None
    if raw.get("closed"):
        resolved_at = _parse_iso(raw.get("updatedAt"))

    return {
        "market_id": raw.get("conditionId", ""),
        "question": raw.get("question", ""),
        "description": raw.get("description", ""),
        "slug": raw.get("slug", ""),
        "category": raw.get("category", ""),
        "tags": tags,
        "outcome_yes_token_id": clob_ids[0] if len(clob_ids) > 0 else "",
        "outcome_no_token_id": clob_ids[1] if len(clob_ids) > 1 else "",
        "neg_risk": bool(raw.get("negRisk")),
        "volume_total": _safe_float(raw.get("volumeNum")),
        "liquidity": _safe_float(raw.get("liquidityNum")),
        "created_at": _parse_iso(raw.get("createdAt")),
        "end_date": _parse_iso(raw.get("endDate")),
        "resolved_at": resolved_at,
        "resolution_outcome": raw.get("outcome"),
        "active": bool(raw.get("active")),
        "closed": bool(raw.get("closed")),
        "event_slug": event_slug,
        "spread": _safe_float(raw.get("spread")),
        "best_bid": _safe_float(raw.get("bestBid")),
        "best_ask": _safe_float(raw.get("bestAsk")),
    }


def _build_market_index(markets: list[dict]) -> dict:
    """Build a slug -> summary dict sorted by volume descending."""
    entries = []
    for m in markets:
        entries.append(
            (
                m["slug"],
                {
                    "condition_id": m["market_id"],
                    "yes_token_id": m["outcome_yes_token_id"],
                    "no_token_id": m["outcome_no_token_id"],
                    "volume": m["volume_total"],
                    "active": m["active"],
                    "closed": m["closed"],
                },
            )
        )
    entries.sort(key=lambda x: x[1]["volume"], reverse=True)
    return dict(entries)


def ingest_markets() -> Path:
    """Fetch all markets from Gamma and write markets.parquet + market_index.json."""
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )

    parquet_path = OUTPUT_DIR / "markets.parquet"
    index_path = OUTPUT_DIR / "market_index.json"

    writer = ParquetWriter(parquet_path, MARKETS_SCHEMA)
    all_normalized: list[dict] = []
    total_fetched = 0

    total_skipped = 0
    try:
        # Use _select to fetch only the fields we need — cuts response size dramatically
        fields = ",".join(
            [
                "conditionId",
                "question",
                "description",
                "slug",
                "category",
                "tags",
                "clobTokenIds",
                "negRisk",
                "volumeNum",
                "liquidityNum",
                "createdAt",
                "endDate",
                "updatedAt",
                "outcome",
                "active",
                "closed",
                "events",
                "spread",
                "bestBid",
                "bestAsk",
            ]
        )
        url = f"{GAMMA_BASE_URL}/markets?_select={fields}"
        for page in paginate_gamma(get_public_client(), url, limit=100):
            batch = [_normalize_market(m) for m in page]
            # Filter to markets created on or after the Dome epoch
            filtered = [
                m for m in batch if m["created_at"] is not None and m["created_at"] >= _DOME_EPOCH
            ]
            total_skipped += len(batch) - len(filtered)
            writer.write_batch(filtered)
            all_normalized.extend(filtered)
            total_fetched += len(page)
            if total_fetched % 1000 < 100:
                logger.info(
                    "Fetched %d markets, %d included, %d pre-Oct-14 skipped",
                    total_fetched,
                    len(all_normalized),
                    total_skipped,
                )
    finally:
        writer.close()

    logger.info(
        "Total fetched: %d, included (post-Oct-14): %d, skipped: %d",
        total_fetched,
        len(all_normalized),
        total_skipped,
    )

    # Write market index
    index = _build_market_index(all_normalized)
    index_path.write_text(json.dumps(index, indent=2))
    logger.info("Market index written to %s (%d entries)", index_path, len(index))

    return parquet_path


def main() -> None:
    path = ingest_markets()
    size_mb = path.stat().st_size / (1024 * 1024)
    print(f"Done. Output: {path} ({size_mb:.1f} MB)")


if __name__ == "__main__":
    main()
