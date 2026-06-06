# tests/unit/test_config_validation.py
"""Tests unitarios para la validación de configuraciones de estrategias."""

import pytest

from src.strategies.buy_above_threshold.config import BuyAboveThresholdConfig
from src.strategies.mean_reversion.config import MeanReversionConfig


class TestBuyAboveThresholdConfig:
    """Tests de validación para BuyAboveThresholdConfig."""

    def test_valid_config_passes(self):
        """Una configuración válida no lanza excepción."""
        config = BuyAboveThresholdConfig(
            threshold=0.75,
            required_ticks=3,
            stop_loss_pct=0.15,
            target_price=0.90,
        )
        config.validate()  # No debe lanzar

    def test_threshold_out_of_range_raises(self):
        """threshold debe estar entre 0 y 1 (exclusive)."""
        for bad_value in (0.0, 1.0, 1.5, -0.5):
            config = BuyAboveThresholdConfig(threshold=bad_value)
            with pytest.raises(ValueError, match="threshold"):
                config.validate()

    def test_stop_drop_floor_above_threshold_raises(self):
        """stop_drop_floor debe ser menor que threshold."""
        config = BuyAboveThresholdConfig(
            threshold=0.75,
            stop_drop_floor=0.80,  # Mayor que threshold
        )
        with pytest.raises(ValueError, match="stop_drop_floor"):
            config.validate()

    def test_stop_drop_floor_zero_or_negative_raises(self):
        """stop_drop_floor debe ser > 0.0 (implícito en < threshold)."""
        # 0.0 <= 0.0 no pasa la validación de > 0
        config = BuyAboveThresholdConfig(
            threshold=0.75,
            stop_drop_floor=0.0,
        )
        with pytest.raises(ValueError, match="stop_drop_floor"):
            config.validate()

    def test_target_below_threshold_raises(self):
        """target_price debe ser > threshold."""
        config = BuyAboveThresholdConfig(
            threshold=0.75,
            target_price=0.70,  # Menor que threshold
        )
        with pytest.raises(ValueError, match="target_price"):
            config.validate()

    def test_target_price_above_one_raises(self):
        """target_price no puede ser > 1.0."""
        config = BuyAboveThresholdConfig(
            threshold=0.75,
            target_price=1.2,
        )
        with pytest.raises(ValueError, match="target_price"):
            config.validate()

    def test_negative_stop_loss_raises(self):
        """stop_loss_pct debe ser > 0."""
        for bad in (0.0, -0.1, -1.0):
            config = BuyAboveThresholdConfig(stop_loss_pct=bad)
            with pytest.raises(ValueError, match="stop_loss_pct"):
                config.validate()

    def test_zero_timeout_raises(self):
        """timeout_minutes debe ser > 0."""
        for bad in (0.0, -5.0):
            config = BuyAboveThresholdConfig(timeout_minutes=bad)
            with pytest.raises(ValueError, match="timeout_minutes"):
                config.validate()

    def test_zero_required_ticks_raises(self):
        """required_ticks debe ser >= 1."""
        for bad in (0, -1):
            config = BuyAboveThresholdConfig(required_ticks=bad)
            with pytest.raises(ValueError, match="required_ticks"):
                config.validate()

    def test_max_spread_validation(self):
        """max_spread debe estar entre 0 y 1.0 (no explícitamente validado en BAT)."""
        # BAT config no valida max_spread explícitamente — solo se usa en filtros.
        # Verificamos que no rompe nada con valores extremos.
        config = BuyAboveThresholdConfig(max_spread=0.0, threshold=0.75, target_price=0.90)
        config.validate()  # No debe lanzar

    def test_min_volume_validation(self):
        """min_volume_pusd se acepta con cualquier valor (sin validación explícita)."""
        config = BuyAboveThresholdConfig(min_volume_pusd=0.0, threshold=0.75, target_price=0.90)
        config.validate()  # No debe lanzar


class TestMeanReversionConfig:
    """Tests de validación para MeanReversionConfig."""

    def test_valid_config_passes(self):
        """Una configuración válida no lanza excepción."""
        config = MeanReversionConfig(
            ma_window=20,
            entry_zscore=-2.0,
            exit_zscore=0.0,
        )
        config.validate()  # No debe lanzar

    def test_ma_window_too_small_raises(self):
        """ma_window debe ser >= 3."""
        for bad in (0, 1, 2):
            config = MeanReversionConfig(ma_window=bad)
            with pytest.raises(ValueError, match="ma_window"):
                config.validate()

    def test_entry_zscore_below_minus_five_raises(self):
        """entry_zscore debe ser >= -5.0."""
        for bad in (-6.0, -10.0):
            config = MeanReversionConfig(entry_zscore=bad)
            with pytest.raises(ValueError, match="entry_zscore"):
                config.validate()

    def test_entry_zscore_not_below_exit_zscore_raises(self):
        """entry_zscore debe ser < exit_zscore."""
        config = MeanReversionConfig(
            entry_zscore=1.0,
            exit_zscore=0.0,  # entry >= exit → error
        )
        with pytest.raises(ValueError, match="entry_zscore"):
            config.validate()

    def test_exit_zscore_below_minus_four_raises(self):
        """exit_zscore debe ser >= -4.0."""
        for bad in (-5.0, -10.0):
            config = MeanReversionConfig(exit_zscore=bad)
            with pytest.raises(ValueError, match="exit_zscore"):
                config.validate()

    def test_stop_loss_pct_out_of_range_raises(self):
        """stop_loss_pct debe estar entre 0 y 0.50."""
        for bad in (0.0, -0.1, 0.51, 1.0):
            config = MeanReversionConfig(stop_loss_pct=bad)
            with pytest.raises(ValueError, match="stop_loss_pct"):
                config.validate()

    def test_zero_timeout_raises(self):
        """timeout_minutes debe ser > 0."""
        for bad in (0.0, -5.0):
            config = MeanReversionConfig(timeout_minutes=bad)
            with pytest.raises(ValueError, match="timeout_minutes"):
                config.validate()

    def test_max_spread_out_of_range_raises(self):
        """max_spread debe estar entre 0 y 1.0."""
        for bad in (0.0, 1.0, 1.5, -0.1):
            config = MeanReversionConfig(max_spread=bad)
            with pytest.raises(ValueError, match="max_spread"):
                config.validate()

    def test_min_volume_pusd_zero_or_negative_raises(self):
        """min_volume_pusd debe ser > 0."""
        for bad in (0.0, -100.0):
            config = MeanReversionConfig(min_volume_pusd=bad)
            with pytest.raises(ValueError, match="min_volume_pusd"):
                config.validate()

    def test_position_size_pusd_zero_or_negative_raises(self):
        """position_size_pusd debe ser > 0."""
        for bad in (0.0, -10.0):
            config = MeanReversionConfig(position_size_pusd=bad)
            with pytest.raises(ValueError, match="position_size_pusd"):
                config.validate()
