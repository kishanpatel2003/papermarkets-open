import pytest

from papermarkets.simulator.position_tracker import PositionTracker


class TestPositionSimpleBuy:
    def test_buy_updates_position_and_bankroll(self):
        tracker = PositionTracker(starting_bankroll=1000.0)
        tracker.record_trade("m1", "Yes", "BUY", size=100, price=0.50)
        assert tracker.bankroll == pytest.approx(950.0)
        pos = tracker.get_position("m1", "Yes")
        assert pos.net_shares == pytest.approx(100.0)
        assert pos.cost_basis == pytest.approx(50.0)
        assert pos.avg_entry_price == pytest.approx(0.50)


class TestPositionBuySell:
    def test_sell_realizes_profit(self):
        tracker = PositionTracker(starting_bankroll=1000.0)
        tracker.record_trade("m1", "Yes", "BUY", size=100, price=0.50)
        tracker.record_trade("m1", "Yes", "SELL", size=50, price=0.70)
        pos = tracker.get_position("m1", "Yes")
        assert pos.net_shares == pytest.approx(50.0)
        assert pos.cost_basis == pytest.approx(25.0)
        # realized: 50 * 0.70 - 50 * 0.50 = 35 - 25 = 10
        assert pos.realized_pnl == pytest.approx(10.0)
        # bankroll: 1000 - 50 + 35 = 985
        assert tracker.bankroll == pytest.approx(985.0)


class TestPositionResolveWin:
    def test_resolve_winning_token(self):
        tracker = PositionTracker(starting_bankroll=1000.0)
        tracker.record_trade("m1", "Yes", "BUY", size=100, price=0.50)
        pnl = tracker.resolve_market("m1", winning_label="Yes")
        assert pnl == pytest.approx(50.0)
        assert tracker.bankroll == pytest.approx(1050.0)
        pos = tracker.get_position("m1", "Yes")
        assert pos.net_shares == pytest.approx(0.0)
        assert pos.realized_pnl == pytest.approx(50.0)


class TestPositionResolveLoss:
    def test_resolve_losing_token(self):
        tracker = PositionTracker(starting_bankroll=1000.0)
        tracker.record_trade("m1", "Yes", "BUY", size=100, price=0.50)
        pnl = tracker.resolve_market("m1", winning_label="No")
        assert pnl == pytest.approx(-50.0)
        assert tracker.bankroll == pytest.approx(950.0)
        pos = tracker.get_position("m1", "Yes")
        assert pos.net_shares == pytest.approx(0.0)
        assert pos.realized_pnl == pytest.approx(-50.0)


class TestMultipleBuysPartialSell:
    def test_avg_cost_with_multiple_buys(self):
        tracker = PositionTracker(starting_bankroll=1000.0)
        tracker.record_trade("m1", "Yes", "BUY", size=100, price=0.50)
        tracker.record_trade("m1", "Yes", "BUY", size=50, price=0.60)
        pos = tracker.get_position("m1", "Yes")
        # cost_basis = 50 + 30 = 80, shares = 150, avg = 80/150 ≈ 0.5333
        assert pos.net_shares == pytest.approx(150.0)
        assert pos.cost_basis == pytest.approx(80.0)
        assert pos.avg_entry_price == pytest.approx(80.0 / 150.0)

        tracker.record_trade("m1", "Yes", "SELL", size=75, price=0.80)
        pos = tracker.get_position("m1", "Yes")
        assert pos.net_shares == pytest.approx(75.0)
        # sold_cost_basis = 0.5333 * 75 = 40.0, realized = 60 - 40 = 20
        expected_sold_cost = (80.0 / 150.0) * 75
        expected_realized = 75 * 0.80 - expected_sold_cost
        assert pos.realized_pnl == pytest.approx(expected_realized)


class TestSellExceedsHeld:
    def test_sell_more_than_held_raises(self):
        tracker = PositionTracker(starting_bankroll=1000.0)
        tracker.record_trade("m1", "Yes", "BUY", size=10, price=0.50)
        with pytest.raises(ValueError, match="SELL .* exceeds held"):
            tracker.record_trade("m1", "Yes", "SELL", size=20, price=0.60)


class TestBuyExceedsBankroll:
    def test_buy_exceeds_bankroll_raises(self):
        tracker = PositionTracker(starting_bankroll=10.0)
        with pytest.raises(ValueError, match="exceeds bankroll"):
            tracker.record_trade("m1", "Yes", "BUY", size=100, price=0.50)


class TestDrawdownTracking:
    def test_drawdown_after_loss(self):
        tracker = PositionTracker(starting_bankroll=1000.0)
        tracker.record_trade("m1", "Yes", "BUY", size=100, price=0.50)
        # bankroll = 950 (paid 50 for shares), peak = 1000, drawdown = 50
        tracker.resolve_market("m1", winning_label="No")
        # losing token pays 0, bankroll stays 950, peak = 1000, drawdown = 50
        assert tracker.peak_bankroll == pytest.approx(1000.0)
        assert tracker.max_drawdown == pytest.approx(50.0)
        assert tracker.max_drawdown_pct == pytest.approx(0.05)


class TestHasPosition:
    def test_has_position_after_buy(self):
        tracker = PositionTracker(starting_bankroll=1000.0)
        assert not tracker.has_position("m1", "Yes")
        tracker.record_trade("m1", "Yes", "BUY", size=10, price=0.50)
        assert tracker.has_position("m1", "Yes")

    def test_no_position_after_full_sell(self):
        tracker = PositionTracker(starting_bankroll=1000.0)
        tracker.record_trade("m1", "Yes", "BUY", size=10, price=0.50)
        tracker.record_trade("m1", "Yes", "SELL", size=10, price=0.60)
        assert not tracker.has_position("m1", "Yes")


class TestFees:
    def test_buy_fee_deducted(self):
        tracker = PositionTracker(starting_bankroll=1000.0)
        tracker.record_trade("m1", "Yes", "BUY", size=100, price=0.50, fee=2.0)
        assert tracker.bankroll == pytest.approx(948.0)

    def test_sell_fee_deducted(self):
        tracker = PositionTracker(starting_bankroll=1000.0)
        tracker.record_trade("m1", "Yes", "BUY", size=100, price=0.50)
        tracker.record_trade("m1", "Yes", "SELL", size=100, price=0.60, fee=1.0)
        # proceeds = 100 * 0.60 - 1 = 59, bankroll = 950 + 59 = 1009
        assert tracker.bankroll == pytest.approx(1009.0)


class TestMultiMarketIndependence:
    def test_positions_in_different_markets(self):
        tracker = PositionTracker(starting_bankroll=1000.0)
        tracker.record_trade("m1", "Yes", "BUY", size=50, price=0.40)
        tracker.record_trade("m2", "No", "BUY", size=30, price=0.60)

        p1 = tracker.get_position("m1", "Yes")
        p2 = tracker.get_position("m2", "No")
        assert p1.net_shares == pytest.approx(50.0)
        assert p2.net_shares == pytest.approx(30.0)
        assert p1.avg_entry_price == pytest.approx(0.40)
        assert p2.avg_entry_price == pytest.approx(0.60)

        # Resolve m1 doesn't affect m2
        tracker.resolve_market("m1", winning_label="Yes")
        p2_after = tracker.get_position("m2", "No")
        assert p2_after.net_shares == pytest.approx(30.0)


class TestInvalidSide:
    def test_invalid_side_raises(self):
        tracker = PositionTracker(starting_bankroll=1000.0)
        with pytest.raises(ValueError, match="side must be"):
            tracker.record_trade("m1", "Yes", "SHORT", size=10, price=0.50)


class TestTotalRealizedPnl:
    def test_tracks_across_trades(self):
        tracker = PositionTracker(starting_bankroll=1000.0)
        tracker.record_trade("m1", "Yes", "BUY", size=100, price=0.50)
        tracker.record_trade("m1", "Yes", "SELL", size=100, price=0.70)
        assert tracker.total_realized_pnl == pytest.approx(20.0)
