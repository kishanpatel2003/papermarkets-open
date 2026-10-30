"""Benchmark runner — measures query execution times against real data."""

import time
from datetime import UTC, datetime
from pathlib import Path

from papermarkets.queries.connection import get_connection
from papermarkets.queries.v0_queries import daily_volume, market_accuracy, price_chart
from papermarkets.queries.v1_queries import (
    orderbook_at_time,
    top_wallets,
    trades_by_wallet,
    wallet_pnl,
)


def _run_benchmark(name: str, func, *args, **kwargs) -> dict:
    start = time.perf_counter()
    try:
        result = func(*args, **kwargs)
        rows = result.fetchall()
        elapsed = time.perf_counter() - start
        return {"query": name, "time_ms": round(elapsed * 1000, 2), "rows": len(rows)}
    except Exception as e:
        elapsed = time.perf_counter() - start
        return {"query": name, "time_ms": round(elapsed * 1000, 2), "rows": 0, "error": str(e)}


def run_all(data_dir: Path) -> list[dict]:
    con = get_connection(data_dir)
    results = []

    # Check which views exist
    views = {row[0] for row in con.execute("SHOW TABLES").fetchall()}

    if "trades" in views:
        # Grab a sample wallet for wallet queries
        sample = con.execute("SELECT user_address FROM trades LIMIT 1").fetchone()
        sample_wallet = sample[0] if sample else "0x0000"

        results.append(_run_benchmark("trades_by_wallet", trades_by_wallet, con, sample_wallet))
        results.append(_run_benchmark("top_wallets(volume)", top_wallets, con, "volume", 20))
        results.append(
            _run_benchmark("top_wallets(trade_count)", top_wallets, con, "trade_count", 20)
        )

    if "trades" in views and "markets" in views:
        sample = con.execute("SELECT user_address FROM trades LIMIT 1").fetchone()
        sample_wallet = sample[0] if sample else "0x0000"
        results.append(_run_benchmark("wallet_pnl", wallet_pnl, con, sample_wallet))
        results.append(_run_benchmark("top_wallets(pnl)", top_wallets, con, "pnl", 20))

    if "orderbooks" in views and "markets" in views:
        sample = con.execute("SELECT slug FROM markets LIMIT 1").fetchone()
        slug = sample[0] if sample else "test"
        ts = datetime(2025, 11, 1, tzinfo=UTC)
        results.append(_run_benchmark("orderbook_at_time", orderbook_at_time, con, slug, ts))

    if "prices" in views and "markets" in views:
        sample = con.execute("SELECT slug FROM markets LIMIT 1").fetchone()
        slug = sample[0] if sample else "test"
        results.append(_run_benchmark("price_chart", price_chart, con, slug))

    if "trades" in views:
        results.append(_run_benchmark("daily_volume", daily_volume, con))

    if "prices" in views and "markets" in views:
        results.append(_run_benchmark("market_accuracy", market_accuracy, con))

    return results


def main():
    from papermarkets.ingest.config import OUTPUT_DIR

    print(f"Running benchmarks against {OUTPUT_DIR}\n")
    results = run_all(OUTPUT_DIR)

    if not results:
        print("No data found — benchmarks skipped.")
        return

    name_width = max(len(r["query"]) for r in results)
    print(f"{'Query':<{name_width}}  {'Time (ms)':>10}  {'Rows':>8}")
    print("-" * (name_width + 22))
    for r in results:
        error = f"  ERROR: {r['error']}" if "error" in r else ""
        print(f"{r['query']:<{name_width}}  {r['time_ms']:>10.2f}  {r['rows']:>8}{error}")


if __name__ == "__main__":
    main()
