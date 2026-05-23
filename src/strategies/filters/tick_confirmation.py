# src/strategies/filters/tick_confirmation.py

from src.domain.value_objects.market_tick import MarketTick
from src.strategies.base import StrategyState
from src.strategies.filters.base import FilterResult, IFilter


class TickConfirmationFilter(IFilter):
    """
    Exige que la condición de entrada se cumpla en N ticks consecutivos.
    Evita entradas en picos momentáneos de precio (falsos positivos).
    El contador de ticks consecutivos viene del StrategyState.
    """

    def __init__(self, required_ticks: int = 3):
        # required_ticks: cuántos ticks seguidos deben cumplir la condición
        self._required = required_ticks

    @property
    def name(self) -> str:
        return "TickConfirmationFilter"

    def apply(self, tick: MarketTick, state: StrategyState) -> FilterResult:
        if state.consecutive_ticks >= self._required:
            return FilterResult.ok(
                self.name,
                f"consecutive_ticks={state.consecutive_ticks} >= required={self._required}",
            )
        return FilterResult.fail(
            self.name,
            f"consecutive_ticks={state.consecutive_ticks} < required={self._required} "
            f"(esperando confirmación)",
        )
