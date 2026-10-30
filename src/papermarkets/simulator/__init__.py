"""Copy-trading simulation engine."""

from pathlib import Path

from papermarkets.simulator.config import SimulationConfig
from papermarkets.simulator.result import SimulationResult


def simulate(
    config: SimulationConfig,
    data_dir: str | Path = "data/output",
) -> SimulationResult:
    """Run a copy-trading simulation."""
    from papermarkets.simulator.engine import run_simulation

    return run_simulation(config, Path(data_dir))


__all__ = ["simulate", "SimulationConfig", "SimulationResult"]
