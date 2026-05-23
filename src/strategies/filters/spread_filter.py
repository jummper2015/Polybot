# src/strategies/filters/spread_filter.py

from src.domain.value_objects.market_tick import MarketTick
from src.strategies.base import StrategyState
from src.strategies.filters.base import FilterResult, IFilter


class SpreadFilter(IFilter):
    """
    Rechaza ticks donde el spread bid-ask es demasiado alto.
    Un spread alto indica mercado ilíquido o manipulado.
    """

    def __init__(self, max_spread: float = 0.03):
        # max_spread: máximo diferencial bid-ask aceptable (default 3%)
        self._max_spread = max_spread

    @property
    def name(self) -> str:
        return "SpreadFilter"

    def apply(self, tick: MarketTick, state: StrategyState) -> FilterResult:
        if tick.spread <= self._max_spread:
            return FilterResult.ok(
                self.name,
                f"spread={tick.spread:.4f} <= max={self._max_spread}",
            )
        return FilterResult.fail(
            self.name,
            f"spread={tick.spread:.4f} > max={self._max_spread} (mercado ilíquido)",
        )
