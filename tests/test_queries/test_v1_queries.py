"""Tests for V1 copy-trading queries."""

from datetime import UTC, datetime

import pytest

from papermarkets.queries.v1_queries import (
    orderbook_at_time,
    top_wallets,
    trades_by_wallet,
    trades_by_wallet_market,
    wallet_pnl,
)


class TestTradesByWallet:
    def test_returns_all_trades_for_wallet(self, con):
        rows = trades_by_wallet(con, "wallet_alice").fetchall()
        assert len(rows) == 4  # t1, t3, t4 in btc + t5 in eth

    def test_returns_empty_for_unknown_wallet(self, con):
        rows = trades_by_wallet(con, "wallet_nobody").fetchall()
        assert len(rows) == 0

    def test_filters_by_start(self, con):
        start = datetime(2025, 11, 1, tzinfo=UTC)
        rows = trades_by_wallet(con, "wallet_alice", start=start).fetchall()
        # alice: t1(Oct20), t3(Nov5), t4(Nov10), t5(Oct25)
        # >= Nov 1: t3, t4
        assert len(rows) == 2

    def test_filters_by_end(self, con):
        end = datetime(2025, 10, 31, tzinfo=UTC)
        rows = trades_by_wallet(con, "wallet_alice", end=end).fetchall()
        # alice: t1(Oct20), t5(Oct25) — both <= Oct 31
        assert len(rows) == 2

    def test_ordered_by_timestamp(self, con):
        rows = trades_by_wallet(con, "wallet_alice").fetchall()
        # timestamp is column index 7 in the trades schema
        timestamps = [row[7] for row in rows]
        assert timestamps == sorted(timestamps)


class TestTradesByWalletMarket:
    def test_filters_to_specific_market(self, con):
        rows = trades_by_wallet_market(con, "wallet_alice", "btc-100k").fetchall()
        # alice in btc: t1, t3, t4
        assert len(rows) == 3

    def test_returns_empty_for_wrong_market(self, con):
        rows = trades_by_wallet_market(con, "wallet_alice", "sol-500").fetchall()
        assert len(rows) == 0


class TestOrderbookAtTime:
    def test_returns_nearest_snapshot(self, con):
        ts = datetime(2025, 11, 2, tzinfo=UTC)
        rows = orderbook_at_time(con, "btc-100k", ts).fetchall()
        assert len(rows) == 1

    def test_returns_empty_for_market_without_books(self, con):
        ts = datetime(2025, 11, 1, tzinfo=UTC)
        rows = orderbook_at_time(con, "eth-10k", ts).fetchall()
        assert len(rows) == 0


class TestWalletPnl:
    def test_pnl_for_resolved_markets(self, con):
        # Columns: market_id, question, resolution_outcome, token_label,
        #          net_shares, cost_basis, payout, pnl
        rows = wallet_pnl(con, "wallet_alice").fetchall()
        # alice traded btc (resolved Yes) and eth (resolved No)
        # btc Yes: bought 100 @ 0.60, sold 50 @ 0.70 => net 50, cost = 60 - 35 = 25
        #   payout = 50, pnl = 25
        # btc No: bought 150 @ 0.35 => net 150, cost = 52.5
        #   payout = 0, pnl = -52.5
        # eth Yes: bought 500 @ 0.20 => net 500, cost = 100
        #   payout = 0, pnl = -100
        assert len(rows) == 3
        total_pnl = sum(row[7] for row in rows)  # pnl column
        assert total_pnl == pytest.approx(25.0 - 52.5 - 100.0)

    def test_pnl_empty_for_unknown_wallet(self, con):
        rows = wallet_pnl(con, "wallet_nobody").fetchall()
        assert len(rows) == 0


class TestTopWallets:
    def test_by_volume(self, con):
        rows = top_wallets(con, "volume", 10).fetchall()
        assert len(rows) == 2  # alice and bob

    def test_by_trade_count(self, con):
        rows = top_wallets(con, "trade_count", 10).fetchall()
        assert len(rows) == 2

    def test_by_pnl(self, con):
        rows = top_wallets(con, "pnl", 10).fetchall()
        assert len(rows) >= 1

    def test_invalid_metric_raises(self, con):
        with pytest.raises(ValueError, match="Unknown metric"):
            top_wallets(con, "invalid")
