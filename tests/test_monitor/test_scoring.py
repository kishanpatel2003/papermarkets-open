"""Tests for the suspicion scoring engine."""

import pytest

from papermarkets.monitor.scoring import (
    SuspicionScore,
    compute_suspicion,
    score_concentration,
    score_conviction,
    score_coordination,
    score_freshness,
    score_position_size,
    score_price_impact,
)
from papermarkets.monitor.state import WalletProfile


def _wallet(total_trades: int = 100) -> WalletProfile:
    return WalletProfile(
        eoa="0xabc",
        proxy="0xdef",
        handle="test",
        pseudonym="tester",
        total_volume=1000.0,
        total_trades=total_trades,
        total_markets=5,
    )


# --- score_freshness ---


class TestScoreFreshness:
    def test_none_returns_max(self):
        assert score_freshness(None) == 1.0

    def test_very_new_wallet(self):
        assert score_freshness(_wallet(total_trades=5)) == 0.8

    def test_boundary_10(self):
        assert score_freshness(_wallet(total_trades=9)) == 0.8
        assert score_freshness(_wallet(total_trades=10)) == 0.5

    def test_moderate_wallet(self):
        assert score_freshness(_wallet(total_trades=49)) == 0.5
        assert score_freshness(_wallet(total_trades=50)) == 0.2

    def test_established_wallet(self):
        assert score_freshness(_wallet(total_trades=199)) == 0.2
        assert score_freshness(_wallet(total_trades=200)) == 0.0

    def test_veteran_wallet(self):
        assert score_freshness(_wallet(total_trades=10000)) == 0.0


# --- score_position_size ---


class TestScorePositionSize:
    def test_small_notional_returns_zero(self):
        assert score_position_size(5.0, 1.0, None) == 0.0

    def test_none_book_returns_default(self):
        assert score_position_size(100.0, 0.50, None) == 0.5

    def test_empty_book(self):
        assert score_position_size(100.0, 0.50, {"bids": [], "asks": []}) == 0.5

    def test_large_trade_vs_thin_book(self):
        book = {"bids": [[0.50, 20.0]], "asks": [[0.51, 20.0]]}
        # notional = 100 * 0.50 = 50, depth near 0.50 = 20*0.50 + 20*0.51 = 20.2
        # ratio = 50/20.2 ≈ 2.47 -> >0.50 -> 1.0
        assert score_position_size(100.0, 0.50, book) == 1.0

    def test_moderate_trade(self):
        book = {"bids": [[0.50, 200.0]], "asks": [[0.51, 200.0]]}
        # notional = 50*0.50=25, depth = 200*0.50 + 200*0.51 = 202
        # ratio = 25/202 ≈ 0.124 -> >0.10 -> 0.4
        assert score_position_size(50.0, 0.50, book) == 0.4

    def test_tiny_trade_in_deep_book(self):
        book = {"bids": [[0.50, 5000.0]], "asks": [[0.51, 5000.0]]}
        # notional = 10*0.50=5 < 10 -> 0.0
        assert score_position_size(10.0, 0.50, book) == 0.0


# --- score_conviction ---


class TestScoreConviction:
    def test_sell_side_returns_zero(self):
        assert score_conviction(0.03, 0.03, "SELL") == 0.0

    def test_buy_very_low_prob(self):
        assert score_conviction(0.03, 0.03, "BUY") == 1.0

    def test_buy_low_prob(self):
        assert score_conviction(0.08, 0.08, "BUY") == 0.8

    def test_buy_moderate_prob(self):
        assert score_conviction(0.15, 0.15, "BUY") == 0.5

    def test_buy_high_prob_returns_zero(self):
        assert score_conviction(0.50, 0.50, "BUY") == 0.0

    def test_overpay_multiplier(self):
        # mid=0.03, trade=0.06 -> overpay=2.0, base=1.0 -> 2.0
        assert score_conviction(0.06, 0.03, "BUY") == 2.0

    def test_overpay_capped_at_two(self):
        # mid=0.03, trade=0.12 -> overpay=min(4.0, 2.0)=2.0, base=1.0 -> 2.0
        assert score_conviction(0.12, 0.03, "BUY") == 2.0

    def test_case_insensitive_side(self):
        assert score_conviction(0.03, 0.03, "buy") == 1.0
        assert score_conviction(0.03, 0.03, "sell") == 0.0

    def test_zero_mid_price_no_overpay(self):
        # mid=0, trade=0.05 -> base=1.0, no overpay (guard against division by zero)
        assert score_conviction(0.05, 0.0, "BUY") == 1.0


# --- score_concentration ---


class TestScoreConcentration:
    def test_empty_positions(self):
        assert score_concentration({}) == 0.0

    def test_single_outcome(self):
        assert score_concentration({"token_a": 500.0}) == 1.0

    def test_dominant_outcome(self):
        assert score_concentration({"a": 900.0, "b": 100.0}) == 0.7

    def test_moderately_concentrated(self):
        assert score_concentration({"a": 700.0, "b": 300.0}) == 0.4

    def test_spread_across_three(self):
        assert score_concentration({"a": 40.0, "b": 35.0, "c": 25.0}) == 0.0

    def test_two_even_split(self):
        # 50/50 across 2 outcomes: max_share=0.5, n_outcomes=2 -> 0.2
        assert score_concentration({"a": 50.0, "b": 50.0}) == 0.2


# --- score_coordination ---


class TestScoreCoordination:
    def test_no_trades(self):
        assert score_coordination("token_a", []) == 0.0

    def test_single_fresh_wallet(self):
        windows = [(100, "0xaaa", 0.8)]
        assert score_coordination("token_a", windows) == 0.0

    def test_two_fresh_wallets(self):
        windows = [(100, "0xaaa", 0.8), (110, "0xbbb", 0.9)]
        assert score_coordination("token_a", windows) == 0.3

    def test_three_fresh_wallets(self):
        windows = [
            (100, "0xaaa", 0.8),
            (110, "0xbbb", 0.9),
            (120, "0xccc", 0.6),
        ]
        assert score_coordination("token_a", windows) == 0.7

    def test_five_fresh_wallets(self):
        windows = [(i, f"0x{i}", 0.8) for i in range(5)]
        assert score_coordination("token_a", windows) == 1.0

    def test_stale_wallets_ignored(self):
        windows = [(i, f"0x{i}", 0.3) for i in range(10)]
        assert score_coordination("token_a", windows) == 0.0

    def test_duplicate_wallets_deduplicated(self):
        windows = [(100, "0xaaa", 0.8), (110, "0xaaa", 0.9), (120, "0xbbb", 0.6)]
        assert score_coordination("token_a", windows) == 0.3


# --- score_price_impact ---


class TestScorePriceImpact:
    def test_no_movement(self):
        assert score_price_impact(0.50, 0.50, "BUY") == 0.0

    def test_small_buy_impact(self):
        assert score_price_impact(0.50, 0.52, "BUY") == 0.3

    def test_medium_buy_impact(self):
        assert score_price_impact(0.50, 0.54, "BUY") == 0.7

    def test_large_buy_impact(self):
        assert score_price_impact(0.50, 0.56, "BUY") == 1.0

    def test_sell_impact(self):
        assert score_price_impact(0.50, 0.46, "SELL") == 0.7

    def test_negative_impact_buy(self):
        # price went down on a buy — no positive impact
        assert score_price_impact(0.50, 0.48, "BUY") == 0.0


# --- compute_suspicion ---


class TestComputeSuspicion:
    def test_all_zeros(self):
        signals = {
            k: 0.0
            for k in [
                "freshness",
                "position_size",
                "conviction",
                "concentration",
                "coordination",
                "price_impact",
            ]
        }
        result = compute_suspicion(signals)
        assert isinstance(result, SuspicionScore)
        assert result.composite == 0.0

    def test_all_max(self):
        signals = {
            k: 1.0
            for k in [
                "freshness",
                "position_size",
                "conviction",
                "concentration",
                "coordination",
                "price_impact",
            ]
        }
        result = compute_suspicion(signals)
        assert result.composite == pytest.approx(1.0)

    def test_weighted_correctly(self):
        signals = {"freshness": 1.0, "conviction": 1.0}
        result = compute_suspicion(signals)
        assert result.composite == pytest.approx(0.20 + 0.25)
        assert result.position_size == 0.0

    def test_missing_signals_default_zero(self):
        result = compute_suspicion({})
        assert result.composite == 0.0
