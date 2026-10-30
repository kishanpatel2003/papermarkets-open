"""Master orchestrator that runs all ingesters in parallel."""

import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from papermarkets.ingest.config import OUTPUT_DIR
from papermarkets.ingest.ingest_orderbooks import ingest_orderbooks
from papermarkets.ingest.ingest_prices import ingest_prices
from papermarkets.ingest.ingest_trades import ingest_trades

logger = logging.getLogger(__name__)


def _get_test_slugs() -> list[str]:
    """Pick the first 5 markets from market_index.json sorted by volume."""
    index_path = OUTPUT_DIR / "market_index.json"
    index = json.loads(index_path.read_text())
    # Index is already sorted by volume descending (see ingest_markets._build_market_index)
    slugs = list(index.keys())[:5]
    logger.info("Test mode: selected %d markets — %s", len(slugs), slugs)
    return slugs


def _count_parquet_files(directory: Path) -> tuple[int, int]:
    """Return (file_count, total_bytes) for all .parquet files under directory."""
    files = list(directory.rglob("*.parquet"))
    total_bytes = sum(f.stat().st_size for f in files)
    return len(files), total_bytes


def ingest_all(test_mode: bool = False, workers: int = 20) -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )

    index_path = OUTPUT_DIR / "market_index.json"
    if not index_path.exists():
        logger.error("market_index.json not found at %s — run ingest_markets first", index_path)
        return

    slugs = _get_test_slugs() if test_mode else None

    logger.info("Starting all ingesters in parallel with %d workers each...", workers)

    # Run all three ingesters concurrently — each parallelizes across markets internally
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {
            pool.submit(ingest_orderbooks, slugs=slugs, workers=workers): "orderbooks",
            pool.submit(ingest_trades, slugs=slugs, workers=workers): "trades",
            pool.submit(ingest_prices, slugs=slugs, workers=workers): "prices",
        }
        for future in as_completed(futures):
            name = futures[future]
            try:
                future.result()
                logger.info("%s ingester finished", name)
            except Exception:
                logger.exception("%s ingester failed", name)

    # Summary
    ob_count, ob_size = _count_parquet_files(OUTPUT_DIR / "orderbook_snapshots")
    tr_count, tr_size = _count_parquet_files(OUTPUT_DIR / "trades")
    pr_count, pr_size = _count_parquet_files(OUTPUT_DIR / "prices")
    total_size = ob_size + tr_size + pr_size

    logger.info("=== Ingestion complete ===")
    logger.info("Orderbooks: %d files (%.1f MB)", ob_count, ob_size / (1024 * 1024))
    logger.info("Trades:     %d files (%.1f MB)", tr_count, tr_size / (1024 * 1024))
    logger.info("Prices:     %d files (%.1f MB)", pr_count, pr_size / (1024 * 1024))
    total_count = ob_count + tr_count + pr_count
    logger.info("Total:      %d files (%.1f MB)", total_count, total_size / (1024 * 1024))


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Run all data ingesters")
    parser.add_argument("--test", action="store_true", help="Run with 5 test markets only")
    parser.add_argument("--workers", type=int, default=20, help="Parallel markets per ingester")
    args = parser.parse_args()

    ingest_all(test_mode=args.test, workers=args.workers)


if __name__ == "__main__":
    main()
