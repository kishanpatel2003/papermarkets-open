"""Ingest trade history from the Dome API into Parquet."""

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
from papermarkets.ingest.writer import TRADES_SCHEMA, ParquetWriter, safe_filename

logger = logging.getLogger(__name__)

# Dome trade data starts Oct 14, 2025 00:00 UTC — timestamps in SECONDS
_START_TIME_S = 1760400000

_DEFAULT_WORKERS = 20


def _load_market_index(path: Path | None = None) -> dict:
    index_path = path or (OUTPUT_DIR / "market_index.json")
    return json.loads(index_path.read_text())


def _normalize_trade(order: dict) -> dict:
    # Dome trade timestamps are in seconds
    ts = order["timestamp"]
    return {
        "trade_id": order["order_hash"],
        "market_id": order["condition_id"],
        "token_id": order["token_id"],
        "token_label": order["token_label"],
        "side": order["side"],
        "price": float(order["price"]),
        "size": float(order["shares_normalized"]),
        "timestamp": datetime.fromtimestamp(ts, tz=UTC),
        "user_address": order["user"],
        "taker_address": order.get("taker", ""),
        "tx_hash": order.get("tx_hash", ""),
        "block_number": int(order.get("block_number", 0)),
    }


_CHUNK_S = 30 * 24 * 60 * 60  # 30 days in seconds


def _ingest_token_trades(token_id: str, start_time_s: int, end_time_s: int) -> list[dict]:
    """Fetch all trades for a token, chunking by 30-day windows."""
    if not token_id:
        return []
    records = []
    window_start = max(start_time_s, _START_TIME_S)
    while window_start < end_time_s:
        window_end = min(window_start + _CHUNK_S, end_time_s)
        url = (
            f"{DOME_BASE_URL}/polymarket/orders"
            f"?token_id={token_id}"
            f"&start_time={window_start}"
            f"&end_time={window_end}"
            f"&limit=1000"
        )
        for page in paginate_dome(get_dome_client(), url, items_field="orders"):
            for order in page:
                records.append(_normalize_trade(order))
        window_start = window_end
    return records


def _process_one_market(slug: str, info: dict, progress: ProgressTracker, end_time_s: int) -> None:
    """Ingest trades for a single market."""
    if progress.is_completed(slug):
        return

    progress.mark_started(slug)
    writer = ParquetWriter(OUTPUT_DIR / "trades" / f"{safe_filename(slug)}.parquet", TRADES_SCHEMA)
    try:
        start_s = info.get("created_at", _START_TIME_S)
        yes_tid, no_tid = info["yes_token_id"], info["no_token_id"]
        with ThreadPoolExecutor(max_workers=2) as pool:
            yes_future = pool.submit(_ingest_token_trades, yes_tid, start_s, end_time_s)
            no_future = pool.submit(_ingest_token_trades, no_tid, start_s, end_time_s)
            yes_trades = yes_future.result()
            no_trades = no_future.result()
        # Deduplicate by trade_id (same trade appears for both YES and NO tokens)
        seen = set()
        all_trades = []
        for trade in yes_trades + no_trades:
            if trade["trade_id"] not in seen:
                seen.add(trade["trade_id"])
                all_trades.append(trade)
        writer.write_batch(all_trades)
        logger.info("Trades: %s — %d trades", slug, len(all_trades))
        progress.mark_completed(slug)
    except SkipMarket:
        logger.warning("Skipping %s (404 or not found)", slug)
        progress.mark_completed(slug)
    except RateLimitExceeded:
        logger.warning("Rate limited during %s, moving on", slug)
    except Exception:
        logger.exception("Error ingesting trades for %s", slug)
    finally:
        writer.close()


def ingest_trades(
    market_index_path: Path | None = None,
    slugs: list[str] | None = None,
    workers: int = _DEFAULT_WORKERS,
) -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )

    index = _load_market_index(market_index_path)
    progress = ProgressTracker(OUTPUT_DIR / "progress_trades.json")
    end_time_s = int(time.time())

    if slugs:
        markets = [(s, index[s]) for s in slugs if s in index]
    else:
        markets = sorted(index.items(), key=lambda x: x[1]["volume"], reverse=True)

    total = len(markets)
    logger.info("Trades: %d markets to process with %d workers", total, workers)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(_process_one_market, slug, info, progress, end_time_s): slug
            for slug, info in markets
            if not progress.is_completed(slug)
        }
        for future in as_completed(futures):
            slug = futures[future]
            try:
                future.result()
            except Exception:
                logger.exception("Unhandled error for %s", slug)

    logger.info("Trades complete: %d markets processed", progress.completed_count())


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Ingest trade history")
    parser.add_argument("--slugs", type=str, help="Comma-separated market slugs")
    parser.add_argument("--workers", type=int, default=_DEFAULT_WORKERS, help="Parallel markets")
    args = parser.parse_args()

    slugs = args.slugs.split(",") if args.slugs else None
    ingest_trades(slugs=slugs, workers=args.workers)


if __name__ == "__main__":
    main()
