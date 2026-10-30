"""Ingest historical order book snapshots from the Dome API into Parquet."""

import hashlib
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path

from papermarkets.ingest.client import get_dome_client
from papermarkets.ingest.config import DOME_BASE_URL, OUTPUT_DIR
from papermarkets.ingest.exceptions import RateLimitExceeded, SkipMarket
from papermarkets.ingest.pagination import paginate_dome
from papermarkets.ingest.progress import ProgressTracker
from papermarkets.ingest.writer import ORDERBOOK_SCHEMA, ParquetWriter, safe_filename

logger = logging.getLogger(__name__)

# Dome orderbook data starts Oct 14, 2025 00:00 UTC
_START_TIME_MS = 1760400000000

_DEFAULT_WORKERS = 20


def _load_market_index(path: Path | None = None) -> dict:
    index_path = path or (OUTPUT_DIR / "market_index.json")
    return json.loads(index_path.read_text())


def _normalize_snapshot(snapshot: dict, market_id: str, token_id: str) -> dict:
    bids = snapshot.get("bids", [])
    asks = snapshot.get("asks", [])

    best_bid = max((float(b["price"]) for b in bids), default=0.0)
    best_ask = min((float(a["price"]) for a in asks), default=1.0)
    spread = best_ask - best_bid
    mid_price = (best_bid + best_ask) / 2

    bids_json = json.dumps(bids)
    asks_json = json.dumps(asks)
    book_hash = hashlib.md5((bids_json + asks_json).encode()).hexdigest()[:16]

    # Dome orderbook timestamps are in milliseconds
    ts_ms = snapshot["timestamp"]
    timestamp = datetime.fromtimestamp(ts_ms / 1000, tz=UTC)

    return {
        "market_id": market_id,
        "token_id": token_id,
        "timestamp": timestamp,
        "bids": bids_json,
        "asks": asks_json,
        "best_bid": best_bid,
        "best_ask": best_ask,
        "spread": spread,
        "mid_price": mid_price,
        "bid_depth": len(bids),
        "ask_depth": len(asks),
        "total_bid_size": sum(float(b["size"]) for b in bids),
        "total_ask_size": sum(float(a["size"]) for a in asks),
        "book_hash": book_hash,
    }


_CHUNK_MS = 30 * 24 * 60 * 60 * 1000  # 30 days in ms


def _ingest_token_orderbooks(
    token_id: str, market_id: str, start_time_ms: int, end_time_ms: int
) -> list[dict]:
    """Fetch all orderbook snapshots for a token, chunking by 30-day windows."""
    if not token_id:
        return []
    records = []
    window_start = max(start_time_ms, _START_TIME_MS)
    while window_start < end_time_ms:
        window_end = min(window_start + _CHUNK_MS, end_time_ms)
        url = (
            f"{DOME_BASE_URL}/polymarket/orderbooks"
            f"?token_id={token_id}"
            f"&start_time={window_start}"
            f"&end_time={window_end}"
            f"&limit=200"
        )
        for page in paginate_dome(get_dome_client(), url, items_field="snapshots"):
            for snapshot in page:
                records.append(_normalize_snapshot(snapshot, market_id, token_id))
        window_start = window_end
    return records


def _process_one_market(
    slug: str, info: dict, progress: ProgressTracker, end_time_ms: int
) -> None:
    """Ingest orderbooks for a single market."""
    if progress.is_completed(slug):
        return

    progress.mark_started(slug)
    writer = ParquetWriter(
        OUTPUT_DIR / "orderbook_snapshots" / f"{safe_filename(slug)}.parquet", ORDERBOOK_SCHEMA
    )
    try:
        cid = info["condition_id"]
        start_ms = info.get("created_at", _START_TIME_MS // 1000) * 1000
        with ThreadPoolExecutor(max_workers=2) as pool:
            yes_future = pool.submit(
                _ingest_token_orderbooks, info["yes_token_id"], cid, start_ms, end_time_ms
            )
            no_future = pool.submit(
                _ingest_token_orderbooks, info["no_token_id"], cid, start_ms, end_time_ms
            )
            yes_records = yes_future.result()
            no_records = no_future.result()
        writer.write_batch(yes_records + no_records)
        logger.info(
            "Orderbooks: %s — %d YES, %d NO (%d/%d done)",
            slug,
            len(yes_records),
            len(no_records),
            progress.completed_count() + 1,
            -1,  # filled in by caller via log
        )
        progress.mark_completed(slug)
    except SkipMarket:
        logger.warning("Skipping %s (404 or not found)", slug)
        progress.mark_completed(slug)
    except RateLimitExceeded:
        logger.warning("Rate limited during %s, moving on", slug)
    except Exception:
        logger.exception("Error ingesting orderbooks for %s", slug)
    finally:
        writer.close()


def ingest_orderbooks(
    market_index_path: Path | None = None,
    slugs: list[str] | None = None,
    workers: int = _DEFAULT_WORKERS,
) -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )

    index = _load_market_index(market_index_path)
    progress = ProgressTracker(OUTPUT_DIR / "progress_orderbooks.json")
    end_time_ms = int(time.time() * 1000)

    if slugs:
        markets = [(s, index[s]) for s in slugs if s in index]
    else:
        markets = sorted(index.items(), key=lambda x: x[1]["volume"], reverse=True)

    total = len(markets)
    logger.info("Orderbooks: %d markets to process with %d workers", total, workers)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(_process_one_market, slug, info, progress, end_time_ms): slug
            for slug, info in markets
            if not progress.is_completed(slug)
        }
        for future in as_completed(futures):
            slug = futures[future]
            try:
                future.result()
            except Exception:
                logger.exception("Unhandled error for %s", slug)

    logger.info("Orderbooks complete: %d markets processed", progress.completed_count())


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Ingest order book snapshots")
    parser.add_argument("--slugs", type=str, help="Comma-separated market slugs")
    parser.add_argument("--workers", type=int, default=_DEFAULT_WORKERS, help="Parallel markets")
    args = parser.parse_args()

    slugs = args.slugs.split(",") if args.slugs else None
    ingest_orderbooks(slugs=slugs, workers=args.workers)


if __name__ == "__main__":
    main()
