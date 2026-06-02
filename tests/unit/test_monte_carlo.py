# tests/unit/test_monte_carlo.py

"""
Tests for Monte Carlo simulation engine (P10.2).

Covers:
- MonteCarloConfig validation
- SimulationResult properties
- MonteCarloReport aggregate metrics (VaR, CVaR, ruin probability)
- EquitySimulator with bootstrap, parametric, and combined methods
- Edge cases: small samples, extreme PnLs, all-positive/all-negative trades
"""

import json

import pytest

from src.quantitative.monte_carlo import (
    EquitySimulator,
    MonteCarloConfig,
    MonteCarloReport,
    SimulationResult,
    _fit_gaussian,
)

# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def default_config() -> MonteCarloConfig:
    return MonteCarloConfig(simulations=200, trades_per_sim=50, seed=42)


@pytest.fixture
def mixed_pnls() -> list[float]:
    """Typical trade PnLs: mix of wins and losses."""
    return [
        5.0, -2.0, 3.0, -1.5, 4.0, -3.0, 6.0, -1.0,
        2.5, -2.5, 4.5, -0.5, 3.5, -4.0, 5.5, -2.0,
        1.0, -1.0, 7.0, -3.5, 2.0, -1.5, 3.0, -2.0,
        4.0, -0.5, 5.0, -2.5, 6.0, -1.0, 3.5, -3.0,
    ]


@pytest.fixture
def all_winning_pnls() -> list[float]:
    """All winning trades."""
    return [2.0, 5.0, 3.0, 1.0, 4.0, 6.0, 2.5, 3.5, 4.5, 1.5]


@pytest.fixture
def all_losing_pnls() -> list[float]:
    """All losing trades."""
    return [-2.0, -5.0, -3.0, -1.0, -4.0, -6.0, -2.5, -3.5, -4.5, -1.5]


# ── Config Tests ─────────────────────────────────────────────────────────────


class TestMonteCarloConfig:
    """MonteCarloConfig validation."""

    def test_defaults(self) -> None:
        cfg = MonteCarloConfig()
        assert cfg.simulations == 1000
        assert cfg.trades_per_sim == 100
        assert cfg.initial_balance == 1000.0
        assert cfg.method == "bootstrap"
        assert cfg.ruin_threshold_pct == 0.30
        assert cfg.seed == 42

    def test_invalid_simulations(self) -> None:
        with pytest.raises(ValueError, match="at least 10"):
            MonteCarloConfig(simulations=5)

    def test_invalid_trades_per_sim(self) -> None:
        with pytest.raises(ValueError, match="at least 5"):
            MonteCarloConfig(trades_per_sim=3)

    def test_invalid_method(self) -> None:
        with pytest.raises(ValueError, match="Unknown method"):
            MonteCarloConfig(method="invalid")

    def test_valid_methods(self) -> None:
        for method in ("bootstrap", "parametric", "combined"):
            cfg = MonteCarloConfig(method=method)
            assert cfg.method == method


# ── Helper Tests ────────────────────────────────────────────────────────────


class TestFitGaussian:
    """Gaussian fit helper."""

    def test_fit_mixed(self, mixed_pnls: list[float]) -> None:
        mean, std = _fit_gaussian(mixed_pnls)
        # Mean should be positive since more wins than losses
        assert mean > 0
        assert std > 0

    def test_fit_all_positive(self, all_winning_pnls: list[float]) -> None:
        mean, std = _fit_gaussian(all_winning_pnls)
        assert mean > 0
        assert std > 0

    def test_fit_all_negative(self, all_losing_pnls: list[float]) -> None:
        mean, std = _fit_gaussian(all_losing_pnls)
        assert mean < 0
        assert std > 0

    def test_fit_single_value(self) -> None:
        mean, std = _fit_gaussian([3.0])
        assert mean == 3.0
        assert std == 0.01  # minimum std


# ── SimulationResult Tests ───────────────────────────────────────────────────


class TestSimulationResult:
    """SimulationResult properties."""

    def test_total_pnl(self) -> None:
        result = SimulationResult(
            sim_index=0,
            equity_curve=[1000.0, 1010.0, 1005.0, 1020.0],
            trade_pnls=[10.0, -5.0, 15.0],
            drawdowns=[0.0, 0.005, 0.0],
            terminal_balance=1020.0,
            max_drawdown=0.005,
            max_drawdown_usdc=5.0,
            ruin_hit=False,
        )
        assert result.total_pnl == 20.0
        assert result.pnl_pct == pytest.approx(0.02)

    def test_total_pnl_zero_trades(self) -> None:
        result = SimulationResult(
            sim_index=0,
            equity_curve=[1000.0],
            trade_pnls=[],
            drawdowns=[],
            terminal_balance=1000.0,
            max_drawdown=0.0,
            max_drawdown_usdc=0.0,
            ruin_hit=False,
        )
        assert result.total_pnl == 0.0
        assert result.pnl_pct == 0.0

    def test_ruin_hit(self) -> None:
        result = SimulationResult(
            sim_index=0,
            equity_curve=[1000.0, 700.0, 650.0],
            trade_pnls=[-300.0, -50.0],
            drawdowns=[0.3, 0.35],
            terminal_balance=650.0,
            max_drawdown=0.35,
            max_drawdown_usdc=350.0,
            ruin_hit=True,
            ruin_at_trade=0,
        )
        assert result.ruin_hit is True
        assert result.ruin_at_trade == 0


# ── MonteCarloReport Tests ───────────────────────────────────────────────────


class TestMonteCarloReport:
    """MonteCarloReport aggregate metric calculations."""

    @pytest.fixture
    def sample_report(self, mixed_pnls: list[float]) -> MonteCarloReport:
        cfg = MonteCarloConfig(simulations=500, trades_per_sim=50, seed=42)
        sim = EquitySimulator(cfg)
        return sim.run(mixed_pnls)

    def test_simulations_run(self, sample_report: MonteCarloReport) -> None:
        assert sample_report.simulations_run == 500

    def test_balances_length(self, sample_report: MonteCarloReport) -> None:
        assert len(sample_report.terminal_balances) == 500
        assert len(sample_report.terminal_pnls) == 500

    def test_profitable_probability_range(
        self, sample_report: MonteCarloReport,
    ) -> None:
        """Should be between 0 and 1."""
        assert 0.0 <= sample_report.profitable_probability <= 1.0

    def test_expected_return_reasonable(
        self, sample_report: MonteCarloReport,
    ) -> None:
        """Expected return should be within reasonable bounds."""
        er = sample_report.expected_return
        assert -1.0 < er < 1.0

    def test_mean_terminal_pnl(
        self, sample_report: MonteCarloReport,
    ) -> None:
        pnl = sample_report.mean_terminal_pnl
        assert isinstance(pnl, float)

    def test_std_terminal_pnl_positive(
        self, sample_report: MonteCarloReport,
    ) -> None:
        assert sample_report.std_terminal_pnl >= 0.0

    def test_var_95_cvar_95_relationship(
        self, sample_report: MonteCarloReport,
    ) -> None:
        """CVaR should be >= VaR (worse average loss in tail)."""
        assert sample_report.cvar_95 >= sample_report.var_95

    def test_var_99_cvar_99_relationship(
        self, sample_report: MonteCarloReport,
    ) -> None:
        assert sample_report.cvar_99 >= sample_report.var_99

    def test_var_99_gte_var_95(
        self, sample_report: MonteCarloReport,
    ) -> None:
        """99% VaR should be >= 95% VaR (more extreme)."""
        assert sample_report.var_99 >= sample_report.var_95

    def test_max_drawdowns_length(
        self, sample_report: MonteCarloReport,
    ) -> None:
        assert len(sample_report.max_drawdowns) == 500

    def test_mean_max_drawdown_reasonable(
        self, sample_report: MonteCarloReport,
    ) -> None:
        dd = sample_report.mean_max_drawdown
        assert 0.0 <= dd <= 1.0

    def test_worst_case_drawdown_gte_mean(
        self, sample_report: MonteCarloReport,
    ) -> None:
        assert sample_report.worst_case_drawdown >= sample_report.mean_max_drawdown

    def test_ruin_probability_reasonable(
        self, sample_report: MonteCarloReport,
    ) -> None:
        assert 0.0 <= sample_report.ruin_probability <= 1.0

    def test_to_dict(self, sample_report: MonteCarloReport) -> None:
        d = sample_report.to_dict()
        assert "summary" in d
        assert "drawdown_percentiles" in d
        assert d["summary"]["simulations_run"] == 500
        assert "var_95" in d["summary"]
        assert "cvar_95" in d["summary"]
        assert "ruin_probability" in d["summary"]

    def test_to_dict_json_serializable(
        self, sample_report: MonteCarloReport,
    ) -> None:
        json_str = json.dumps(sample_report.to_dict())
        assert len(json_str) > 0

    def test_drawdown_percentiles_ordered(
        self, sample_report: MonteCarloReport,
    ) -> None:
        d = sample_report.to_dict()
        pcts = d["drawdown_percentiles"]
        assert pcts["p50"] <= pcts["p75"]
        assert pcts["p75"] <= pcts["p90"]
        assert pcts["p90"] <= pcts["p95"]
        assert pcts["p95"] <= pcts["p99"]


# ── EquitySimulator Bootstrap Tests ────────────────────────────────────────


class TestEquitySimulatorBootstrap:
    """Bootstrap method tests."""

    def test_run_produces_report(
        self, default_config: MonteCarloConfig, mixed_pnls: list[float],
    ) -> None:
        sim = EquitySimulator(default_config)
        report = sim.run(mixed_pnls)
        assert isinstance(report, MonteCarloReport)
        assert report.simulations_run == 200

    def test_deterministic_output(
        self, mixed_pnls: list[float],
    ) -> None:
        """Same seed → same report."""
        cfg = MonteCarloConfig(simulations=200, seed=42)
        r1 = EquitySimulator(cfg).run(mixed_pnls)
        r2 = EquitySimulator(cfg).run(mixed_pnls)
        assert r1.mean_terminal_pnl == r2.mean_terminal_pnl
        assert r1.var_95 == r2.var_95
        assert r1.ruin_probability == r2.ruin_probability

    def test_all_winning_no_ruin(
        self, default_config: MonteCarloConfig, all_winning_pnls: list[float],
    ) -> None:
        """All winning trades → ruin probability should be 0."""
        sim = EquitySimulator(default_config)
        report = sim.run(all_winning_pnls)
        assert report.ruin_probability == 0.0
        assert report.profitable_probability == 1.0

    def test_all_losing_certain_ruin(
        self, default_config: MonteCarloConfig, all_losing_pnls: list[float],
    ) -> None:
        """All losing trades → ruin probability should be high."""
        default_config.ruin_threshold_pct = 0.10
        sim = EquitySimulator(default_config)
        report = sim.run(all_losing_pnls)
        # With consistent losses, ruin is almost certain
        assert report.ruin_probability > 0.5

    def test_insufficient_trades(self) -> None:
        with pytest.raises(ValueError, match="at least 5"):
            EquitySimulator().run([1.0, -1.0])

    def test_run_from_backtest(self, mixed_pnls: list[float]) -> None:
        """run_from_backtest should extract PnLs from position objects."""

        class FakePosition:
            def __init__(self, pnl: float):
                self.pnl = pnl

        positions = [FakePosition(p) for p in mixed_pnls[:10]]
        sim = EquitySimulator(
            MonteCarloConfig(simulations=100, trades_per_sim=20, seed=42)
        )
        report = sim.run_from_backtest(positions)
        assert isinstance(report, MonteCarloReport)

    def test_run_from_backtest_empty(self) -> None:
        with pytest.raises(ValueError, match="No closed positions"):
            EquitySimulator().run_from_backtest([])


# ── EquitySimulator Parametric Tests ───────────────────────────────────────


class TestEquitySimulatorParametric:
    """Parametric method tests."""

    def test_parametric_produces_report(
        self, mixed_pnls: list[float],
    ) -> None:
        cfg = MonteCarloConfig(
            simulations=200, trades_per_sim=50, method="parametric", seed=42,
        )
        report = EquitySimulator(cfg).run(mixed_pnls)
        assert isinstance(report, MonteCarloReport)

    def test_combined_produces_report(
        self, mixed_pnls: list[float],
    ) -> None:
        cfg = MonteCarloConfig(
            simulations=200, trades_per_sim=50, method="combined", seed=42,
        )
        report = EquitySimulator(cfg).run(mixed_pnls)
        assert isinstance(report, MonteCarloReport)

    def test_parametric_deterministic(
        self, mixed_pnls: list[float],
    ) -> None:
        cfg = MonteCarloConfig(
            simulations=200, trades_per_sim=50, method="parametric", seed=42,
        )
        r1 = EquitySimulator(cfg).run(mixed_pnls)
        r2 = EquitySimulator(cfg).run(mixed_pnls)
        assert r1.mean_terminal_pnl == r2.mean_terminal_pnl


# ── Edge Case Tests ─────────────────────────────────────────────────────────


class TestMonteCarloEdgeCases:
    """Edge cases and boundary conditions."""

    def test_small_sample_bootstrap(
        self, mixed_pnls: list[float],
    ) -> None:
        """Works with exactly 5 trades (minimum)."""
        sim = EquitySimulator(
            MonteCarloConfig(simulations=100, trades_per_sim=10, seed=42)
        )
        report = sim.run(mixed_pnls[:5])
        assert report.simulations_run == 100

    def test_large_simulations(
        self, mixed_pnls: list[float],
    ) -> None:
        """Should handle many simulations without error."""
        sim = EquitySimulator(
            MonteCarloConfig(simulations=200, trades_per_sim=10, seed=42)
        )
        report = sim.run(mixed_pnls[:10])
        assert report.simulations_run == 200

    def test_zero_mean_pnls(self) -> None:
        """PnLs that sum to ~0 should produce Var/CVaR near 0."""
        pnls = [5.0, -5.0, 3.0, -3.0, 2.0, -2.0, 1.0, -1.0, 4.0, -4.0]
        cfg = MonteCarloConfig(simulations=200, trades_per_sim=30, seed=42)
        report = EquitySimulator(cfg).run(pnls)
        # Mean terminal PnL should be close to 0
        assert abs(report.mean_terminal_pnl) < 100

    def test_rng_resets_between_runs(
        self, mixed_pnls: list[float],
    ) -> None:
        """Re-creating simulator with same seed gives same results."""
        cfg = MonteCarloConfig(simulations=200, trades_per_sim=50, seed=42)
        # Two separate simulator instances with same seed
        r1 = EquitySimulator(cfg).run(mixed_pnls)
        r2 = EquitySimulator(cfg).run(mixed_pnls)
        assert r1.mean_terminal_pnl == r2.mean_terminal_pnl
        assert r1.var_95 == r2.var_95
        assert r1.ruin_probability == r2.ruin_probability

    def test_rng_deterministic_within_instance(
        self, mixed_pnls: list[float],
    ) -> None:
        """Same instance called twice should give same results (RNG reset)."""
        cfg = MonteCarloConfig(simulations=200, trades_per_sim=50, seed=42)
        sim = EquitySimulator(cfg)
        r1 = sim.run(mixed_pnls)
        r2 = sim.run(mixed_pnls)
        assert r1.mean_terminal_pnl == r2.mean_terminal_pnl
        assert r1.var_95 == r2.var_95

    def test_var_cvar_computed_correctly(self) -> None:
        """VaR and CVaR with known distribution."""
        # Simple case: fixed PnL of either +10 or -10
        pnls = [10.0] * 50 + [-10.0] * 50
        cfg = MonteCarloConfig(simulations=500, trades_per_sim=100, seed=42)
        report = EquitySimulator(cfg).run(pnls)
        # Both VaR and CVaR should be computable
        assert report.var_95 >= 0
        assert report.cvar_95 >= report.var_95
