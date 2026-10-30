"""Tests for V0 analytics queries."""

import pytest

from papermarkets.queries.v0_queries import daily_volume, market_accuracy, price_chart


class TestPriceChart:
    def test_returns_prices_for_market(self, con):
        rows = price_chart(con, "btc-100k").fetchall()
        assert len(rows) == 5

    def test_returns_empty_for_unknown_slug(self, con):
        rows = price_chart(con, "nonexistent").fetchall()
        assert len(rows) == 0

    def test_ordered_by_timestamp(self, con):
        rows = price_chart(con, "btc-100k").fetchall()
        timestamps = [row[0] for row in rows]
        assert timestamps == sorted(timestamps)


class TestDailyVolume:
    def test_returns_daily_aggregates(self, con):
        rows = daily_volume(con).fetchall()
        assert len(rows) >= 1
        for row in rows:
            assert row[1] > 0  # volume
            assert row[2] > 0  # trade_count


class TestMarketAccuracy:
    def test_returns_resolved_markets(self, con):
        rows = market_accuracy(con).fetchall()
        assert len(rows) == 2

    def test_prediction_error_is_nonnegative(self, con):
        # Columns: market_id, question, resolution_outcome, final_price,
        #          outcome_value, prediction_error
        rows = market_accuracy(con).fetchall()
        for row in rows:
            assert row[5] >= 0  # prediction_error

    def test_outcome_value_matches_resolution(self, con):
        rows = market_accuracy(con).fetchall()
        by_id = {row[0]: row for row in rows}
        btc = by_id["cond_aaa"]
        assert btc[4] == pytest.approx(1.0)  # outcome_value for Yes
        eth = by_id["cond_bbb"]
        assert eth[4] == pytest.approx(0.0)  # outcome_value for No
