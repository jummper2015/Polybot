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


class RegimeInfo(BaseModel):
    """Estado de régimen para un mercado activo (P11.1)."""
    asset:         str
    window:        str
    regime:        str
    confidence:    float
    strategies_active:   list[str]
    strategies_inactive: list[str]
    orchestrator_enabled: bool


class ExitReasonStats(BaseModel):
    """Atribución de PnL por motivo de salida."""
    reason:        str
    count:         int
    pct_of_trades: float
    total_pnl:     float
    avg_pnl:       float
    win_rate:      float


class RegimeStatsRow(BaseModel):
    """Atribución de PnL por régimen detectado."""
    regime:    str
    count:     int
    total_pnl: float
    win_rate:  float


class QuantMetrics(BaseModel):
    """Métricas cuantitativas derivadas de posiciones cerradas (PostTradeAnalyzer)."""
    total_trades:           int
    expectancy_usdc:        float
    expectancy_pct:         float
    profit_factor:          float
    sharpe_estimate:        float
    max_consecutive_wins:   int
    max_consecutive_losses: int
    best_trade:             float
    worst_trade:            float
    avg_winner:             float
    avg_loser:              float
    avg_duration_ticks:     float
    best_exit_reason:       str
    worst_exit_reason:      str
    best_regime:            str
    worst_regime:           str
    by_exit_reason:         list[ExitReasonStats]
    by_regime:              list[RegimeStatsRow]
    updated_at:             str


class RiskRuleStat(BaseModel):
    """Recuento de bloqueos por regla del RiskEngine."""
    rule:        str
    deny_count:  int
    priority:    int


class RiskActivity(BaseModel):
    """Snapshot de actividad del RiskEngine.

    Counters acumulados desde el arranque del proceso. drawdown/exposure
    son lecturas actuales del gauge.
    """
    rules:           list[RiskRuleStat]
    allow_count:     int
    deny_count:      int
    drawdown_pct:    float
    exposure_pct:    float
    mode:            str
    updated_at:      str


class EventTypeCount(BaseModel):
    asset:      str
    event_type: str
    severity:   str
    count:      int


class EventActionCount(BaseModel):
    asset:  str
    action: str
    count:  int


class EventActivity(BaseModel):
    """Snapshot de actividad del EventDetector (P11.4).

    Counters acumulados desde arranque. active_halts es el conjunto de
    (asset, market_id) con EVENT_ACTIVE=1 en este instante.
    """
    by_type:         list[EventTypeCount]
    by_action:       list[EventActionCount]
    halt_entries:    int
    active_halts:    list[dict]
    updated_at:      str


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

    Deduplica por (asset, window) — solo 1 mercado por combinación
    (BTC 5m, BTC 15m, ETH 5m, ETH 15m = hasta 4 mercados).
    """
    container = request.app.state.container
    markets   = await container.market_service.get_active_markets()
    overview  = []

    # Deduplicate by (asset, window) — keep highest-volume market per pair
    seen_pairs: set[tuple[str, str]] = set()
    markets_sorted = sorted(markets, key=lambda m: m.volume_24h, reverse=True)
    deduped: list = []
    for market in markets_sorted:
        key = (market.asset.value, market.window.value)
        if key in seen_pairs:
            continue
        seen_pairs.add(key)
        deduped.append(market)

    for market in deduped:
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

        # Extrae best_bid / best_ask del orderbook (max bid, min ask)
        # Polymarket /book API returns bids ASC and asks DESC;
        # use max/min to get the true best levels regardless of sort order.
        best_bid = max((b.price for b in orderbook_bids), default=0.0)
        best_ask = min((a.price for a in orderbook_asks), default=0.0)

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


@router.get(
    "/dashboard/regimes",
    response_model=list[RegimeInfo],
    summary="Estado de régimen por mercado (P11.1)",
)
async def get_regimes_overview(request: Request) -> list[RegimeInfo]:
    """
    Devuelve el régimen actual detectado para cada mercado activo.
    Incluye qué estrategias están activas/inactivas en ese régimen.
    """
    container = request.app.state.container

    # Accede al orchestrator desde el container
    orchestrator = container.strategy_orchestrator
    if orchestrator is None:
        return []

    markets = await container.market_service.get_active_markets()
    regime_list: list[RegimeInfo] = []

    # Deduplicate by (asset, window) — multiple Polymarket token IDs
    # can point to the same market (e.g., BTC-5m-USDC vs BTC-5m-USDC-e)
    seen: set[tuple[str, str]] = set()

    for market in markets:
        key = (market.asset.value, market.window.value)
        if key in seen:
            continue
        seen.add(key)

        # Use the public get_regime_status method (encapsulates all internals)
        status = orchestrator.get_regime_status(market.id)

        regime_list.append(RegimeInfo(
            asset=market.asset.value,
            window=market.window.value,
            regime=status["regime"].value if status else "unknown",
            confidence=round(status["confidence"], 4) if status else 0.0,
            strategies_active=status["strategies_active"] if status else [],
            strategies_inactive=status["strategies_inactive"] if status else [],
            orchestrator_enabled=orchestrator.enabled,
        ))

    return regime_list


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


# ── Helpers internos para Prometheus counters ─────────────────────────

def _counter_samples(counter) -> list[tuple[dict, float]]:
    """Recolecta las muestras de un Counter Prometheus.

    Devuelve lista de (labels_dict, value) para las series con sufijo _total
    (los counters generan _total y _created; queremos solo _total).
    """
    samples = []
    try:
        for family in counter.collect():
            for sample in family.samples:
                if sample.name.endswith("_total"):
                    samples.append((dict(sample.labels), float(sample.value)))
    except Exception as exc:
        logger.warning("counter_collect_failed", error=str(exc))
    return samples


def _gauge_value(gauge, labels: dict | None = None) -> float:
    """Lee el valor actual de un Gauge para un conjunto de labels específico.
    Si no se encuentra, devuelve 0.0.
    """
    try:
        for family in gauge.collect():
            for sample in family.samples:
                if labels is None or all(
                    sample.labels.get(k) == v for k, v in labels.items()
                ):
                    return float(sample.value)
    except Exception:
        pass
    return 0.0


# ── Quant Metrics (R1.3-dashboard) ────────────────────────────────────

@router.get(
    "/dashboard/quant-metrics",
    response_model=QuantMetrics,
    summary="Métricas cuantitativas derivadas (Sharpe, expectancy, exit_reason, regime)",
)
async def get_quant_metrics(request: Request) -> QuantMetrics:
    """
    Resume las posiciones cerradas en métricas cuantitativas usando
    PostTradeAnalyzer. Si no hay trades cerrados devuelve un report vacío
    (no rompe el dashboard).
    """
    container = request.app.state.container

    # Lazy import — PostTradeAnalyzer no es necesario en otros endpoints.
    from src.quantitative.post_trade import PostTradeAnalyzer

    positions = await container.repository.get_positions(open_only=False)
    closed = [p for p in positions if p.closed_at is not None and p.pnl is not None]
    initial = container.config.paper_initial_balance

    if not closed:
        return QuantMetrics(
            total_trades=0,
            expectancy_usdc=0.0,
            expectancy_pct=0.0,
            profit_factor=0.0,
            sharpe_estimate=0.0,
            max_consecutive_wins=0,
            max_consecutive_losses=0,
            best_trade=0.0,
            worst_trade=0.0,
            avg_winner=0.0,
            avg_loser=0.0,
            avg_duration_ticks=0.0,
            best_exit_reason="none",
            worst_exit_reason="none",
            best_regime="none",
            worst_regime="none",
            by_exit_reason=[],
            by_regime=[],
            updated_at=datetime.now(timezone.utc).isoformat(),
        )

    analyzer = PostTradeAnalyzer()
    try:
        report = analyzer.analyze(closed, initial_balance=initial)
    except ValueError:
        # Defensa: si PostTradeAnalyzer rechaza la entrada, devuelve estado vacío.
        report = None

    if report is None:
        return QuantMetrics(
            total_trades=len(closed),
            expectancy_usdc=0.0,
            expectancy_pct=0.0,
            profit_factor=0.0,
            sharpe_estimate=0.0,
            max_consecutive_wins=0,
            max_consecutive_losses=0,
            best_trade=0.0,
            worst_trade=0.0,
            avg_winner=0.0,
            avg_loser=0.0,
            avg_duration_ticks=0.0,
            best_exit_reason="none",
            worst_exit_reason="none",
            best_regime="none",
            worst_regime="none",
            by_exit_reason=[],
            by_regime=[],
            updated_at=datetime.now(timezone.utc).isoformat(),
        )

    # PF inf → 999.0 cap para serialización JSON.
    pf = report.profit_factor
    if pf == float("inf"):
        pf = 999.0

    by_exit = [
        ExitReasonStats(
            reason=e.reason,
            count=e.count,
            pct_of_trades=round(
                e.count / max(1, report.total_trades) * 100, 1
            ),
            total_pnl=round(e.total_pnl, 4),
            avg_pnl=round(e.avg_pnl, 4),
            win_rate=round(e.win_rate, 4),
        )
        for e in report.by_exit_reason
    ]
    by_regime = [
        RegimeStatsRow(
            regime=r.regime,
            count=r.count,
            total_pnl=round(r.total_pnl, 4),
            win_rate=round(r.win_rate, 4),
        )
        for r in report.by_regime
    ]

    return QuantMetrics(
        total_trades=report.total_trades,
        expectancy_usdc=round(report.expectancy, 4),
        expectancy_pct=round(report.expectancy_pct, 4),
        profit_factor=round(pf, 4),
        sharpe_estimate=round(report.sharpe_estimate, 4),
        max_consecutive_wins=report.max_consecutive_wins,
        max_consecutive_losses=report.max_consecutive_losses,
        best_trade=round(report.best_trade, 4),
        worst_trade=round(report.worst_trade, 4),
        avg_winner=round(report.avg_winner, 4),
        avg_loser=round(report.avg_loser, 4),
        avg_duration_ticks=round(report.avg_duration_ticks, 2),
        best_exit_reason=report.best_exit_reason,
        worst_exit_reason=report.worst_exit_reason,
        best_regime=report.best_regime,
        worst_regime=report.worst_regime,
        by_exit_reason=by_exit,
        by_regime=by_regime,
        updated_at=datetime.now(timezone.utc).isoformat(),
    )


# ── Risk Engine activity (R1.3-dashboard) ─────────────────────────────

@router.get(
    "/dashboard/risk-activity",
    response_model=RiskActivity,
    summary="Snapshot del RiskEngine: reglas, denies por regla, drawdown, exposure",
)
async def get_risk_activity(request: Request) -> RiskActivity:
    """
    Lee:
    - rules summary del RiskEngine (orden + prioridad).
    - RISK_DECISIONS counter para totales allow/deny.
    - RISK_RULE_TRIGGERED counter para conteo de denies por regla.
    - RISK_DRAWDOWN_GAUGE / RISK_EXPOSURE_GAUGE para valores actuales.

    Counters acumulados desde el arranque del proceso.
    """
    container = request.app.state.container
    mode = container.config.trading_mode

    # Lazy import — el módulo metrics pesa.
    from src.infrastructure.observability.metrics import (
        RISK_DECISIONS,
        RISK_DRAWDOWN_GAUGE,
        RISK_EXPOSURE_GAUGE,
        RISK_RULE_TRIGGERED,
    )

    # Catálogo de reglas (lista + prioridad)
    try:
        rules_summary = container.risk_engine.get_rules_summary()
    except Exception:
        rules_summary = []

    # Conteo de denies por regla
    deny_by_rule: dict[str, float] = {}
    for labels, value in _counter_samples(RISK_RULE_TRIGGERED):
        rule = labels.get("rule", "unknown")
        deny_by_rule[rule] = deny_by_rule.get(rule, 0.0) + value

    # Totales allow/deny
    allow_total = 0.0
    deny_total = 0.0
    for labels, value in _counter_samples(RISK_DECISIONS):
        if labels.get("mode") and labels["mode"] != mode:
            continue
        result = (labels.get("result") or "").lower()
        if result == "allow":
            allow_total += value
        elif result == "deny":
            deny_total += value

    # Une rules_summary con deny_count
    rules: list[RiskRuleStat] = []
    for entry in rules_summary:
        name = entry.get("rule") or entry.get("name") or "unknown"
        priority = int(entry.get("priority", 0))
        rules.append(RiskRuleStat(
            rule=name,
            deny_count=int(deny_by_rule.get(name, 0.0)),
            priority=priority,
        ))
    # Reglas que dispararon DENY pero no están en summary (defensa)
    seen = {r.rule for r in rules}
    for name, count in deny_by_rule.items():
        if name not in seen:
            rules.append(RiskRuleStat(
                rule=name, deny_count=int(count), priority=999,
            ))

    return RiskActivity(
        rules=sorted(rules, key=lambda r: r.priority),
        allow_count=int(allow_total),
        deny_count=int(deny_total),
        drawdown_pct=round(_gauge_value(RISK_DRAWDOWN_GAUGE, {"mode": mode}), 4),
        exposure_pct=round(_gauge_value(RISK_EXPOSURE_GAUGE, {"mode": mode}), 4),
        mode=mode,
        updated_at=datetime.now(timezone.utc).isoformat(),
    )


# ── Event Detector activity (R1.3-dashboard, P11.4) ──────────────────

@router.get(
    "/dashboard/events",
    response_model=EventActivity,
    summary="Eventos detectados (P11.4): por tipo, por acción, HALTs activos",
)
async def get_event_activity(request: Request) -> EventActivity:
    """
    Snapshot agregado del EventDetector:
    - EVENT_DETECTED: por (asset, event_type, severity).
    - EVENT_RESPONSE: por (asset, action).
    - EVENT_HALT_ENTRIES: total de entradas bloqueadas.
    - EVENT_ACTIVE: lista de markets con un evento bloqueante activo.
    """
    # Lazy import.
    from src.infrastructure.observability.metrics import (
        EVENT_ACTIVE,
        EVENT_DETECTED,
        EVENT_HALT_ENTRIES,
        EVENT_RESPONSE,
    )

    by_type = [
        EventTypeCount(
            asset=labels.get("asset", "?"),
            event_type=labels.get("event_type", "?"),
            severity=labels.get("severity", "?"),
            count=int(value),
        )
        for labels, value in _counter_samples(EVENT_DETECTED)
        if value > 0
    ]
    by_type.sort(key=lambda x: -x.count)

    by_action = [
        EventActionCount(
            asset=labels.get("asset", "?"),
            action=labels.get("action", "?"),
            count=int(value),
        )
        for labels, value in _counter_samples(EVENT_RESPONSE)
        if value > 0
    ]
    by_action.sort(key=lambda x: -x.count)

    halt_entries_total = sum(
        int(value)
        for _labels, value in _counter_samples(EVENT_HALT_ENTRIES)
    )

    active_halts: list[dict] = []
    try:
        for family in EVENT_ACTIVE.collect():
            for sample in family.samples:
                if float(sample.value) >= 1.0:
                    active_halts.append({
                        "asset": sample.labels.get("asset", "?"),
                        "market_id": sample.labels.get("market_id", "?"),
                    })
    except Exception:
        pass

    return EventActivity(
        by_type=by_type,
        by_action=by_action,
        halt_entries=halt_entries_total,
        active_halts=active_halts,
        updated_at=datetime.now(timezone.utc).isoformat(),
    )
