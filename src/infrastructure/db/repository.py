# src/infrastructure/db/repository.py

import structlog
from datetime import datetime
from sqlalchemy import select, update, func
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.application.ports.repository_port import IRepositoryPort
from src.domain.entities.market import Market, Asset, Window, MarketStatus
from src.domain.entities.order import Order, OrderSide, OrderStatus, TradingMode
from src.domain.entities.position import Position
from src.infrastructure.db.models import (
    MarketModel, OrderModel, PositionModel, AuditLogModel
)

logger = structlog.get_logger(__name__)


class SQLAlchemyRepository(IRepositoryPort):
    """
    Implementación concreta de IRepositoryPort usando SQLAlchemy async.
    La capa de aplicación NUNCA importa esta clase directamente —
    solo conoce el contrato IRepositoryPort.
    """

    def __init__(self, session_factory: async_sessionmaker):
        # session_factory: crea una nueva sesión por operación
        self._session_factory = session_factory

    # ------------------------------------------------------------------
    # MARKETS
    # ------------------------------------------------------------------

    async def save_market(self, market: Market) -> Market:
        """
        Upsert: inserta el market si no existe, actualiza si ya existe.
        Usado tanto en el discovery inicial como en actualizaciones de precio.
        """
        async with self._session_factory() as session:
            async with session.begin():
                existing = await session.get(MarketModel, market.id)

                if existing:
                    # Actualiza campos que pueden cambiar
                    existing.status     = market.status.value
                    existing.yes_price  = market.yes_price
                    existing.no_price   = market.no_price
                    existing.volume_24h = market.volume_24h
                    existing.updated_at = datetime.utcnow()
                else:
                    # Inserta nuevo market
                    model = self._market_to_model(market)
                    session.add(model)

        return market

    async def get_active_markets(
        self,
        asset:  str | None = None,
        window: str | None = None,
    ) -> list[Market]:
        """
        Devuelve mercados activos no expirados.
        Filtra por asset y/o window si se especifican.
        """
        async with self._session_factory() as session:
            stmt = (
                select(MarketModel)
                .where(MarketModel.status == "active")
                .where(MarketModel.expiry > datetime.utcnow())
                .order_by(MarketModel.volume_24h.desc())
            )

            if asset:
                stmt = stmt.where(MarketModel.asset == asset)
            if window:
                stmt = stmt.where(MarketModel.window == window)

            result  = await session.execute(stmt)
            models  = result.scalars().all()
            return [self._model_to_market(m) for m in models]

    async def get_market_by_id(self, market_id: str) -> Market | None:
        """Busca un market por su condition_id."""
        async with self._session_factory() as session:
            model = await session.get(MarketModel, market_id)
            return self._model_to_market(model) if model else None

    # ------------------------------------------------------------------
    # ORDERS
    # ------------------------------------------------------------------

    async def save_order(self, order: Order) -> Order:
        """
        Upsert de orden.
        Llamado múltiples veces: al crear (PENDING), al fill y al fallar.
        """
        async with self._session_factory() as session:
            async with session.begin():
                existing = await session.get(OrderModel, order.id)

                if existing:
                    # Actualiza estado de la orden
                    existing.fill_price = order.fill_price
                    existing.slippage   = order.slippage
                    existing.status     = order.status.value
                    existing.filled_at  = order.filled_at
                    existing.error      = order.error
                else:
                    model = self._order_to_model(order)
                    session.add(model)

        return order

    async def get_orders(
        self,
        status: str | None = None,
        limit:  int        = 50,
    ) -> list[Order]:
        """Devuelve órdenes ordenadas por fecha descendente."""
        async with self._session_factory() as session:
            stmt = (
                select(OrderModel)
                .order_by(OrderModel.created_at.desc())
                .limit(limit)
            )
            if status:
                stmt = stmt.where(OrderModel.status == status)

            result = await session.execute(stmt)
            models = result.scalars().all()
            return [self._model_to_order(m) for m in models]

    async def get_order_by_id(self, order_id: str) -> Order | None:
        """Busca una orden por su UUID."""
        async with self._session_factory() as session:
            model = await session.get(OrderModel, order_id)
            return self._model_to_order(model) if model else None

    # ------------------------------------------------------------------
    # POSITIONS
    # ------------------------------------------------------------------

    async def save_position(self, position: Position) -> Position:
        """
        Upsert de posición.
        Llamado al abrir (sin PnL) y al cerrar (con PnL).
        """
        async with self._session_factory() as session:
            async with session.begin():
                existing = await session.get(PositionModel, position.id)

                if existing:
                    existing.exit_price  = position.exit_price
                    existing.pnl         = position.pnl
                    existing.pnl_pct     = position.pnl_pct
                    existing.exit_reason = position.exit_reason
                    existing.closed_at   = position.closed_at
                else:
                    model = self._position_to_model(position)
                    session.add(model)

        return position

    async def get_positions(
        self,
        mode:      str | None = None,
        open_only: bool       = False,
    ) -> list[Position]:
        """
        Devuelve posiciones ordenadas por fecha de apertura descendente.
        `open_only=True` filtra solo las que tienen `closed_at IS NULL`.
        """
        async with self._session_factory() as session:
            stmt = (
                select(PositionModel)
                .order_by(PositionModel.opened_at.desc())
            )
            if mode:
                stmt = stmt.where(PositionModel.mode == mode)
            if open_only:
                stmt = stmt.where(PositionModel.closed_at.is_(None))

            result = await session.execute(stmt)
            models = result.scalars().all()
            return [self._model_to_position(m) for m in models]

    async def get_position_by_id(self, position_id: str) -> Position | None:
        """Busca una posición por su UUID."""
        async with self._session_factory() as session:
            model = await session.get(PositionModel, position_id)
            return self._model_to_position(model) if model else None

    async def get_open_positions_count(self) -> int:
        """Cuenta posiciones abiertas. Usado por RiskEngine."""
        async with self._session_factory() as session:
            stmt   = (
                select(func.count(PositionModel.id))
                .where(PositionModel.closed_at.is_(None))
            )
            result = await session.execute(stmt)
            return result.scalar() or 0

    async def get_total_pnl(self, mode: str | None = None) -> float:
        """
        Suma de PnL realizado de posiciones cerradas.
        Usado por RiskEngine para calcular drawdown.
        """
        async with self._session_factory() as session:
            stmt = (
                select(func.coalesce(func.sum(PositionModel.pnl), 0.0))
                .where(PositionModel.closed_at.isnot(None))
            )
            if mode:
                stmt = stmt.where(PositionModel.mode == mode)

            result = await session.execute(stmt)
            return float(result.scalar() or 0.0)

    # ------------------------------------------------------------------
    # AUDIT LOG
    # ------------------------------------------------------------------

    async def save_audit_log(self, entry: dict) -> None:
        """
        INSERT-only en audit_logs.
        Nunca se llama UPDATE ni DELETE sobre esta tabla.
        """
        async with self._session_factory() as session:
            async with session.begin():
                model = AuditLogModel(
                    action    = entry.get("audit_action", "unknown"),
                    order_id  = entry.get("order_id"),
                    market_id = entry.get("market_id"),
                    amount    = entry.get("amount"),
                    details   = {
                        k: v for k, v in entry.items()
                        if k not in ("audit_action","order_id","market_id","amount","timestamp")
                    },
                )
                session.add(model)

    # ------------------------------------------------------------------
    # MAPPERS: Domain ↔ ORM
    # ------------------------------------------------------------------

    def _market_to_model(self, m: Market) -> MarketModel:
        return MarketModel(
            id=m.id, asset=m.asset.value, window=m.window.value,
            question=m.question, status=m.status.value,
            yes_token_id=m.yes_token_id, no_token_id=m.no_token_id,
            yes_price=m.yes_price, no_price=m.no_price,
            volume_24h=m.volume_24h, expiry=m.expiry,
            discovered_at=m.discovered_at,
        )

    def _model_to_market(self, m: MarketModel) -> Market:
        return Market(
            id=m.id, asset=Asset(m.asset), window=Window(m.window),
            question=m.question, status=MarketStatus(m.status),
            yes_token_id=m.yes_token_id, no_token_id=m.no_token_id,
            yes_price=m.yes_price, no_price=m.no_price,
            volume_24h=m.volume_24h, expiry=m.expiry,
            discovered_at=m.discovered_at,
        )

    def _order_to_model(self, o: Order) -> OrderModel:
        return OrderModel(
            id=o.id, market_id=o.market_id,
            side=o.side.value, amount=o.amount,
            target_price=o.target_price, fill_price=o.fill_price,
            slippage=o.slippage, status=o.status.value,
            mode=o.mode.value, strategy=o.strategy,
            reason=o.reason, error=o.error,
            created_at=o.created_at, filled_at=o.filled_at,
        )

    def _model_to_order(self, m: OrderModel) -> Order:
        return Order(
            id=m.id, market_id=m.market_id,
            side=OrderSide(m.side), amount=m.amount,
            target_price=m.target_price, fill_price=m.fill_price,
            slippage=m.slippage, status=OrderStatus(m.status),
            mode=TradingMode(m.mode), strategy=m.strategy,
            reason=m.reason, error=m.error,
            created_at=m.created_at, filled_at=m.filled_at,
        )

    def _position_to_model(self, p: Position) -> PositionModel:
        return PositionModel(
            id=p.id, market_id=p.market_id,
            asset=p.asset, window=p.window,
            side=p.side, amount=p.amount,
            shares=p.shares, entry_price=p.entry_price,
            exit_price=p.exit_price, pnl=p.pnl,
            pnl_pct=p.pnl_pct, mode=p.mode,
            strategy=p.strategy, exit_reason=p.exit_reason,
            opened_at=p.opened_at, closed_at=p.closed_at,
        )

    def _model_to_position(self, m: PositionModel) -> Position:
        return Position(
            id=m.id, market_id=m.market_id,
            asset=m.asset, window=m.window,
            side=m.side, amount=m.amount,
            shares=m.shares, entry_price=m.entry_price,
            exit_price=m.exit_price, pnl=m.pnl,
            pnl_pct=m.pnl_pct, mode=m.mode,
            strategy=m.strategy, exit_reason=m.exit_reason,
            opened_at=m.opened_at, closed_at=m.closed_at,
        )