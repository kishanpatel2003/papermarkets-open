from dataclasses import dataclass


@dataclass
class Position:
    """State of a position in one token."""

    net_shares: float = 0.0
    cost_basis: float = 0.0
    avg_entry_price: float = 0.0
    realized_pnl: float = 0.0


class PositionTracker:
    """Tracks the copier's portfolio state across the simulation."""

    def __init__(self, starting_bankroll: float) -> None:
        self._bankroll = starting_bankroll
        self._peak_bankroll = starting_bankroll
        self._max_drawdown = 0.0
        self._positions: dict[tuple[str, str], Position] = {}
        self._total_realized_pnl = 0.0

    def record_trade(
        self,
        market_id: str,
        token_label: str,
        side: str,
        size: float,
        price: float,
        fee: float = 0.0,
    ) -> None:
        """Record a trade. Updates position, bankroll, and drawdown tracking.

        BUY: deducts (size * price + fee) from bankroll, adds shares to position.
        SELL: adds (size * price - fee) to bankroll, removes shares from position.
              Computes realized P&L on the sold shares using average cost.
        """
        key = (market_id, token_label)
        pos = self._positions.setdefault(key, Position())

        if side == "BUY":
            cost = size * price + fee
            if cost > self._bankroll + 1e-9:
                raise ValueError(f"BUY cost ${cost:.2f} exceeds bankroll ${self._bankroll:.2f}")
            self._bankroll -= cost
            pos.cost_basis += size * price
            pos.net_shares += size
            pos.avg_entry_price = pos.cost_basis / pos.net_shares if pos.net_shares > 0 else 0.0
        elif side == "SELL":
            if size > pos.net_shares + 1e-9:
                raise ValueError(f"SELL {size} shares exceeds held {pos.net_shares}")
            proceeds = size * price - fee
            sold_cost_basis = pos.avg_entry_price * size
            realized = proceeds - sold_cost_basis
            pos.realized_pnl += realized
            self._total_realized_pnl += realized
            pos.net_shares -= size
            pos.cost_basis -= sold_cost_basis
            if pos.net_shares < 1e-9:
                pos.net_shares = 0.0
                pos.cost_basis = 0.0
                pos.avg_entry_price = 0.0
            else:
                pos.avg_entry_price = pos.cost_basis / pos.net_shares
            self._bankroll += proceeds
        else:
            raise ValueError(f"side must be 'BUY' or 'SELL', got {side!r}")

        self._update_drawdown()

    def has_position(self, market_id: str, token_label: str) -> bool:
        """True if copier holds > 0 shares of this token."""
        key = (market_id, token_label)
        pos = self._positions.get(key)
        return pos is not None and pos.net_shares > 1e-9

    def get_position(self, market_id: str, token_label: str) -> Position:
        key = (market_id, token_label)
        return self._positions.get(key, Position())

    def resolve_market(self, market_id: str, winning_label: str) -> float:
        """Settle a resolved market. Winning tokens pay 1.0 per share, losers pay 0.0.
        Returns the total realized P&L from this resolution.
        """
        resolution_pnl = 0.0
        keys_to_clear = [k for k in self._positions if k[0] == market_id]
        for key in keys_to_clear:
            pos = self._positions[key]
            if pos.net_shares < 1e-9:
                continue
            token_label = key[1]
            payout = pos.net_shares * 1.0 if token_label == winning_label else 0.0
            pnl = payout - pos.cost_basis
            pos.realized_pnl += pnl
            self._total_realized_pnl += pnl
            resolution_pnl += pnl
            self._bankroll += payout
            pos.net_shares = 0.0
            pos.cost_basis = 0.0
            pos.avg_entry_price = 0.0

        self._update_drawdown()
        return resolution_pnl

    def _update_drawdown(self) -> None:
        if self._bankroll > self._peak_bankroll:
            self._peak_bankroll = self._bankroll
        drawdown = self._peak_bankroll - self._bankroll
        if drawdown > self._max_drawdown:
            self._max_drawdown = drawdown

    @property
    def bankroll(self) -> float:
        return self._bankroll

    @property
    def peak_bankroll(self) -> float:
        return self._peak_bankroll

    @property
    def max_drawdown(self) -> float:
        return self._max_drawdown

    @property
    def max_drawdown_pct(self) -> float:
        return self._max_drawdown / self._peak_bankroll if self._peak_bankroll > 0 else 0.0

    @property
    def all_positions(self) -> dict[tuple[str, str], Position]:
        return dict(self._positions)

    @property
    def total_realized_pnl(self) -> float:
        return self._total_realized_pnl
