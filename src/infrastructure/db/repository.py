# src/infrastructure/db/repository.py

from datetime import datetime

import structlog
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from src.application.ports.repository_port import IRepositoryPort
from src.domain.entities.market import Market
from src.domain.entities.order import Order
from src.domain.entities.position import Position
from src.domain.enums.asset import Asset
from src.domain.enums.market_status import MarketStatus
from src.domain.enums.order_side import OrderSide
from src.domain.enums.order_status import OrderStatus
from src.domain.enums.trading_mode import TradingMode
from src.domain.enums.window import Window
from src.infrastructure.db.models import (
    AuditLogModel,
    BotSettingsModel,
    MarketModel,
    OrderModel,
    PositionModel,
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

        R2.5.4: si hay conflicto de unique (asset, window, expiry) — el mismo
        mercado lógico ya existe con distinto condition_id — loguea warning
        y retorna sin crash (el mercado existente gana).
        """
        try:
            async with self._session_factory() as session:
                async with session.begin():
                    existing = await session.get(MarketModel, market.id)

                    if existing:
                        # Actualiza campos que pueden cambiar
                        existing.asset      = market.asset.value
                        existing.window     = market.window.value
                        existing.status     = market.status.value
                        existing.yes_price  = market.yes_price
                        existing.no_price   = market.no_price
                        existing.volume_24h = market.volume_24h
                        existing.updated_at = datetime.utcnow()
                    else:
                        # Inserta nuevo market
                        model = self._market_to_model(market)
                        session.add(model)

        except IntegrityError as e:
            # R2.5.4: duplicate (asset, window, expiry) → ya existe, no crash
            if "uq_markets_asset_window_expiry" in str(e).lower():
                logger.warning(
                    "duplicate_market_by_asset_window_expiry_ignored",
                    market_id=market.id,
                    asset=market.asset.value,
                    window=market.window.value,
                )
                return market
            raise

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

        R2.2.2 (Ola 1.2): si el IntegrityError es por colisión del UNIQUE
        `ix_orders_idempotency` (misma idempotency_key), retorna la orden
        existente sin crash — es el comportamiento esperado de idempotencia:
        un reintento post-timeout NO debe crear una orden nueva.
        """
        try:
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

        except IntegrityError as e:
            # R2.2.2 (Ola 1.2): idempotency_key collision — no crash
            if order.idempotency_key and "ix_orders_idempotency" in str(e).lower():
                logger.warning(
                    "duplicate_order_by_idempotency_key_ignored",
                    order_id=order.id,
                    idempotency_key=order.idempotency_key,
                    market_id=order.market_id,
                )
                # Retorna la orden previamente persistida con la misma key
                existing_by_key = await self._get_order_by_idempotency_key(
                    order.idempotency_key
                )
                return existing_by_key if existing_by_key else order
            raise

        return order

    async def _get_order_by_idempotency_key(
        self, idempotency_key: str
    ) -> Order | None:
        """
        Lookup interno por idempotency_key. Usado por save_order para
        resolver el ganador de una race condition.
        """
        async with self._session_factory() as session:
            stmt = (
                select(OrderModel)
                .where(OrderModel.idempotency_key == idempotency_key)
                .limit(1)
            )
            result = await session.execute(stmt)
            model  = result.scalar_one_or_none()
            return self._model_to_order(model) if model else None

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

        R2.5.3: si el IntegrityError es por duplicate open position
        (uq_positions_open), loguea warning y retorna la posición sin
        crash. Otros IntegrityError se relanzan.
        """
        try:
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

        except IntegrityError as e:
            # R2.5.3: duplicate open position → ya abierta, no crash
            if "uq_positions_open" in str(e).lower():
                logger.warning(
                    "duplicate_open_position_ignored",
                    market_id=position.market_id,
                    mode=position.mode,
                )
                return position
            raise

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
    # BOT SETTINGS
    # ------------------------------------------------------------------

    async def get_bot_setting(self, key: str) -> str | None:
        """Obtiene un valor de configuración por su key."""
        async with self._session_factory() as session:
            model = await session.get(BotSettingsModel, key)
            return model.value if model else None

    async def set_bot_setting(self, key: str, value: str) -> None:
        """Upsert de configuración clave-valor."""
        async with self._session_factory() as session:
            async with session.begin():
                existing = await session.get(BotSettingsModel, key)
                if existing:
                    existing.value = value
                    existing.updated_at = datetime.utcnow()
                else:
                    model = BotSettingsModel(key=key, value=value)
                    session.add(model)

    async def get_all_bot_settings(self) -> dict[str, str]:
        """Devuelve todas las configuraciones como diccionario."""
        async with self._session_factory() as session:
            stmt = select(BotSettingsModel)
            result = await session.execute(stmt)
            models = result.scalars().all()
            return {m.key: m.value for m in models}

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
        # R2.2.2 (Ola 1.1): idempotency_key DEBE persistirse a BD.
        # Sin este mapeo el UNIQUE index ix_orders_idempotency no bloquea
        # reintentos post-timeout y genera órdenes duplicadas en real.
        return OrderModel(
            id=o.id, market_id=o.market_id,
            side=o.side.value, amount=o.amount,
            target_price=o.target_price, fill_price=o.fill_price,
            slippage=o.slippage, status=o.status.value,
            mode=o.mode.value, strategy=o.strategy,
            reason=o.reason, error=o.error,
            idempotency_key=o.idempotency_key,
            created_at=o.created_at, filled_at=o.filled_at,
        )

    def _model_to_order(self, m: OrderModel) -> Order:
        # R2.2.2 (Ola 1.1): round-trip simétrico con _order_to_model.
        return Order(
            id=m.id, market_id=m.market_id,
            side=OrderSide(m.side), amount=m.amount,
            target_price=m.target_price, fill_price=m.fill_price,
            slippage=m.slippage, status=OrderStatus(m.status),
            mode=TradingMode(m.mode), strategy=m.strategy,
            reason=m.reason, error=m.error,
            idempotency_key=m.idempotency_key,
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
