# src/application/ports/market_data_port.py

from abc import ABC, abstractmethod

from src.domain.value_objects.market_tick import MarketTick


class IMarketDataPort(ABC):
    """
    Contrato para obtener datos de mercado.
    Implementado por el cliente HTTP/WS de Polymarket.
    """

    @abstractmethod
    async def get_active_markets(
        self, asset: str, window: str
    ) -> list[dict]: ...

    @abstractmethod
    async def get_market_tick(self, market_id: str) -> MarketTick: ...

    @abstractmethod
    async def subscribe_order_book(
        self,
        market_id: str,
        callback,           # async callable que recibe MarketTick
    ) -> None: ...

    @abstractmethod
    async def unsubscribe_order_book(self, market_id: str) -> None: ...
