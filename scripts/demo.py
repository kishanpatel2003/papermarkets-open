"""Offline example: a market buy consumes several example price levels."""
from dataclasses import asdict
import json
from papermarkets.simulator.book_walker import simulate_fill

result = simulate_fill(
    side="BUY", order_size=250,
    bids=[(0.48, 500)], asks=[(0.50, 100), (0.52, 100), (0.55, 100)],
)
print(json.dumps(asdict(result), indent=2))
assert result.fill_size == 250
assert result.levels_consumed == 3
assert abs(result.total_cost - 129.5) < 1e-9
