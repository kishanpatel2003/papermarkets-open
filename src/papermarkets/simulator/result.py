from dataclasses import dataclass, field
from datetime import datetime

from papermarkets.simulator.config import SimulationConfig


@dataclass
class PreflightReport:
    wallet: str
    total_whale_trades: int
    markets_traded: int
    markets_with_orderbook_data: int
    markets_without_orderbook_data: int
    trades_in_covered_markets: int
    trades_in_uncovered_markets: int
    coverage_pct: float
    resolved_markets: int
    median_snapshot_gap_ms: dict[str, float] = field(default_factory=dict)


@dataclass
class SimulatedTrade:
    """One trade in the simulation log."""

    # Whale's original trade
    whale_timestamp: datetime
    whale_market_id: str
    whale_market_slug: str
    whale_token_label: str
    whale_side: str
    whale_price: float
    whale_size: float

    # Copier's simulated execution
    copier_entry_time: datetime
    copier_side: str
    copier_order_size: float
    copier_fill_size: float
    copier_fill_price: float
    copier_total_cost: float

    # Quality metrics
    slippage_vs_whale: float
    slippage_pct: float
    book_staleness_ms: int
    levels_consumed: int
    fully_filled: bool

    # Confidence
    confidence: str

    # Portfolio state after this trade
    bankroll_after: float

    # Skip info
    skipped: bool = False
    skip_reason: str | None = None


@dataclass
class SimulationResult:
    """Complete output of a simulation run."""

    config: SimulationConfig
    preflight: PreflightReport
    trades: list[SimulatedTrade]

    # Performance -- resolved (ground truth)
    resolved_pnl: float
    resolved_markets: int
    resolved_win_rate: float

    # Performance -- unresolved (mark-to-market)
    unresolved_pnl: float
    unresolved_markets: int

    # Performance -- combined
    starting_bankroll: float
    ending_bankroll: float
    total_pnl: float
    total_pnl_pct: float

    # Execution quality
    total_trades_attempted: int
    total_trades_filled: int
    total_trades_skipped: int
    skip_reasons: dict[str, int] = field(default_factory=dict)
    median_slippage_pct: float = 0.0
    p95_slippage_pct: float = 0.0
    median_book_staleness_ms: float = 0.0
    p95_book_staleness_ms: float = 0.0
    pct_high_confidence: float = 0.0

    # Risk
    max_drawdown: float = 0.0
    max_drawdown_pct: float = 0.0

    def summary(self) -> str:
        """Human-readable summary suitable for printing to terminal."""
        wallet_short = f"{self.config.wallet[:6]}...{self.config.wallet[-4:]}"
        lines = [
            f"Copy-Trading Simulation: {wallet_short}",
            "=" * 40,
            "",
            "Data Coverage",
        ]

        pf = self.preflight
        lines.append(
            f"  Whale trades: {pf.total_whale_trades} total, "
            f"{pf.trades_in_covered_markets} in covered markets "
            f"({pf.coverage_pct:.0%})"
        )
        lines.append(
            f"  Markets: {pf.markets_traded} traded, "
            f"{pf.markets_with_orderbook_data} with orderbook data, "
            f"{pf.resolved_markets} resolved"
        )
        if pf.trades_in_uncovered_markets > 0:
            uncov_pct = pf.trades_in_uncovered_markets / pf.total_whale_trades
            lines.append(
                f"  Warning: {pf.trades_in_uncovered_markets} trades "
                f"({uncov_pct:.0%}) excluded — no orderbook data"
            )

        lines.append("")
        lines.append("Execution Quality")
        lines.append(
            f"  Filled: {self.total_trades_filled} of "
            f"{self.total_trades_attempted} attempted "
            f"({self.total_trades_filled / max(self.total_trades_attempted, 1):.0%})"
        )
        if self.skip_reasons:
            reasons = ", ".join(
                f"{k} ({v})" for k, v in sorted(self.skip_reasons.items(), key=lambda x: -x[1])
            )
            lines.append(f"  Skip reasons: {reasons}")
        lines.append(
            f"  Median slippage: {self.median_slippage_pct:.1%} | "
            f"P95 slippage: {self.p95_slippage_pct:.1%}"
        )

        filled = [t for t in self.trades if not t.skipped]
        if filled:
            high = sum(1 for t in filled if t.confidence == "high")
            med = sum(1 for t in filled if t.confidence == "medium")
            low = sum(1 for t in filled if t.confidence == "low")
            total = len(filled)
            lines.append(
                f"  Fill confidence: {high / total:.0%} high, "
                f"{med / total:.0%} medium, {low / total:.0%} low"
            )

        lines.append("")
        lines.append("Performance (resolved markets only — ground truth)")
        lines.append(
            f"  Resolved P&L: {self.resolved_pnl:+,.2f} ({self.resolved_markets} markets)"
        )
        lines.append(
            f"  Win rate: {self.resolved_win_rate:.0%} "
            f"({int(self.resolved_win_rate * self.resolved_markets)} of "
            f"{self.resolved_markets} resolved positions profitable)"
        )

        lines.append("")
        lines.append("Performance (unresolved — mark-to-market, may change)")
        lines.append(
            f"  Unresolved P&L: {self.unresolved_pnl:+,.2f} ({self.unresolved_markets} markets)"
        )

        lines.append("")
        lines.append("Risk")
        lines.append(
            f"  Max drawdown: ${self.max_drawdown:,.2f} "
            f"({self.max_drawdown_pct:.1%} of starting bankroll)"
        )

        lines.append("")
        lines.append("Portfolio")
        lines.append(f"  Starting bankroll: ${self.starting_bankroll:,.2f}")
        lines.append(f"  Ending cash: ${self.ending_bankroll:,.2f} (resolved only)")
        lines.append(f"  Open positions: {self.unresolved_markets} markets")

        delay_s = (self.config.detection_delay_ms + self.config.execution_latency_ms) / 1000
        staleness_min = self.config.max_book_staleness_ms / 60_000
        lines.append("")
        lines.append(
            f"Config: delay={delay_s:.0f}s, "
            f"size=${self.config.position_size_value:.0f}/trade, "
            f"fee={self.config.fee_rate:.0%}, "
            f"staleness_limit={staleness_min:.0f}min"
        )

        return "\n".join(lines)
