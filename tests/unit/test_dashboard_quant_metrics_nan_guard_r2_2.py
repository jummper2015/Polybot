"""test_dashboard_quant_metrics_nan_guard_r2_2.py"""
import math
from dataclasses import dataclass, field
from unittest.mock import MagicMock, patch

import pytest


class TestFiniteFloatHelperR22:
    """R2.2 Fix #4: _finite_float sanea NaN / inf a JSON-serializable."""

    def _helper(self):
        """
        Import tardio del helper (puede requerir full app stack).
        Si el modulo no se puede importar (deps runtime), saltamos.
        """
        try:
            from src.interfaces.api.routers.dashboard import _finite_float
            return _finite_float
        except Exception as exc:
            pytest.skip(f"_finite_float no importable: {exc}")

    def test_finite_int_passes_through(self):
        f = self._helper()
        assert f(3.14159) == 3.1416  # round to 4 decimals

    def test_finite_float_passes_through(self):
        f = self._helper()
        assert f(1.5) == 1.5

    def test_negative_finite_passes_through(self):
        f = self._helper()
        result = f(-2.71828)
        assert math.isfinite(result)
        assert abs(result - (-2.7183)) < 1e-3

    def test_nan_returns_default(self):
        """NaN -> default 0.0 (sin cap)."""
        f = self._helper()
        result = f(float("nan"))
        assert math.isfinite(result)
        assert result == 0.0

    def test_positive_inf_without_cap_returns_default(self):
        """+inf sin abs_cap -> default. Branch sin-cap explicita."""
        f = self._helper()
        result = f(float("inf"), default=0.0)
        assert math.isfinite(result)
        assert result == 0.0

    def test_negative_inf_without_cap_returns_default(self):
        """-inf sin abs_cap -> default."""
        f = self._helper()
        result = f(float("-inf"), default=0.0)
        assert math.isfinite(result)
        assert result == 0.0

    def test_none_returns_default(self):
        f = self._helper()
        result = f(None)
        assert math.isfinite(result)
        assert result == 0.0

    def test_custom_default_for_nan(self):
        f = self._helper()
        result = f(float("nan"), default=-1.0)
        assert result == -1.0

    def test_abs_cap_caps_positive_finite(self):
        """Finito > abs_cap -> cap con signo (defensivo para sharpe)."""
        f = self._helper()
        result = f(5000.0, abs_cap=999.0)
        assert result == 999.0

    def test_abs_cap_caps_negative_preserving_sign(self):
        """Finito < -abs_cap -> -cap (preserva signo)."""
        f = self._helper()
        result = f(-5000.0, abs_cap=999.0)
        assert result == -999.0

    def test_abs_cap_under_threshold_passes(self):
        f = self._helper()
        result = f(500.0, abs_cap=999.0)
        assert result == 500.0

    def test_precision_parameter(self):
        f = self._helper()
        result = f(1.2345678, precision=2)
        assert result == 1.23

    def test_positive_inf_with_abs_cap_returns_cap(self):
        """
        +inf CON abs_cap -> +cap. Decision explicita para profit_factor:
        con 0 losses el PF es inf; queremos cap 999.0 (no 0.0) porque
        comunica "PF extremo pero finito" al dashboard.
        """
        f = self._helper()
        result = f(float("inf"), abs_cap=999.0, default=0.0)
        assert result == 999.0

    def test_negative_inf_with_abs_cap_returns_negative_cap(self):
        f = self._helper()
        result = f(float("-inf"), abs_cap=999.0, default=0.0)
        assert result == -999.0

    def test_nan_with_abs_cap_returns_default(self):
        """NaN + abs_cap -> default (NaN siempre default, cap no aplica)."""
        f = self._helper()
        result = f(float("nan"), abs_cap=999.0)
        assert result == 0.0

    def test_string_numeric_passes(self):
        f = self._helper()
        result = f("3.14")
        assert result == 3.14

    def test_non_numeric_string_returns_default(self):
        """Defensivo: si llega string no numerico, default."""
        f = self._helper()
        result = f("not_a_number")
        assert math.isfinite(result)
        assert result == 0.0


@dataclass
class FakeExitReason:
    reason: str
    count: int
    total_pnl: float
    avg_pnl: float
    win_rate: float


@dataclass
class FakeRegimeRow:
    regime: str
    count: int
    total_pnl: float
    win_rate: float


@dataclass
class FakePostTradeReport:
    """Replica los campos minimos que get_quant_metrics lee del report real."""
    total_trades: int = 1
    expectancy: float = float("nan")
    expectancy_pct: float = float("nan")
    profit_factor: float = float("inf")
    sharpe_estimate: float = float("nan")
    max_consecutive_wins: int = 0
    max_consecutive_losses: int = 0
    best_trade: float = float("nan")
    worst_trade: float = float("nan")
    avg_winner: float = float("nan")
    avg_loser: float = float("nan")
    avg_duration_ticks: float = float("nan")
    best_exit_reason: str = "none"
    worst_exit_reason: str = "none"
    best_regime: str = "none"
    worst_regime: str = "none"
    by_exit_reason: list = field(default_factory=list)
    by_regime: list = field(default_factory=list)


class FakeClosedPosition:
    closed_at = "2026-06-22T00:00:00"
    pnl = 1.5


from unittest.mock import AsyncMock


class TestQuantMetricsNaNGuardWiringR22:
    """R2.2 Fix #4: el endpoint /quant-metrics devuelve 200 incluso con
    analyzer que retorna NaN/inf (no mas HTTP 500 silencioso)."""

    @pytest.mark.asyncio
    async def test_quant_metrics_returns_finite_payload_with_nan_report(self):
        """Mock PostTradeAnalyzer.analyze -> FakePostTradeReport lleno de
        nan/inf. Llama get_quant_metrics y verifica que serializa sin 500."""
        try:
            from src.interfaces.api.routers.dashboard import get_quant_metrics, QuantMetrics
        except Exception as exc:
            pytest.skip(f"dashboard router no importable: {exc}")

        fake_report = FakePostTradeReport(
            by_exit_reason=[FakeExitReason(
                reason="tp", count=1, total_pnl=float("nan"),
                avg_pnl=float("nan"), win_rate=float("nan"),
            )],
            by_regime=[FakeRegimeRow(
                regime="trending", count=1, total_pnl=float("nan"),
                win_rate=float("nan"),
            )],
        )

        fake_container = MagicMock()
        fake_container.repository.get_positions = AsyncMock(return_value=[
            FakeClosedPosition(),
        ])
        fake_container.config.paper_initial_balance = 1000.0

        with patch("src.quantitative.post_trade.PostTradeAnalyzer") as MockAn:
            mock_analyzer = MockAn.return_value
            mock_analyzer.analyze.return_value = fake_report

            request = MagicMock()
            request.app.state.container = fake_container

            try:
                result = await get_quant_metrics(request)
            except Exception as exc:
                pytest.fail(
                    f"get_quant_metrics lanzo exception con NaN/inf: "
                    f"{type(exc).__name__}: {exc}"
                )

        # Sanity: result es QuantMetrics con todos los floats finitos
        assert isinstance(result, QuantMetrics)
        for field_name in [
            "expectancy_usdc", "expectancy_pct", "profit_factor",
            "sharpe_estimate", "best_trade", "worst_trade",
            "avg_duration_ticks",
        ]:
            v = getattr(result, field_name)
            assert math.isfinite(v), (
                f"{field_name} no es finito tras NaN guard: {v}"
            )

        # profit_factor con inf -> cap 999.0
        assert result.profit_factor == 999.0
        # NaN -> default 0.0
        assert result.sharpe_estimate == 0.0
        assert result.expectancy_usdc == 0.0

        # by_exit / by_regime: primer elemento finito tambien
        assert math.isfinite(result.by_exit_reason[0].total_pnl)
        assert math.isfinite(result.by_regime[0].total_pnl)

    @pytest.mark.asyncio
    async def test_quant_metrics_sharpe_capped_at_10(self):
        """sharpe_estimate fuera de [-10, 10] se clipea al cap."""
        try:
            from src.interfaces.api.routers.dashboard import get_quant_metrics
        except Exception as exc:
            pytest.skip(f"dashboard router no importable: {exc}")

        fake_report = FakePostTradeReport(
            total_trades=50,
            expectancy=0.5, expectancy_pct=0.05,
            profit_factor=2.0, sharpe_estimate=999.0,
            best_trade=15.0, worst_trade=-5.0,
            avg_winner=3.0, avg_loser=-1.5,
            avg_duration_ticks=120.5,
        )

        fake_container = MagicMock()
        fake_container.repository.get_positions = AsyncMock(return_value=[
            FakeClosedPosition(),
        ])
        fake_container.config.paper_initial_balance = 1000.0

        with patch("src.quantitative.post_trade.PostTradeAnalyzer") as MockAn:
            mock_analyzer = MockAn.return_value
            mock_analyzer.analyze.return_value = fake_report
            request = MagicMock()
            request.app.state.container = fake_container
            result = await get_quant_metrics(request)

        # Sharpe cap a 10.0 (no 999.0)
        assert result.sharpe_estimate == 10.0
        assert result.profit_factor == 2.0
        assert result.best_trade == 15.0
