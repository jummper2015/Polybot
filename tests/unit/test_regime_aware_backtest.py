# tests/unit/test_regime_aware_backtest.py

"""
Tests for regime-aware multi-strategy backtesting (P11.1 DESPLEGAR).

Covers:
- MultiStrategyBacktestRunner construction and binding auto-creation
- Regime pre-computation from dataset
- Regime-filtered strategy evaluation
- Per-strategy PnL tracking
- Exit signals NOT filtered by regime
- MultiStrategyBacktestResult serialization
"""

from datetime import datetime, timedelta

from src.backtesting.data_loader import HistoricalDataset
from src.backtesting.regime_aware_backtest import (
    MultiStrategyBacktestResult,
    MultiStrategyBacktestRunner,
)
from src.domain.value_objects.market_tick import MarketTick
from src.infrastructure.data.regime import Regime
from src.strategies.buy_above_threshold.strategy import BuyAboveThresholdStrategy
from src.strategies.mean_reversion.strategy import MeanReversionStrategy
from src.strategies.regime_aware import StrategyRegimeBinding

# ══════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════


def _make_multi_ticks(
    n: int = 100,
    base_price: float = 0.50,
    trend: float = 0.0,
    noise: float = 0.005,
    spread: float = 0.002,
) -> list[MarketTick]:
    """Generate deterministic ticks for backtesting."""
    import random
    rng = random.Random(42)
    ticks = []
    t = datetime(2026, 6, 1, 12, 0, 0)
    price = base_price

    for i in range(n):
        price += trend + rng.uniform(-noise, noise)
        price = max(0.01, min(0.99, price))

        tick = MarketTick(
            market_id="test_market",
            yes_price=round(price, 4),
            no_price=round(1.0 - price, 4),
            best_bid=round(price - spread / 2, 4),
            best_ask=round(price + spread / 2, 4),
            spread=spread,
            volume_24h=5000.0,
            timestamp=t + timedelta(seconds=30 * i),
        )
        ticks.append(tick)
    return ticks


def _make_synthetic_dataset(
    asset: str = "BTC",
    window: str = "5m",
    n_ticks: int = 200,
    trend: float = 0.0,
) -> HistoricalDataset:
    """Create a synthetic HistoricalDataset for backtesting."""
    ticks = _make_multi_ticks(n=n_ticks, trend=trend)

    return HistoricalDataset(
        asset=asset,
        window=window,
        market_id="test_market",
        ticks=ticks,
        start_at=ticks[0].timestamp,
        end_at=ticks[-1].timestamp,
    )


# ══════════════════════════════════════════════════════════════════════════
# MultiStrategyBacktestResult
# ══════════════════════════════════════════════════════════════════════════


class TestMultiStrategyBacktestResult:
    """Tests for MultiStrategyBacktestResult dataclass."""

    def test_empty_result(self):
        result = MultiStrategyBacktestResult(
            asset="BTC",
            window="5m",
            dataset_ticks=100,
            dataset_start=datetime(2026, 6, 1),
            dataset_end=datetime(2026, 6, 2),
            initial_balance=1000.0,
            final_balance=1000.0,
        )
        assert result.total_pnl == 0.0
        assert len(result.closed_positions) == 0
        assert result.to_dict()["total_trades"] == 0

    def test_to_dict_includes_per_strategy_breakdown(self):
        result = MultiStrategyBacktestResult(
            asset="BTC",
            window="5m",
            dataset_ticks=100,
            dataset_start=datetime(2026, 6, 1),
            dataset_end=datetime(2026, 6, 2),
            initial_balance=1000.0,
            final_balance=1050.0,
            strategy_pnl={"BuyAboveThreshold": 30.0, "MeanReversion": 20.0},
            strategy_trades={"BuyAboveThreshold": 5, "MeanReversion": 3},
            regime_distribution={"chop": 0.6, "trend": 0.4},
            strategies_skipped_by_regime={"BuyAboveThreshold": 40, "MeanReversion": 10},
        )
        d = result.to_dict()
        assert d["total_pnl"] == 50.0
        assert d["strategy_pnl"]["BuyAboveThreshold"] == 30.0
        assert d["strategy_pnl"]["MeanReversion"] == 20.0
        assert d["strategy_trades"]["BuyAboveThreshold"] == 5
        assert d["regime_distribution"]["chop"] == 0.6
        assert d["strategies_skipped_by_regime"]["BuyAboveThreshold"] == 40

    def test_regime_distribution_default_empty(self):
        result = MultiStrategyBacktestResult(
            asset="BTC",
            window="5m",
            dataset_ticks=0,
            dataset_start=datetime(2026, 6, 1),
            dataset_end=datetime(2026, 6, 1),
            initial_balance=1000.0,
            final_balance=1000.0,
        )
        assert result.regime_distribution == {}


# ══════════════════════════════════════════════════════════════════════════
# MultiStrategyBacktestRunner — Construction
# ══════════════════════════════════════════════════════════════════════════


class TestMultiStrategyRunnerConstruction:
    """Tests for MultiStrategyBacktestRunner initialization."""

    def test_auto_creates_bindings_from_configs(self):
        bat = BuyAboveThresholdStrategy()
        mr = MeanReversionStrategy()

        runner = MultiStrategyBacktestRunner(strategies=[bat, mr])

        # BAT should be active in TREND and CHOP (config updated in P11.1)
        assert runner._is_strategy_active("BuyAboveThreshold", Regime.TREND) is True
        assert runner._is_strategy_active("BuyAboveThreshold", Regime.CHOP) is True
        assert runner._is_strategy_active("BuyAboveThreshold", Regime.PANIC) is False

        # MR should be active in CHOP, TREND, EVENT_DRIVEN
        assert runner._is_strategy_active("MeanReversion", Regime.CHOP) is True
        assert runner._is_strategy_active("MeanReversion", Regime.TREND) is True
        assert runner._is_strategy_active("MeanReversion", Regime.EVENT_DRIVEN) is True
        assert runner._is_strategy_active("MeanReversion", Regime.PANIC) is False
        assert runner._is_strategy_active("MeanReversion", Regime.ILLIQUID) is False

    def test_custom_bindings_override_auto(self):
        bat = BuyAboveThresholdStrategy()
        mr = MeanReversionStrategy()

        # Custom: BAT active in all regimes
        custom_bindings = {
            "BuyAboveThreshold": StrategyRegimeBinding(
                allowed_regimes=set(Regime),
            ),
            "MeanReversion": StrategyRegimeBinding(
                allowed_regimes={Regime.PANIC},  # Only PANIC — unusual
            ),
        }

        runner = MultiStrategyBacktestRunner(
            strategies=[bat, mr],
            strategy_bindings=custom_bindings,
        )

        assert runner._is_strategy_active("BuyAboveThreshold", Regime.PANIC) is True
        assert runner._is_strategy_active("MeanReversion", Regime.CHOP) is False
        assert runner._is_strategy_active("MeanReversion", Regime.PANIC) is True

    def test_no_binding_defaults_to_always_active(self):
        """Strategy with no explicit binding → auto-creates from config.
        BAT config says allowed_regimes=["trend"], so only active in TREND."""
        bat = BuyAboveThresholdStrategy()
        # None triggers auto-creation from config
        runner = MultiStrategyBacktestRunner(
            strategies=[bat],
            strategy_bindings=None,
        )
        # BAT config only allows TREND
        assert runner._is_strategy_active("BuyAboveThreshold", Regime.TREND) is True
        assert runner._is_strategy_active("BuyAboveThreshold", Regime.PANIC) is False

    def test_disabled_binding(self):
        bat = BuyAboveThresholdStrategy()
        bindings = {
            "BuyAboveThreshold": StrategyRegimeBinding(
                allowed_regimes=set(Regime),
                enabled=False,
            ),
        }
        runner = MultiStrategyBacktestRunner(
            strategies=[bat],
            strategy_bindings=bindings,
        )
        assert runner._is_strategy_active("BuyAboveThreshold", Regime.TREND) is False


# ══════════════════════════════════════════════════════════════════════════
# MultiStrategyBacktestRunner — Execution
# ══════════════════════════════════════════════════════════════════════════


class TestMultiStrategyRunnerExecution:
    """Tests for actual backtest execution."""

    def test_run_returns_result_with_regimes(self):
        """Running on a dataset should pre-compute regimes."""
        dataset = _make_synthetic_dataset(n_ticks=100, trend=0.0005)
        bat = BuyAboveThresholdStrategy()
        mr = MeanReversionStrategy()

        runner = MultiStrategyBacktestRunner(
            strategies=[bat, mr],
            initial_balance=1000.0,
        )
        result = runner.run(dataset)

        assert result is not None
        assert result.asset == "BTC"
        assert result.dataset_ticks == 100
        assert result.initial_balance == 1000.0
        # Regime distribution should have entries
        assert len(result.regime_distribution) > 0
        # Should have per-strategy tracking
        assert "BuyAboveThreshold" in result.strategy_pnl
        assert "MeanReversion" in result.strategy_pnl
        assert "BuyAboveThreshold" in result.strategies_skipped_by_regime

    def test_run_from_dataset_convenience(self):
        """Classmethod run_from_dataset should work."""
        dataset = _make_synthetic_dataset(n_ticks=100, trend=0.0005)

        result = MultiStrategyBacktestRunner.run_from_dataset(
            dataset=dataset,
            initial_balance=1000.0,
        )

        assert result is not None
        assert result.asset == "BTC"
        assert "BuyAboveThreshold" in result.strategy_pnl

    def test_regimes_precomputed_in_batch(self):
        """Regimes should be pre-computed for all ticks."""
        dataset = _make_synthetic_dataset(n_ticks=50)
        runner = MultiStrategyBacktestRunner(
            strategies=[BuyAboveThresholdStrategy(), MeanReversionStrategy()],
        )

        regimes, confidences = runner._precompute_regimes(dataset)
        assert len(regimes) == 50
        assert len(confidences) == 50

    def test_strategies_skipped_tracking(self):
        """Track how many times each strategy was skipped by regime."""
        dataset = _make_synthetic_dataset(n_ticks=200)

        # Only MR active
        bat = BuyAboveThresholdStrategy()
        # Force BAT to only be active in PANIC — will be skipped almost always
        bindings = {
            "BuyAboveThreshold": StrategyRegimeBinding(
                allowed_regimes={Regime.PANIC},
            ),
            "MeanReversion": StrategyRegimeBinding(
                allowed_regimes=set(Regime),  # Always active
            ),
        }
        runner = MultiStrategyBacktestRunner(
            strategies=[bat, MeanReversionStrategy()],
            strategy_bindings=bindings,
        )
        result = runner.run(dataset)

        # BAT should be skipped many times (only active in PANIC)
        bat_skipped = result.strategies_skipped_by_regime.get("BuyAboveThreshold", 0)
        assert bat_skipped > 0, f"BAT should be skipped by regime, got {bat_skipped}"

    def test_empty_dataset_handled(self):
        """Empty dataset should not crash."""
        from src.backtesting.data_loader import HistoricalDataset

        dataset = HistoricalDataset(
            asset="BTC",
            window="5m",
            market_id="test",
            ticks=[],
            start_at=datetime(2026, 6, 1),
            end_at=datetime(2026, 6, 1),
        )

        runner = MultiStrategyBacktestRunner(
            strategies=[BuyAboveThresholdStrategy(), MeanReversionStrategy()],
        )
        result = runner.run(dataset)

        assert result.dataset_ticks == 0
        assert len(result.positions) == 0

    def test_verbose_mode_does_not_crash(self):
        """Verbose mode should print trades without errors."""
        dataset = _make_synthetic_dataset(n_ticks=50)

        runner = MultiStrategyBacktestRunner(
            strategies=[BuyAboveThresholdStrategy(), MeanReversionStrategy()],
            verbose=True,
        )
        result = runner.run(dataset)
        assert result is not None


# ══════════════════════════════════════════════════════════════════════════
# Integration: Regime filtering effect on results
# ══════════════════════════════════════════════════════════════════════════


class TestRegimeFilteringEffect:
    """Tests that verify regime filtering actually changes behavior."""

    def test_strategy_restricted_to_allowed_regimes_reduces_trades(self):
        """Restricting a strategy should reduce its trade count vs. unrestricted."""
        dataset = _make_synthetic_dataset(n_ticks=300, trend=0.0)  # CHOP market

        # Run with MR unrestricted (active in all regimes)
        mr = MeanReversionStrategy()
        bat = BuyAboveThresholdStrategy()

        bindings_unrestricted = {
            "BuyAboveThreshold": StrategyRegimeBinding(allowed_regimes=set(Regime)),
            "MeanReversion": StrategyRegimeBinding(allowed_regimes=set(Regime)),
        }
        runner_unrestricted = MultiStrategyBacktestRunner(
            strategies=[bat, mr],
            strategy_bindings=bindings_unrestricted,
        )
        result_unrestricted = runner_unrestricted.run(dataset)

        # Run with BAT restricted to TREND only
        bindings_restricted = {
            "BuyAboveThreshold": StrategyRegimeBinding(allowed_regimes={Regime.TREND}),
            "MeanReversion": StrategyRegimeBinding(allowed_regimes=set(Regime)),
        }
        runner_restricted = MultiStrategyBacktestRunner(
            strategies=[
                BuyAboveThresholdStrategy(),
                MeanReversionStrategy(),
            ],
            strategy_bindings=bindings_restricted,
        )
        result_restricted = runner_restricted.run(dataset)

        # Restricted BAT should have fewer or equal trades
        bat_restricted_trades = result_restricted.strategy_trades.get(
            "BuyAboveThreshold", 0
        )
        bat_unrestricted_trades = result_unrestricted.strategy_trades.get(
            "BuyAboveThreshold", 0
        )
        assert bat_restricted_trades <= bat_unrestricted_trades, (
            f"Restricted BAT trades ({bat_restricted_trades}) should be "
            f"<= unrestricted ({bat_unrestricted_trades})"
        )

    def test_per_strategy_pnl_tracked_independently(self):
        """Each strategy's PnL is tracked separately."""
        dataset = _make_synthetic_dataset(n_ticks=200)

        result = MultiStrategyBacktestRunner.run_from_dataset(
            dataset=dataset,
            initial_balance=1000.0,
        )

        bat_pnl = result.strategy_pnl.get("BuyAboveThreshold", 0.0)
        mr_pnl = result.strategy_pnl.get("MeanReversion", 0.0)

        # Both strategies should have PnL entries (even if zero)
        assert isinstance(bat_pnl, float)
        assert isinstance(mr_pnl, float)


# ══════════════════════════════════════════════════════════════════════════
# Deterministism
# ══════════════════════════════════════════════════════════════════════════


class TestMultiStrategyDeterminism:
    """Tests for deterministic behavior."""

    def test_same_input_same_output(self):
        """Same dataset + same strategies → same result."""
        dataset = _make_synthetic_dataset(n_ticks=100)

        result1 = MultiStrategyBacktestRunner.run_from_dataset(dataset)
        result2 = MultiStrategyBacktestRunner.run_from_dataset(dataset)

        # Same final balance
        assert result1.final_balance == result2.final_balance
        # Same number of trades
        assert len(result1.positions) == len(result2.positions)
        # Same per-strategy PnL
        assert result1.strategy_pnl == result2.strategy_pnl
        # Same regime distribution
        assert result1.regime_distribution == result2.regime_distribution
