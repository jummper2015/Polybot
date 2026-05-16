# src/application/services/portfolio_service.py

import structlog
from datetime import datetime, timezone

from src.application.ports.repository_port import IRepositoryPort
from src.execution.paper_handler import PaperTradingHandler
from src.infrastructure.cache.redis_client import RedisClient
from src.interfaces.api.schemas.position_schema import (
    PositionResponse,
    PositionsListResponse,
    SideEnum,
    TradingModeEnum,
)

logger = structlog.get_logger(__name__)


class PortfolioService:
    """
    Caso de uso: consulta y cálculo del portfolio.
    Agrega posiciones, calcula PnL y devuelve estado del balance.
    """

    def __init__(
        self,
        repository:     IRepositoryPort,
        paper_handler:  PaperTradingHandler,
        redis:          RedisClient,
    ):
        self._repo          = repository
        self._paper_handler = paper_handler
        self._redis         = redis

    async def get_positions(
        self,
        mode:      str | None = None,
        open_only: bool       = False,
    ) -> PositionsListResponse:
        """
        Devuelve todas las posiciones con PnL actualizado.
        Para posiciones abiertas, calcula PnL no realizado con precio actual.
        """
        positions = await self._repo.get_positions(mode=mode, open_only=open_only)

        responses  = []
        total_pnl  = 0.0
        open_count = 0

        for pos in positions:
            current_price = None
            pnl           = pos.pnl       # PnL realizado (posiciones cerradas)
            pnl_pct       = pos.pnl_pct

            if pos.is_open:
                open_count += 1
                # Obtiene precio actual para calcular PnL no realizado
                ws_state      = await self._redis.get_ws_state(pos.market_id)
                current_price = float(ws_state.get("last_yes_price", pos.entry_price)) \
                                if ws_state else pos.entry_price

                pnl     = pos.calculate_unrealized_pnl(current_price)
                pnl_pct = pos.calculate_unrealized_pnl_pct(current_price)

            if pnl:
                total_pnl += pnl

            responses.append(PositionResponse(
                id            = pos.id,
                market_id     = pos.market_id,
                asset         = pos.asset,
                window        = pos.window,
                side          = SideEnum(pos.side),
                amount        = pos.amount,
                entry_price   = pos.entry_price,
                current_price = current_price,
                pnl           = round(pnl, 4) if pnl is not None else None,
                pnl_pct       = round(pnl_pct, 4) if pnl_pct is not None else None,
                mode          = TradingModeEnum(pos.mode),
                opened_at     = pos.opened_at,
                closed_at     = pos.closed_at,
            ))

        return PositionsListResponse(
            total     = len(responses),
            open      = open_count,
            closed    = len(responses) - open_count,
            total_pnl = round(total_pnl, 4),
            positions = responses,
        )

    async def get_position_by_id(self, position_id: str) -> PositionResponse:
        """Devuelve una posición específica con PnL actualizado."""
        pos = await self._repo.get_position_by_id(position_id)
        if not pos:
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail="Position not found")

        result = await self.get_positions()
        for p in result.positions:
            if p.id == position_id:
                return p

    async def get_balance(self) -> float:
        """
        Devuelve el balance actual.
        En paper: viene del PaperTradingHandler (en memoria + Redis).
        En real: consultará la wallet (implementado en C12).
        """
        # Intenta desde Redis primero (más rápido)
        cached_balance = await self._redis.get_paper_balance()
        if cached_balance is not None:
            return cached_balance

        # Fallback: balance del handler en memoria
        return self._paper_handler.get_balance()

    async def get_summary(self) -> dict:
        """
        Resumen del portfolio para el endpoint /status y Telegram.
        Incluye balance, PnL total, posiciones abiertas y métricas clave.
        """
        balance     = await self.get_balance()
        positions   = await self.get_positions()
        total_pnl   = self._paper_handler.get_total_pnl()
        total_pnl_pct = self._paper_handler.get_total_pnl_pct()

        return {
            "balance_usdc":     round(balance, 2),
            "total_pnl_usdc":   round(total_pnl, 4),
            "total_pnl_pct":    f"{total_pnl_pct:.2%}",
            "open_positions":   positions.open,
            "closed_positions": positions.closed,
            "total_trades":     positions.total,
            "timestamp":        datetime.now(timezone.utc).isoformat(),
        }