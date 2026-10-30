"""Individual validation checks for ingested Parquet data."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

# Dome data starts Oct 14, 2025 00:00:00 UTC
DOME_EPOCH_S = 1760400000
DOME_EPOCH_MS = DOME_EPOCH_S * 1000

MARKETS_SCHEMA: dict[str, pa.DataType] = {
    "market_id": pa.string(),
    "question": pa.string(),
    "description": pa.string(),
    "slug": pa.string(),
    "category": pa.string(),
    "tags": pa.string(),
    "outcome_yes_token_id": pa.string(),
    "outcome_no_token_id": pa.string(),
    "neg_risk": pa.bool_(),
    "volume_total": pa.float64(),
    "liquidity": pa.float64(),
    "created_at": pa.timestamp("ms", tz="UTC"),
    "end_date": pa.timestamp("ms", tz="UTC"),
    "resolved_at": pa.timestamp("ms", tz="UTC"),
    "resolution_outcome": pa.string(),
    "active": pa.bool_(),
    "closed": pa.bool_(),
    "event_slug": pa.string(),
    "spread": pa.float64(),
    "best_bid": pa.float64(),
    "best_ask": pa.float64(),
}

TRADES_SCHEMA: dict[str, pa.DataType] = {
    "trade_id": pa.string(),
    "market_id": pa.string(),
    "token_id": pa.string(),
    "token_label": pa.string(),
    "side": pa.string(),
    "price": pa.float64(),
    "size": pa.float64(),
    "timestamp": pa.timestamp("s", tz="UTC"),
    "user_address": pa.string(),
    "taker_address": pa.string(),
    "tx_hash": pa.string(),
    "block_number": pa.int64(),
}

ORDERBOOK_SCHEMA: dict[str, pa.DataType] = {
    "market_id": pa.string(),
    "token_id": pa.string(),
    "timestamp": pa.timestamp("ms", tz="UTC"),
    "bids": pa.string(),
    "asks": pa.string(),
    "best_bid": pa.float64(),
    "best_ask": pa.float64(),
    "spread": pa.float64(),
    "mid_price": pa.float64(),
    "bid_depth": pa.int32(),
    "ask_depth": pa.int32(),
    "total_bid_size": pa.float64(),
    "total_ask_size": pa.float64(),
    "book_hash": pa.string(),
}

PRICES_SCHEMA: dict[str, pa.DataType] = {
    "market_id": pa.string(),
    "token_id": pa.string(),
    "timestamp": pa.timestamp("s", tz="UTC"),
    "mid_price": pa.float64(),
}


@dataclass
class Issue:
    level: str  # "error", "warning", "info"
    check_name: str
    message: str
    details: dict | None = field(default_factory=dict)


def _types_compatible(actual: pa.DataType, expected: pa.DataType) -> bool:
    """Check type compatibility, allowing timestamp resolution differences.

    Parquet doesn't preserve the exact timestamp unit (s vs ms vs us) on
    round-trip, so we only compare the base type and timezone.
    """
    if actual == expected:
        return True
    if pa.types.is_timestamp(actual) and pa.types.is_timestamp(expected):
        return actual.tz == expected.tz
    return False


def check_schema(
    table: pa.Table,
    expected: dict[str, pa.DataType],
    file_label: str,
) -> list[Issue]:
    """Compare a table's schema against expected columns and types."""
    issues: list[Issue] = []
    actual_names = set(table.schema.names)
    expected_names = set(expected.keys())

    for col in sorted(expected_names - actual_names):
        issues.append(Issue("error", "schema", f"{file_label}: missing required column '{col}'"))

    for col in sorted(actual_names - expected_names):
        issues.append(Issue("warning", "schema", f"{file_label}: unexpected column '{col}'"))

    for col in sorted(actual_names & expected_names):
        actual_type = table.schema.field(col).type
        expected_type = expected[col]
        if not _types_compatible(actual_type, expected_type):
            issues.append(
                Issue(
                    "error",
                    "schema",
                    f"{file_label}: column '{col}' has type "
                    f"{actual_type}, expected {expected_type}",
                )
            )

    return issues


def check_schemas(data_dir: Path) -> list[Issue]:
    """Validate schemas of all Parquet files."""
    issues: list[Issue] = []

    markets_path = data_dir / "markets.parquet"
    if markets_path.exists():
        table = pq.read_table(markets_path)
        issues.extend(check_schema(table, MARKETS_SCHEMA, "markets.parquet"))
    else:
        issues.append(Issue("error", "schema", "markets.parquet not found"))

    for subdir, expected in [
        ("trades", TRADES_SCHEMA),
        ("orderbook_snapshots", ORDERBOOK_SCHEMA),
        ("prices", PRICES_SCHEMA),
    ]:
        subdir_path = data_dir / subdir
        if not subdir_path.exists():
            issues.append(Issue("warning", "schema", f"{subdir}/ directory not found"))
            continue
        files = sorted(subdir_path.glob("*.parquet"))
        if not files:
            issues.append(Issue("warning", "schema", f"{subdir}/ has no Parquet files"))
            continue
        # Check schema of the first file only (all should be identical)
        table = pq.read_table(files[0])
        issues.extend(check_schema(table, expected, f"{subdir}/{files[0].name}"))

    return issues


def check_referential_integrity(data_dir: Path) -> list[Issue]:
    """Check that child files reference valid market_ids and token_ids."""
    issues: list[Issue] = []

    markets_path = data_dir / "markets.parquet"
    if not markets_path.exists():
        issues.append(
            Issue("error", "referential_integrity", "Cannot check: markets.parquet missing")
        )
        return issues

    markets = pq.read_table(markets_path)
    market_ids = set(markets.column("market_id").to_pylist())

    # Build token_id -> market_id mapping
    valid_token_ids: set[str] = set()
    if "outcome_yes_token_id" in markets.schema.names:
        valid_token_ids.update(
            t for t in markets.column("outcome_yes_token_id").to_pylist() if t is not None
        )
    if "outcome_no_token_id" in markets.schema.names:
        valid_token_ids.update(
            t for t in markets.column("outcome_no_token_id").to_pylist() if t is not None
        )

    for subdir in ["trades", "orderbook_snapshots", "prices"]:
        subdir_path = data_dir / subdir
        if not subdir_path.exists():
            continue

        for fpath in sorted(subdir_path.glob("*.parquet")):
            table = pq.read_table(fpath, columns=["market_id"])
            child_ids = set(table.column("market_id").to_pylist())
            orphans = child_ids - market_ids
            if orphans:
                issues.append(
                    Issue(
                        "error",
                        "referential_integrity",
                        f"{subdir}/{fpath.name}: "
                        f"{len(orphans)} market_id(s) not in markets.parquet",
                        {"orphan_ids": sorted(orphans)[:10]},
                    )
                )

            if "token_id" in pq.read_schema(str(fpath)).names:
                token_table = pq.read_table(fpath, columns=["token_id"])
                child_tokens = set(token_table.column("token_id").to_pylist())
                orphan_tokens = child_tokens - valid_token_ids
                if orphan_tokens:
                    issues.append(
                        Issue(
                            "warning",
                            "referential_integrity",
                            f"{subdir}/{fpath.name}: "
                            f"{len(orphan_tokens)} token_id(s) not in markets",
                            {"orphan_token_ids": sorted(orphan_tokens)[:10]},
                        )
                    )

    return issues


def check_temporal_consistency(data_dir: Path) -> list[Issue]:
    """Verify timestamps fall within expected bounds."""
    issues: list[Issue] = []
    now_s = int(time.time())
    now_ms = now_s * 1000

    # Load market time ranges for per-market checks
    market_ranges: dict[str, tuple[int | None, int | None]] = {}
    markets_path = data_dir / "markets.parquet"
    if markets_path.exists():
        markets = pq.read_table(markets_path, columns=["market_id", "created_at", "resolved_at"])
        for i in range(markets.num_rows):
            mid = markets.column("market_id")[i].as_py()
            created = markets.column("created_at")[i].as_py()
            resolved = markets.column("resolved_at")[i].as_py()
            created_ts = int(created.timestamp()) if created else None
            resolved_ts = int(resolved.timestamp()) if resolved else None
            market_ranges[mid] = (created_ts, resolved_ts)

    # Trades: timestamp in seconds
    trades_dir = data_dir / "trades"
    if trades_dir.exists():
        for fpath in sorted(trades_dir.glob("*.parquet")):
            table = pq.read_table(fpath, columns=["timestamp", "market_id"])
            if table.num_rows == 0:
                continue
            ts_col = table.column("timestamp")
            # Convert to Python timestamps (seconds)
            timestamps = [t.as_py().timestamp() for t in ts_col if t.as_py() is not None]
            if not timestamps:
                continue

            min_ts, max_ts = min(timestamps), max(timestamps)
            if min_ts < DOME_EPOCH_S:
                issues.append(
                    Issue(
                        "error",
                        "temporal",
                        f"trades/{fpath.name}: timestamps before Dome epoch (Oct 14 2025)",
                        {"earliest": min_ts},
                    )
                )
            if max_ts > now_s + 60:  # 60s buffer for clock skew
                issues.append(
                    Issue(
                        "error",
                        "temporal",
                        f"trades/{fpath.name}: timestamps in the future",
                        {"latest": max_ts},
                    )
                )

            # Per-market range check
            market_id_col = table.column("market_id")
            for i in range(table.num_rows):
                ts_val = ts_col[i].as_py()
                mid = market_id_col[i].as_py()
                if ts_val is None or mid is None or mid not in market_ranges:
                    continue
                created, resolved = market_ranges[mid]
                ts_s = ts_val.timestamp()
                if created and ts_s < created - 86400:  # 1 day tolerance
                    issues.append(
                        Issue(
                            "warning",
                            "temporal",
                            f"trades/{fpath.name}: trade before market creation",
                            {"trade_ts": ts_s, "created_at": created, "market_id": mid},
                        )
                    )
                    break  # one warning per file is enough
                if resolved and ts_s > resolved + 86400:
                    issues.append(
                        Issue(
                            "warning",
                            "temporal",
                            f"trades/{fpath.name}: trade after market resolution",
                            {"trade_ts": ts_s, "resolved_at": resolved, "market_id": mid},
                        )
                    )
                    break

    # Orderbook snapshots: timestamp in milliseconds
    ob_dir = data_dir / "orderbook_snapshots"
    if ob_dir.exists():
        for fpath in sorted(ob_dir.glob("*.parquet")):
            table = pq.read_table(fpath, columns=["timestamp"])
            if table.num_rows == 0:
                continue
            timestamps = [
                t.as_py().timestamp() * 1000 for t in table.column("timestamp") if t.as_py()
            ]
            if not timestamps:
                continue
            min_ts, max_ts = min(timestamps), max(timestamps)
            if min_ts < DOME_EPOCH_MS:
                issues.append(
                    Issue(
                        "error",
                        "temporal",
                        f"orderbook_snapshots/{fpath.name}: timestamps before Dome epoch",
                        {"earliest_ms": min_ts},
                    )
                )
            if max_ts > now_ms + 60000:
                issues.append(
                    Issue(
                        "error",
                        "temporal",
                        f"orderbook_snapshots/{fpath.name}: timestamps in the future",
                        {"latest_ms": max_ts},
                    )
                )

    # Prices: timestamp in seconds
    prices_dir = data_dir / "prices"
    if prices_dir.exists():
        for fpath in sorted(prices_dir.glob("*.parquet")):
            table = pq.read_table(fpath, columns=["timestamp"])
            if table.num_rows == 0:
                continue
            timestamps = [t.as_py().timestamp() for t in table.column("timestamp") if t.as_py()]
            if not timestamps:
                continue
            min_ts, max_ts = min(timestamps), max(timestamps)
            if min_ts < DOME_EPOCH_S:
                issues.append(
                    Issue(
                        "warning",
                        "temporal",
                        f"prices/{fpath.name}: timestamps before Dome epoch",
                        {"earliest": min_ts},
                    )
                )
            if max_ts > now_s + 60:
                issues.append(
                    Issue(
                        "error",
                        "temporal",
                        f"prices/{fpath.name}: timestamps in the future",
                        {"latest": max_ts},
                    )
                )

    return issues


def check_value_ranges(data_dir: Path) -> list[Issue]:
    """Validate prices in [0,1], sizes > 0, no crossed books."""
    issues: list[Issue] = []

    # Trades: price in [0,1], size > 0
    trades_dir = data_dir / "trades"
    if trades_dir.exists():
        for fpath in sorted(trades_dir.glob("*.parquet")):
            table = pq.read_table(fpath, columns=["price", "size"])
            if table.num_rows == 0:
                continue
            import pyarrow.compute as pc

            prices = table.column("price")
            min_p = pc.min(prices).as_py()
            max_p = pc.max(prices).as_py()
            if min_p is not None and min_p < 0.0:
                issues.append(
                    Issue(
                        "error",
                        "value_range",
                        f"trades/{fpath.name}: price below 0",
                        {"min_price": min_p},
                    )
                )
            if max_p is not None and max_p > 1.0:
                issues.append(
                    Issue(
                        "error",
                        "value_range",
                        f"trades/{fpath.name}: price above 1",
                        {"max_price": max_p},
                    )
                )

            sizes = table.column("size")
            min_size = pc.min(sizes).as_py()
            if min_size is not None and min_size <= 0:
                issues.append(
                    Issue(
                        "error",
                        "value_range",
                        f"trades/{fpath.name}: non-positive trade size",
                        {"min_size": min_size},
                    )
                )

    # Orderbook: best_bid <= best_ask, spread >= 0
    ob_dir = data_dir / "orderbook_snapshots"
    if ob_dir.exists():
        for fpath in sorted(ob_dir.glob("*.parquet")):
            table = pq.read_table(fpath, columns=["best_bid", "best_ask", "spread"])
            if table.num_rows == 0:
                continue
            import pyarrow.compute as pc

            bids = table.column("best_bid").to_pylist()
            asks = table.column("best_ask").to_pylist()
            spreads = table.column("spread").to_pylist()

            crossed = sum(
                1
                for b, a in zip(bids, asks, strict=False)
                if b is not None and a is not None and b > a
            )
            if crossed > 0:
                issues.append(
                    Issue(
                        "error",
                        "value_range",
                        f"orderbook_snapshots/{fpath.name}: {crossed} crossed book(s) (bid > ask)",
                    )
                )

            neg_spread = sum(1 for s in spreads if s is not None and s < 0)
            if neg_spread > 0:
                issues.append(
                    Issue(
                        "error",
                        "value_range",
                        f"orderbook_snapshots/{fpath.name}: {neg_spread} negative spread(s)",
                    )
                )

    # Prices: mid_price in [0,1]
    prices_dir = data_dir / "prices"
    if prices_dir.exists():
        for fpath in sorted(prices_dir.glob("*.parquet")):
            table = pq.read_table(fpath, columns=["mid_price"])
            if table.num_rows == 0:
                continue
            import pyarrow.compute as pc

            min_p = pc.min(table.column("mid_price")).as_py()
            max_p = pc.max(table.column("mid_price")).as_py()
            if min_p is not None and min_p < 0.0:
                issues.append(
                    Issue(
                        "error",
                        "value_range",
                        f"prices/{fpath.name}: mid_price below 0",
                        {"min_price": min_p},
                    )
                )
            if max_p is not None and max_p > 1.0:
                issues.append(
                    Issue(
                        "error",
                        "value_range",
                        f"prices/{fpath.name}: mid_price above 1",
                        {"max_price": max_p},
                    )
                )

    return issues


def check_completeness(data_dir: Path) -> tuple[list[Issue], dict]:
    """Report coverage: how many markets have trades/orderbooks/prices."""
    issues: list[Issue] = []
    stats: dict = {
        "total_markets": 0,
        "markets_with_trades": 0,
        "markets_with_orderbooks": 0,
        "markets_with_prices": 0,
        "total_trade_rows": 0,
        "total_orderbook_rows": 0,
        "total_price_rows": 0,
    }

    markets_path = data_dir / "markets.parquet"
    if not markets_path.exists():
        issues.append(Issue("error", "completeness", "markets.parquet missing"))
        return issues, stats

    markets = pq.read_table(markets_path)
    stats["total_markets"] = markets.num_rows
    market_slugs = set()
    if "slug" in markets.schema.names:
        market_slugs = {s for s in markets.column("slug").to_pylist() if s is not None}

    trade_slugs: set[str] = set()
    trades_dir = data_dir / "trades"
    if trades_dir.exists():
        for fpath in sorted(trades_dir.glob("*.parquet")):
            trade_slugs.add(fpath.stem)
            stats["total_trade_rows"] += pq.read_metadata(str(fpath)).num_rows

    ob_slugs: set[str] = set()
    ob_dir = data_dir / "orderbook_snapshots"
    if ob_dir.exists():
        for fpath in sorted(ob_dir.glob("*.parquet")):
            ob_slugs.add(fpath.stem)
            stats["total_orderbook_rows"] += pq.read_metadata(str(fpath)).num_rows

    price_slugs: set[str] = set()
    prices_dir = data_dir / "prices"
    if prices_dir.exists():
        for fpath in sorted(prices_dir.glob("*.parquet")):
            price_slugs.add(fpath.stem)
            stats["total_price_rows"] += pq.read_metadata(str(fpath)).num_rows

    stats["markets_with_trades"] = len(trade_slugs)
    stats["markets_with_orderbooks"] = len(ob_slugs)
    stats["markets_with_prices"] = len(price_slugs)

    # Markets with trades but no orderbooks (or vice versa)
    trades_no_ob = trade_slugs - ob_slugs
    if trades_no_ob:
        issues.append(
            Issue(
                "info",
                "completeness",
                f"{len(trades_no_ob)} market(s) have trades but no orderbook snapshots",
            )
        )
    ob_no_trades = ob_slugs - trade_slugs
    if ob_no_trades:
        issues.append(
            Issue(
                "info",
                "completeness",
                f"{len(ob_no_trades)} market(s) have orderbook snapshots but no trades",
            )
        )

    # Files not matching any market slug
    if market_slugs:
        orphan_trade_files = trade_slugs - market_slugs
        if orphan_trade_files:
            issues.append(
                Issue(
                    "warning",
                    "completeness",
                    f"{len(orphan_trade_files)} trade file(s) don't match any market slug",
                    {"slugs": sorted(orphan_trade_files)[:10]},
                )
            )

    return issues, stats


def check_duplicates(data_dir: Path) -> list[Issue]:
    """Detect duplicate records in trades and orderbook files."""
    issues: list[Issue] = []

    trades_dir = data_dir / "trades"
    if trades_dir.exists():
        for fpath in sorted(trades_dir.glob("*.parquet")):
            table = pq.read_table(fpath, columns=["trade_id"])
            if table.num_rows == 0:
                continue
            trade_ids = table.column("trade_id").to_pylist()
            n_unique = len(set(trade_ids))
            n_dupes = len(trade_ids) - n_unique
            if n_dupes > 0:
                issues.append(
                    Issue(
                        "error",
                        "duplicates",
                        f"trades/{fpath.name}: {n_dupes} duplicate trade_id(s)",
                    )
                )

    ob_dir = data_dir / "orderbook_snapshots"
    if ob_dir.exists():
        for fpath in sorted(ob_dir.glob("*.parquet")):
            schema_names = pq.read_schema(str(fpath)).names
            needed = {"timestamp", "token_id", "book_hash"}
            if not needed.issubset(schema_names):
                continue
            table = pq.read_table(fpath, columns=["timestamp", "token_id", "book_hash"])
            if table.num_rows == 0:
                continue
            keys = [
                (
                    table.column("timestamp")[i].as_py(),
                    table.column("token_id")[i].as_py(),
                    table.column("book_hash")[i].as_py(),
                )
                for i in range(table.num_rows)
            ]
            n_dupes = len(keys) - len(set(keys))
            if n_dupes > 0:
                issues.append(
                    Issue(
                        "error",
                        "duplicates",
                        f"orderbook_snapshots/{fpath.name}: "
                        f"{n_dupes} duplicate (timestamp, token_id, book_hash)",
                    )
                )

    return issues


def check_cross_source(data_dir: Path) -> list[Issue]:
    """Sample-based cross-source validation between trades, prices, and orderbooks."""
    issues: list[Issue] = []

    trades_dir = data_dir / "trades"
    prices_dir = data_dir / "prices"
    ob_dir = data_dir / "orderbook_snapshots"
    if not trades_dir.exists() or not prices_dir.exists():
        return issues

    # For each market that has both trades and prices, spot-check alignment
    trade_files = {f.stem: f for f in trades_dir.glob("*.parquet")}
    price_files = {f.stem: f for f in prices_dir.glob("*.parquet")}
    ob_files = {f.stem: f for f in ob_dir.glob("*.parquet")} if ob_dir.exists() else {}

    shared_slugs = sorted(set(trade_files) & set(price_files))
    # Sample up to 5 markets to avoid scanning everything
    for slug in shared_slugs[:5]:
        trades = pq.read_table(trade_files[slug], columns=["price", "timestamp"])
        prices = pq.read_table(price_files[slug], columns=["mid_price", "timestamp"])

        if trades.num_rows == 0 or prices.num_rows == 0:
            continue

        # Check that trade price range roughly overlaps with price series range
        import pyarrow.compute as pc

        trade_min = pc.min(trades.column("price")).as_py()
        trade_max = pc.max(trades.column("price")).as_py()
        price_min = pc.min(prices.column("mid_price")).as_py()
        price_max = pc.max(prices.column("mid_price")).as_py()

        if trade_min is None or price_min is None:
            continue

        # If trade prices and mid-prices don't overlap at all, flag it
        if trade_max < price_min or trade_min > price_max:
            issues.append(
                Issue(
                    "warning",
                    "cross_source",
                    f"{slug}: trade prices [{trade_min:.3f}, {trade_max:.3f}] "
                    f"don't overlap with mid-prices [{price_min:.3f}, {price_max:.3f}]",
                )
            )

    # Check that orderbook best_bid/best_ask align with trade prices (sample)
    shared_ob = sorted(set(trade_files) & set(ob_files))
    for slug in shared_ob[:5]:
        ob = pq.read_table(ob_files[slug], columns=["best_bid", "best_ask"])
        trades = pq.read_table(trade_files[slug], columns=["price"])
        if ob.num_rows == 0 or trades.num_rows == 0:
            continue

        import pyarrow.compute as pc

        ob_bid_max = pc.max(ob.column("best_bid")).as_py()
        ob_ask_min = pc.min(ob.column("best_ask")).as_py()
        trade_min = pc.min(trades.column("price")).as_py()
        trade_max = pc.max(trades.column("price")).as_py()

        if ob_bid_max is None or trade_min is None:
            continue

        # Trades should generally fall within the range of observed bids/asks
        if trade_max > 0 and ob_ask_min > 0 and trade_min > ob_bid_max * 2:
            issues.append(
                Issue(
                    "warning",
                    "cross_source",
                    f"{slug}: trade prices far outside orderbook range",
                    {
                        "trade_range": [trade_min, trade_max],
                        "ob_bid_max": ob_bid_max,
                        "ob_ask_min": ob_ask_min,
                    },
                )
            )

    return issues


def run_all_checks(data_dir: Path) -> tuple[list[Issue], dict]:
    """Run every validation check and return issues + stats."""
    all_issues: list[Issue] = []

    all_issues.extend(check_schemas(data_dir))
    all_issues.extend(check_referential_integrity(data_dir))
    all_issues.extend(check_temporal_consistency(data_dir))
    all_issues.extend(check_value_ranges(data_dir))

    completeness_issues, stats = check_completeness(data_dir)
    all_issues.extend(completeness_issues)

    all_issues.extend(check_duplicates(data_dir))
    all_issues.extend(check_cross_source(data_dir))

    # Add file sizes to stats
    file_sizes: dict[str, int] = {}
    markets_path = data_dir / "markets.parquet"
    if markets_path.exists():
        file_sizes["markets.parquet"] = markets_path.stat().st_size
    for subdir in ["trades", "orderbook_snapshots", "prices"]:
        subdir_path = data_dir / subdir
        if subdir_path.exists():
            total = sum(f.stat().st_size for f in subdir_path.glob("*.parquet"))
            file_sizes[subdir] = total
    stats["file_sizes"] = file_sizes

    return all_issues, stats
