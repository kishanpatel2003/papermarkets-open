"""Tests for the trade correlation engine."""

import time
from unittest.mock import MagicMock, patch

import pytest

from papermarkets.monitor.config import Outcome
from papermarkets.monitor.state import (
    Alert,
    ClobTradeEvent,
    MonitorState,
    OrderbookSnapshot,
    TradeEvent,
    WalletProfile,
)


def _outcome(
    name: str = "Will X happen?",
    yes_token_id: str = "tok_yes",
    no_token_id: str = "tok_no",
) -> Outcome:
    return Outcome(
        name=name,
        condition_id="cond_1",
        slug="will-x-happen",
        yes_token_id=yes_token_id,
        no_token_id=no_token_id,
        yes_price=0.10,
        volume=5000.0,
        active=True,
    )


def _clob_event(
    token_id: str = "tok_yes",
    price: float = 0.10,
    size: float = 100.0,
    side: str = "BUY",
    timestamp: int = 1_700_000_000_000,
) -> ClobTradeEvent:
    return ClobTradeEvent(
        token_id=token_id,
        price=price,
        size=size,
        side=side,
        timestamp=timestamp,
    )


def _dome_trade(
    token_id: str = "tok_yes",
    price: float = 0.10,
    size: float = 100.0,
    side: str = "BUY",
    timestamp: int = 1_700_000_010,
    user_address: str = "0xabc123def456",
) -> TradeEvent:
    return TradeEvent(
        trade_id="trade_1",
        market_id="market_1",
        token_id=token_id,
        token_label="Yes",
        side=side,
        price=price,
        size=size,
        timestamp=timestamp,
        user_address=user_address,
        taker_address=user_address,
        tx_hash="0xtx1",
        block_number=12345,
        outcome_name="Will X happen?",
    )


def _wallet(total_trades: int = 5) -> WalletProfile:
    return WalletProfile(
        eoa="0xeoa",
        proxy="0xproxy",
        handle="test",
        pseudonym="tester",
        total_volume=500.0,
        total_trades=total_trades,
        total_markets=2,
    )


@pytest.fixture()
def state():
    return MonitorState()


@pytest.fixture()
def wallet_cache():
    cache = MagicMock()
    cache.get.return_value = _wallet(total_trades=5)
    return cache


@pytest.fixture()
def outcomes():
    return [_outcome()]


@pytest.fixture()
def correlator(state, wallet_cache, outcomes):
    from papermarkets.monitor.correlator import TradeCorrelator

    return TradeCorrelator(
        state=state,
        wallet_cache=wallet_cache,
        outcomes=outcomes,
        alert_threshold=0.2,
        no_sound=True,
        ntfy_topic="",
    )


# --- Correlation matching ---


class TestTryMatch:
    def test_match_same_token_close_timestamp_similar_size(self, correlator):
        """Match when token_id, timestamp (~within 30s), and size (~10%) all agree."""
        clob = _clob_event(token_id="tok_yes", size=100.0, timestamp=1_700_000_000_000)
        correlator.on_clob_trade(clob)

        # Dome trade 10 seconds later, same size
        dome = _dome_trade(token_id="tok_yes", size=100.0, timestamp=1_700_000_010)
        matched = correlator._try_match(dome)

        assert matched is not None
        assert matched.token_id == "tok_yes"
        assert matched.size == 100.0

    def test_no_match_different_token(self, correlator):
        """No match when token_id differs."""
        clob = _clob_event(token_id="tok_yes")
        correlator.on_clob_trade(clob)

        dome = _dome_trade(token_id="tok_no")
        matched = correlator._try_match(dome)

        assert matched is None

    def test_no_match_timestamp_gap_over_30s(self, correlator):
        """No match when timestamp gap > 30 seconds."""
        clob = _clob_event(timestamp=1_700_000_000_000)
        correlator.on_clob_trade(clob)

        # 31 seconds later
        dome = _dome_trade(timestamp=1_700_000_031)
        matched = correlator._try_match(dome)

        assert matched is None

    def test_no_match_size_diff_over_10_pct(self, correlator):
        """No match when size difference > 10%."""
        clob = _clob_event(size=100.0)
        correlator.on_clob_trade(clob)

        dome = _dome_trade(size=120.0)  # 20% difference
        matched = correlator._try_match(dome)

        assert matched is None

    def test_match_removes_from_pending(self, correlator):
        """A matched CLOB event is consumed from the pending buffer."""
        clob = _clob_event()
        correlator.on_clob_trade(clob)
        assert len(correlator._pending) == 1

        dome = _dome_trade()
        correlator._try_match(dome)

        assert len(correlator._pending) == 0

    def test_match_within_size_tolerance(self, correlator):
        """Size within 10% tolerance should still match."""
        clob = _clob_event(size=100.0)
        correlator.on_clob_trade(clob)

        dome = _dome_trade(size=109.0)  # 9% difference — within tolerance
        matched = correlator._try_match(dome)

        assert matched is not None


# --- Cleanup ---


class TestCleanupStale:
    def test_stale_entries_removed(self, correlator):
        """Entries older than 60 seconds get removed."""
        stale_ts = int(time.time() * 1000) - 90_000  # 90 seconds ago
        clob = _clob_event(timestamp=stale_ts)
        correlator._pending.append(clob)

        correlator._cleanup_stale()

        assert len(correlator._pending) == 0

    def test_fresh_entries_preserved(self, correlator):
        """Entries less than 60 seconds old are kept."""
        fresh_ts = int(time.time() * 1000) - 10_000  # 10 seconds ago
        clob = _clob_event(timestamp=fresh_ts)
        correlator._pending.append(clob)

        correlator._cleanup_stale()

        assert len(correlator._pending) == 1


# --- Fast alert detection ---


class TestFastAlert:
    def test_large_low_prob_buy_triggers_fast_alert(self, correlator, state, capsys):
        """Large trade ($500+) on low-prob outcome (<0.15) triggers fast alert."""
        state.current_prices["tok_yes"] = 0.10
        # size=6000, price=0.10 => notional=$600, mid<0.15, BUY -> anomalous
        clob = _clob_event(size=6000.0, price=0.10, side="BUY")
        correlator.on_clob_trade(clob)

        output = capsys.readouterr().out
        assert "FAST" in output

    def test_large_notional_any_price_triggers(self, correlator, state, capsys):
        """Any trade over $2000 notional triggers fast alert regardless of price."""
        state.current_prices["tok_yes"] = 0.50
        clob = _clob_event(size=5000.0, price=0.50, side="BUY")
        correlator.on_clob_trade(clob)

        output = capsys.readouterr().out
        assert "FAST" in output

    def test_small_trade_no_fast_alert(self, correlator, state, capsys):
        """Small trade does not trigger fast alert."""
        state.current_prices["tok_yes"] = 0.50
        clob = _clob_event(size=10.0, price=0.50, side="BUY")
        correlator.on_clob_trade(clob)

        output = capsys.readouterr().out
        assert "FAST" not in output


# --- Book update ---


class TestOnBookUpdate:
    def test_sets_latest_books(self, correlator, state):
        bids = [[0.10, 500.0], [0.09, 300.0]]
        asks = [[0.11, 400.0], [0.12, 200.0]]

        correlator.on_book_update("tok_yes", bids, asks)

        assert "tok_yes" in state.latest_books
        snap = state.latest_books["tok_yes"]
        assert isinstance(snap, OrderbookSnapshot)
        assert snap.bids == bids
        assert snap.asks == asks

    def test_sets_mid_price(self, correlator, state):
        bids = [[0.10, 500.0]]
        asks = [[0.12, 400.0]]

        correlator.on_book_update("tok_yes", bids, asks)

        assert state.current_prices["tok_yes"] == pytest.approx(0.11)

    def test_bid_only_sets_bid_price(self, correlator, state):
        correlator.on_book_update("tok_yes", [[0.10, 100.0]], [])
        assert state.current_prices["tok_yes"] == pytest.approx(0.10)

    def test_ask_only_sets_ask_price(self, correlator, state):
        correlator.on_book_update("tok_yes", [], [[0.12, 100.0]])
        assert state.current_prices["tok_yes"] == pytest.approx(0.12)

    def test_empty_book_no_price(self, correlator, state):
        correlator.on_book_update("tok_yes", [], [])
        assert "tok_yes" not in state.current_prices


# --- Score and alert flow ---


class TestScoreAndAlert:
    def test_high_score_trade_generates_alert(self, correlator, state, wallet_cache, capsys):
        """Trade with high suspicion score generates an alert in state.alerts."""
        # Fresh wallet (5 trades) + low-prob buy => high freshness + conviction
        wallet_cache.get.return_value = _wallet(total_trades=3)
        state.current_prices["tok_yes"] = 0.05

        trade = _dome_trade(price=0.05, size=200.0, side="BUY")

        with patch("papermarkets.monitor.correlator.log_alert"):
            correlator._score_and_alert(trade)

        assert len(state.alerts) > 0
        alert = state.alerts[0]
        assert isinstance(alert, Alert)
        assert alert.wallet == trade.user_address

    def test_low_score_trade_no_alert(self, correlator, state, wallet_cache, capsys):
        """Trade with low suspicion score does not generate an alert."""
        # Established wallet + high-prob sell => all scores near zero
        wallet_cache.get.return_value = _wallet(total_trades=500)
        state.current_prices["tok_yes"] = 0.80

        trade = _dome_trade(price=0.80, size=5.0, side="SELL")

        with patch("papermarkets.monitor.correlator.log_alert"):
            correlator._score_and_alert(trade)

        assert len(state.alerts) == 0

    def test_alert_updates_watchlist(self, correlator, state, wallet_cache, capsys):
        """High-scoring trade adds the wallet to the watchlist."""
        wallet_cache.get.return_value = _wallet(total_trades=3)
        state.current_prices["tok_yes"] = 0.05

        trade = _dome_trade(price=0.05, size=200.0, side="BUY")

        with patch("papermarkets.monitor.correlator.log_alert"):
            correlator._score_and_alert(trade)

        assert trade.user_address in state.watchlist

    def test_score_and_alert_tracks_trade_window(self, correlator, state, wallet_cache, capsys):
        """Processing a trade adds it to the trade_windows for coordination scoring."""
        trade = _dome_trade()

        with patch("papermarkets.monitor.correlator.log_alert"):
            correlator._score_and_alert(trade)

        assert trade.user_address in state.trade_windows
        assert len(state.trade_windows[trade.user_address]) == 1
