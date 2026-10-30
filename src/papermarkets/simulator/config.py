from dataclasses import dataclass
from datetime import datetime


@dataclass
class SimulationConfig:
    """Configuration for a copy-trading simulation."""

    # Target
    wallet: str
    start: datetime | None = None
    end: datetime | None = None

    # Timing
    detection_delay_ms: int = 30_000
    execution_latency_ms: int = 2_000

    # Position sizing
    position_size_mode: str = "fixed_usd"
    position_size_value: float = 100.0
    max_position_pct: float = 0.25

    # Risk
    bankroll: float = 10_000.0
    max_slippage_pct: float = 0.05
    partial_fill_policy: str = "accept"

    # Fees
    fee_rate: float = 0.02

    # Data quality
    max_book_staleness_ms: int = 600_000

    def __post_init__(self) -> None:
        """Validate configuration values."""
        if self.bankroll <= 0:
            raise ValueError(f"bankroll must be > 0, got {self.bankroll}")
        if self.detection_delay_ms < 0:
            raise ValueError(f"detection_delay_ms must be >= 0, got {self.detection_delay_ms}")
        if self.execution_latency_ms < 0:
            raise ValueError(f"execution_latency_ms must be >= 0, got {self.execution_latency_ms}")
        if self.position_size_value <= 0:
            raise ValueError(f"position_size_value must be > 0, got {self.position_size_value}")
        if not (0 < self.max_position_pct <= 1):
            raise ValueError(f"max_position_pct must be in (0, 1], got {self.max_position_pct}")
        if not (0 <= self.fee_rate < 1):
            raise ValueError(f"fee_rate must be in [0, 1), got {self.fee_rate}")
        if self.position_size_mode not in ("fixed_usd", "fixed_shares"):
            raise ValueError(
                f"position_size_mode must be 'fixed_usd' or 'fixed_shares', "
                f"got {self.position_size_mode!r}"
            )
        if self.partial_fill_policy not in ("accept", "reject"):
            raise ValueError(
                f"partial_fill_policy must be 'accept' or 'reject', "
                f"got {self.partial_fill_policy!r}"
            )
        if self.max_book_staleness_ms <= 0:
            raise ValueError(
                f"max_book_staleness_ms must be > 0, got {self.max_book_staleness_ms}"
            )
        if self.max_slippage_pct < 0:
            raise ValueError(f"max_slippage_pct must be >= 0, got {self.max_slippage_pct}")
