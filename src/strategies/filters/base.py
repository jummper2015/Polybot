# src/strategies/filters/base.py

from abc import ABC, abstractmethod
from dataclasses import dataclass

from src.domain.value_objects.market_tick import MarketTick
from src.strategies.base import StrategyState


@dataclass(frozen=True)
class FilterResult:
    """
    Resultado inmutable de aplicar un filtro.
    Si passed=False, reason explica por qué falló.
    """
    passed:      bool
    reason:      str
    filter_name: str

    @staticmethod
    def ok(filter_name: str, reason: str = "passed") -> "FilterResult":
        """Factory para resultado positivo."""
        return FilterResult(passed=True, reason=reason, filter_name=filter_name)

    @staticmethod
    def fail(filter_name: str, reason: str) -> "FilterResult":
        """Factory para resultado negativo."""
        return FilterResult(passed=False, reason=reason, filter_name=filter_name)


class IFilter(ABC):
    """
    Contrato para todos los filtros de entrada/salida.
    Los filtros son stateless — todo el estado viene en StrategyState.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Nombre del filtro para logs y métricas."""
        ...

    @abstractmethod
    def apply(
        self,
        tick:  MarketTick,
        state: StrategyState,
    ) -> FilterResult:
        """
        Evalúa si el tick pasa el filtro.
        Método síncrono — los filtros no hacen I/O.
        """
        ...
