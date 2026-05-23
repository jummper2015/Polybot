# src/strategies/filters/liquidity_filter.py

from src.domain.value_objects.market_tick import MarketTick
from src.strategies.base import StrategyState
from src.strategies.filters.base import FilterResult, IFilter


class LiquidityFilter(IFilter):
    """
    Rechaza mercados con volumen insuficiente.
    Volumen bajo = riesgo de no poder ejecutar la orden al precio esperado.
    """

    def __init__(self, min_volume_usdc: float = 1000.0):
        # min_volume_usdc: volumen mínimo en USDC en las últimas 24h
        self._min_volume = min_volume_usdc

    @property
    def name(self) -> str:
        return "LiquidityFilter"

    def apply(self, tick: MarketTick, state: StrategyState) -> FilterResult:
        if tick.volume_24h >= self._min_volume:
            return FilterResult.ok(
                self.name,
                f"volume={tick.volume_24h:.0f} >= min={self._min_volume:.0f} USDC",
            )
        return FilterResult.fail(
            self.name,
            f"volume={tick.volume_24h:.0f} < min={self._min_volume:.0f} USDC (liquidez insuficiente)",
        )
