"""Tests for the validation checks using synthetic Parquet data."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from papermarkets.validation.checks import (
    DOME_EPOCH_S,
    check_completeness,
    check_cross_source,
    check_duplicates,
    check_referential_integrity,
    check_schemas,
    check_temporal_consistency,
    check_value_ranges,
    run_all_checks,
)

# ---------------------------------------------------------------------------
# Helpers to build synthetic Parquet files
# ---------------------------------------------------------------------------

MARKET_ID = "0xabc123"
MARKET_SLUG = "test-market"
YES_TOKEN = "111111"
NO_TOKEN = "222222"


def _ts_s(offset_days: int = 0) -> int:
    """Return a valid timestamp in seconds (after Dome epoch)."""
    return DOME_EPOCH_S + 86400 * offset_days + 3600


def _ts_ms(offset_days: int = 0) -> int:
    return _ts_s(offset_days) * 1000


def write_valid_markets(data_dir: Path) -> None:
    table = pa.table(
        {
            "market_id": [MARKET_ID],
            "question": ["Will X happen?"],
            "description": ["Description"],
            "slug": [MARKET_SLUG],
            "category": ["crypto"],
            "tags": [json.dumps(["btc"])],
            "outcome_yes_token_id": [YES_TOKEN],
            "outcome_no_token_id": [NO_TOKEN],
            "neg_risk": [False],
            "volume_total": [1000.0],
            "liquidity": [500.0],
            "created_at": pa.array(
                [datetime(2025, 10, 15, tzinfo=UTC)], type=pa.timestamp("ms", tz="UTC")
            ),
            "end_date": pa.array(
                [datetime(2026, 1, 1, tzinfo=UTC)], type=pa.timestamp("ms", tz="UTC")
            ),
            "resolved_at": pa.array([None], type=pa.timestamp("ms", tz="UTC")),
            "resolution_outcome": pa.array([None], type=pa.string()),
            "active": [True],
            "closed": [False],
            "event_slug": ["test-event"],
            "spread": [0.02],
            "best_bid": [0.48],
            "best_ask": [0.50],
        }
    )
    pq.write_table(table, data_dir / "markets.parquet")


def write_valid_trades(data_dir: Path) -> None:
    trades_dir = data_dir / "trades"
    trades_dir.mkdir(exist_ok=True)
    table = pa.table(
        {
            "trade_id": ["t1", "t2"],
            "market_id": [MARKET_ID, MARKET_ID],
            "token_id": [YES_TOKEN, NO_TOKEN],
            "token_label": ["Yes", "No"],
            "side": ["BUY", "SELL"],
            "price": [0.55, 0.45],
            "size": [10.0, 5.0],
            "timestamp": pa.array(
                [
                    datetime(2025, 10, 16, tzinfo=UTC),
                    datetime(2025, 10, 17, tzinfo=UTC),
                ],
                type=pa.timestamp("s", tz="UTC"),
            ),
            "user_address": ["0xuser1", "0xuser2"],
            "taker_address": ["0xtaker1", "0xtaker2"],
            "tx_hash": ["0xtx1", "0xtx2"],
            "block_number": [100, 200],
        }
    )
    pq.write_table(table, trades_dir / f"{MARKET_SLUG}.parquet")


def write_valid_orderbooks(data_dir: Path) -> None:
    ob_dir = data_dir / "orderbook_snapshots"
    ob_dir.mkdir(exist_ok=True)
    table = pa.table(
        {
            "market_id": [MARKET_ID],
            "token_id": [YES_TOKEN],
            "timestamp": pa.array(
                [datetime(2025, 10, 16, tzinfo=UTC)],
                type=pa.timestamp("ms", tz="UTC"),
            ),
            "bids": [json.dumps([{"price": 0.48, "size": 100}])],
            "asks": [json.dumps([{"price": 0.50, "size": 100}])],
            "best_bid": [0.48],
            "best_ask": [0.50],
            "spread": [0.02],
            "mid_price": [0.49],
            "bid_depth": pa.array([1], type=pa.int32()),
            "ask_depth": pa.array([1], type=pa.int32()),
            "total_bid_size": [100.0],
            "total_ask_size": [100.0],
            "book_hash": ["hash1"],
        }
    )
    pq.write_table(table, ob_dir / f"{MARKET_SLUG}.parquet")


def write_valid_prices(data_dir: Path) -> None:
    prices_dir = data_dir / "prices"
    prices_dir.mkdir(exist_ok=True)
    table = pa.table(
        {
            "market_id": [MARKET_ID],
            "token_id": [YES_TOKEN],
            "timestamp": pa.array(
                [datetime(2025, 10, 16, tzinfo=UTC)],
                type=pa.timestamp("s", tz="UTC"),
            ),
            "mid_price": [0.55],
        }
    )
    pq.write_table(table, prices_dir / f"{MARKET_SLUG}.parquet")


def write_all_valid(data_dir: Path) -> None:
    write_valid_markets(data_dir)
    write_valid_trades(data_dir)
    write_valid_orderbooks(data_dir)
    write_valid_prices(data_dir)


# ---------------------------------------------------------------------------
# Schema checks
# ---------------------------------------------------------------------------


class TestSchemaChecks:
    def test_valid_data_no_issues(self, tmp_path: Path) -> None:
        write_all_valid(tmp_path)
        issues = check_schemas(tmp_path)
        assert not any(i.level == "error" for i in issues)

    def test_missing_markets_file(self, tmp_path: Path) -> None:
        issues = check_schemas(tmp_path)
        assert any(i.level == "error" and "markets.parquet not found" in i.message for i in issues)

    def test_missing_column(self, tmp_path: Path) -> None:
        # Write markets with a missing column
        table = pa.table({"market_id": ["x"], "question": ["q"]})
        pq.write_table(table, tmp_path / "markets.parquet")
        issues = check_schemas(tmp_path)
        missing = [i for i in issues if i.level == "error" and "missing required" in i.message]
        assert len(missing) > 0

    def test_wrong_type(self, tmp_path: Path) -> None:
        write_all_valid(tmp_path)
        # Overwrite trades with wrong type for price (int instead of float)
        trades_dir = tmp_path / "trades"
        table = pa.table(
            {
                "trade_id": ["t1"],
                "market_id": [MARKET_ID],
                "token_id": [YES_TOKEN],
                "token_label": ["Yes"],
                "side": ["BUY"],
                "price": pa.array([1], type=pa.int64()),  # wrong type
                "size": [10.0],
                "timestamp": pa.array(
                    [datetime(2025, 10, 16, tzinfo=UTC)],
                    type=pa.timestamp("s", tz="UTC"),
                ),
                "user_address": ["0x1"],
                "taker_address": ["0x2"],
                "tx_hash": ["0xtx"],
                "block_number": [100],
            }
        )
        pq.write_table(table, trades_dir / f"{MARKET_SLUG}.parquet")
        issues = check_schemas(tmp_path)
        type_errors = [i for i in issues if "type" in i.message and i.level == "error"]
        assert len(type_errors) > 0

    def test_extra_column_warns(self, tmp_path: Path) -> None:
        write_all_valid(tmp_path)
        # Add an extra column to markets
        markets = pq.read_table(tmp_path / "markets.parquet")
        extra = markets.append_column("bonus", pa.array(["x"]))
        pq.write_table(extra, tmp_path / "markets.parquet")
        issues = check_schemas(tmp_path)
        warnings = [i for i in issues if i.level == "warning" and "unexpected" in i.message]
        assert len(warnings) == 1


# ---------------------------------------------------------------------------
# Referential integrity
# ---------------------------------------------------------------------------


class TestReferentialIntegrity:
    def test_valid_refs(self, tmp_path: Path) -> None:
        write_all_valid(tmp_path)
        issues = check_referential_integrity(tmp_path)
        errors = [i for i in issues if i.level == "error"]
        assert len(errors) == 0

    def test_orphan_market_id(self, tmp_path: Path) -> None:
        write_all_valid(tmp_path)
        # Add a trade with an unknown market_id
        trades_dir = tmp_path / "trades"
        table = pa.table(
            {
                "trade_id": ["t_orphan"],
                "market_id": ["0xNONEXISTENT"],
                "token_id": ["999"],
                "token_label": ["Yes"],
                "side": ["BUY"],
                "price": [0.5],
                "size": [1.0],
                "timestamp": pa.array(
                    [datetime(2025, 10, 16, tzinfo=UTC)],
                    type=pa.timestamp("s", tz="UTC"),
                ),
                "user_address": ["0x1"],
                "taker_address": ["0x2"],
                "tx_hash": ["0xtx"],
                "block_number": [100],
            }
        )
        pq.write_table(table, trades_dir / "orphan.parquet")
        issues = check_referential_integrity(tmp_path)
        errors = [i for i in issues if i.level == "error" and "market_id" in i.message]
        assert len(errors) >= 1

    def test_orphan_token_id(self, tmp_path: Path) -> None:
        write_all_valid(tmp_path)
        trades_dir = tmp_path / "trades"
        table = pa.table(
            {
                "trade_id": ["t_bad_token"],
                "market_id": [MARKET_ID],
                "token_id": ["999999"],  # not a valid token
                "token_label": ["Yes"],
                "side": ["BUY"],
                "price": [0.5],
                "size": [1.0],
                "timestamp": pa.array(
                    [datetime(2025, 10, 16, tzinfo=UTC)],
                    type=pa.timestamp("s", tz="UTC"),
                ),
                "user_address": ["0x1"],
                "taker_address": ["0x2"],
                "tx_hash": ["0xtx"],
                "block_number": [100],
            }
        )
        pq.write_table(table, trades_dir / "bad-token.parquet")
        issues = check_referential_integrity(tmp_path)
        token_issues = [i for i in issues if "token_id" in i.message]
        assert len(token_issues) >= 1


# ---------------------------------------------------------------------------
# Temporal consistency
# ---------------------------------------------------------------------------


class TestTemporalConsistency:
    def test_valid_timestamps(self, tmp_path: Path) -> None:
        write_all_valid(tmp_path)
        issues = check_temporal_consistency(tmp_path)
        errors = [i for i in issues if i.level == "error"]
        assert len(errors) == 0

    def test_trade_before_dome_epoch(self, tmp_path: Path) -> None:
        write_all_valid(tmp_path)
        trades_dir = tmp_path / "trades"
        table = pa.table(
            {
                "trade_id": ["old"],
                "market_id": [MARKET_ID],
                "token_id": [YES_TOKEN],
                "token_label": ["Yes"],
                "side": ["BUY"],
                "price": [0.5],
                "size": [1.0],
                "timestamp": pa.array(
                    [datetime(2020, 1, 1, tzinfo=UTC)],
                    type=pa.timestamp("s", tz="UTC"),
                ),
                "user_address": ["0x1"],
                "taker_address": ["0x2"],
                "tx_hash": ["0xtx"],
                "block_number": [1],
            }
        )
        pq.write_table(table, trades_dir / "old-trade.parquet")
        issues = check_temporal_consistency(tmp_path)
        errors = [i for i in issues if i.level == "error" and "before Dome epoch" in i.message]
        assert len(errors) >= 1

    def test_future_timestamp(self, tmp_path: Path) -> None:
        write_all_valid(tmp_path)
        trades_dir = tmp_path / "trades"
        table = pa.table(
            {
                "trade_id": ["future"],
                "market_id": [MARKET_ID],
                "token_id": [YES_TOKEN],
                "token_label": ["Yes"],
                "side": ["BUY"],
                "price": [0.5],
                "size": [1.0],
                "timestamp": pa.array(
                    [datetime(2030, 1, 1, tzinfo=UTC)],
                    type=pa.timestamp("s", tz="UTC"),
                ),
                "user_address": ["0x1"],
                "taker_address": ["0x2"],
                "tx_hash": ["0xtx"],
                "block_number": [1],
            }
        )
        pq.write_table(table, trades_dir / "future-trade.parquet")
        issues = check_temporal_consistency(tmp_path)
        errors = [i for i in issues if i.level == "error" and "future" in i.message]
        assert len(errors) >= 1


# ---------------------------------------------------------------------------
# Value ranges
# ---------------------------------------------------------------------------


class TestValueRanges:
    def test_valid_values(self, tmp_path: Path) -> None:
        write_all_valid(tmp_path)
        issues = check_value_ranges(tmp_path)
        errors = [i for i in issues if i.level == "error"]
        assert len(errors) == 0

    def test_price_above_1(self, tmp_path: Path) -> None:
        write_all_valid(tmp_path)
        trades_dir = tmp_path / "trades"
        table = pa.table(
            {
                "trade_id": ["bad"],
                "market_id": [MARKET_ID],
                "token_id": [YES_TOKEN],
                "token_label": ["Yes"],
                "side": ["BUY"],
                "price": [1.5],
                "size": [10.0],
                "timestamp": pa.array(
                    [datetime(2025, 10, 16, tzinfo=UTC)],
                    type=pa.timestamp("s", tz="UTC"),
                ),
                "user_address": ["0x1"],
                "taker_address": ["0x2"],
                "tx_hash": ["0xtx"],
                "block_number": [1],
            }
        )
        pq.write_table(table, trades_dir / "bad-price.parquet")
        issues = check_value_ranges(tmp_path)
        errors = [i for i in issues if i.level == "error" and "above 1" in i.message]
        assert len(errors) >= 1

    def test_negative_size(self, tmp_path: Path) -> None:
        write_all_valid(tmp_path)
        trades_dir = tmp_path / "trades"
        table = pa.table(
            {
                "trade_id": ["neg"],
                "market_id": [MARKET_ID],
                "token_id": [YES_TOKEN],
                "token_label": ["Yes"],
                "side": ["BUY"],
                "price": [0.5],
                "size": [-1.0],
                "timestamp": pa.array(
                    [datetime(2025, 10, 16, tzinfo=UTC)],
                    type=pa.timestamp("s", tz="UTC"),
                ),
                "user_address": ["0x1"],
                "taker_address": ["0x2"],
                "tx_hash": ["0xtx"],
                "block_number": [1],
            }
        )
        pq.write_table(table, trades_dir / "neg-size.parquet")
        issues = check_value_ranges(tmp_path)
        errors = [i for i in issues if i.level == "error" and "non-positive" in i.message]
        assert len(errors) >= 1

    def test_crossed_book(self, tmp_path: Path) -> None:
        write_all_valid(tmp_path)
        ob_dir = tmp_path / "orderbook_snapshots"
        table = pa.table(
            {
                "market_id": [MARKET_ID],
                "token_id": [YES_TOKEN],
                "timestamp": pa.array(
                    [datetime(2025, 10, 16, tzinfo=UTC)],
                    type=pa.timestamp("ms", tz="UTC"),
                ),
                "bids": [json.dumps([])],
                "asks": [json.dumps([])],
                "best_bid": [0.60],  # bid > ask = crossed
                "best_ask": [0.50],
                "spread": [-0.10],
                "mid_price": [0.55],
                "bid_depth": pa.array([0], type=pa.int32()),
                "ask_depth": pa.array([0], type=pa.int32()),
                "total_bid_size": [0.0],
                "total_ask_size": [0.0],
                "book_hash": ["crossed"],
            }
        )
        pq.write_table(table, ob_dir / "crossed.parquet")
        issues = check_value_ranges(tmp_path)
        errors = [i for i in issues if i.level == "error" and "crossed" in i.message]
        assert len(errors) >= 1


# ---------------------------------------------------------------------------
# Completeness
# ---------------------------------------------------------------------------


class TestCompleteness:
    def test_stats(self, tmp_path: Path) -> None:
        write_all_valid(tmp_path)
        issues, stats = check_completeness(tmp_path)
        assert stats["total_markets"] == 1
        assert stats["markets_with_trades"] == 1
        assert stats["markets_with_orderbooks"] == 1
        assert stats["markets_with_prices"] == 1
        assert stats["total_trade_rows"] == 2

    def test_trades_without_orderbooks(self, tmp_path: Path) -> None:
        write_valid_markets(tmp_path)
        write_valid_trades(tmp_path)
        # No orderbooks or prices
        issues, _ = check_completeness(tmp_path)
        info = [i for i in issues if i.level == "info" and "no orderbook" in i.message]
        assert len(info) == 1


# ---------------------------------------------------------------------------
# Duplicates
# ---------------------------------------------------------------------------


class TestDuplicates:
    def test_no_duplicates(self, tmp_path: Path) -> None:
        write_all_valid(tmp_path)
        issues = check_duplicates(tmp_path)
        assert len(issues) == 0

    def test_duplicate_trade_ids(self, tmp_path: Path) -> None:
        trades_dir = tmp_path / "trades"
        trades_dir.mkdir(parents=True)
        table = pa.table(
            {
                "trade_id": ["t1", "t1"],  # duplicate
                "market_id": [MARKET_ID, MARKET_ID],
                "token_id": [YES_TOKEN, YES_TOKEN],
                "token_label": ["Yes", "Yes"],
                "side": ["BUY", "BUY"],
                "price": [0.5, 0.5],
                "size": [10.0, 10.0],
                "timestamp": pa.array(
                    [
                        datetime(2025, 10, 16, tzinfo=UTC),
                        datetime(2025, 10, 16, tzinfo=UTC),
                    ],
                    type=pa.timestamp("s", tz="UTC"),
                ),
                "user_address": ["0x1", "0x1"],
                "taker_address": ["0x2", "0x2"],
                "tx_hash": ["0xtx", "0xtx"],
                "block_number": [100, 100],
            }
        )
        pq.write_table(table, trades_dir / "dupes.parquet")
        issues = check_duplicates(tmp_path)
        errors = [i for i in issues if i.level == "error" and "duplicate trade_id" in i.message]
        assert len(errors) == 1

    def test_duplicate_orderbook_snapshots(self, tmp_path: Path) -> None:
        ob_dir = tmp_path / "orderbook_snapshots"
        ob_dir.mkdir(parents=True)
        ts = datetime(2025, 10, 16, tzinfo=UTC)
        table = pa.table(
            {
                "market_id": [MARKET_ID, MARKET_ID],
                "token_id": [YES_TOKEN, YES_TOKEN],
                "timestamp": pa.array([ts, ts], type=pa.timestamp("ms", tz="UTC")),
                "bids": ["[]", "[]"],
                "asks": ["[]", "[]"],
                "best_bid": [0.48, 0.48],
                "best_ask": [0.50, 0.50],
                "spread": [0.02, 0.02],
                "mid_price": [0.49, 0.49],
                "bid_depth": pa.array([0, 0], type=pa.int32()),
                "ask_depth": pa.array([0, 0], type=pa.int32()),
                "total_bid_size": [0.0, 0.0],
                "total_ask_size": [0.0, 0.0],
                "book_hash": ["same_hash", "same_hash"],  # duplicate
            }
        )
        pq.write_table(table, ob_dir / "dupes.parquet")
        issues = check_duplicates(tmp_path)
        errors = [i for i in issues if i.level == "error" and "duplicate" in i.message]
        assert len(errors) == 1


# ---------------------------------------------------------------------------
# Cross-source validation
# ---------------------------------------------------------------------------


class TestCrossSource:
    def test_aligned_data_no_issues(self, tmp_path: Path) -> None:
        write_all_valid(tmp_path)
        issues = check_cross_source(tmp_path)
        warnings = [i for i in issues if i.level == "warning"]
        assert len(warnings) == 0

    def test_non_overlapping_prices(self, tmp_path: Path) -> None:
        write_all_valid(tmp_path)
        # Overwrite prices with values outside trade range
        prices_dir = tmp_path / "prices"
        table = pa.table(
            {
                "market_id": [MARKET_ID],
                "token_id": [YES_TOKEN],
                "timestamp": pa.array(
                    [datetime(2025, 10, 16, tzinfo=UTC)],
                    type=pa.timestamp("s", tz="UTC"),
                ),
                "mid_price": [0.01],  # trades are at 0.45-0.55, this doesn't overlap
            }
        )
        pq.write_table(table, prices_dir / f"{MARKET_SLUG}.parquet")
        # Overwrite trades to be far from price
        trades_dir = tmp_path / "trades"
        table = pa.table(
            {
                "trade_id": ["t1"],
                "market_id": [MARKET_ID],
                "token_id": [YES_TOKEN],
                "token_label": ["Yes"],
                "side": ["BUY"],
                "price": [0.90],
                "size": [10.0],
                "timestamp": pa.array(
                    [datetime(2025, 10, 16, tzinfo=UTC)],
                    type=pa.timestamp("s", tz="UTC"),
                ),
                "user_address": ["0x1"],
                "taker_address": ["0x2"],
                "tx_hash": ["0xtx"],
                "block_number": [100],
            }
        )
        pq.write_table(table, trades_dir / f"{MARKET_SLUG}.parquet")
        issues = check_cross_source(tmp_path)
        warnings = [i for i in issues if "don't overlap" in i.message]
        assert len(warnings) == 1

    def test_no_prices_dir_no_crash(self, tmp_path: Path) -> None:
        write_valid_markets(tmp_path)
        write_valid_trades(tmp_path)
        issues = check_cross_source(tmp_path)
        assert issues == []


# ---------------------------------------------------------------------------
# run_all_checks integration
# ---------------------------------------------------------------------------


class TestRunAllChecks:
    def test_valid_data_passes(self, tmp_path: Path) -> None:
        write_all_valid(tmp_path)
        issues, stats = run_all_checks(tmp_path)
        errors = [i for i in issues if i.level == "error"]
        assert len(errors) == 0
        assert stats["total_markets"] == 1
        assert "file_sizes" in stats

    def test_empty_dir_reports_errors(self, tmp_path: Path) -> None:
        issues, stats = run_all_checks(tmp_path)
        errors = [i for i in issues if i.level == "error"]
        assert len(errors) > 0
