# src/risk/rules/drawdown.py

from src.domain.value_objects.risk_decision import RiskDecision
from src.domain.value_objects.signal import Signal
from src.risk.base import IRule
from src.risk.context import RiskContext


class DrawdownRule(IRule):
    """
    Pausa el bot si la pérdida diaria supera un umbral.
    Protege contra días de mercado adverso que vaciarían la cuenta.
    Se resetea a medianoche UTC (gestionado por TradingService).
    """

    def __init__(self, max_daily_drawdown_pct: float = 0.10):
        # max_daily_drawdown_pct: pérdida máxima diaria permitida (10% default)
        self._max_drawdown = max_daily_drawdown_pct

    @property
    def name(self) -> str:
        return "DrawdownRule"

    @property
    def priority(self) -> int:
        return 2

    def evaluate(self, signal: Signal, context: RiskContext) -> RiskDecision:
        drawdown = context.drawdown_pct

        if drawdown >= self._max_drawdown:
            return self._deny(
                f"daily_drawdown={drawdown:.2%} >= "
                f"max_allowed={self._max_drawdown:.2%} "
                f"(initial={context.initial_day_balance:.2f}, "
                f"current={context.current_balance:.2f} USDC) — "
                f"bot pausado hasta mañana UTC"
            )

        # Avisa si estamos cerca del límite (> 80% del drawdown máximo)
        warning_threshold = self._max_drawdown * 0.80
        if drawdown >= warning_threshold:
            reason = (
                f"drawdown={drawdown:.2%} approaching limit={self._max_drawdown:.2%} "
                f"(warning at {warning_threshold:.2%})"
            )
        else:
            reason = f"drawdown={drawdown:.2%} < max={self._max_drawdown:.2%}"

        return self._allow(reason)
