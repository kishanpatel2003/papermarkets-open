"""Ingest price history from the CLOB API into Parquet."""

import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path

from papermarkets.ingest.client import get_public_client
from papermarkets.ingest.config import CLOB_BASE_URL, OUTPUT_DIR
from papermarkets.ingest.exceptions import RateLimitExceeded, SkipMarket
from papermarkets.ingest.progress import ProgressTracker
from papermarkets.ingest.writer import PRICES_SCHEMA, ParquetWriter, safe_filename

logger = logging.getLogger(__name__)

_DEFAULT_WORKERS = 20


def _load_market_index(path: Path | None = None) -> dict:
    index_path = path or (OUTPUT_DIR / "market_index.json")
    return json.loads(index_path.read_text())


def _fetch_price_history(token_id: str, is_closed: bool) -> list[dict]:
    """Fetch price history for a single token, falling back to daily for closed markets."""
    url = f"{CLOB_BASE_URL}/prices-history?market={token_id}&interval=all&fidelity=1"
    data = get_public_client().get(url)
    history = data.get("history", [])

    # Closed markets only return data at daily fidelity
    if is_closed and not history:
        url = f"{CLOB_BASE_URL}/prices-history?market={token_id}&interval=all&fidelity=1440"
        data = get_public_client().get(url)
        history = data.get("history", [])

    return history


def _normalize_price_point(point: dict, market_id: str, token_id: str) -> dict:
    return {
        "market_id": market_id,
        "token_id": token_id,
        "timestamp": datetime.fromtimestamp(point["t"], tz=UTC),
        "mid_price": float(point["p"]),
    }


def _process_one_market(slug: str, info: dict, progress: ProgressTracker) -> None:
    """Ingest prices for a single market."""
    if progress.is_completed(slug):
        return

    progress.mark_started(slug)
    writer = ParquetWriter(OUTPUT_DIR / "prices" / f"{safe_filename(slug)}.parquet", PRICES_SCHEMA)
    is_closed = info.get("closed", False)

    try:
        token_ids = [t for t in (info["yes_token_id"], info["no_token_id"]) if t]

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = {pool.submit(_fetch_price_history, tid, is_closed): tid for tid in token_ids}
        records = []
        for future, token_id in futures.items():
            history = future.result()
            for point in history:
                records.append(_normalize_price_point(point, info["condition_id"], token_id))

        writer.write_batch(records)
        logger.info("Prices: %s — %d data points", slug, len(records))
        progress.mark_completed(slug)
    except SkipMarket:
        logger.warning("Skipping %s (404 or not found)", slug)
        progress.mark_completed(slug)
    except RateLimitExceeded:
        logger.warning("Rate limited during %s, moving on", slug)
    except Exception:
        logger.exception("Error ingesting prices for %s", slug)
    finally:
        writer.close()


def ingest_prices(
    market_index_path: Path | None = None,
    slugs: list[str] | None = None,
    workers: int = _DEFAULT_WORKERS,
) -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )

    index = _load_market_index(market_index_path)
    progress = ProgressTracker(OUTPUT_DIR / "progress_prices.json")

    if slugs:
        markets = [(s, index[s]) for s in slugs if s in index]
    else:
        markets = sorted(index.items(), key=lambda x: x[1]["volume"], reverse=True)

    total = len(markets)
    logger.info("Prices: %d markets to process with %d workers", total, workers)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(_process_one_market, slug, info, progress): slug
            for slug, info in markets
            if not progress.is_completed(slug)
        }
        for future in as_completed(futures):
            slug = futures[future]
            try:
                future.result()
            except Exception:
                logger.exception("Unhandled error for %s", slug)

    logger.info("Prices complete: %d markets processed", progress.completed_count())


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Ingest price history")
    parser.add_argument("--slugs", type=str, help="Comma-separated market slugs")
    parser.add_argument("--workers", type=int, default=_DEFAULT_WORKERS, help="Parallel markets")
    args = parser.parse_args()

    slugs = args.slugs.split(",") if args.slugs else None
    ingest_prices(slugs=slugs, workers=args.workers)


if __name__ == "__main__":
    main()
