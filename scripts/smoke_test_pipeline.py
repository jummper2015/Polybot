# scripts/smoke_test_pipeline.py
"""
Smoke test E2E del pipeline PolyBot (R2.1-smoke).

Ejercita la cadena completa **discovery → strategy → risk → paper execution**
contra los markets cripto que Polymarket SÍ tiene activos hoy, esquivando el
filtro M5/M15 de producción (bloqueado por B5).

Lo que valida:
  - Objetivo #1 (lectura): el bot lee `gamma-api.polymarket.com/markets`
    (público) + CLOB REST `/book` (público) → demuestra conectividad sin
    credenciales.
  - Objetivo #2 (paper): inyecta esos markets en repo + Redis, llena el
    buffer de ticks, y corre N ciclos completos del `TradingService`. La
    estrategia/risk/paper handler operan sobre datos reales.

Lo que NO valida (queda bloqueado por B5):
  - Objetivo #3 (rotación de eventos M5/M15 + reclamo de ganancias).
  - Real trading. Cero efectos en cadena.

Sin tocar `MarketService.discover_markets()` ni los filtros M5/M15 — éstos
siguen siendo correctos para cuando B5 se resuelva.

Salida:
  data/reports/smoke_test_pipeline_<ts>.json
  data/reports/smoke_test_pipeline_latest.json (symlink)
  stdout: resumen por market + summary global.

Exit codes:
  0 — pipeline ejercitado sin excepciones.
  1 — Gamma devolvió 0 markets cripto (B5 más severo, fuera del bot).
  2 — al menos un ciclo lanzó excepción no controlada.

Uso:
  python scripts/smoke_test_pipeline.py
  python scripts/smoke_test_pipeline.py --n-cycles 3 --warmup-ticks 10
  python scripts/smoke_test_pipeline.py --force-fake-signal
  python scripts/smoke_test_pipeline.py --output data/reports/custom.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import traceback
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

# Project root on sys.path para importar src.* y scripts.*
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import structlog

from src.core.config import load_config
from src.core.container import Container
from src.domain.entities.market import Market
from src.domain.enums.asset import Asset
from src.domain.enums.market_status import MarketStatus
from src.domain.enums.signal_type import SignalType
from src.domain.enums.window import Window
from src.domain.value_objects.signal import Signal
from src.infrastructure.observability.logging import configure_logging
from src.infrastructure.polymarket.market_filters import detect_asset

logger = structlog.get_logger(__name__)


# ── Exit codes ───────────────────────────────────────────────────────────────
EXIT_OK = 0
EXIT_NO_MARKETS = 1
EXIT_PIPELINE_ERROR = 2

# ── Defaults ─────────────────────────────────────────────────────────────────
GAMMA_MARKETS_URL = "https://gamma-api.polymarket.com/markets"
DEFAULT_N_CYCLES = 5
DEFAULT_WARMUP_TICKS = 25
DEFAULT_CYCLE_INTERVAL_S = 10.0
DEFAULT_WARMUP_INTERVAL_S = 1.0
DEFAULT_OUTPUT_DIR = Path("data/reports")
DEFAULT_LIMIT_PER_ASSET = 2

# Validation labels
OBJ1_KEY = "objective_1_connectivity_read"
OBJ2_KEY = "objective_2_paper_execution"
OBJ3_KEY = "objective_3_m5_m15_rotation"
OBJ3_BLOCKED = "BLOCKED_BY_B5"


# ══════════════════════════════════════════════════════════════════════════════
# 1. DISCOVERY ALTERNATIVO (Gamma directo)
# ══════════════════════════════════════════════════════════════════════════════


async def fetch_active_crypto_markets(
    limit_per_asset: int = DEFAULT_LIMIT_PER_ASSET,
    client: httpx.AsyncClient | None = None,
) -> list[dict]:
    """Trae markets activos de Gamma y selecciona top-N por volumen para BTC y ETH.

    No exige M5/M15. Usa la helper canónica ``detect_asset`` de
    ``src.infrastructure.polymarket.market_filters`` para clasificar.

    Si ``client`` es ``None``, crea uno con timeout=10s. En tests, inyectar
    un client mockeado.
    """
    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=10.0)
    try:
        response = await client.get(
            GAMMA_MARKETS_URL,
            params={"active": "true", "closed": "false", "limit": 200},
        )
        response.raise_for_status()
        raw_markets = response.json()
    finally:
        if owns_client:
            await client.aclose()

    if not isinstance(raw_markets, list):
        return []

    classified: dict[str, list[dict]] = {"BTC": [], "ETH": []}
    for raw in raw_markets:
        asset = detect_asset(raw)
        if asset in classified:
            classified[asset].append(raw)

    selected: list[dict] = []
    for asset_name, markets in classified.items():
        markets.sort(
            key=lambda m: float(m.get("volume24hr") or 0),
            reverse=True,
        )
        selected.extend(markets[:limit_per_asset])

    return selected


# ══════════════════════════════════════════════════════════════════════════════
# 2. PARSEO Gamma dict → Market entity
# ══════════════════════════════════════════════════════════════════════════════


def _parse_json_list(value: Any) -> list[Any]:
    """Parsea Gamma fields que vienen como JSON-string (`outcomePrices`,
    `clobTokenIds`, `outcomes`). Devuelve [] ante None / parse-error."""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except (json.JSONDecodeError, ValueError):
            return []
    return []


def _parse_end_date(raw: dict) -> datetime:
    """Devuelve `endDate` como datetime naive UTC; fallback = now + 1d.

    Gamma usa `endDate` ISO 8601 con `Z`. El placeholder de un día evita que
    `Market.is_active()` lo rechace inmediatamente.
    """
    end_str = raw.get("endDate") or raw.get("endDateIso") or ""
    if not end_str:
        return (datetime.utcnow() + timedelta(days=1)).replace(microsecond=0)
    try:
        dt = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt
    except (ValueError, TypeError):
        return (datetime.utcnow() + timedelta(days=1)).replace(microsecond=0)


def build_market_from_gamma(raw: dict) -> Market:
    """Construye una entidad ``Market`` a partir del dict crudo de Gamma.

    Notas:
    - `window` se fija a ``Window.M15`` como placeholder. El `_run_market_cycle`
      del TradingService NO filtra por window; sólo se usa para metadata
      y logging. Los markets longevos del smoke test no corresponden a
      M5/M15 reales — esto se documenta en el reporte.
    - `condition_id` viene como `conditionId` en Gamma.
    - `clobTokenIds`, `outcomePrices`, `outcomes` son JSON-strings.
    """
    asset_name = detect_asset(raw)
    if asset_name not in ("BTC", "ETH"):
        raise ValueError(
            f"market sin asset BTC/ETH identificable: {raw.get('slug', '?')}"
        )
    asset = Asset(asset_name)

    token_ids = _parse_json_list(raw.get("clobTokenIds"))
    prices = _parse_json_list(raw.get("outcomePrices"))

    # Yes = primer outcome positivo (Yes/Up); No = el otro.
    yes_token_id = str(token_ids[0]) if len(token_ids) > 0 else ""
    no_token_id = str(token_ids[1]) if len(token_ids) > 1 else ""

    try:
        yes_price = float(prices[0]) if len(prices) > 0 else 0.5
    except (TypeError, ValueError):
        yes_price = 0.5
    try:
        no_price = float(prices[1]) if len(prices) > 1 else 1.0 - yes_price
    except (TypeError, ValueError):
        no_price = 1.0 - yes_price

    tick_size_raw = raw.get("orderPriceMinTickSize")
    if tick_size_raw is not None:
        tick_size = str(tick_size_raw)
    else:
        tick_size = "0.01"

    try:
        min_order_size = float(raw.get("orderMinSize") or 1.0)
    except (TypeError, ValueError):
        min_order_size = 1.0

    return Market(
        id=str(raw.get("conditionId") or raw.get("condition_id") or ""),
        asset=asset,
        window=Window.M15,  # placeholder (ver docstring)
        question=str(raw.get("question") or ""),
        status=MarketStatus.ACTIVE,
        yes_token_id=yes_token_id,
        no_token_id=no_token_id,
        yes_price=yes_price,
        no_price=no_price,
        volume_24h=float(raw.get("volume24hr") or 0.0),
        expiry=_parse_end_date(raw),
        neg_risk=bool(raw.get("negRisk", False)),
        tick_size=tick_size,
        min_order_size=min_order_size,
    )


# ══════════════════════════════════════════════════════════════════════════════
# 3. INYECCIÓN en repo + Redis
# ══════════════════════════════════════════════════════════════════════════════


async def inject_markets(container: Container, markets: list[Market]) -> None:
    """Persiste cada market en DB y Redis para que ``MarketService`` y el
    ``TradingService`` lo encuentren sin pasar por ``discover_markets``."""
    for market in markets:
        await container.repository.save_market(market)
        await container.redis.set_market(market, ttl_seconds=3900)


# ══════════════════════════════════════════════════════════════════════════════
# 4. WARMUP de ticks (rellena buffer de MR)
# ══════════════════════════════════════════════════════════════════════════════


@dataclass
class WarmupResult:
    market_id: str
    ticks_fetched: int
    ticks_unavailable: int
    price_min: float | None
    price_max: float | None


async def warmup_market_ticks(
    container: Container,
    market: Market,
    n_ticks: int,
    interval_s: float,
) -> WarmupResult:
    """Polea `get_market_tick` N veces para llenar el buffer del orchestrator.

    Cada tick válido se inyecta vía ``strategy_orchestrator.on_tick`` para que
    MR acumule historia y pueda calcular z-score en los ciclos siguientes.
    """
    log = logger.bind(action="warmup", market_id=market.id[:20])
    fetched = 0
    unavailable = 0
    prices: list[float] = []

    for i in range(n_ticks):
        try:
            tick = await container.market_service.get_market_tick(market.id)
        except Exception as e:
            log.warning("warmup_tick_error", iter=i, error=str(e)[:120])
            tick = None

        if tick is None:
            unavailable += 1
        else:
            fetched += 1
            prices.append(float(tick.yes_price))
            try:
                await container.strategy_orchestrator.on_tick(market, tick)
            except Exception as e:
                log.warning("warmup_on_tick_error", iter=i, error=str(e)[:120])

        if i < n_ticks - 1:
            await asyncio.sleep(interval_s)

    return WarmupResult(
        market_id=market.id,
        ticks_fetched=fetched,
        ticks_unavailable=unavailable,
        price_min=min(prices) if prices else None,
        price_max=max(prices) if prices else None,
    )


# ══════════════════════════════════════════════════════════════════════════════
# 5. CICLOS por market (TradingService._run_market_cycle)
# ══════════════════════════════════════════════════════════════════════════════


@dataclass
class CycleRecord:
    cycle: int
    market_id: str
    duration_ms: float
    error: str | None
    positions_before: int
    positions_after: int
    balance_before: float
    balance_after: float


async def run_single_cycle(
    container: Container,
    market: Market,
    cycle_num: int,
) -> CycleRecord:
    """Ejecuta un único ciclo del TradingService, captura excepciones y devuelve
    métricas observables (sin tocar el código del TradingService).
    """
    log = logger.bind(action="cycle", market_id=market.id[:20], cycle=cycle_num)

    # Snapshot pre-ciclo
    try:
        positions_before = len(await container.repository.get_positions(open_only=True))
    except Exception:
        positions_before = -1
    balance_before = _get_paper_balance(container)

    start = time.monotonic()
    err: str | None = None
    try:
        await container.trading_service._run_market_cycle(market)
    except Exception as e:
        err = f"{type(e).__name__}: {str(e)[:200]}"
        log.error("cycle_exception", error=err)
    duration_ms = (time.monotonic() - start) * 1000.0

    # Snapshot post-ciclo
    try:
        positions_after = len(await container.repository.get_positions(open_only=True))
    except Exception:
        positions_after = -1
    balance_after = _get_paper_balance(container)

    return CycleRecord(
        cycle=cycle_num,
        market_id=market.id,
        duration_ms=round(duration_ms, 2),
        error=err,
        positions_before=positions_before,
        positions_after=positions_after,
        balance_before=round(balance_before, 4),
        balance_after=round(balance_after, 4),
    )


def _get_paper_balance(container: Container) -> float:
    handler = container.execution_handler
    if hasattr(handler, "get_balance"):
        try:
            return float(handler.get_balance())
        except Exception:
            return 0.0
    return 0.0


# ══════════════════════════════════════════════════════════════════════════════
# 6. --force-fake-signal: inyecta Signal directo al execution handler
# ══════════════════════════════════════════════════════════════════════════════


async def force_fake_signal(
    container: Container,
    market: Market,
    amount: float = 5.0,
) -> dict:
    """Inyecta un Signal BUY_YES sintético directo en ``execution_handler.execute_entry``.

    Salta strategy + risk para validar que el paper handler ejecuta el flujo
    completo (slippage, fill, persistencia, balance).
    Devuelve un dict con el resultado.
    """
    signal = Signal(
        type=SignalType.BUY_YES,
        market_id=market.id,
        confidence=0.9,
        source_strategy="smoke_test_force",
        reason="forced fake signal for E2E paper exec test",
        timestamp=datetime.utcnow(),
    )
    handler = container.execution_handler
    try:
        result = await handler.execute_entry(
            signal=signal,
            market_id=market.id,
            amount=amount,
        )
        return {
            "market_id": market.id,
            "success": bool(getattr(result, "success", False)),
            "fill_price": getattr(result, "fill_price", None),
            "slippage": getattr(result, "slippage", None),
            "error": getattr(result, "error", None),
        }
    except Exception as e:
        return {
            "market_id": market.id,
            "success": False,
            "fill_price": None,
            "slippage": None,
            "error": f"{type(e).__name__}: {str(e)[:200]}",
        }


# ══════════════════════════════════════════════════════════════════════════════
# 7. REPORTE
# ══════════════════════════════════════════════════════════════════════════════


def build_report(
    markets: list[Market],
    warmup_results: list[WarmupResult],
    cycle_records: list[CycleRecord],
    forced_results: list[dict],
    elapsed_s: float,
    config: dict,
) -> dict:
    """Construye el JSON final con validaciones por objetivo."""

    # Discovery falló → B5 más severo
    if not markets:
        return {
            "status": "fail_no_markets",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "elapsed_seconds": round(elapsed_s, 2),
            "config": config,
            "b5_context": (
                "Polymarket no devuelve ningún market cripto activo — "
                "ni siquiera los longevos. B5 más severo de lo conocido."
            ),
            "markets_used": [],
            "validations": {
                OBJ1_KEY: "FAIL_NO_MARKETS",
                OBJ2_KEY: "NOT_RUN",
                OBJ3_KEY: OBJ3_BLOCKED,
            },
            "summary": {
                "total_cycles_run": 0,
                "total_errors": 0,
                "total_orders": 0,
            },
        }

    total_errors = sum(1 for r in cycle_records if r.error)
    forced_success = sum(1 for r in forced_results if r.get("success"))
    orders_inferred = sum(
        1
        for r in cycle_records
        if r.positions_after > r.positions_before >= 0
    ) + forced_success

    # Objetivo 1: lectura. Pasa si tenemos markets y al menos 1 warmup tick recibido.
    any_tick = any(w.ticks_fetched > 0 for w in warmup_results)
    obj1 = "PASS" if any_tick else "FAIL_NO_TICKS"

    # Objetivo 2: paper exec. PASS si pipeline corrió sin excepciones.
    if total_errors > 0:
        obj2 = "FAIL_PIPELINE_ERROR"
    elif orders_inferred > 0:
        obj2 = "PASS_WITH_ORDER"
    else:
        obj2 = "PASS_NO_SIGNAL"

    obj3 = OBJ3_BLOCKED

    return {
        "status": "success" if total_errors == 0 else "partial",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "elapsed_seconds": round(elapsed_s, 2),
        "config": config,
        "b5_context": (
            "Polymarket tiene 0 markets BTC/ETH M5/M15 abiertos hoy; "
            "el smoke test usa markets cripto longevos como fallback "
            "para ejercitar el pipeline E2E."
        ),
        "markets_used": [
            {
                "id": m.id,
                "asset": m.asset.value,
                "window_placeholder": m.window.value,
                "question": m.question,
                "yes_price_at_inject": m.yes_price,
                "no_price_at_inject": m.no_price,
                "volume_24h": m.volume_24h,
                "expiry": m.expiry.isoformat(),
                "tick_size": m.tick_size,
                "min_order_size": m.min_order_size,
            }
            for m in markets
        ],
        "warmup": [
            {
                "market_id": w.market_id,
                "ticks_fetched": w.ticks_fetched,
                "ticks_unavailable": w.ticks_unavailable,
                "price_min": w.price_min,
                "price_max": w.price_max,
            }
            for w in warmup_results
        ],
        "cycles": [
            {
                "cycle": r.cycle,
                "market_id": r.market_id,
                "duration_ms": r.duration_ms,
                "error": r.error,
                "positions_before": r.positions_before,
                "positions_after": r.positions_after,
                "balance_before": r.balance_before,
                "balance_after": r.balance_after,
            }
            for r in cycle_records
        ],
        "forced_signals": forced_results,
        "summary": {
            "markets_count": len(markets),
            "total_cycles_run": len(cycle_records),
            "total_errors": total_errors,
            "total_orders": orders_inferred,
            "forced_signals_executed": forced_success,
        },
        "validations": {
            OBJ1_KEY: obj1,
            OBJ2_KEY: obj2,
            OBJ3_KEY: obj3,
        },
    }


def write_report(report: dict, output_path: Path) -> Path:
    """Escribe el JSON y mantiene un `latest` symlink/copy."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(report, f, indent=2, default=str)
    latest = output_path.parent / "smoke_test_pipeline_latest.json"
    try:
        if latest.exists() or latest.is_symlink():
            latest.unlink()
        latest.symlink_to(output_path.name)
    except OSError:
        # Filesystem sin symlinks → copia
        with open(latest, "w") as f:
            json.dump(report, f, indent=2, default=str)
    return output_path


# ══════════════════════════════════════════════════════════════════════════════
# 8. CLI
# ══════════════════════════════════════════════════════════════════════════════


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Smoke test E2E del pipeline PolyBot (R2.1-smoke)",
    )
    parser.add_argument(
        "--n-cycles", type=int, default=DEFAULT_N_CYCLES,
        help=f"Ciclos por market (default: {DEFAULT_N_CYCLES})",
    )
    parser.add_argument(
        "--warmup-ticks", type=int, default=DEFAULT_WARMUP_TICKS,
        help=f"Ticks de warmup por market (default: {DEFAULT_WARMUP_TICKS})",
    )
    parser.add_argument(
        "--cycle-interval", type=float, default=DEFAULT_CYCLE_INTERVAL_S,
        help=f"Segundos entre ciclos (default: {DEFAULT_CYCLE_INTERVAL_S})",
    )
    parser.add_argument(
        "--warmup-interval", type=float, default=DEFAULT_WARMUP_INTERVAL_S,
        help=f"Segundos entre ticks de warmup (default: {DEFAULT_WARMUP_INTERVAL_S})",
    )
    parser.add_argument(
        "--limit-per-asset", type=int, default=DEFAULT_LIMIT_PER_ASSET,
        help=f"Top-N markets por asset (default: {DEFAULT_LIMIT_PER_ASSET})",
    )
    parser.add_argument(
        "--force-fake-signal", action="store_true",
        help="Inyecta Signal BUY_YES directo al paper handler para forzar "
             "ejecución end-to-end (salta strategy+risk).",
    )
    parser.add_argument(
        "--force-amount", type=float, default=5.0,
        help="Monto del signal forzado en pUSD (default: 5.0)",
    )
    parser.add_argument(
        "--output", type=str, default=None,
        help="Ruta del JSON de salida. Default: "
             "data/reports/smoke_test_pipeline_<ts>.json",
    )
    parser.add_argument(
        "--log-level", type=str, default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    return parser.parse_args(argv)


# ══════════════════════════════════════════════════════════════════════════════
# 9. ORQUESTACIÓN
# ══════════════════════════════════════════════════════════════════════════════


async def bootstrap_smoke_container() -> Container:
    """Inicializa el container en paper mode SIN llamar a discover_markets()."""
    config = load_config()
    container = Container(config=config)
    await container.init()

    # Migraciones DB (mismo patrón que run_paper_marathon.bootstrap_marathon).
    import subprocess
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Alembic migration failed: {result.stderr}")

    return container


async def run_smoke(args: argparse.Namespace) -> tuple[dict, int]:
    """Ejecuta el smoke test completo y devuelve (report, exit_code)."""
    start = time.monotonic()
    config = {
        "n_cycles": args.n_cycles,
        "warmup_ticks": args.warmup_ticks,
        "cycle_interval_s": args.cycle_interval,
        "warmup_interval_s": args.warmup_interval,
        "limit_per_asset": args.limit_per_asset,
        "force_fake_signal": args.force_fake_signal,
        "force_amount": args.force_amount,
    }

    # 1. Discovery alternativo
    try:
        raw_markets = await fetch_active_crypto_markets(args.limit_per_asset)
    except Exception as e:
        logger.error("smoke_discovery_failed", error=str(e))
        elapsed = time.monotonic() - start
        return (
            build_report([], [], [], [], elapsed, config),
            EXIT_NO_MARKETS,
        )

    if not raw_markets:
        elapsed = time.monotonic() - start
        return (
            build_report([], [], [], [], elapsed, config),
            EXIT_NO_MARKETS,
        )

    markets: list[Market] = []
    for raw in raw_markets:
        try:
            markets.append(build_market_from_gamma(raw))
        except Exception as e:
            logger.warning(
                "smoke_market_parse_failed",
                slug=raw.get("slug", "?"),
                error=str(e),
            )

    if not markets:
        elapsed = time.monotonic() - start
        return (
            build_report([], [], [], [], elapsed, config),
            EXIT_NO_MARKETS,
        )

    # 2. Bootstrap + inyección
    container = await bootstrap_smoke_container()
    pipeline_exit_code = EXIT_OK
    warmup_results: list[WarmupResult] = []
    cycle_records: list[CycleRecord] = []
    forced_results: list[dict] = []

    try:
        await inject_markets(container, markets)
        logger.info("smoke_markets_injected", count=len(markets))

        # 3. Warmup
        for market in markets:
            wr = await warmup_market_ticks(
                container, market,
                n_ticks=args.warmup_ticks,
                interval_s=args.warmup_interval,
            )
            warmup_results.append(wr)

        # 4. Ciclos completos
        for cycle_idx in range(1, args.n_cycles + 1):
            for market in markets:
                rec = await run_single_cycle(container, market, cycle_idx)
                cycle_records.append(rec)
                if rec.error:
                    pipeline_exit_code = EXIT_PIPELINE_ERROR
            if cycle_idx < args.n_cycles:
                await asyncio.sleep(args.cycle_interval)

        # 5. (Opcional) forzar signal directo al paper handler
        if args.force_fake_signal:
            for market in markets:
                fr = await force_fake_signal(
                    container, market, amount=args.force_amount,
                )
                forced_results.append(fr)
                if not fr.get("success") and "error" in fr:
                    logger.warning(
                        "smoke_forced_signal_failed",
                        market=market.id[:20],
                        error=fr.get("error"),
                    )

    except Exception as e:
        pipeline_exit_code = EXIT_PIPELINE_ERROR
        logger.error(
            "smoke_unhandled_pipeline_error",
            error=str(e),
            traceback=traceback.format_exc()[-500:],
        )
    finally:
        try:
            await container.shutdown()
        except Exception as e:
            logger.warning("smoke_shutdown_error", error=str(e)[:200])

    elapsed = time.monotonic() - start
    report = build_report(
        markets=markets,
        warmup_results=warmup_results,
        cycle_records=cycle_records,
        forced_results=forced_results,
        elapsed_s=elapsed,
        config=config,
    )
    return report, pipeline_exit_code


def _format_summary_lines(report: dict) -> list[str]:
    v = report.get("validations", {})
    s = report.get("summary", {})
    lines = [
        "═" * 60,
        "  SMOKE TEST PIPELINE — RESUMEN",
        "═" * 60,
        f"  Estado:               {report.get('status')}",
        f"  Elapsed:              {report.get('elapsed_seconds')}s",
        f"  Markets usados:       {s.get('markets_count', 0)}",
        f"  Ciclos ejecutados:    {s.get('total_cycles_run', 0)}",
        f"  Errores en pipeline:  {s.get('total_errors', 0)}",
        f"  Órdenes (inferidas):  {s.get('total_orders', 0)}",
        "  ─" * 30,
        f"  {OBJ1_KEY}:  {v.get(OBJ1_KEY)}",
        f"  {OBJ2_KEY}:    {v.get(OBJ2_KEY)}",
        f"  {OBJ3_KEY}:   {v.get(OBJ3_KEY)}",
        "═" * 60,
    ]
    return lines


async def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    configure_logging(log_level=args.log_level)

    output_path = (
        Path(args.output)
        if args.output
        else DEFAULT_OUTPUT_DIR / (
            f"smoke_test_pipeline_"
            f"{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.json"
        )
    )

    report, exit_code = await run_smoke(args)
    write_report(report, output_path)

    for line in _format_summary_lines(report):
        print(line)
    print(f"  Reporte: {output_path}")
    print(f"  Latest:  {output_path.parent}/smoke_test_pipeline_latest.json")

    return exit_code


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        print("\n[smoke] Interrumpido por el usuario")
        sys.exit(130)
