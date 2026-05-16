# src/strategies/filters/time_filter.py

from datetime import datetime, timezone
from src.strategies.filters.base import IFilter, FilterResult
from src.domain.value_objects.market_tick import MarketTick
from src.strategies.base import StrategyState


class TimeFilter(IFilter):
    """
    Bloquea operaciones en ventanas horarias de baja liquidez.
    Por defecto bloquea entre 00:00 y 06:00 UTC (mercados dormidos).
    Configurable con lista de rangos bloqueados (hora_inicio, hora_fin) en UTC.
    """

    def __init__(
        self,
        blocked_hours: list[tuple[int, int]] | None = None,
    ):
        # blocked_hours: lista de (hora_inicio, hora_fin) UTC donde NO operamos
        # Default: bloquea madrugada UTC (00:00 - 06:00)
        self._blocked = blocked_hours or [(0, 6)]

    @property
    def name(self) -> str:
        return "TimeFilter"

    def apply(self, tick: MarketTick, state: StrategyState) -> FilterResult:
        # Usa el timestamp del tick, no datetime.utcnow(), para consistencia
        hour = tick.timestamp.hour

        for start_h, end_h in self._blocked:
            if start_h <= hour < end_h:
                return FilterResult.fail(
                    self.name,
                    f"hora={hour:02d}:xx UTC en ventana bloqueada [{start_h:02d}h-{end_h:02d}h)",
                )

        return FilterResult.ok(
            self.name,
            f"hora={hour:02d}:xx UTC fuera de ventanas bloqueadas",
        )