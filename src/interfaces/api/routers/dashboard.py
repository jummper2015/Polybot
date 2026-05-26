# src/interfaces/api/routers/dashboard.py

from datetime import datetime, timezone

import structlog
from fastapi import APIRouter, Query, Request
from pydantic import BaseModel

logger = structlog.get_logger(__name__)
router = APIRouter()


# ── Schemas de respuesta del dashboard ───────────────────────────────

class EquityPoint(BaseModel):
    """Un punto en la curva de equity."""
    timestamp:       str
    cumulative_pnl:  float
    trade_pnl:       float
    balance:         float
    exit_reason:     str


class DashboardSummary(BaseModel):
    """Resumen del portfolio para el panel superior."""
    balance:              float
    initial_balance:      float
    total_pnl_usdc:       float
    total_pnl_pct:        float
    open_positions:       int
    closed_positions:     int
    total_trades:         int
    win_rate:             float
    profit_factor:        float
    max_drawdown_pct:     float
    active_markets:       int
    bot_running:          bool
    trading_mode:         str
    drawdown_pct:         float
    updated_at:           str


class OrderbookLevel(BaseModel):
    """Un nivel del order book (precio + tamaño)."""
    price: float
    size:  float


class MarketOverview(BaseModel):
    """Estado de un mercado activo con order book en tiempo real."""
    market_id:     str
    asset:         str
    window:        str
    yes_price:     float
    best_bid:      float
    best_ask:      float
    spread:        float
    volume_24h:    float
    ws_connected:  bool
    consecutive_ticks: int
    orderbook_bids: list[OrderbookLevel]
    orderbook_asks: list[OrderbookLevel]


class RecentTrade(BaseModel):
    """Trade reciente para la tabla de actividad."""
    id:           str
    asset:        str
    window:       str
    side:         str
    amount:       float
    entry_price:  float
    exit_price:   float | None
    pnl:          float | None
    pnl_pct:      float | None
    mode:         str
    exit_reason:  str | None
    opened_at:    str
    closed_at:    str | None
    is_open:      bool


# ── Endpoints ─────────────────────────────────────────────────────────

@router.get(
    "/dashboard/summary",
    response_model=DashboardSummary,
    summary="Resumen del portfolio para el dashboard",
)
async def get_dashboard_summary(request: Request) -> DashboardSummary:
    """
    Datos del panel superior del dashboard.
    Balance, PnL, win rate, drawdown y estado del bot.
    """
    container = request.app.state.container

    # Consulta posiciones desde DB
    positions = await container.repository.get_positions(open_only=False)
    closed    = [p for p in positions if p.closed_at is not None]
    open_pos  = [p for p in positions if p.closed_at is None]

    # Calcula métricas
    total_pnl    = sum(p.pnl or 0 for p in closed)
    pnls         = [p.pnl for p in closed if p.pnl is not None]
    winners      = [p for p in pnls if p > 0]
    losers_abs   = [abs(p) for p in pnls if p < 0]
    win_rate     = len(winners) / len(pnls) if pnls else 0.0
    gross_profit = sum(winners)
    gross_loss   = sum(losers_abs)
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else 0.0

    # Balance
    balance = await container.portfolio_service.get_balance()
    initial = container.config.paper_initial_balance
    pnl_pct = total_pnl / initial if initial > 0 else 0.0

    # Drawdown actual
    drawdown = (initial - balance) / initial if initial > 0 else 0.0

    # Max drawdown histórico
    max_dd   = _calculate_max_drawdown(closed, initial)

    # Mercados activos
    markets  = await container.market_service.get_active_markets()
    bot_status = await container.trading_service.get_status()

    return DashboardSummary(
        balance=round(balance, 2),
        initial_balance=initial,
        total_pnl_usdc=round(total_pnl, 4),
        total_pnl_pct=round(pnl_pct, 4),
        open_positions=len(open_pos),
        closed_positions=len(closed),
        total_trades=len(positions),
        win_rate=round(win_rate, 4),
        profit_factor=round(profit_factor, 4),
        max_drawdown_pct=round(max_dd, 4),
        active_markets=len(markets),
        bot_running=bot_status.get("running", False),
        trading_mode=container.config.trading_mode,
        drawdown_pct=round(max(drawdown, 0.0), 4),
        updated_at=datetime.now(timezone.utc).isoformat(),
    )


@router.get(
    "/dashboard/equity",
    response_model=list[EquityPoint],
    summary="Curva de equity (PnL acumulado por trade)",
)
async def get_equity_curve(
    request: Request,
    mode:    str | None = Query(None, description="paper o real"),
    limit:   int        = Query(200, ge=1, le=1000),
) -> list[EquityPoint]:
    """
    Devuelve la curva de equity: PnL acumulado punto a punto.
    Usado para el gráfico principal del dashboard.
    Solo incluye posiciones cerradas (PnL realizado).
    """
    container = request.app.state.container
    positions = await container.repository.get_positions(
        mode=mode, open_only=False
    )

    # Solo posiciones cerradas con PnL
    closed = sorted(
        [p for p in positions if p.closed_at and p.pnl is not None],
        key=lambda p: p.closed_at,
    )[-limit:]   # Últimas N posiciones

    initial_balance = container.config.paper_initial_balance
    cumulative_pnl  = 0.0
    equity_points   = []

    for pos in closed:
        cumulative_pnl += pos.pnl
        balance         = initial_balance + cumulative_pnl

        equity_points.append(EquityPoint(
            timestamp=pos.closed_at.isoformat(),
            cumulative_pnl=round(cumulative_pnl, 4),
            trade_pnl=round(pos.pnl, 4),
            balance=round(balance, 4),
            exit_reason=pos.exit_reason or "unknown",
        ))

    return equity_points


@router.get(
    "/dashboard/markets",
    response_model=list[MarketOverview],
    summary="Estado de mercados activos con precio en tiempo real",
)
async def get_markets_overview(request: Request) -> list[MarketOverview]:
    """
    Lista de mercados activos con precio actual desde Redis/WS.
    Incluye estado de la conexión WebSocket por mercado.
    """
    container = request.app.state.container
    markets   = await container.market_service.get_active_markets()
    overview  = []

    for market in markets:
        # Obtiene estado WS desde Redis
        ws_state = await container.redis.get_ws_state(market.id)
        ws_connected = (
            ws_state.get("status") == "connected" if ws_state else False
        )

        # Obtiene ticks consecutivos del state de la estrategia
        strategy_state = container.strategy_engine.get_state(
            strategy_name="BuyAboveThreshold",
            market_id=market.id,
        )
        consecutive_ticks = (
            strategy_state.consecutive_ticks if strategy_state else 0
        )

        # Obtiene orderbook desde Redis (lleno por el WS)
        orderbook = await container.redis.get_orderbook(market.id)
        bids_raw = orderbook.get("bids", []) if orderbook else []
        asks_raw = orderbook.get("asks", []) if orderbook else []

        orderbook_bids = [
            OrderbookLevel(price=round(float(b["price"]), 4), size=round(float(b["size"]), 2))
            for b in bids_raw
        ]
        orderbook_asks = [
            OrderbookLevel(price=round(float(a["price"]), 4), size=round(float(a["size"]), 2))
            for a in asks_raw
        ]

        # Extrae best_bid / best_ask del orderbook si está disponible
        best_bid = orderbook_bids[0].price if orderbook_bids else 0.0
        best_ask = orderbook_asks[0].price if orderbook_asks else 0.0

        overview.append(MarketOverview(
            market_id=market.id,
            asset=market.asset.value,
            window=market.window.value,
            yes_price=market.yes_price,
            best_bid=round(best_bid, 4),
            best_ask=round(best_ask, 4),
            spread=round(market.yes_price - market.no_price, 4),
            volume_24h=market.volume_24h,
            ws_connected=ws_connected,
            consecutive_ticks=consecutive_ticks,
            orderbook_bids=orderbook_bids,
            orderbook_asks=orderbook_asks,
        ))

    return overview


@router.get(
    "/dashboard/trades",
    response_model=list[RecentTrade],
    summary="Trades recientes para la tabla de actividad",
)
async def get_recent_trades(
    request:   Request,
    limit:     int         = Query(50, ge=1, le=200),
    mode:      str | None  = Query(None),
    open_only: bool        = Query(False),
) -> list[RecentTrade]:
    """
    Lista de trades recientes ordenados por fecha de apertura descendente.
    Incluye posiciones abiertas (sin PnL) y cerradas (con PnL).
    """
    container = request.app.state.container
    positions = await container.repository.get_positions(
        mode=mode, open_only=open_only
    )

    # Obtiene info de asset/window desde Redis para cada posición
    trades = []
    for pos in positions[:limit]:
        trades.append(RecentTrade(
            id          = pos.id,
            asset       = pos.asset,
            window      = pos.window,
            side        = pos.side,
            amount      = round(pos.amount, 2),
            entry_price = round(pos.entry_price, 4),
            exit_price  = round(pos.exit_price, 4) if pos.exit_price else None,
            pnl         = round(pos.pnl, 4)     if pos.pnl     else None,
            pnl_pct     = round(pos.pnl_pct, 4) if pos.pnl_pct else None,
            mode        = pos.mode,
            exit_reason = pos.exit_reason,
            opened_at   = pos.opened_at.isoformat(),
            closed_at   = pos.closed_at.isoformat() if pos.closed_at else None,
            is_open     = pos.closed_at is None,
        ))

    return trades


# ── Helper interno ────────────────────────────────────────────────────

def _calculate_max_drawdown(closed_positions, initial_balance: float) -> float:
    """Calcula max drawdown desde la lista de posiciones cerradas."""
    if not closed_positions:
        return 0.0

    balance      = initial_balance
    peak_balance = initial_balance
    max_dd       = 0.0

    for pos in sorted(closed_positions, key=lambda p: p.closed_at or datetime.min):
        balance     += (pos.pnl or 0)
        peak_balance = max(peak_balance, balance)
        if peak_balance > 0:
            dd     = (peak_balance - balance) / peak_balance
            max_dd = max(max_dd, dd)

    return max_dd
