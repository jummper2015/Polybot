# src/backtesting/metrics.py

import math

import structlog

from src.backtesting.engine import BacktestResult

logger = structlog.get_logger(__name__)


class BacktestMetrics:
    """
    Calcula todas las métricas de rendimiento de un backtest.
    Todas las fórmulas son estándar de finanzas cuantitativas.
    """

    def __init__(self, result: BacktestResult):
        self._result   = result
        self._closed   = result.closed_positions
        self._pnls     = [p.pnl for p in self._closed if p.pnl is not None]
        self._pnl_pcts = [p.pnl_pct for p in self._closed if p.pnl_pct is not None]

    def compute_all(self) -> dict:
        """
        Calcula y devuelve todas las métricas en un diccionario.
        Listo para serializar a JSON/CSV.
        """
        metrics = {
            # ── Resumen general ───────────────────────────────────────
            "summary": {
                "asset":             self._result.asset,
                "window":            self._result.window,
                "dataset_ticks":     self._result.dataset_ticks,
                "dataset_start":     self._result.dataset_start.isoformat(),
                "dataset_end":       self._result.dataset_end.isoformat(),
                "dataset_hours":     round(
                    (self._result.dataset_end - self._result.dataset_start
                    ).total_seconds() / 3600, 2
                ),
                "initial_balance":   self._result.initial_balance,
                "final_balance":     round(self._result.final_balance, 4),
                "total_positions":   len(self._result.positions),
                "closed_positions":  len(self._closed),
                "open_at_end":       len(self._result.open_positions),
            },

            # ── PnL ───────────────────────────────────────────────────
            "pnl": {
                "total_pnl_usdc":    round(self._total_pnl(), 4),
                "total_pnl_pct":     round(self._total_pnl_pct(), 4),
                "avg_pnl_per_trade": round(self._avg_pnl(), 4),
                "avg_pnl_pct":       round(self._avg_pnl_pct(), 4),
                "best_trade_usdc":   round(max(self._pnls, default=0), 4),
                "worst_trade_usdc":  round(min(self._pnls, default=0), 4),
                "total_profit_usdc": round(
                    sum(p for p in self._pnls if p > 0), 4
                ),
                "total_loss_usdc":   round(
                    sum(p for p in self._pnls if p < 0), 4
                ),
            },

            # ── Win Rate y Profit Factor ──────────────────────────────
            "performance": {
                "win_rate":          round(self._win_rate(), 4),
                "loss_rate":         round(1.0 - self._win_rate(), 4),
                "profit_factor":     round(self._profit_factor(), 4),
                "winners":           len([p for p in self._pnls if p > 0]),
                "losers":            len([p for p in self._pnls if p < 0]),
                "breakeven":         len([p for p in self._pnls if p == 0]),
            },

            # ── Risk Metrics ──────────────────────────────────────────
            "risk": {
                "sharpe_ratio":      round(self._sharpe_ratio(), 4),
                "sortino_ratio":     round(self._sortino_ratio(), 4),
                "max_drawdown_pct":  round(self._max_drawdown(), 4),
                "max_drawdown_usdc": round(
                    self._max_drawdown() * self._result.initial_balance, 4
                ),
                "volatility_pct":    round(self._volatility(), 4),
                "calmar_ratio":      round(self._calmar_ratio(), 4),
            },

            # ── Duración de trades ────────────────────────────────────
            "duration": {
                "avg_ticks_in_trade": round(self._avg_duration_ticks(), 1),
                "exit_reasons":       self._exit_reason_breakdown(),
            },

            # ── Config usada ──────────────────────────────────────────
            "config": {
                "threshold":          self._result.config.threshold,
                "stop_loss_pct":      self._result.config.stop_loss_pct,
                "target_price":       self._result.config.target_price,
                "required_ticks":     self._result.config.required_ticks,
                "timeout_minutes":    self._result.config.timeout_minutes,
                "position_size_usdc": self._result.config.position_size_usdc,
            },
        }

        logger.info(
            "metrics_computed",
            asset=self._result.asset,
            window=self._result.window,
            total_pnl=metrics["pnl"]["total_pnl_usdc"],
            sharpe=metrics["risk"]["sharpe_ratio"],
            win_rate=metrics["performance"]["win_rate"],
            max_dd=metrics["risk"]["max_drawdown_pct"],
        )

        return metrics

    # ------------------------------------------------------------------
    # CÁLCULOS INDIVIDUALES
    # ------------------------------------------------------------------

    def _total_pnl(self) -> float:
        return self._result.final_balance - self._result.initial_balance

    def _total_pnl_pct(self) -> float:
        if self._result.initial_balance <= 0:
            return 0.0
        return self._total_pnl() / self._result.initial_balance

    def _avg_pnl(self) -> float:
        if not self._pnls:
            return 0.0
        return sum(self._pnls) / len(self._pnls)

    def _avg_pnl_pct(self) -> float:
        if not self._pnl_pcts:
            return 0.0
        return sum(self._pnl_pcts) / len(self._pnl_pcts)

    def _win_rate(self) -> float:
        """Porcentaje de trades con PnL positivo."""
        if not self._pnls:
            return 0.0
        winners = sum(1 for p in self._pnls if p > 0)
        return winners / len(self._pnls)

    def _profit_factor(self) -> float:
        """
        Ratio ganancias totales / pérdidas totales.
        > 1.0 = sistema rentable. > 1.5 = bueno. > 2.0 = excelente.
        """
        gross_profit = sum(p for p in self._pnls if p > 0)
        gross_loss   = abs(sum(p for p in self._pnls if p < 0))
        if gross_loss == 0:
            return float("inf") if gross_profit > 0 else 0.0
        return gross_profit / gross_loss

    def _sharpe_ratio(self, risk_free_rate: float = 0.0) -> float:
        """
        Sharpe ratio: retorno ajustado por volatilidad.
        Anualizado asumiendo ~17520 ticks de 30s por año.
        > 1.0 = aceptable. > 2.0 = bueno. > 3.0 = excelente.
        """
        if len(self._pnl_pcts) < 2:
            return 0.0

        avg_return = sum(self._pnl_pcts) / len(self._pnl_pcts)
        std_return = self._std(self._pnl_pcts)

        if std_return == 0:
            return 0.0

        # Factor de anualización para backtesting de predicción
        annualization = math.sqrt(len(self._pnl_pcts))
        return (avg_return - risk_free_rate) / std_return * annualization

    def _sortino_ratio(self) -> float:
        """
        Sortino ratio: como Sharpe pero solo penaliza volatilidad negativa.
        Más apropiado para estrategias asimétricas.
        """
        if len(self._pnl_pcts) < 2:
            return 0.0

        avg_return    = sum(self._pnl_pcts) / len(self._pnl_pcts)
        negative_pcts = [p for p in self._pnl_pcts if p < 0]

        if not negative_pcts:
            return float("inf") if avg_return > 0 else 0.0

        downside_std = self._std(negative_pcts)
        if downside_std == 0:
            return 0.0

        return avg_return / downside_std * math.sqrt(len(self._pnl_pcts))

    def _max_drawdown(self) -> float:
        """
        Máxima caída desde un pico de balance hasta el siguiente valle.
        Calculado sobre la curva de equity del backtest.
        """
        if not self._pnls:
            return 0.0

        # Construye la curva de equity
        balance      = self._result.initial_balance
        peak_balance = balance
        max_dd       = 0.0

        for pnl in self._pnls:
            balance     += pnl
            peak_balance = max(peak_balance, balance)
            drawdown     = (peak_balance - balance) / peak_balance
            max_dd       = max(max_dd, drawdown)

        return max_dd

    def _volatility(self) -> float:
        """Desviación estándar de los retornos por trade."""
        return self._std(self._pnl_pcts) if self._pnl_pcts else 0.0

    def _calmar_ratio(self) -> float:
        """
        Calmar ratio: retorno anual / max drawdown.
        Mide retorno obtenido por unidad de drawdown máximo.
        """
        max_dd = self._max_drawdown()
        if max_dd == 0:
            return float("inf") if self._total_pnl_pct() > 0 else 0.0
        return self._total_pnl_pct() / max_dd

    def _avg_duration_ticks(self) -> float:
        """Duración media de los trades en número de ticks."""
        durations = [
            p.duration_ticks for p in self._closed
            if p.duration_ticks is not None
        ]
        if not durations:
            return 0.0
        return sum(durations) / len(durations)

    def _exit_reason_breakdown(self) -> dict[str, int]:
        """Distribución de razones de salida."""
        reasons: dict[str, int] = {}
        for pos in self._closed:
            reason = (pos.exit_reason or "unknown").split(":")[0].strip()
            reasons[reason] = reasons.get(reason, 0) + 1
        return dict(sorted(reasons.items(), key=lambda x: -x[1]))

    @staticmethod
    def _std(values: list[float]) -> float:
        """Desviación estándar poblacional."""
        if len(values) < 2:
            return 0.0
        mean = sum(values) / len(values)
        variance = sum((v - mean) ** 2 for v in values) / len(values)
        return math.sqrt(variance)

    @classmethod
    def compare(
        cls, results: list[BacktestResult]
    ) -> list[dict]:
        """
        Compara múltiples resultados de backtesting.
        Ordena por Sharpe ratio descendente.
        Usado para el parameter sweep.
        """
        comparisons = []

        for result in results:
            m = cls(result)
            metrics = m.compute_all()
            comparisons.append({
                "threshold":     result.config.threshold,
                "stop_loss_pct": result.config.stop_loss_pct,
                "target_price":  result.config.target_price,
                "sharpe_ratio":  metrics["risk"]["sharpe_ratio"],
                "win_rate":      metrics["performance"]["win_rate"],
                "profit_factor": metrics["performance"]["profit_factor"],
                "total_pnl":     metrics["pnl"]["total_pnl_usdc"],
                "max_drawdown":  metrics["risk"]["max_drawdown_pct"],
                "total_trades":  metrics["summary"]["closed_positions"],
            })

        # Ordena por Sharpe ratio descendente
        comparisons.sort(key=lambda x: x["sharpe_ratio"], reverse=True)
        return comparisons
