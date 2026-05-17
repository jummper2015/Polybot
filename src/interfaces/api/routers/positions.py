# src/interfaces/api/routers/positions.py

from fastapi import APIRouter, Request, Query
from src.interfaces.api.schemas.position_schema import PositionResponse, PositionsListResponse

router = APIRouter()


@router.get("/positions", response_model=PositionsListResponse, summary="Lista posiciones")
async def get_positions(
    request: Request,
    mode:   str | None = Query(None, description="paper o real"),
    open_only: bool    = Query(False, description="Solo posiciones abiertas"),
) -> PositionsListResponse:
    """
    Devuelve todas las posiciones (abiertas y cerradas) con su PnL.
    Filtrable por modo (paper/real) y estado (open/closed).
    """
    portfolio_service = request.app.state.container.portfolio_service
    return await portfolio_service.get_positions(mode=mode, open_only=open_only)


@router.get("/positions/{position_id}", response_model=PositionResponse, summary="Detalle posición")
async def get_position(request: Request, position_id: str) -> PositionResponse:
    """Devuelve el detalle completo de una posición incluyendo PnL actual."""
    portfolio_service = request.app.state.container.portfolio_service
    return await portfolio_service.get_position_by_id(position_id)