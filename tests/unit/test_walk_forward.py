# tests/unit/test_walk_forward.py

"""
Tests for walk-forward validation framework (P10.1).

Covers:
- WalkForwardConfig validation
- FoldResult properties
- WalkForwardReport aggregate metrics
- WalkForwardValidator with synthetic data
- Edge cases: empty datasets, insufficient ticks, single fold
"""

import json
from datetime import datetime, timezone

import pytest

from src.backtesting.data_loader import DataLoader, HistoricalDataset
from src.quantitative.walk_forward import (
    FoldResult,
    WalkForwardConfig,
    WalkForwardReport,
    WalkForwardValidator,
)

# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def default_config() -> WalkForwardConfig:
    return WalkForwardConfig(folds=3, train_ratio=0.5, val_ratio=0.2, seed=42)


@pytest.fixture
def small_dataset() -> HistoricalDataset:
    """Synthetic dataset with enough ticks for walk-forward."""
    loader = DataLoader()
    return loader.generate_synthetic(
        asset="BTC", window="5m", n_ticks=600)


@pytest.fixture
def tiny_dataset() -> HistoricalDataset:
    """Very small dataset — not enough ticks for walk-forward folds."""
    loader = DataLoader()
    return loader.generate_synthetic(
        asset="BTC", window="5m", n_ticks=30,
    )


# ── Config Tests ─────────────────────────────────────────────────────────────


class TestWalkForwardConfig:
    """WalkForwardConfig validation."""

    def test_defaults(self) -> None:
        cfg = WalkForwardConfig()
        assert cfg.folds == 5
        assert cfg.train_ratio == 0.5
        assert cfg.val_ratio == 0.2
        assert cfg.test_ratio == pytest.approx(0.3)
        assert cfg.min_ticks_per_fold == 50
        assert cfg.seed == 42

    def test_test_ratio_computed(self) -> None:
        cfg = WalkForwardConfig(folds=5, train_ratio=0.6, val_ratio=0.15)
        assert cfg.test_ratio == pytest.approx(0.25)

    def test_invalid_folds(self) -> None:
        with pytest.raises(ValueError, match="at least 2"):
            WalkForwardConfig(folds=1)

    def test_invalid_train_ratio_zero(self) -> None:
        with pytest.raises(ValueError, match="train_ratio"):
            WalkForwardConfig(train_ratio=0.0)

    def test_invalid_train_ratio_one(self) -> None:
        with pytest.raises(ValueError, match="train_ratio"):
            WalkForwardConfig(train_ratio=1.0)

    def test_invalid_val_ratio(self) -> None:
        with pytest.raises(ValueError, match="val_ratio"):
            WalkForwardConfig(val_ratio=0.0)

    def test_train_val_sum_exceeds_one(self) -> None:
        with pytest.raises(ValueError, match="train_ratio.*val_ratio"):
            WalkForwardConfig(train_ratio=0.7, val_ratio=0.5)

    def test_invalid_min_ticks(self) -> None:
        with pytest.raises(ValueError, match="min_ticks_per_fold"):
            WalkForwardConfig(min_ticks_per_fold=0)

    def test_custom_sweep_params(self) -> None:
        cfg = WalkForwardConfig(
            sweep_thresholds=[0.60, 0.70],
            sweep_stop_losses=[0.05, 0.10],
            sweep_targets=[0.80, 0.90],
        )
        assert cfg.sweep_thresholds == [0.60, 0.70]
        assert cfg.sweep_stop_losses == [0.05, 0.10]
        assert cfg.sweep_targets == [0.80, 0.90]


# ── FoldResult Tests ─────────────────────────────────────────────────────────


class TestFoldResult:
    """FoldResult properties and edge cases."""

    def test_oos_is_ratio_positive(self) -> None:
        """OoS Sharpe / IS Sharpe when both are positive."""
        fold = FoldResult(
            fold_index=0, train_start=0, train_end=100,
            val_start=100, val_end=150,
            test_start=150, test_end=200,
            train_sharpe=2.0, oos_sharpe=1.0,
        )
        assert fold.oos_is_sharpe_ratio == pytest.approx(0.5)

    def test_oos_is_ratio_zero_is_sharpe(self) -> None:
        """When IS Sharpe is 0, ratio is 0."""
        fold = FoldResult(
            fold_index=0, train_start=0, train_end=100,
            val_start=100, val_end=150,
            test_start=150, test_end=200,
            train_sharpe=0.0, oos_sharpe=1.0,
        )
        assert fold.oos_is_sharpe_ratio == 0.0

    def test_oos_is_ratio_negative(self) -> None:
        """Negative IS Sharpe, positive OoS → negative ratio."""
        fold = FoldResult(
            fold_index=0, train_start=0, train_end=100,
            val_start=100, val_end=150,
            test_start=150, test_end=200,
            train_sharpe=-1.0, oos_sharpe=0.5,
        )
        assert fold.oos_is_sharpe_ratio == pytest.approx(-0.5)

    def test_has_trades_true(self) -> None:
        fold = FoldResult(
            fold_index=0, train_start=0, train_end=100,
            val_start=100, val_end=150,
            test_start=150, test_end=200,
            train_trades=5,
        )
        assert fold.has_trades is True

    def test_has_trades_false(self) -> None:
        fold = FoldResult(
            fold_index=0, train_start=0, train_end=100,
            val_start=100, val_end=150,
            test_start=150, test_end=200,
        )
        assert fold.has_trades is False

    def test_all_fields_defaults(self) -> None:
        """Unspecified fields should have sensible defaults."""
        fold = FoldResult(
            fold_index=2, train_start=500, train_end=600,
            val_start=600, val_end=700,
            test_start=700, test_end=800,
        )
        assert fold.train_sharpe == 0.0
        assert fold.oos_sharpe == 0.0
        assert fold.val_sharpe == 0.0
        assert fold.train_trades == 0
        assert fold.oos_pnl == 0.0
        assert fold.best_threshold == 0.0


# ── WalkForwardReport Tests ──────────────────────────────────────────────────


class TestWalkForwardReport:
    """WalkForwardReport aggregate metric calculations."""

    @pytest.fixture
    def sample_report(self) -> WalkForwardReport:
        cfg = WalkForwardConfig(folds=3)
        folds = [
            FoldResult(
                fold_index=0, train_start=0, train_end=100,
                val_start=100, val_end=140, test_start=140, test_end=200,
                train_sharpe=2.0, oos_sharpe=1.5, oos_pnl=15.0, oos_win_rate=0.6,
                oos_profit_factor=2.0, oos_max_dd=0.1, oos_trades=10,
                best_threshold=0.75, best_stop_loss=0.15, best_target=0.90,
                train_trades=8,
            ),
            FoldResult(
                fold_index=1, train_start=200, train_end=300,
                val_start=300, val_end=340, test_start=340, test_end=400,
                train_sharpe=1.5, oos_sharpe=1.0, oos_pnl=10.0, oos_win_rate=0.55,
                oos_profit_factor=1.8, oos_max_dd=0.12, oos_trades=8,
                best_threshold=0.73, best_stop_loss=0.14, best_target=0.88,
                train_trades=7,
            ),
            FoldResult(
                fold_index=2, train_start=400, train_end=500,
                val_start=500, val_end=540, test_start=540, test_end=600,
                train_sharpe=1.8, oos_sharpe=1.2, oos_pnl=12.0, oos_win_rate=0.58,
                oos_profit_factor=1.9, oos_max_dd=0.11, oos_trades=9,
                best_threshold=0.74, best_stop_loss=0.15, best_target=0.89,
                train_trades=9,
            ),
        ]
        return WalkForwardReport(
            asset="BTC", config=cfg, total_ticks=600, folds=folds,
        )

    def test_completed_folds(self, sample_report: WalkForwardReport) -> None:
        assert len(sample_report.completed_folds) == 3

    def test_oos_sharpe_mean(self, sample_report: WalkForwardReport) -> None:
        expected = (1.5 + 1.0 + 1.2) / 3
        assert sample_report.oos_sharpe_mean == pytest.approx(expected)

    def test_is_sharpe_mean(self, sample_report: WalkForwardReport) -> None:
        expected = (2.0 + 1.5 + 1.8) / 3
        assert sample_report.is_sharpe_mean == pytest.approx(expected)

    def test_oos_is_ratio(self, sample_report: WalkForwardReport) -> None:
        ratios = [1.5 / 2.0, 1.0 / 1.5, 1.2 / 1.8]
        expected = sum(ratios) / 3
        assert sample_report.oos_is_ratio == pytest.approx(expected)

    def test_oos_total_pnl(self, sample_report: WalkForwardReport) -> None:
        assert sample_report.oos_total_pnl == pytest.approx(37.0)

    def test_oos_total_trades(self, sample_report: WalkForwardReport) -> None:
        assert sample_report.oos_total_trades == 27

    def test_consistent_profitable(
        self, sample_report: WalkForwardReport,
    ) -> None:
        """All 3 folds have positive OoS PnL → profitable."""
        assert sample_report.consistent_profitable is True

    def test_parameter_stability_low(
        self, sample_report: WalkForwardReport,
    ) -> None:
        """Similar parameters across folds → low CV (< 0.1)."""
        stability = sample_report.parameter_stability
        assert stability < 0.1

    def test_max_oos_drawdown(self, sample_report: WalkForwardReport) -> None:
        assert sample_report.max_oos_drawdown == pytest.approx(0.12)

    def test_to_dict(self, sample_report: WalkForwardReport) -> None:
        d = sample_report.to_dict()
        assert d["asset"] == "BTC"
        assert d["completed_folds"] == 3
        assert "summary" in d
        assert "folds" in d
        assert len(d["folds"]) == 3
        assert d["summary"]["oos_is_ratio"] == pytest.approx(
            sample_report.oos_is_ratio, abs=1e-3
        )

    def test_empty_report(self) -> None:
        cfg = WalkForwardConfig(folds=3)
        report = WalkForwardReport(asset="ETH", config=cfg, total_ticks=0)
        assert len(report.completed_folds) == 0
        assert report.oos_sharpe_mean == 0.0
        assert report.oos_is_ratio == 0.0
        assert report.consistent_profitable is False
        assert report.oos_total_pnl == 0.0

    def test_one_fold_only_param_stability(self) -> None:
        """Single fold → parameter_stability should be 0 (not enough data)."""
        cfg = WalkForwardConfig(folds=3)
        folds = [
            FoldResult(
                fold_index=0, train_start=0, train_end=100,
                val_start=100, val_end=140, test_start=140, test_end=200,
                train_sharpe=2.0, oos_sharpe=1.5, oos_pnl=10.0,
                best_threshold=0.75, best_stop_loss=0.15, best_target=0.90,
                train_trades=5, oos_trades=3,
            ),
        ]
        report = WalkForwardReport(
            asset="BTC", config=cfg, total_ticks=200, folds=folds,
        )
        assert report.parameter_stability == 0.0

    def test_consistent_profitable_false(self) -> None:
        """Only 1 of 3 folds profitable."""
        cfg = WalkForwardConfig(folds=3)
        folds = [
            FoldResult(
                fold_index=0, train_start=0, train_end=100,
                val_start=100, val_end=140, test_start=140, test_end=200,
                oos_pnl=10.0, train_trades=1, oos_trades=1,
            ),
            FoldResult(
                fold_index=1, train_start=200, train_end=300,
                val_start=300, val_end=340, test_start=340, test_end=400,
                oos_pnl=-5.0, train_trades=1, oos_trades=1,
            ),
            FoldResult(
                fold_index=2, train_start=400, train_end=500,
                val_start=500, val_end=540, test_start=540, test_end=600,
                oos_pnl=-3.0, train_trades=1, oos_trades=1,
            ),
        ]
        report = WalkForwardReport(
            asset="BTC", config=cfg, total_ticks=600, folds=folds,
        )
        assert report.consistent_profitable is False


# ── WalkForwardValidator Tests ───────────────────────────────────────────────


class TestWalkForwardValidator:
    """Integration tests for WalkForwardValidator with synthetic data."""

    def test_run_produces_report(
        self,
        default_config: WalkForwardConfig,
        small_dataset: HistoricalDataset,
    ) -> None:
        """Validator should produce a WalkForwardReport with fold results."""
        validator = WalkForwardValidator(config=default_config)
        report = validator.run(small_dataset)

        assert isinstance(report, WalkForwardReport)
        assert report.asset == "BTC"
        assert report.total_ticks == small_dataset.tick_count
        assert len(report.folds) > 0

    def test_folds_are_sequential(
        self,
        default_config: WalkForwardConfig,
        small_dataset: HistoricalDataset,
    ) -> None:
        """Each fold's test window should follow the previous fold."""
        validator = WalkForwardValidator(config=default_config)
        report = validator.run(small_dataset)

        assert len(report.folds) >= 1
        for i in range(1, len(report.folds)):
            prev = report.folds[i - 1]
            curr = report.folds[i]
            assert curr.test_start >= prev.test_end

    def test_fold_windows_non_overlapping(
        self,
        default_config: WalkForwardConfig,
    ) -> None:
        """Train/val/test windows within a fold should be non-overlapping."""
        default_config.folds = 2
        loader = DataLoader()
        dataset = loader.generate_synthetic(
            asset="ETH", window="5m", n_ticks=400)

        validator = WalkForwardValidator(config=default_config)
        report = validator.run(dataset)

        for fold in report.folds:
            assert fold.train_end == fold.val_start
            assert fold.val_end == fold.test_start
            assert fold.test_start < fold.test_end

    def test_tiny_dataset_skips_folds(
        self,
        default_config: WalkForwardConfig,
        tiny_dataset: HistoricalDataset,
    ) -> None:
        """Dataset with too few ticks should produce empty report."""
        validator = WalkForwardValidator(config=default_config)
        report = validator.run(tiny_dataset)

        assert len(report.completed_folds) == 0

    def test_custom_sweep_params_used(
        self,
        small_dataset: HistoricalDataset,
    ) -> None:
        """When sweep params are provided, they constrain optimization."""
        cfg = WalkForwardConfig(
            folds=2,
            train_ratio=0.5,
            val_ratio=0.2,
            sweep_thresholds=[0.70, 0.75],
            sweep_stop_losses=[0.15],
            sweep_targets=[0.90],
        )
        validator = WalkForwardValidator(config=cfg)
        report = validator.run(small_dataset)

        for fold in report.folds:
            if fold.best_threshold != 0.0:
                assert fold.best_threshold in (0.70, 0.75)

    def test_deterministic_output(
        self,
        small_dataset: HistoricalDataset,
    ) -> None:
        """Same input + same seed → same report."""
        cfg = WalkForwardConfig(folds=2, seed=42)

        v1 = WalkForwardValidator(config=cfg)
        v2 = WalkForwardValidator(config=cfg)

        r1 = v1.run(small_dataset)
        r2 = v2.run(small_dataset)

        assert r1.oos_sharpe_mean == r2.oos_sharpe_mean
        assert r1.oos_total_pnl == r2.oos_total_pnl
        assert r1.oos_total_trades == r2.oos_total_trades

    def test_verbose_mode_no_crash(
        self,
        small_dataset: HistoricalDataset,
    ) -> None:
        """Verbose mode should not crash."""
        cfg = WalkForwardConfig(folds=2, verbose=True)
        validator = WalkForwardValidator(config=cfg)
        report = validator.run(small_dataset)

        assert isinstance(report, WalkForwardReport)

    def test_initial_balance_respected(
        self,
        small_dataset: HistoricalDataset,
    ) -> None:
        """Custom initial_balance should be used."""
        cfg = WalkForwardConfig(folds=2)
        validator = WalkForwardValidator(
            config=cfg, initial_balance=500.0,
        )
        report = validator.run(small_dataset)

        assert isinstance(report, WalkForwardReport)

    def test_report_to_dict_serializable(
        self,
        default_config: WalkForwardConfig,
        small_dataset: HistoricalDataset,
    ) -> None:
        """to_dict() should produce a JSON-serializable dict."""
        validator = WalkForwardValidator(config=default_config)
        report = validator.run(small_dataset)

        d = report.to_dict()
        json_str = json.dumps(d)
        assert len(json_str) > 0

    def test_multiple_assets(
        self,
        default_config: WalkForwardConfig,
    ) -> None:
        """Validator should work with ETH as well as BTC."""
        loader = DataLoader()
        dataset = loader.generate_synthetic(
            asset="ETH", window="5m", n_ticks=400)

        validator = WalkForwardValidator(config=default_config)
        report = validator.run(dataset)

        assert report.asset == "ETH"
        assert isinstance(report, WalkForwardReport)


# ── Edge Case Tests ──────────────────────────────────────────────────────────


class TestWalkForwardEdgeCases:
    """Edge cases and boundary conditions."""

    def test_empty_dataset(self, default_config: WalkForwardConfig) -> None:
        dataset = HistoricalDataset(
            asset="BTC", window="5m", market_id="empty",
            ticks=[], start_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            end_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        validator = WalkForwardValidator(config=default_config)
        report = validator.run(dataset)

        assert len(report.completed_folds) == 0
        assert report.oos_total_pnl == 0.0

    def test_single_fold_config(self) -> None:
        """Config with folds=2 should work (minimum valid)."""
        cfg = WalkForwardConfig(folds=2)
        assert cfg.folds == 2

    def test_max_folds_for_dataset(
        self, default_config: WalkForwardConfig,
    ) -> None:
        """Even with many folds requested, validator handles real data size."""
        default_config.folds = 10
        default_config.min_ticks_per_fold = 10
        loader = DataLoader()
        dataset = loader.generate_synthetic(
            asset="BTC", window="5m", n_ticks=500)

        validator = WalkForwardValidator(config=default_config)
        report = validator.run(dataset)

        assert len(report.folds) >= 1

    def test_min_ticks_causes_skip(
        self, default_config: WalkForwardConfig,
    ) -> None:
        """High min_ticks_per_fold should cause all folds to be skipped."""
        default_config.folds = 3
        default_config.min_ticks_per_fold = 500
        loader = DataLoader()
        dataset = loader.generate_synthetic(
            asset="BTC", window="5m", n_ticks=200)

        validator = WalkForwardValidator(config=default_config)
        report = validator.run(dataset)

        assert len(report.completed_folds) == 0
