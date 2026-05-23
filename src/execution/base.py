# src/execution/base.py

from abc import ABC, abstractmethod

from src.domain.entities.position import Position
from src.domain.value_objects.signal import Signal
from src.domain.value_objects.trade_result import TradeResult


class IExecutionHandler(ABC):
    """
    Contrato que tanto PaperTradingHandler como RealTradingHandler implementan.
    TradingService solo conoce esta interfaz — nunca el handler concreto.
    """

    @abstractmethod
    async def execute_entry(
        self,
        signal:    Signal,
        market_id: str,
        amount:    float,
    ) -> TradeResult: ...

    @abstractmethod
    async def execute_exit(
        self,
        position: Position,
        reason:   str,
    ) -> TradeResult: ...

    @abstractmethod
    async def execute_hedge(
        self,
        position:     Position,
        hedge_amount: float,
    ) -> TradeResult: ...
