"""Shared fixtures: synthetic Parquet data for query tests."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from papermarkets.queries.connection import get_connection


@pytest.fixture()
def data_dir(tmp_path: Path) -> Path:
    """Create a tmp directory with synthetic Parquet files matching the V0 schema."""
    _write_markets(tmp_path)
    _write_trades(tmp_path)
    _write_orderbooks(tmp_path)
    _write_prices(tmp_path)
    return tmp_path


@pytest.fixture()
def con(data_dir: Path):
    return get_connection(data_dir)


# ── helpers ──────────────────────────────────────────────────────────────


def _ts(year: int, month: int, day: int, hour: int = 0) -> datetime:
    return datetime(year, month, day, hour, tzinfo=UTC)


def _write_markets(root: Path):
    table = pa.table(
        {
            "market_id": ["cond_aaa", "cond_bbb", "cond_ccc"],
            "question": [
                "Will BTC hit 100k?",
                "Will ETH hit 10k?",
                "Will SOL hit 500?",
            ],
            "description": ["BTC question", "ETH question", "SOL question"],
            "slug": ["btc-100k", "eth-10k", "sol-500"],
            "category": ["crypto", "crypto", "crypto"],
            "tags": ['["btc"]', '["eth"]', '["sol"]'],
            "outcome_yes_token_id": ["tok_a_yes", "tok_b_yes", "tok_c_yes"],
            "outcome_no_token_id": ["tok_a_no", "tok_b_no", "tok_c_no"],
            "neg_risk": [False, False, False],
            "volume_total": [500_000.0, 200_000.0, 50_000.0],
            "liquidity": [10_000.0, 5_000.0, 1_000.0],
            "created_at": pa.array(
                [_ts(2025, 10, 15), _ts(2025, 10, 20), _ts(2025, 11, 1)],
                type=pa.timestamp("ms", tz="UTC"),
            ),
            "end_date": pa.array(
                [_ts(2025, 12, 31), _ts(2026, 6, 30), None],
                type=pa.timestamp("ms", tz="UTC"),
            ),
            "resolved_at": pa.array(
                [_ts(2026, 1, 1), _ts(2026, 1, 15), None],
                type=pa.timestamp("ms", tz="UTC"),
            ),
            "resolution_outcome": ["Yes", "No", None],
            "active": [False, False, True],
            "closed": [True, True, False],
            "event_slug": ["btc-milestones", "eth-milestones", "sol-milestones"],
            "spread": [0.02, 0.03, 0.05],
            "best_bid": [0.65, 0.20, 0.10],
            "best_ask": [0.67, 0.23, 0.15],
        }
    )
    pq.write_table(table, root / "markets.parquet")


def _write_trades(root: Path):
    trades_dir = root / "trades"
    trades_dir.mkdir()

    # BTC market trades — two wallets
    table = pa.table(
        {
            "trade_id": ["t1", "t2", "t3", "t4"],
            "market_id": ["cond_aaa"] * 4,
            "token_id": ["tok_a_yes", "tok_a_yes", "tok_a_no", "tok_a_yes"],
            "token_label": ["Yes", "Yes", "No", "Yes"],
            "side": ["BUY", "BUY", "BUY", "SELL"],
            "price": [0.60, 0.65, 0.35, 0.70],
            "size": [100.0, 200.0, 150.0, 50.0],
            "timestamp": pa.array(
                [_ts(2025, 10, 20), _ts(2025, 11, 1), _ts(2025, 11, 5), _ts(2025, 11, 10)],
                type=pa.timestamp("s", tz="UTC"),
            ),
            "user_address": ["wallet_alice", "wallet_bob", "wallet_alice", "wallet_alice"],
            "taker_address": ["taker1", "taker2", "taker3", "taker4"],
            "tx_hash": ["0xaa1", "0xaa2", "0xaa3", "0xaa4"],
            "block_number": [100, 200, 300, 400],
        }
    )
    pq.write_table(table, trades_dir / "btc-100k.parquet")

    # ETH market trades
    table = pa.table(
        {
            "trade_id": ["t5", "t6"],
            "market_id": ["cond_bbb"] * 2,
            "token_id": ["tok_b_yes", "tok_b_no"],
            "token_label": ["Yes", "No"],
            "side": ["BUY", "BUY"],
            "price": [0.20, 0.80],
            "size": [500.0, 300.0],
            "timestamp": pa.array(
                [_ts(2025, 10, 25), _ts(2025, 11, 1)],
                type=pa.timestamp("s", tz="UTC"),
            ),
            "user_address": ["wallet_alice", "wallet_bob"],
            "taker_address": ["taker5", "taker6"],
            "tx_hash": ["0xbb1", "0xbb2"],
            "block_number": [500, 600],
        }
    )
    pq.write_table(table, trades_dir / "eth-10k.parquet")


def _write_orderbooks(root: Path):
    ob_dir = root / "orderbook_snapshots"
    ob_dir.mkdir()

    bids = json.dumps([{"price": 0.64, "size": 100}, {"price": 0.63, "size": 200}])
    asks = json.dumps([{"price": 0.66, "size": 150}, {"price": 0.67, "size": 250}])

    table = pa.table(
        {
            "market_id": ["cond_aaa", "cond_aaa", "cond_aaa"],
            "token_id": ["tok_a_yes", "tok_a_yes", "tok_a_yes"],
            "timestamp": pa.array(
                [_ts(2025, 10, 20, 0), _ts(2025, 11, 1, 0), _ts(2025, 11, 5, 12)],
                type=pa.timestamp("ms", tz="UTC"),
            ),
            "bids": [bids, bids, bids],
            "asks": [asks, asks, asks],
            "best_bid": [0.64, 0.64, 0.64],
            "best_ask": [0.66, 0.66, 0.66],
            "spread": [0.02, 0.02, 0.02],
            "mid_price": [0.65, 0.65, 0.65],
            "bid_depth": pa.array([2, 2, 2], type=pa.int32()),
            "ask_depth": pa.array([2, 2, 2], type=pa.int32()),
            "total_bid_size": [300.0, 300.0, 300.0],
            "total_ask_size": [400.0, 400.0, 400.0],
            "book_hash": ["hash1", "hash2", "hash3"],
        }
    )
    pq.write_table(table, ob_dir / "btc-100k.parquet")


def _write_prices(root: Path):
    prices_dir = root / "prices"
    prices_dir.mkdir()

    table = pa.table(
        {
            "market_id": ["cond_aaa"] * 5 + ["cond_bbb"] * 3,
            "token_id": ["tok_a_yes"] * 5 + ["tok_b_yes"] * 3,
            "timestamp": pa.array(
                [
                    _ts(2025, 10, 20),
                    _ts(2025, 10, 25),
                    _ts(2025, 11, 1),
                    _ts(2025, 11, 10),
                    _ts(2025, 12, 30),
                    _ts(2025, 10, 25),
                    _ts(2025, 11, 1),
                    _ts(2026, 1, 10),
                ],
                type=pa.timestamp("s", tz="UTC"),
            ),
            "mid_price": [0.55, 0.60, 0.65, 0.70, 0.90, 0.15, 0.20, 0.18],
        }
    )
    pq.write_table(table, prices_dir / "btc-100k.parquet")
