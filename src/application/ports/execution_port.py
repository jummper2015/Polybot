# src/application/ports/execution_port.py

from abc import ABC, abstractmethod

from src.domain.entities.position import Position
from src.domain.value_objects.signal import Signal
from src.domain.value_objects.trade_result import TradeResult


class IExecutionPort(ABC):
    """
    Contrato de ejecución de órdenes.
    PaperTradingHandler y RealTradingHandler implementan este contrato.
    """

    @abstractmethod
    async def execute_entry(
        self,
        signal: Signal,
        market_id: str,
        amount: float,
    ) -> TradeResult:
        """Ejecuta una orden de entrada (compra YES o NO)."""
        ...

    @abstractmethod
    async def execute_exit(
        self,
        position: Position,
        reason: str,
    ) -> TradeResult:
        """Cierra una posición existente."""
        ...

    @abstractmethod
    async def execute_hedge(
        self,
        position: Position,
        hedge_amount: float,
    ) -> TradeResult:
        """Ejecuta hedge (posición opuesta parcial)."""
        ...
