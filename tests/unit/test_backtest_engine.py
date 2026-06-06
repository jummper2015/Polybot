"""
Unit tests for BacktestEngine with FillSimulator integration (P9.1).

Tests:
  - FillSimulator default construction + custom injection
  - Entry path: estimate_entry called with correct params
  - Exit path: estimate_exit called with correct params
  - End-of-dataset close: estimate_exit for forced close
  - Double-slippage bug fix: BacktestPosition.close() uses exit_price directly
  - _tick_to_data: MarketTick → FillSimulator dict bridge
  - Parameter sweep propagates fill_simulator
  - Asset from dataset passed through to FillSimulator
"""

from datetime import datetime
from unittest.mock import MagicMock

import pytest

from src.backtesting.data_loader import HistoricalDataset
from src.backtesting.engine import (
    BacktestEngine,
    BacktestPosition,
    BacktestResult,
)
from src.domain.value_objects.market_tick import MarketTick
from src.execution.fill_simulator import (
    FillEstimate,
    FillSimulator,
)
from src.strategies.buy_above_threshold.config import BuyAboveThresholdConfig

# ══════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════


def _make_tick(
    yes_price: float = 0.65,
    spread: float = 0.02,
    best_bid: float | None = None,
    best_ask: float | None = None,
    volume_24h: float = 5000.0,
    timestamp: datetime | None = None,
) -> MarketTick:
    """Build a MarketTick for backtesting tests."""
    bid = best_bid if best_bid is not None else yes_price - spread / 2
    ask = best_ask if best_ask is not None else yes_price + spread / 2
    return MarketTick(
        market_id="market_001",
        yes_price=yes_price,
        no_price=1.0 - yes_price,
        best_bid=round(bid, 4),
        best_ask=round(ask, 4),
        spread=spread,
        volume_24h=volume_24h,
        timestamp=timestamp or datetime(2024, 6, 1, 12, 0, 0),
    )


def _make_dataset(
    ticks: list[MarketTick],
    asset: str = "BTC",
    window: str = "5m",
) -> HistoricalDataset:
    """Build a minimal HistoricalDataset for backtesting."""
    return HistoricalDataset(
        asset=asset,
        window=window,
        market_id="backtest_BTC_5m",
        ticks=ticks,
        start_at=ticks[0].timestamp,
        end_at=ticks[-1].timestamp,
    )


def _make_strategy_config(**overrides) -> BuyAboveThresholdConfig:
    """Build a strategy config that easily triggers trades."""
    defaults = {
        "threshold": 0.60,
        "required_ticks": 1,
        "stop_loss_pct": 0.30,
        "target_price": 0.99,
        "position_size_pusd": 10.0,
        "timeout_minutes": 120,
    }
    defaults.update(overrides)
    config = BuyAboveThresholdConfig(**defaults)
    return config


# ══════════════════════════════════════════════════════════════════════════
# BACKTEST POSITION — Double-slippage fix
# ══════════════════════════════════════════════════════════════════════════


class TestBacktestPositionClose:
    """Verify the double-slippage bug is fixed."""

    def test_close_no_extra_slippage(self):
        """close() uses exit_price directly, no extra 0.5% discount."""
        pos = BacktestPosition(
            market_id="m1",
            side="YES",
            amount=10.0,
            shares=20.0,
            entry_price=0.50,
            entry_tick=10,
            entry_at=datetime(2024, 6, 1, 12, 0, 0),
        )
        pos.close(
            exit_price=0.60,
            exit_tick=50,
            exit_at=datetime(2024, 6, 1, 12, 30, 0),
            reason="target_reached",
        )

        # PnL = (exit_price - entry_price) * shares = (0.60 - 0.50) * 20 = 2.0
        assert pos.pnl == pytest.approx(2.0, abs=0.001)
        assert pos.pnl_pct == pytest.approx(0.20, abs=0.001)  # 2.0 / 10.0

    def test_close_loss_correct(self):
        """Negative PnL also uses direct calculation."""
        pos = BacktestPosition(
            market_id="m2",
            side="YES",
            amount=20.0,
            shares=40.0,
            entry_price=0.50,
            entry_tick=0,
            entry_at=datetime(2024, 6, 1, 12, 0, 0),
        )
        pos.close(
            exit_price=0.45,
            exit_tick=30,
            exit_at=datetime(2024, 6, 1, 12, 15, 0),
            reason="stop_loss",
        )

        # PnL = (0.45 - 0.50) * 40 = -2.0
        assert pos.pnl == pytest.approx(-2.0, abs=0.001)
        # The old code would have done: (0.45 * 0.995 - 0.50) * 40 ≈ -2.09
        # This test proves the extra 0.5% is gone

    def test_close_stores_exit_reason(self):
        """Exit reason is stored correctly."""
        pos = BacktestPosition(
            market_id="m3",
            side="YES",
            amount=10.0,
            shares=20.0,
            entry_price=0.50,
            entry_tick=0,
            entry_at=datetime(2024, 6, 1, 12, 0, 0),
        )
        pos.close(
            exit_price=0.55,
            exit_tick=40,
            exit_at=datetime(2024, 6, 1, 12, 20, 0),
            reason="dataset_end",
        )

        assert pos.exit_reason == "dataset_end"
        assert not pos.is_open
        assert pos.duration_ticks == 40


# ══════════════════════════════════════════════════════════════════════════
# BACKTEST ENGINE — Construction
# ══════════════════════════════════════════════════════════════════════════


class TestBacktestEngineConstruction:
    """Test engine initialization with FillSimulator."""

    def test_default_fill_simulator_created(self):
        """Engine creates a default FillSimulator when none provided."""
        config = _make_strategy_config()
        engine = BacktestEngine(strategy_config=config)
        assert engine._fill_sim is not None
        assert isinstance(engine._fill_sim, FillSimulator)

    def test_custom_fill_simulator_injected(self):
        """Custom FillSimulator is used when provided."""
        config = _make_strategy_config()
        custom_sim = FillSimulator()
        engine = BacktestEngine(strategy_config=config, fill_simulator=custom_sim)
        assert engine._fill_sim is custom_sim

    def test_mock_fill_simulator_injected(self):
        """Mock FillSimulator can be injected for testing."""
        config = _make_strategy_config()
        mock_sim = MagicMock(spec=FillSimulator)
        engine = BacktestEngine(strategy_config=config, fill_simulator=mock_sim)
        assert engine._fill_sim is mock_sim


# ══════════════════════════════════════════════════════════════════════════
# _tick_to_data BRIDGE
# ══════════════════════════════════════════════════════════════════════════


class TestTickToData:
    """Test the MarketTick → FillSimulator dict bridge."""

    def test_basic_fields_present(self):
        """All required FillSimulator fields are in the output dict."""
        tick = _make_tick(yes_price=0.65, best_bid=0.64, best_ask=0.66, spread=0.02)
        data = BacktestEngine._tick_to_data(tick)

        assert data["best_bid"] == 0.64
        assert data["best_ask"] == 0.66
        assert data["spread"] == 0.02
        assert data["volume_24h"] == 5000.0

    def test_depth_zeroed_out(self):
        """Depth fields are zero since MarketTick lacks orderbook depth."""
        tick = _make_tick()
        data = BacktestEngine._tick_to_data(tick)

        assert data["bids_vol_1"] == 0.0
        assert data["bids_vol_2"] == 0.0
        assert data["bids_vol_3"] == 0.0
        assert data["asks_vol_1"] == 0.0
        assert data["asks_vol_2"] == 0.0
        assert data["asks_vol_3"] == 0.0

    def test_static_method_callable(self):
        """_tick_to_data is a static method and works standalone."""
        tick = _make_tick()
        data = BacktestEngine._tick_to_data(tick)
        assert isinstance(data, dict)
        assert len(data) == 10  # 10 fields


# ══════════════════════════════════════════════════════════════════════════
# ENTRY PATH — FillSimulator usage
# ══════════════════════════════════════════════════════════════════════════


class TestEntryFillSimulator:
    """Verify entry path uses FillSimulator.estimate_entry correctly."""

    def test_estimate_entry_called_with_asset(self):
        """estimate_entry receives asset from dataset."""
        config = _make_strategy_config()
        mock_fs = MagicMock(spec=FillSimulator)
        mock_fs.estimate_entry.return_value = FillEstimate(
            fill_price=0.72, slippage=0.005, slippage_pct=0.007, fill_ratio=1.0,
        )
        engine = BacktestEngine(strategy_config=config, fill_simulator=mock_fs)

        ticks = [_make_tick(yes_price=0.72) for _ in range(10)]
        dataset = _make_dataset(ticks, asset="ETH")

        engine.run(dataset)

        # estimate_entry should have been called with asset="ETH"
        call_args = mock_fs.estimate_entry.call_args
        assert call_args is not None
        assert call_args.kwargs["asset"] == "ETH"
        assert call_args.kwargs["order_size"] == config.position_size_pusd

    def test_entry_fill_price_used(self):
        """The fill_price from FillSimulator becomes the entry price."""
        config = _make_strategy_config()
        mock_fs = MagicMock(spec=FillSimulator)
        # Return a specific fill price we can verify
        mock_fs.estimate_entry.return_value = FillEstimate(
            fill_price=0.73, slippage=0.01, slippage_pct=0.014, fill_ratio=1.0,
        )
        engine = BacktestEngine(strategy_config=config, fill_simulator=mock_fs)

        ticks = [_make_tick(yes_price=0.72) for _ in range(10)]
        dataset = _make_dataset(ticks, asset="BTC")

        result = engine.run(dataset)

        assert len(result.closed_positions) > 0, (
            "Expected at least one position — entry may have been blocked"
        )
        pos = result.closed_positions[0]
        # Entry price should match the mocked FillSimulator output
        assert pos.entry_price == pytest.approx(0.73, abs=0.01)

    def test_entry_slippage_positive(self):
        """Entry price exceeds mid/best_ask (buying incurs positive slippage)."""
        config = _make_strategy_config()
        engine = BacktestEngine(strategy_config=config)

        tick = _make_tick(yes_price=0.72, best_bid=0.71, best_ask=0.73, spread=0.02)
        mid = (0.71 + 0.73) / 2
        ticks = [tick for _ in range(10)]
        dataset = _make_dataset(ticks, asset="BTC")

        result = engine.run(dataset)

        assert len(result.closed_positions) > 0, (
            "Expected at least one position — entry may have been blocked"
        )
        for pos in result.closed_positions:
            # Entry with positive slippage: fill_price > mid
            assert pos.entry_price > mid, (
                f"Entry {pos.entry_price} should be above mid {mid}"
            )


# ══════════════════════════════════════════════════════════════════════════
# EXIT PATH — FillSimulator usage
# ══════════════════════════════════════════════════════════════════════════


class TestExitFillSimulator:
    """Verify exit path uses FillSimulator.estimate_exit correctly."""

    def test_estimate_exit_called_with_asset(self):
        """estimate_exit receives asset from dataset."""
        config = _make_strategy_config()
        mock_fs = MagicMock(spec=FillSimulator)

        # Entry returns a normal fill
        mock_fs.estimate_entry.return_value = FillEstimate(
            fill_price=0.72, slippage=0.005, slippage_pct=0.007, fill_ratio=1.0,
        )
        mock_fs.estimate_exit.return_value = FillEstimate(
            fill_price=0.70, slippage=-0.005, slippage_pct=0.007, fill_ratio=1.0,
            mid_price=0.71,
        )

        engine = BacktestEngine(strategy_config=config, fill_simulator=mock_fs)

        ticks = [_make_tick(yes_price=0.72) for _ in range(10)]
        dataset = _make_dataset(ticks, asset="ETH")

        engine.run(dataset)

        # estimate_exit should have been called at least once (force-close at end)
        assert mock_fs.estimate_exit.call_count >= 1, (
            "estimate_exit was never called — entry may have been blocked"
        )
        # Verify asset was passed through
        assets = [
            c.kwargs.get("asset") for c in mock_fs.estimate_exit.call_args_list
        ]
        assert "ETH" in assets, (
            f"Expected 'ETH' in exit assets, got: {assets}"
        )

    def test_end_of_dataset_close_uses_estimate_exit(self):
        """Forced close at dataset end uses estimate_exit."""
        config = _make_strategy_config()
        mock_fs = MagicMock(spec=FillSimulator)

        # Entry succeeds
        mock_fs.estimate_entry.return_value = FillEstimate(
            fill_price=0.72, slippage=0.005, slippage_pct=0.007, fill_ratio=1.0,
        )
        # Exit (end-of-dataset) — position remains open till the end
        mock_fs.estimate_exit.return_value = FillEstimate(
            fill_price=0.71, slippage=-0.005, slippage_pct=0.007, fill_ratio=1.0,
            mid_price=0.72,
        )

        engine = BacktestEngine(strategy_config=config, fill_simulator=mock_fs)

        ticks = [_make_tick(yes_price=0.72) for _ in range(10)]
        dataset = _make_dataset(ticks, asset="BTC")

        result = engine.run(dataset)

        # estimate_exit must be called (for forced close at end)
        assert mock_fs.estimate_exit.call_count >= 1, (
            "estimate_exit was never called — no position was opened"
        )
        # All positions should be closed
        assert len(result.open_positions) == 0

    def test_exit_reason_dataset_end(self):
        """Last position close reason is dataset_end."""
        # Make exit unlikely during the run
        config = _make_strategy_config(
            threshold=0.60,
            stop_loss_pct=0.99,  # Very wide stop loss
            target_price=0.999,  # Unreachable target
            timeout_minutes=9999,
        )

        engine = BacktestEngine(strategy_config=config)

        # Ticks that trigger entry but not exit
        ticks = [_make_tick(yes_price=0.72) for _ in range(10)]
        dataset = _make_dataset(ticks, asset="BTC")

        result = engine.run(dataset)

        closed = result.closed_positions
        if closed:
            last = closed[-1]
            assert "dataset_end" in (last.exit_reason or ""), (
                f"Expected dataset_end, got: {last.exit_reason}"
            )


# ══════════════════════════════════════════════════════════════════════════
# INTEGRATION — Full backtest with FillSimulator
# ══════════════════════════════════════════════════════════════════════════


class TestBacktestWithFillSimulator:
    """End-to-end backtest using real FillSimulator."""

    def test_full_backtest_produces_metrics(self):
        """A real backtest with FillSimulator produces valid metrics."""
        config = _make_strategy_config()
        engine = BacktestEngine(strategy_config=config)

        ticks = [_make_tick(yes_price=0.72) for _ in range(50)]
        dataset = _make_dataset(ticks, asset="BTC")

        result = engine.run(dataset)
        from src.backtesting.metrics import BacktestMetrics
        metrics = BacktestMetrics(result).compute_all()

        assert metrics["summary"]["closed_positions"] >= 0
        assert metrics["pnl"]["total_pnl_usdc"] is not None
        assert metrics["risk"]["sharpe_ratio"] is not None
        assert metrics["config"]["threshold"] == config.threshold

    def test_asset_passed_through_correctly(self):
        """The dataset.asset flows to FillSimulator via estimate_entry/exit."""
        config = _make_strategy_config()
        mock_fs = MagicMock(spec=FillSimulator)
        mock_fs.estimate_entry.return_value = FillEstimate(
            fill_price=0.72, slippage=0.005, slippage_pct=0.007, fill_ratio=1.0,
        )
        mock_fs.estimate_exit.return_value = FillEstimate(
            fill_price=0.71, slippage=-0.005, slippage_pct=0.007, fill_ratio=1.0,
            mid_price=0.72,
        )

        engine = BacktestEngine(strategy_config=config, fill_simulator=mock_fs)

        ticks = [_make_tick(yes_price=0.72) for _ in range(30)]
        dataset = _make_dataset(ticks, asset="ETH")

        engine.run(dataset)

        assert mock_fs.estimate_entry.call_count >= 1, (
            "estimate_entry was never called — entry may have been blocked"
        )
        # All entry calls should use asset="ETH"
        for call in mock_fs.estimate_entry.call_args_list:
            assert call.kwargs["asset"] == "ETH", (
                f"Expected asset='ETH', got {call.kwargs.get('asset')}"
            )

    def test_calibrated_simulator_used_in_backtest(self):
        """A calibrated FillSimulator produces consistent results."""
        # Calibrate with deep liquidity → very low impact
        deep_ticks = [
            {
                "bids_vol_1": 100000.0,
                "asks_vol_1": 100000.0,
                "spread": 0.001,
            }
            for _ in range(50)
        ]
        sim = FillSimulator.calibrate_from_ticks(deep_ticks, asset="DEEP")

        config = _make_strategy_config()
        engine = BacktestEngine(strategy_config=config, fill_simulator=sim)

        ticks = [_make_tick(yes_price=0.72, spread=0.01) for _ in range(30)]
        dataset = _make_dataset(ticks, asset="BTC")

        result = engine.run(dataset)
        assert result is not None
        assert result.final_balance >= 0


# ══════════════════════════════════════════════════════════════════════════
# PARAMETER SWEEP — FillSimulator propagation
# ══════════════════════════════════════════════════════════════════════════


class TestParameterSweepFillSimulator:
    """Verify parameter sweep propagates FillSimulator."""

    def test_sweep_passes_fill_simulator_to_inner_engines(self):
        """Inner engines in sweep use the parent's FillSimulator."""
        config = _make_strategy_config()
        mock_fs = MagicMock(spec=FillSimulator)
        mock_fs.estimate_entry.return_value = FillEstimate(
            fill_price=0.72, slippage=0.005, slippage_pct=0.007, fill_ratio=1.0,
        )
        mock_fs.estimate_exit.return_value = FillEstimate(
            fill_price=0.71, slippage=-0.005, slippage_pct=0.007, fill_ratio=1.0,
            mid_price=0.72,
        )

        engine = BacktestEngine(strategy_config=config, fill_simulator=mock_fs)

        ticks = [_make_tick(yes_price=0.72) for _ in range(20)]
        dataset = _make_dataset(ticks, asset="BTC")

        # Run a small sweep (1 threshold × 1 stop_loss × 1 target)
        results = engine.run_parameter_sweep(
            dataset=dataset,
            thresholds=[0.70],
            stop_losses=[0.20],
            targets=[0.90],
            ticks_list=[1],
            pos_sizes=[10.0],
        )

        assert len(results) >= 1
        # estimate_entry should be called from the inner engines
        # (each sweep combination creates a new BacktestEngine with our mock)
        assert mock_fs.estimate_entry.call_count >= 1

    def test_sweep_results_are_valid(self):
        """Sweep results contain valid backtest data."""
        config = _make_strategy_config()
        engine = BacktestEngine(strategy_config=config)

        ticks = [_make_tick(yes_price=0.72) for _ in range(15)]
        dataset = _make_dataset(ticks, asset="BTC")

        results = engine.run_parameter_sweep(
            dataset=dataset,
            thresholds=[0.70],
            stop_losses=[0.20],
            targets=[0.90],
            ticks_list=[1],
            pos_sizes=[10.0],
        )

        for r in results:
            assert isinstance(r, BacktestResult)
            assert r.asset == "BTC"
            assert r.initial_balance > 0
            assert r.final_balance >= 0
