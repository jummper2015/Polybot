# src/risk/rules/max_exposure.py

from src.domain.value_objects.signal import Signal
from src.domain.value_objects.risk_decision import RiskDecision
from src.risk.base import IRule
from src.risk.context import RiskContext


class MaxExposureRule(IRule):
    """
    Limita el porcentaje del balance invertido en un solo mercado.
    En lugar de denegar directamente, intenta reducir el monto
    al máximo permitido si hay margen disponible.
    """

    def __init__(self, max_exposure_pct: float = 0.30):
        # max_exposure_pct: máximo % del balance en un mercado (30% default)
        self._max_exposure = max_exposure_pct

    @property
    def name(self) -> str:
        return "MaxExposureRule"

    @property
    def priority(self) -> int:
        return 3

    def evaluate(self, signal: Signal, context: RiskContext) -> RiskDecision:
        # Cuánto se puede invertir máximo en este mercado
        max_allowed_usdc = context.current_balance * self._max_exposure

        # Cuánto ya está invertido en este mercado
        already_invested = context.market_exposure_usdc

        # Cuánto queda disponible para este mercado
        available_usdc = max_allowed_usdc - already_invested

        if available_usdc <= 0:
            return self._deny(
                f"market_exposure={already_invested:.2f} USDC ya alcanzó "
                f"max_allowed={max_allowed_usdc:.2f} USDC "
                f"({self._max_exposure:.0%} de {context.current_balance:.2f})"
            )

        if context.requested_amount > available_usdc:
            # Reduce el monto al máximo disponible en lugar de denegar
            return self._allow(
                reason=(
                    f"amount_adjusted: requested={context.requested_amount:.2f} → "
                    f"adjusted={available_usdc:.2f} USDC "
                    f"(market_limit={max_allowed_usdc:.2f} USDC)"
                ),
                suggested_amount=round(available_usdc, 2),
            )

        return self._allow(
            f"exposure_ok: market={already_invested + context.requested_amount:.2f} "
            f"<= max={max_allowed_usdc:.2f} USDC ({self._max_exposure:.0%})"
        )