# src/quantitative/monte_carlo.py

"""
Monte Carlo simulation engine for equity curve analysis.

Generates synthetic equity paths by resampling historical trade PnLs
to estimate drawdown distributions, tail risk, and worst-case scenarios.

Architecture:
    Historical PnLs → EquitySimulator
        ├── Bootstrap resampling (with replacement)
        ├── Parametric resampling (Gaussian fit)
        └── Combined resampling
            │
            ├── N simulated equity curves
            ├── Drawdown distributions per simulation
            └── Aggregate tail risk (VaR, CVaR, ruin probability)

Usage:
    from src.quantitative.monte_carlo import EquitySimulator, MonteCarloConfig

    config = MonteCarloConfig(simulations=1000, trades_per_sim=100)
    simulator = EquitySimulator(config)
    report = simulator.run(trade_pnls=[-2.5, 5.0, -1.0, ...])

    print(f"VaR 95%: {report.var_95:.2f} USDC")
    print(f"CVaR 95%: {report.cvar_95:.2f} USDC")
    print(f"Ruin probability: {report.ruin_probability:.2%}")
"""

import math
import random
from dataclasses import dataclass, field
from typing import Optional

import structlog

logger = structlog.get_logger(__name__)


# ── Configuration ────────────────────────────────────────────────────────────


@dataclass
class MonteCarloConfig:
    """Configuration for Monte Carlo equity simulation."""

    simulations: int = 1000
    """Number of simulated equity paths to generate."""

    trades_per_sim: int = 100
    """Number of trades to simulate per path. If None, uses len(trade_pnls)."""

    initial_balance: float = 1000.0
    """Starting equity for all simulations."""

    method: str = "bootstrap"
    """Resampling method: 'bootstrap' (empirical), 'parametric' (Gaussian),
    or 'combined' (both)."""

    ruin_threshold_pct: float = 0.30
    """Equity drawdown threshold to define 'ruin' (as fraction of initial balance).
    0.30 means ruin = equity drops below 70% of initial."""

    seed: int = 42
    """Random seed for reproducibility."""

    def __post_init__(self) -> None:
        if self.simulations < 10:
            raise ValueError(
                f"Need at least 10 simulations, got {self.simulations}"
            )
        if self.trades_per_sim < 5:
            raise ValueError(
                f"Need at least 5 trades per simulation, got {self.trades_per_sim}"
            )
        if self.method not in ("bootstrap", "parametric", "combined"):
            raise ValueError(
                f"Unknown method '{self.method}'. "
                "Use 'bootstrap', 'parametric', or 'combined'."
            )


# ── Results ──────────────────────────────────────────────────────────────────


@dataclass
class SimulationResult:
    """Result of a single Monte Carlo simulation path."""

    sim_index: int
    """0-based simulation index."""

    equity_curve: list[float]
    """Equity after each trade. Length = trades_per_sim + 1
    (includes initial balance at index 0)."""

    trade_pnls: list[float]
    """The PnL of each trade in this simulation."""

    drawdowns: list[float]
    """Drawdown from peak after each trade. Always >= 0."""

    terminal_balance: float
    """Final equity after all trades."""

    max_drawdown: float
    """Maximum peak-to-trough drawdown as a fraction."""

    max_drawdown_usdc: float
    """Maximum peak-to-trough drawdown in USDC."""

    ruin_hit: bool
    """Whether this simulation hit the ruin threshold at any point."""

    ruin_at_trade: Optional[int] = None
    """Trade index where ruin was first hit (None if never hit)."""

    @property
    def total_pnl(self) -> float:
        """Total PnL = terminal_balance - initial_balance."""
        if len(self.equity_curve) < 2:
            return 0.0
        return self.equity_curve[-1] - self.equity_curve[0]

    @property
    def pnl_pct(self) -> float:
        """Total PnL as percentage of initial balance."""
        if not self.equity_curve or self.equity_curve[0] == 0:
            return 0.0
        return self.total_pnl / self.equity_curve[0]


@dataclass
class MonteCarloReport:
    """Aggregate Monte Carlo simulation report."""

    config: MonteCarloConfig
    """Configuration used for the simulation."""

    simulations_run: int
    """Number of simulations actually completed."""

    trades_per_sim: int
    """Number of trades per simulation."""

    historical_trades: int
    """Number of historical trades used for resampling."""

    # ── Terminal PnL distribution ──
    terminal_balances: list[float]
    terminal_pnls: list[float]

    @property
    def mean_terminal_balance(self) -> float:
        vals = self.terminal_balances
        return sum(vals) / len(vals) if vals else 0.0

    @property
    def median_terminal_balance(self) -> float:
        vals = sorted(self.terminal_balances)
        if not vals:
            return 0.0
        mid = len(vals) // 2
        if len(vals) % 2 == 0:
            return (vals[mid - 1] + vals[mid]) / 2
        return vals[mid]

    @property
    def mean_terminal_pnl(self) -> float:
        vals = self.terminal_pnls
        return sum(vals) / len(vals) if vals else 0.0

    @property
    def std_terminal_pnl(self) -> float:
        vals = self.terminal_pnls
        if len(vals) < 2:
            return 0.0
        mean = self.mean_terminal_pnl
        return math.sqrt(
            sum((v - mean) ** 2 for v in vals) / (len(vals) - 1)
        )

    # ── Profitability ──

    @property
    def profitable_probability(self) -> float:
        """Probability of ending profitable (P(terminal_pnl > 0))."""
        if not self.terminal_pnls:
            return 0.0
        return sum(1 for p in self.terminal_pnls if p > 0) / len(self.terminal_pnls)

    @property
    def expected_return(self) -> float:
        """Expected return = mean_terminal_pnl / initial_balance."""
        bal = self.config.initial_balance
        return self.mean_terminal_pnl / bal if bal > 0 else 0.0

    # ── Drawdown distribution ──
    # (populated by the simulator)
    max_drawdowns: list[float] = field(default_factory=list)

    @property
    def mean_max_drawdown(self) -> float:
        vals = self.max_drawdowns
        return sum(vals) / len(vals) if vals else 0.0

    @property
    def median_max_drawdown(self) -> float:
        vals = sorted(self.max_drawdowns)
        if not vals:
            return 0.0
        mid = len(vals) // 2
        if len(vals) % 2 == 0:
            return (vals[mid - 1] + vals[mid]) / 2
        return vals[mid]

    @property
    def worst_case_drawdown(self) -> float:
        """Maximum drawdown across all simulations."""
        return max(self.max_drawdowns) if self.max_drawdowns else 0.0

    # ── Tail Risk (VaR / CVaR) ──

    @property
    def var_95(self) -> float:
        """Value at Risk 95% — 95th percentile worst PnL (as positive number).
        There is a 5% chance of losing MORE than this amount.
        Example: var_95 = 50 means 5% chance of losing > 50 USDC."""
        return self._percentile(self.terminal_pnls, 5.0)

    @property
    def var_99(self) -> float:
        """Value at Risk 99% — 99th percentile worst PnL."""
        return self._percentile(self.terminal_pnls, 1.0)

    @property
    def cvar_95(self) -> float:
        """Conditional Value at Risk 95% — expected loss GIVEN loss exceeds VaR95.
        Always >= var_95. Example: cvar_95 = 75 means when things go wrong
        (bottom 5%), the average loss is 75 USDC."""
        return self._expected_tail_loss(self.terminal_pnls, 5.0)

    @property
    def cvar_99(self) -> float:
        """Conditional Value at Risk 99%."""
        return self._expected_tail_loss(self.terminal_pnls, 1.0)

    # ── Ruin Probability ──
    # (populated by the simulator)
    ruin_count: int = 0

    @property
    def ruin_probability(self) -> float:
        """Probability of hitting the ruin threshold in any simulation."""
        if self.simulations_run == 0:
            return 0.0
        return self.ruin_count / self.simulations_run

    # ── Serialization ────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        """Serialize report to a JSON-friendly dictionary."""
        return {
            "config": {
                "simulations": self.config.simulations,
                "trades_per_sim": self.config.trades_per_sim,
                "initial_balance": self.config.initial_balance,
                "method": self.config.method,
                "ruin_threshold_pct": self.config.ruin_threshold_pct,
                "seed": self.config.seed,
            },
            "summary": {
                "simulations_run": self.simulations_run,
                "trades_per_sim": self.trades_per_sim,
                "historical_trades": self.historical_trades,
                "mean_terminal_balance": round(self.mean_terminal_balance, 2),
                "median_terminal_balance": round(self.median_terminal_balance, 2),
                "mean_terminal_pnl": round(self.mean_terminal_pnl, 2),
                "std_terminal_pnl": round(self.std_terminal_pnl, 2),
                "profitable_probability": round(self.profitable_probability, 4),
                "expected_return": round(self.expected_return, 4),
                "mean_max_drawdown": round(self.mean_max_drawdown, 4),
                "median_max_drawdown": round(self.median_max_drawdown, 4),
                "worst_case_drawdown": round(self.worst_case_drawdown, 4),
                "var_95": round(self.var_95, 2),
                "var_99": round(self.var_99, 2),
                "cvar_95": round(self.cvar_95, 2),
                "cvar_99": round(self.cvar_99, 2),
                "ruin_probability": round(self.ruin_probability, 4),
            },
            "drawdown_percentiles": {
                "p50": round(self._dd_percentile(50), 4),
                "p75": round(self._dd_percentile(75), 4),
                "p90": round(self._dd_percentile(90), 4),
                "p95": round(self._dd_percentile(95), 4),
                "p99": round(self._dd_percentile(99), 4),
            },
        }

    # ── Internal helpers ─────────────────────────────────────────────────

    def _percentile(self, values: list[float], pct: float) -> float:
        """Return the pct-th percentile of values (as worst loss = positive)."""
        if not values:
            return 0.0
        sorted_vals = sorted(values)
        idx = int(math.ceil(pct / 100.0 * len(sorted_vals))) - 1
        return abs(min(0.0, sorted_vals[max(0, min(idx, len(sorted_vals) - 1))]))

    def _expected_tail_loss(self, values: list[float], pct: float) -> float:
        """Expected loss in the worst pct% of outcomes."""
        if not values:
            return 0.0
        var_val = self._percentile(values, pct)
        if var_val == 0.0:
            return 0.0
        threshold = -var_val
        tail = [v for v in values if v <= threshold]
        if not tail:
            return 0.0
        return abs(sum(tail) / len(tail))

    def _dd_percentile(self, pct: float) -> float:
        vals = sorted(self.max_drawdowns)
        if not vals:
            return 0.0
        idx = int(math.ceil(pct / 100.0 * len(vals))) - 1
        return min(1.0, vals[max(0, min(idx, len(vals) - 1))])


# ── Simulator ────────────────────────────────────────────────────────────────


class EquitySimulator:
    """
    Monte Carlo equity curve simulator.

    Generates synthetic equity paths by resampling historical trade PnLs,
    computes drawdowns per path, and aggregates tail risk statistics.

    Methods:
    - bootstrap: random sampling with replacement from historical PnLs
    - parametric: Gaussian fit + random sampling
    - combined: mixture of bootstrap and parametric

    Uses the existing MonteCarloReport for aggregate statistics.
    """

    def __init__(self, config: MonteCarloConfig | None = None):
        self._config = config or MonteCarloConfig()
        self._rng = random.Random(self._config.seed)

    # ── Public API ─────────────────────────────────────────────────────────

    def run(self, trade_pnls: list[float]) -> MonteCarloReport:
        """
        Run Monte Carlo simulation on historical trade PnLs.

        Args:
            trade_pnls: List of historical trade PnLs in USDC.
                        Must have at least 5 trades.

        Returns:
            MonteCarloReport with aggregate statistics.
        """
        if len(trade_pnls) < 5:
            raise ValueError(
                f"Need at least 5 historical trades, got {len(trade_pnls)}"
            )

        trades_per_sim = self._config.trades_per_sim
        if trades_per_sim > len(trade_pnls) * 3:
            logger.warning(
                "monte_carlo_trades_warning",
                trades_per_sim=trades_per_sim,
                historical_trades=len(trade_pnls),
                message="Many more simulated trades than historical — "
                        "results may overfit to small sample",
            )

        logger.info(
            "monte_carlo_starting",
            simulations=self._config.simulations,
            trades_per_sim=trades_per_sim,
            historical_trades=len(trade_pnls),
            method=self._config.method,
        )

        # Reset RNG for deterministic runs
        self._rng = random.Random(self._config.seed)

        # Fit parametric model if needed
        fitted_dist = None
        if self._config.method in ("parametric", "combined"):
            fitted_dist = _fit_gaussian(trade_pnls)

        # Run simulations
        all_balances: list[float] = []
        all_pnls: list[float] = []
        all_max_dds: list[float] = []
        ruin_count = 0

        for sim_idx in range(self._config.simulations):
            result = self._simulate_one(
                sim_idx, trade_pnls, trades_per_sim, fitted_dist,
            )

            all_balances.append(result.terminal_balance)
            all_pnls.append(result.total_pnl)
            all_max_dds.append(result.max_drawdown)
            if result.ruin_hit:
                ruin_count += 1

        report = MonteCarloReport(
            config=self._config,
            simulations_run=self._config.simulations,
            trades_per_sim=trades_per_sim,
            historical_trades=len(trade_pnls),
            terminal_balances=all_balances,
            terminal_pnls=all_pnls,
            max_drawdowns=all_max_dds,
            ruin_count=ruin_count,
        )

        logger.info(
            "monte_carlo_complete",
            mean_pnl=round(report.mean_terminal_pnl, 2),
            var_95=round(report.var_95, 2),
            cvar_95=round(report.cvar_95, 2),
            ruin_pct=round(report.ruin_probability, 4),
            profitable_pct=round(report.profitable_probability, 4),
        )

        return report

    def run_from_backtest(
        self,
        positions: list,
        trades_per_sim: int | None = None,
    ) -> MonteCarloReport:
        """
        Convenience method: extract PnLs from BacktestPosition objects
        and run Monte Carlo simulation.

        Args:
            positions: List of BacktestPosition objects with .pnl attribute.
            trades_per_sim: Override trades_per_sim from config.

        Returns:
            MonteCarloReport.
        """
        pnls = [p.pnl for p in positions if p.pnl is not None]
        if not pnls:
            raise ValueError("No closed positions with PnL found")

        # Temporarily override trades_per_sim if provided
        if trades_per_sim is not None:
            saved = self._config.trades_per_sim
            self._config.trades_per_sim = trades_per_sim
            try:
                return self.run(pnls)
            finally:
                self._config.trades_per_sim = saved

        return self.run(pnls)

    # ── Internal ───────────────────────────────────────────────────────────

    def _simulate_one(
        self,
        sim_index: int,
        historical_pnls: list[float],
        trades: int,
        fitted_dist: Optional[tuple[float, float]],
    ) -> SimulationResult:
        """Generate a single equity path."""
        balance = self._config.initial_balance
        equity_curve = [balance]
        trade_pnls: list[float] = []
        drawdowns: list[float] = []
        peak = balance
        max_dd = 0.0
        max_dd_usdc = 0.0
        ruin_hit = False
        ruin_at = None

        ruin_floor = self._config.initial_balance * (
            1.0 - self._config.ruin_threshold_pct
        )

        for i in range(trades):
            pnl = self._sample_pnl(historical_pnls, fitted_dist)
            balance += pnl
            trade_pnls.append(pnl)
            equity_curve.append(balance)

            # Drawdown
            peak = max(peak, balance)
            dd = (peak - balance) / peak if peak > 0 else 0.0
            drawdowns.append(dd)
            if dd > max_dd:
                max_dd = dd
                max_dd_usdc = peak - balance

            # Ruin check
            if not ruin_hit and balance < ruin_floor:
                ruin_hit = True
                ruin_at = i

        return SimulationResult(
            sim_index=sim_index,
            equity_curve=equity_curve,
            trade_pnls=trade_pnls,
            drawdowns=drawdowns,
            terminal_balance=balance,
            max_drawdown=max_dd,
            max_drawdown_usdc=max_dd_usdc,
            ruin_hit=ruin_hit,
            ruin_at_trade=ruin_at,
        )

    def _sample_pnl(
        self,
        historical: list[float],
        fitted_dist: Optional[tuple[float, float]],
    ) -> float:
        """Sample a single trade PnL using the configured method."""
        method = self._config.method

        if method == "bootstrap":
            return self._rng.choice(historical)

        elif method in ("parametric", "combined"):
            if method == "combined" and self._rng.random() < 0.7:
                return self._rng.choice(historical)
            if fitted_dist is None:
                fitted_dist = _fit_gaussian(historical)
            mean, std = fitted_dist
            return self._rng.gauss(mean, max(std, 0.01))

        return self._rng.choice(historical)


# ── Helper ───────────────────────────────────────────────────────────────────


def _fit_gaussian(pnls: list[float]) -> tuple[float, float]:
    """Fit a Gaussian distribution to trade PnLs. Returns (mean, std)."""
    n = len(pnls)
    if n < 2:
        return (pnls[0] if n == 1 else 0.0, 0.01)
    mean = sum(pnls) / n
    variance = sum((p - mean) ** 2 for p in pnls) / (n - 1)
    return (mean, math.sqrt(max(variance, 1e-6)))
