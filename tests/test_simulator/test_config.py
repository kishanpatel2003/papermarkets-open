import pytest

from papermarkets.simulator.config import SimulationConfig


class TestSimulationConfigDefaults:
    def test_valid_default_config(self):
        cfg = SimulationConfig(wallet="0x1234567890abcdef")
        assert cfg.wallet == "0x1234567890abcdef"
        assert cfg.bankroll == 10_000.0
        assert cfg.detection_delay_ms == 30_000
        assert cfg.execution_latency_ms == 2_000
        assert cfg.position_size_mode == "fixed_usd"
        assert cfg.position_size_value == 100.0
        assert cfg.max_position_pct == 0.25
        assert cfg.max_slippage_pct == 0.05
        assert cfg.partial_fill_policy == "accept"
        assert cfg.fee_rate == 0.02
        assert cfg.max_book_staleness_ms == 600_000
        assert cfg.start is None
        assert cfg.end is None

    def test_valid_custom_config(self):
        from datetime import UTC, datetime

        cfg = SimulationConfig(
            wallet="0xabcdef",
            start=datetime(2026, 1, 1, tzinfo=UTC),
            end=datetime(2026, 2, 1, tzinfo=UTC),
            detection_delay_ms=10_000,
            execution_latency_ms=1_000,
            position_size_mode="fixed_shares",
            position_size_value=50.0,
            max_position_pct=0.5,
            bankroll=50_000.0,
            max_slippage_pct=0.10,
            partial_fill_policy="reject",
            fee_rate=0.01,
            max_book_staleness_ms=300_000,
        )
        assert cfg.bankroll == 50_000.0
        assert cfg.position_size_mode == "fixed_shares"
        assert cfg.partial_fill_policy == "reject"


class TestSimulationConfigValidation:
    def test_bankroll_zero(self):
        with pytest.raises(ValueError, match="bankroll must be > 0"):
            SimulationConfig(wallet="0x1", bankroll=0)

    def test_bankroll_negative(self):
        with pytest.raises(ValueError, match="bankroll must be > 0"):
            SimulationConfig(wallet="0x1", bankroll=-100)

    def test_detection_delay_negative(self):
        with pytest.raises(ValueError, match="detection_delay_ms must be >= 0"):
            SimulationConfig(wallet="0x1", detection_delay_ms=-1)

    def test_position_size_value_zero(self):
        with pytest.raises(ValueError, match="position_size_value must be > 0"):
            SimulationConfig(wallet="0x1", position_size_value=0)

    def test_max_position_pct_zero(self):
        with pytest.raises(ValueError, match="max_position_pct must be in"):
            SimulationConfig(wallet="0x1", max_position_pct=0)

    def test_max_position_pct_above_one(self):
        with pytest.raises(ValueError, match="max_position_pct must be in"):
            SimulationConfig(wallet="0x1", max_position_pct=1.5)

    def test_fee_rate_negative(self):
        with pytest.raises(ValueError, match="fee_rate must be in"):
            SimulationConfig(wallet="0x1", fee_rate=-0.1)

    def test_fee_rate_one(self):
        with pytest.raises(ValueError, match="fee_rate must be in"):
            SimulationConfig(wallet="0x1", fee_rate=1.0)

    def test_invalid_position_size_mode(self):
        with pytest.raises(ValueError, match="position_size_mode"):
            SimulationConfig(wallet="0x1", position_size_mode="proportional")

    def test_invalid_partial_fill_policy(self):
        with pytest.raises(ValueError, match="partial_fill_policy"):
            SimulationConfig(wallet="0x1", partial_fill_policy="skip")

    def test_max_book_staleness_zero(self):
        with pytest.raises(ValueError, match="max_book_staleness_ms must be > 0"):
            SimulationConfig(wallet="0x1", max_book_staleness_ms=0)

    def test_max_slippage_pct_negative(self):
        with pytest.raises(ValueError, match="max_slippage_pct must be >= 0"):
            SimulationConfig(wallet="0x1", max_slippage_pct=-0.01)

    def test_execution_latency_negative(self):
        with pytest.raises(ValueError, match="execution_latency_ms must be >= 0"):
            SimulationConfig(wallet="0x1", execution_latency_ms=-1)
