# src/application/ports/repository_port.py

from abc import ABC, abstractmethod
from src.domain.entities.market import Market
from src.domain.entities.order import Order
from src.domain.entities.position import Position


class IRepositoryPort(ABC):
    """
    Contrato que define TODAS las operaciones de persistencia.
    La capa de aplicación solo conoce esta interfaz, nunca SQLAlchemy.
    """

    # --- Markets ---
    @abstractmethod
    async def save_market(self, market: Market) -> Market: ...

    @abstractmethod
    async def get_active_markets(
        self, asset: str | None = None, window: str | None = None
    ) -> list[Market]: ...

    @abstractmethod
    async def get_market_by_id(self, market_id: str) -> Market | None: ...

    # --- Orders ---
    @abstractmethod
    async def save_order(self, order: Order) -> Order: ...

    @abstractmethod
    async def get_orders(
        self, status: str | None = None, limit: int = 50
    ) -> list[Order]: ...

    @abstractmethod
    async def get_order_by_id(self, order_id: str) -> Order | None: ...

    # --- Positions ---
    @abstractmethod
    async def save_position(self, position: Position) -> Position: ...

    @abstractmethod
    async def get_positions(
        self, mode: str | None = None, open_only: bool = False
    ) -> list[Position]: ...

    @abstractmethod
    async def get_position_by_id(self, position_id: str) -> Position | None: ...

    @abstractmethod
    async def get_open_positions_count(self) -> int: ...

    @abstractmethod
    async def get_total_pnl(self, mode: str | None = None) -> float: ...