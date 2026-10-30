import json

import pytest

from papermarkets.simulator.book_walker import parse_book_side, simulate_fill

# ---------------------------------------------------------------------------
# parse_book_side
# ---------------------------------------------------------------------------


class TestParseBookSide:
    def test_valid_json_ascending(self):
        raw = json.dumps(
            [
                {"price": "0.65", "size": "200.0"},
                {"price": "0.62", "size": "100.0"},
                {"price": "0.63", "size": "150.0"},
            ]
        )
        result = parse_book_side(raw)
        assert result == [(0.62, 100.0), (0.63, 150.0), (0.65, 200.0)]

    def test_valid_json_descending(self):
        raw = json.dumps(
            [
                {"price": "0.58", "size": "200.0"},
                {"price": "0.62", "size": "100.0"},
                {"price": "0.60", "size": "250.0"},
            ]
        )
        result = parse_book_side(raw, descending=True)
        assert result == [(0.62, 100.0), (0.60, 250.0), (0.58, 200.0)]

    def test_empty_array(self):
        assert parse_book_side("[]") == []

    def test_already_sorted(self):
        raw = json.dumps([{"price": "0.50", "size": "10"}, {"price": "0.60", "size": "20"}])
        assert parse_book_side(raw) == [(0.50, 10.0), (0.60, 20.0)]

    def test_invalid_json_returns_empty(self):
        assert parse_book_side("not json") == []

    def test_malformed_entries_skipped(self):
        raw = json.dumps(
            [
                {"price": "0.62", "size": "100"},
                {"price": "bad", "size": "100"},
                {"size": "100"},
                {"price": "0.65", "size": "200"},
            ]
        )
        result = parse_book_side(raw)
        assert result == [(0.62, 100.0), (0.65, 200.0)]


# ---------------------------------------------------------------------------
# simulate_fill — BUY
# ---------------------------------------------------------------------------


class TestSimulateFillBuy:
    def test_single_level_full_fill(self):
        r = simulate_fill("BUY", 100, bids=[], asks=[(0.65, 200)])
        assert r.fill_size == 100
        assert r.fill_price == pytest.approx(0.65)
        assert r.unfilled_size == 0
        assert r.levels_consumed == 1
        assert r.total_cost == pytest.approx(65.0)
        assert r.fills == [(0.65, 100)]

    def test_multi_level_vwap(self):
        asks = [(0.62, 200), (0.63, 150), (0.65, 300)]
        r = simulate_fill("BUY", 500, bids=[], asks=asks)
        assert r.fill_size == 500
        expected_cost = 200 * 0.62 + 150 * 0.63 + 150 * 0.65
        assert r.total_cost == pytest.approx(expected_cost)
        assert r.fill_price == pytest.approx(expected_cost / 500)
        assert r.levels_consumed == 3
        assert r.unfilled_size == 0
        assert r.fills == [(0.62, 200), (0.63, 150), (0.65, 150)]

    def test_partial_fill_book_exhausted(self):
        asks = [(0.62, 200), (0.63, 150), (0.65, 300), (0.68, 100)]
        r = simulate_fill("BUY", 800, bids=[], asks=asks)
        assert r.fill_size == 750
        assert r.unfilled_size == 50
        expected_cost = 200 * 0.62 + 150 * 0.63 + 300 * 0.65 + 100 * 0.68
        assert r.total_cost == pytest.approx(expected_cost)
        assert r.fill_price == pytest.approx(expected_cost / 750)
        assert r.levels_consumed == 4

    def test_empty_book(self):
        r = simulate_fill("BUY", 100, bids=[], asks=[])
        assert r.fill_size == 0
        assert r.fill_price == 0.0
        assert r.total_cost == 0.0
        assert r.unfilled_size == 100
        assert r.levels_consumed == 0
        assert r.fills == []

    def test_zero_order_size(self):
        r = simulate_fill("BUY", 0, bids=[], asks=[(0.65, 200)])
        assert r.fill_size == 0
        assert r.fill_price == 0.0
        assert r.unfilled_size == 0
        assert r.levels_consumed == 0
        assert r.fills == []

    def test_single_level_partial(self):
        r = simulate_fill("BUY", 300, bids=[], asks=[(0.65, 100)])
        assert r.fill_size == 100
        assert r.unfilled_size == 200
        assert r.fill_price == pytest.approx(0.65)
        assert r.levels_consumed == 1
        assert r.fills == [(0.65, 100)]

    def test_exact_fill_at_level_boundary(self):
        asks = [(0.62, 200), (0.63, 300)]
        r = simulate_fill("BUY", 200, bids=[], asks=asks)
        assert r.fill_size == 200
        assert r.levels_consumed == 1
        assert r.unfilled_size == 0
        assert r.fills == [(0.62, 200)]

    def test_negative_order_size(self):
        r = simulate_fill("BUY", -10, bids=[], asks=[(0.65, 200)])
        assert r.fill_size == 0
        assert r.fills == []


# ---------------------------------------------------------------------------
# simulate_fill — SELL
# ---------------------------------------------------------------------------


class TestSimulateFillSell:
    def test_sell_basic(self):
        bids = [(0.60, 250), (0.58, 200)]
        r = simulate_fill("SELL", 300, bids=bids, asks=[])
        assert r.fill_size == 300
        expected_cost = 250 * 0.60 + 50 * 0.58
        assert r.total_cost == pytest.approx(expected_cost)
        assert r.fill_price == pytest.approx(expected_cost / 300)
        assert r.levels_consumed == 2
        assert r.unfilled_size == 0
        assert r.fills == [(0.60, 250), (0.58, 50)]

    def test_sell_empty_book(self):
        r = simulate_fill("SELL", 100, bids=[], asks=[])
        assert r.fill_size == 0
        assert r.unfilled_size == 100
        assert r.fill_price == 0.0

    def test_sell_partial_fill(self):
        bids = [(0.60, 50)]
        r = simulate_fill("SELL", 100, bids=bids, asks=[])
        assert r.fill_size == 50
        assert r.unfilled_size == 50
        assert r.fill_price == pytest.approx(0.60)
        assert r.levels_consumed == 1


# ---------------------------------------------------------------------------
# Performance
# ---------------------------------------------------------------------------


class TestPerformance:
    def test_30_level_book_under_1ms(self):
        import time

        asks = [(0.50 + i * 0.01, 100.0) for i in range(30)]
        raw = json.dumps([{"price": str(p), "size": str(s)} for p, s in asks])

        start = time.perf_counter()
        for _ in range(100):
            parsed = parse_book_side(raw)
            simulate_fill("BUY", 1500, bids=[], asks=parsed)
        elapsed = (time.perf_counter() - start) / 100

        assert elapsed < 0.001, f"parse + walk took {elapsed:.4f}s, expected < 1ms"
