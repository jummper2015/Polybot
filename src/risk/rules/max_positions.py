# src/risk/rules/max_positions.py

from src.domain.value_objects.signal import Signal, SignalType
from src.domain.value_objects.risk_decision import RiskDecision
from src.risk.base import IRule
from src.risk.context import RiskContext


class MaxPositionsRule(IRule):
    """
    Limita el número de posiciones abiertas simultáneas.
    No aplica a señales de EXIT ni de hedge BUY_NO
    (salir o cubrir siempre está permitido).
    """

    def __init__(self, max_open_positions: int = 5):
        # max_open_positions: máximo de posiciones abiertas al mismo tiempo
        self._max_positions = max_open_positions

    @property
    def name(self) -> str:
        return "MaxPositionsRule"

    @property
    def priority(self) -> int:
        return 4

    def evaluate(self, signal: Signal, context: RiskContext) -> RiskDecision:
        # Las salidas y hedges siempre se permiten — no consumen "slots"
        if signal.type in (SignalType.EXIT, SignalType.BUY_NO):
            return self._allow(
                f"signal_type={signal.type.value} siempre permitido "
                f"(no consume slot de posición)"
            )

        if context.open_positions_count >= self._max_positions:
            return self._deny(
                f"open_positions={context.open_positions_count} >= "
                f"max={self._max_positions} — "
                f"esperar a cerrar alguna posición antes de abrir nueva"
            )

        remaining = self._max_positions - context.open_positions_count
        return self._allow(
            f"positions={context.open_positions_count}/{self._max_positions} "
            f"({remaining} slots disponibles)"
        )