# src/interfaces/api/routers/orders.py

from fastapi import APIRouter, Request, Query
from src.interfaces.api.schemas.order_schema import OrderResponse, OrdersListResponse

router = APIRouter()


@router.get("/orders", response_model=OrdersListResponse, summary="Lista órdenes")
async def get_orders(
    request: Request,
    status: str | None = Query(None, description="pending, filled, cancelled, failed"),
    limit:  int        = Query(50, ge=1, le=200, description="Máximo de órdenes a devolver"),
) -> OrdersListResponse:
    """
    Devuelve el historial de órdenes del bot.
    Filtrable por status y con límite configurable.
    """
    trading_service = request.app.state.container.trading_service
    return await trading_service.get_orders(status=status, limit=limit)


@router.get("/orders/{order_id}", response_model=OrderResponse, summary="Detalle orden")
async def get_order(request: Request, order_id: str) -> OrderResponse:
    """Devuelve el detalle de una orden específica."""
    trading_service = request.app.state.container.trading_service
    return await trading_service.get_order_by_id(order_id)