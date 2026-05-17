# src/risk/rules/hedge.py

from src.domain.value_objects.signal import Signal, SignalType
from src.domain.value_objects.risk_decision import RiskDecision
from src.risk.base import IRule
from src.risk.context import RiskContext


class HedgeRule(IRule):
    """
    Regla específica para señales de hedge (BUY_NO).
    Solo permite el hedge si la exposición neta total
    no supera el límite configurado.
    Si la señal NO es BUY_NO, esta regla siempre permite.
    """

    def __init__(self, max_net_exposure_pct: float = 0.50):
        # max_net_exposure_pct: exposición total máxima para permitir hedge (50%)
        self._max_exposure = max_net_exposure_pct

    @property
    def name(self) -> str:
        return "HedgeRule"

    @property
    def priority(self) -> int:
        return 5  # Menor prioridad — solo actúa sobre BUY_NO

    def evaluate(self, signal: Signal, context: RiskContext) -> RiskDecision:
        # Solo aplica a señales de hedge
        if signal.type != SignalType.BUY_NO:
            return self._allow(
                f"not_a_hedge_signal (type={signal.type.value}) — regla no aplica"
            )

        net_exposure = context.exposure_pct

        if net_exposure >= self._max_exposure:
            return self._deny(
                f"hedge_denied: net_exposure={net_exposure:.2%} >= "
                f"max={self._max_exposure:.2%} "
                f"(total_invested={context.total_exposure_usdc:.2f} USDC, "
                f"balance={context.current_balance:.2f} USDC) — "
                f"demasiada exposición para añadir hedge"
            )

        # Calcula monto de hedge proporcional a la posición existente
        hedge_amount = min(
            context.requested_amount,
            context.market_exposure_usdc * 0.5,  # Hedge máximo 50% de la posición
        )

        return self._allow(
            reason=(
                f"hedge_allowed: net_exposure={net_exposure:.2%} < "
                f"max={self._max_exposure:.2%}, "
                f"hedge_amount={hedge_amount:.2f} USDC"
            ),
            suggested_amount=round(hedge_amount, 2),
        )