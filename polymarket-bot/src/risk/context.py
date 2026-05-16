# src/risk/context.py

from dataclasses import dataclass
from datetime import datetime


@dataclass
class RiskContext:
    """
    Snapshot del estado del portfolio en el momento de evaluar una señal.
    Construido por TradingService justo antes de llamar a RiskEngine.evaluate().
    Inmutable durante la evaluación — las reglas solo leen, nunca modifican.
    """

    # ── Balance ───────────────────────────────────────────────────────
    current_balance:      float   # USDC disponibles ahora mismo
    initial_day_balance:  float   # USDC al inicio del día (reset a medianoche UTC)

    # ── Posiciones ────────────────────────────────────────────────────
    open_positions_count: int     # Total de posiciones abiertas (todos los mercados)
    market_exposure_usdc: float   # USDC ya invertidos en ESTE mercado específico
    total_exposure_usdc:  float   # USDC invertidos en TODOS los mercados

    # ── Orden solicitada ──────────────────────────────────────────────
    requested_amount:     float   # USDC que la estrategia quiere invertir ahora
    market_id:            str     # Mercado al que va dirigida la señal
    trading_mode:         str     # "paper" o "real"

    # ── Timestamp ─────────────────────────────────────────────────────
    evaluated_at:         datetime = None

    def __post_init__(self):
        if self.evaluated_at is None:
            self.evaluated_at = datetime.utcnow()

    @property
    def drawdown_pct(self) -> float:
        """
        Pérdida porcentual respecto al balance inicial del día.
        Positivo = pérdida. Negativo = ganancia.
        """
        if self.initial_day_balance <= 0:
            return 0.0
        return (
            self.initial_day_balance - self.current_balance
        ) / self.initial_day_balance

    @property
    def exposure_pct(self) -> float:
        """Porcentaje del balance total expuesto en mercados."""
        if self.current_balance <= 0:
            return 0.0
        return self.total_exposure_usdc / self.current_balance

    @property
    def market_exposure_pct(self) -> float:
        """Porcentaje del balance expuesto en este mercado específico."""
        if self.current_balance <= 0:
            return 0.0
        return self.market_exposure_usdc / self.current_balance