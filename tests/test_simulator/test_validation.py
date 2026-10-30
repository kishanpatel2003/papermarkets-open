"""Validation tests for simulation invariants and edge cases."""

import pytest

from papermarkets.simulator.book_walker import parse_book_side, simulate_fill
from papermarkets.simulator.config import SimulationConfig
from papermarkets.simulator.position_tracker import PositionTracker
from papermarkets.simulator.result import PreflightReport, SimulationResult


class TestBankrollInvariant:
    """Bankroll must never go negative."""

    def test_tiny_bankroll_large_trades(self):
        tracker = PositionTracker(1.0)
        with pytest.raises(ValueError, match="exceeds bankroll"):
            tracker.record_trade("mkt1", "Yes", "BUY", 100, 0.50)

    def test_multiple_buys_drain_bankroll(self):
        tracker = PositionTracker(100.0)
        tracker.record_trade("mkt1", "Yes", "BUY", 100, 0.50)  # cost=50
        tracker.record_trade("mkt2", "Yes", "BUY", 80, 0.50)  # cost=40, bankroll=10
        with pytest.raises(ValueError, match="exceeds bankroll"):
            tracker.record_trade("mkt3", "Yes", "BUY", 30, 0.50)  # cost=15 > 10

    def test_bankroll_recovers_after_sell(self):
        tracker = PositionTracker(100.0)
        tracker.record_trade("mkt1", "Yes", "BUY", 100, 0.50)  # bankroll=50
        tracker.record_trade("mkt1", "Yes", "SELL", 50, 0.80)  # proceeds=40, bankroll=90
        tracker.record_trade("mkt2", "Yes", "BUY", 150, 0.50)  # cost=75, bankroll=15
        assert tracker.bankroll == pytest.approx(15.0)

    def test_bankroll_never_negative_after_fees(self):
        tracker = PositionTracker(50.0)
        # cost = 100 * 0.50 + 1.0 fee = 51.0 > 50.0
        with pytest.raises(ValueError, match="exceeds bankroll"):
            tracker.record_trade("mkt1", "Yes", "BUY", 100, 0.50, fee=1.0)


class TestFillPriceInvariant:
    """Fill prices must be VWAP across consumed levels."""

    def test_fill_price_is_vwap_not_first_level(self):
        asks = [(0.60, 100.0), (0.65, 100.0), (0.70, 100.0)]
        fill = simulate_fill("BUY", 200, [], asks)
        assert fill.fill_price == pytest.approx(0.625)
        assert fill.fill_price != 0.60

    def test_sell_fill_price_from_bids(self):
        bids = [(0.55, 200.0), (0.50, 300.0)]
        fill = simulate_fill("SELL", 300, bids, [])
        expected = (200 * 0.55 + 100 * 0.50) / 300
        assert fill.fill_price == pytest.approx(expected)


class TestBookParsing:
    """Book JSON strings parse correctly."""

    def test_parse_asks_ascending(self):
        raw = '[{"price": "0.62", "size": "200.0"}, {"price": "0.65", "size": "300.0"}]'
        levels = parse_book_side(raw, descending=False)
        assert levels == [(0.62, 200.0), (0.65, 300.0)]

    def test_bids_sorted_descending(self):
        raw = '[{"price": "0.50", "size": "100"}, {"price": "0.60", "size": "200"}]'
        levels = parse_book_side(raw, descending=True)
        assert levels[0][0] > levels[1][0]


class TestNoShortSelling:
    """Cannot sell tokens not held."""

    def test_sell_without_position_raises(self):
        tracker = PositionTracker(1000.0)
        with pytest.raises(ValueError, match="exceeds held"):
            tracker.record_trade("mkt1", "Yes", "SELL", 10, 0.50)

    def test_sell_different_token_raises(self):
        tracker = PositionTracker(1000.0)
        tracker.record_trade("mkt1", "Yes", "BUY", 100, 0.50)
        with pytest.raises(ValueError, match="exceeds held"):
            tracker.record_trade("mkt1", "No", "SELL", 10, 0.50)


class TestPnlSeparation:
    """Resolved and unresolved P&L tracked separately."""

    def test_resolve_computes_pnl(self):
        tracker = PositionTracker(1000.0)
        tracker.record_trade("mkt1", "Yes", "BUY", 100, 0.40)
        pnl = tracker.resolve_market("mkt1", "Yes")
        assert pnl == pytest.approx(60.0)

    def test_resolve_loss(self):
        tracker = PositionTracker(1000.0)
        tracker.record_trade("mkt1", "Yes", "BUY", 100, 0.60)
        pnl = tracker.resolve_market("mkt1", "No")
        assert pnl == pytest.approx(-60.0)

    def test_unresolved_positions_remain(self):
        tracker = PositionTracker(1000.0)
        tracker.record_trade("mkt1", "Yes", "BUY", 100, 0.40)
        tracker.record_trade("mkt2", "Yes", "BUY", 50, 0.50)
        tracker.resolve_market("mkt1", "Yes")
        assert tracker.has_position("mkt2", "Yes")
        pos = tracker.get_position("mkt2", "Yes")
        assert pos.net_shares == pytest.approx(50.0)


class TestNoDataFabrication:
    """Empty or missing orderbook data produces empty fills."""

    def test_empty_book_returns_zero_fill(self):
        fill = simulate_fill("BUY", 100, [], [])
        assert fill.fill_size == 0
        assert fill.fill_price == 0.0

    def test_empty_json_parses_to_empty(self):
        assert parse_book_side("[]") == []

    def test_invalid_json_returns_empty(self):
        assert parse_book_side("not json at all") == []


class TestConfigEdgeCases:
    """Config boundary values."""

    def test_zero_delay_is_valid(self):
        config = SimulationConfig(wallet="0xtest", detection_delay_ms=0, execution_latency_ms=0)
        assert config.detection_delay_ms == 0

    def test_zero_fee_is_valid(self):
        config = SimulationConfig(wallet="0xtest", fee_rate=0.0)
        assert config.fee_rate == 0.0

    def test_max_position_pct_one_is_valid(self):
        config = SimulationConfig(wallet="0xtest", max_position_pct=1.0)
        assert config.max_position_pct == 1.0

    def test_very_large_staleness_valid(self):
        config = SimulationConfig(wallet="0xtest", max_book_staleness_ms=86_400_000)
        assert config.max_book_staleness_ms == 86_400_000


class TestDrawdownTracking:
    """Drawdown tracks peak-to-trough correctly."""

    def test_drawdown_across_multiple_losses(self):
        tracker = PositionTracker(1000.0)
        tracker.record_trade("m1", "Yes", "BUY", 100, 0.50)  # bankroll=950
        tracker.resolve_market("m1", "No")  # payout=0, bankroll=950
        tracker.record_trade("m2", "Yes", "BUY", 100, 0.50)  # bankroll=900
        tracker.resolve_market("m2", "No")  # payout=0, bankroll=900
        assert tracker.max_drawdown == pytest.approx(100.0)
        assert tracker.peak_bankroll == pytest.approx(1000.0)

    def test_drawdown_resets_on_new_peak(self):
        tracker = PositionTracker(1000.0)
        tracker.record_trade("m1", "Yes", "BUY", 100, 0.30)  # cost=30, bankroll=970
        tracker.resolve_market("m1", "Yes")  # payout=100, bankroll=1070
        tracker.record_trade("m2", "Yes", "BUY", 200, 0.50)  # cost=100, bankroll=970
        tracker.resolve_market("m2", "No")  # payout=0, bankroll=970
        assert tracker.max_drawdown == pytest.approx(100.0)
        assert tracker.peak_bankroll == pytest.approx(1070.0)


class TestPartialFills:
    """Partial fill behavior."""

    def test_partial_fill_fills_available(self):
        asks = [(0.60, 50.0)]
        fill = simulate_fill("BUY", 100, [], asks)
        assert fill.fill_size == pytest.approx(50.0)
        assert fill.unfilled_size == pytest.approx(50.0)

    def test_total_cost_is_sum_of_level_costs(self):
        asks = [(0.60, 100.0), (0.70, 100.0)]
        fill = simulate_fill("BUY", 150, [], asks)
        assert fill.total_cost == pytest.approx(100 * 0.60 + 50 * 0.70)
        assert fill.fill_size == pytest.approx(150.0)


class TestSummaryOutput:
    """SimulationResult.summary() produces readable output."""

    def test_summary_returns_string(self):
        config = SimulationConfig(wallet="0x1234567890abcdef1234567890abcdef12345678")
        preflight = PreflightReport(
            wallet=config.wallet,
            total_whale_trades=100,
            markets_traded=5,
            markets_with_orderbook_data=4,
            markets_without_orderbook_data=1,
            trades_in_covered_markets=80,
            trades_in_uncovered_markets=20,
            coverage_pct=0.8,
            resolved_markets=2,
        )
        result = SimulationResult(
            config=config,
            preflight=preflight,
            trades=[],
            resolved_pnl=100.0,
            resolved_markets=2,
            resolved_win_rate=0.5,
            unresolved_pnl=50.0,
            unresolved_markets=3,
            starting_bankroll=10000.0,
            ending_bankroll=10100.0,
            total_pnl=100.0,
            total_pnl_pct=0.01,
            total_trades_attempted=80,
            total_trades_filled=60,
            total_trades_skipped=20,
            skip_reasons={"no_orderbook_data": 15, "slippage_exceeded": 5},
        )
        s = result.summary()
        assert isinstance(s, str)
        assert "0x1234...5678" in s
        assert "100" in s
        assert "no_orderbook_data" in s
