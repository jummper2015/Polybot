# src/risk/rules/min_balance.py

from src.domain.value_objects.risk_decision import RiskDecision
from src.domain.value_objects.signal import Signal
from src.risk.base import IRule
from src.risk.context import RiskContext


class MinBalanceRule(IRule):
    """
    Regla de mayor prioridad.
    Garantiza que siempre quede un colchón mínimo de USDC libre.
    Nunca se puede desactivar — es un guardrail de seguridad.
    """

    def __init__(self, min_balance_usdc: float = 50.0):
        # min_balance_usdc: mínimo USDC que debe quedar libre DESPUÉS de la orden
        self._min_balance = min_balance_usdc

    @property
    def name(self) -> str:
        return "MinBalanceRule"

    @property
    def priority(self) -> int:
        return 1  # Mayor prioridad absoluta

    def evaluate(self, signal: Signal, context: RiskContext) -> RiskDecision:
        # Simula el balance después de ejecutar la orden
        balance_after = context.current_balance - context.requested_amount

        if balance_after < self._min_balance:
            return self._deny(
                f"balance_after={balance_after:.2f} USDC < "
                f"min_required={self._min_balance:.2f} USDC "
                f"(current={context.current_balance:.2f}, "
                f"requested={context.requested_amount:.2f})"
            )

        return self._allow(
            f"balance_after={balance_after:.2f} USDC >= min={self._min_balance:.2f} USDC"
        )
