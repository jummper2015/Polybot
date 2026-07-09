# src/application/ports/repository_port.py

from abc import ABC, abstractmethod

from src.domain.entities.market import Market
from src.domain.entities.order import Order
from src.domain.entities.position import Position
from src.domain.value_objects.redeem_receipt import RedeemReceipt


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

    # --- Bot Settings ---
    @abstractmethod
    async def get_bot_setting(self, key: str) -> str | None: ...

    @abstractmethod
    async def set_bot_setting(self, key: str, value: str) -> None: ...

    @abstractmethod
    async def get_all_bot_settings(self) -> dict[str, str]: ...

    # --- Redeem Operations (R2.0-redeem-impl F1 Paso 2) ---
    @abstractmethod
    async def save_redeem_operation(self, receipt: RedeemReceipt) -> None:
        """
        Persiste o actualiza redeem_operation en DB.

        Si redeem_op_id ya existe → UPDATE (idempotente).
        Si no existe → INSERT.

        Usado por CTFRedeemer para:
          1. INSERT pre-submit (status=pending, tx_hash=None)
          2. UPDATE post-MINED (tx_hash, gas_used, mined_at)
          3. UPDATE post-CONFIRMED (pusd_received, confirmed_at)
        """
        ...

    @abstractmethod
    async def get_redeem_operation(self, redeem_op_id: str) -> RedeemReceipt | None:
        """Obtiene redeem_operation por UUID (None si no existe)."""
        ...

    @abstractmethod
    async def get_pending_redeems(self, limit: int = 100) -> list[RedeemReceipt]:
        """
        Consulta redeem_operations con status IN (pending, submitted, mined).

        Usado por reconcile_on_startup para reanudar ops pendientes tras
        restart del bot. Ordenado por created_at ASC (FIFO).
        """
        ...

    @abstractmethod
    async def check_duplicate_redeem(self, condition_id: str) -> bool:
        """
        Verifica si ya existe redeem_operation PENDING/SUBMITTED/MINED
        para este condition_id (mercado).

        Usado por DuplicateRedeemError guard antes de iniciar redeem.
        True = ya existe operación activa, no reintentar.
        """
        ...
