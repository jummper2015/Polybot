# src/interfaces/api/routers/markets.py

from fastapi import APIRouter, Query, Request

from src.interfaces.api.schemas.market_schema import MarketResponse, MarketsListResponse

router = APIRouter()


@router.get("/markets", response_model=MarketsListResponse, summary="Lista mercados activos")
async def get_markets(
    request: Request,
    asset:  str | None = Query(None, description="Filtrar por BTC o ETH"),
    window: str | None = Query(None, description="Filtrar por 5m o 15m"),
) -> MarketsListResponse:
    """
    Devuelve los mercados BTC/ETH activos descubiertos en Polymarket.
    Permite filtrar por asset y/o ventana temporal.
    """
    market_service = request.app.state.container.market_service

    # Delega la lógica al service de aplicación
    markets = await market_service.get_active_markets(asset=asset, window=window)

    return MarketsListResponse(
        total=len(markets),
        markets=markets,
    )


@router.get("/markets/{market_id}", response_model=MarketResponse, summary="Detalle de un mercado")
async def get_market(request: Request, market_id: str) -> MarketResponse:
    """Devuelve el detalle y precio actual de un mercado específico."""
    market_service = request.app.state.container.market_service
    return await market_service.get_market_by_id(market_id)
