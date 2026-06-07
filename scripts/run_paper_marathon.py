#!/usr/bin/env python3
"""
Paper Trading Marathon — Ejecuta N ciclos de paper trading con auto-reinicio y métricas.

R1.1 del PLAN_ESTRATÉGICO v4.0: Valida que el sistema puede correr 100+ ciclos
de paper trading sin errores, crashes ni memory leaks.

Características:
    - N ciclos configurables (default: 100)
    - Auto-reinicio con exponential backoff en caso de crash
    - Métricas por ciclo: latencia, señales generadas, posiciones abiertas, PnL, balance
    - Guarda resultados en reports/paper_marathon.json
    - Graceful shutdown con SIGTERM/SIGINT
    - Sin dependencia de FastAPI ni Telegram (headless)

Usage:
    python scripts/run_paper_marathon.py --cycles 100
    python scripts/run_paper_marathon.py --cycles 200 --output reports/custom.json
    python scripts/run_paper_marathon.py --cycles 0  # Indefinido (Ctrl+C para parar)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ── Path setup ────────────────────────────────────────────────────────────────
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import structlog

from src.core.config import load_config
from src.core.container import Container
from src.infrastructure.observability.logging import configure_logging
from src.infrastructure.observability.tracing import init_tracing, shutdown_tracing

logger = structlog.get_logger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────
DEFAULT_CYCLES = 100
DEFAULT_OUTPUT = Path("reports/paper_marathon.json")
CYCLE_INTERVAL_SECONDS = 30  # Debe coincidir con TradingService.CYCLE_INTERVAL_SECONDS

# ── Crash recovery ───────────────────────────────────────────────────────────
MAX_RESTARTS = 5
INITIAL_BACKOFF_SECONDS = 5.0
MAX_BACKOFF_SECONDS = 300.0
BACKOFF_MULTIPLIER = 2.0

_shutdown_requested = False


# ══════════════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════════════


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Paper Trading Marathon — R1.1 Validación de estabilidad",
    )
    parser.add_argument(
        "--cycles", type=int, default=DEFAULT_CYCLES,
        help=f"Número de ciclos a ejecutar (0 = indefinido, default: {DEFAULT_CYCLES})",
    )
    parser.add_argument(
        "--output", type=str, default=str(DEFAULT_OUTPUT),
        help=f"Ruta del archivo JSON de resultados (default: {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--no-backoff", action="store_true",
        help="Desactiva el auto-reinicio con backoff (modo fail-fast)",
    )
    parser.add_argument(
        "--log-level", type=str, default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Nivel de logging (default: INFO)",
    )
    return parser.parse_args()


# ══════════════════════════════════════════════════════════════════════════════
# SIGNAL HANDLING
# ══════════════════════════════════════════════════════════════════════════════


def setup_signal_handlers() -> None:
    def handler(sig: int, _frame: Any) -> None:
        global _shutdown_requested
        if not _shutdown_requested:
            logger.warning("shutdown_requested", signal=signal.Signals(sig).name)
            _shutdown_requested = True

    signal.signal(signal.SIGINT, handler)
    signal.signal(signal.SIGTERM, handler)


# ══════════════════════════════════════════════════════════════════════════════
# CONTAINER BOOTSTRAP
# ══════════════════════════════════════════════════════════════════════════════


async def bootstrap_marathon() -> Container:
    """Inicializa el container para el marathon (sin FastAPI ni Telegram)."""
    # ── Config ─────────────────────────────────────────────────────────
    config = load_config()

    # ── Container ──────────────────────────────────────────────────────
    container = Container(config=config)
    await container.init()

    # ── Descubrir mercados ────────────────────────────────────────────
    # Necesario porque el marathon no llama a TradingService.start()
    # (que es donde normalmente se ejecuta discover_markets).
    # Sin esto, get_active_markets() devuelve vacío en DB fresca.
    await container.market_service.discover_markets()

    # ── Migraciones ────────────────────────────────────────────────────
    import subprocess
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Alembic migration failed: {result.stderr}")

    logger.info(
        "marathon_container_ready",
        mode=config.trading_mode,
        balance=container.execution_handler.get_balance()
        if hasattr(container.execution_handler, "get_balance") else "N/A",
    )

    return container


# ══════════════════════════════════════════════════════════════════════════════
# MARATHON LOOP
# ══════════════════════════════════════════════════════════════════════════════


async def run_marathon(
    container: Container,
    num_cycles: int,
) -> list[dict[str, Any]]:
    """
    Ejecuta N ciclos de paper trading y recolecta métricas por ciclo.

    Cada ciclo:
        1. Obtiene mercados activos
        2. Corre _run_market_cycle para cada mercado (mide latencia)
        3. Recolecta métricas del estado del sistema
        4. Guarda en el registro de ciclos
    """
    cycles_log: list[dict[str, Any]] = []
    trading_svc = container.trading_service
    strategy_engine = container.strategy_orchestrator
    execution_handler = container.execution_handler
    repository = container.repository

    for cycle_num in range(1, num_cycles + 1):
        if _shutdown_requested:
            logger.info("marathon_interrupted", cycles_completed=cycle_num - 1)
            break

        cycle_start_ts = time.monotonic()
        cycle_start_dt = datetime.now(timezone.utc)

        # ── 1. Obtener mercados activos ────────────────────────────────
        try:
            markets = await container.market_service.get_active_markets()
            active_markets = [m for m in markets if m.is_active()]
        except Exception as e:
            logger.warning("marathon_market_fetch_error",
                          cycle=cycle_num, error=str(e))
            active_markets = []

        # ── 2. Ejecutar ciclo por mercado ──────────────────────────────
        market_cycles = 0
        signals_generated = 0
        orders_executed = 0
        market_latencies: list[float] = []
        errors_this_cycle = 0

        for market in active_markets:
            if _shutdown_requested:
                break

            market_start = time.monotonic()
            try:
                # Inicio de ciclo de estrategia (resetea estado por tick)
                await strategy_engine.on_cycle_start(market)

                # Obtener tick actual
                tick = await trading_svc._get_current_tick(market)
                if tick is None:
                    await strategy_engine.on_exit(market)
                    continue

                # Procesar tick
                await strategy_engine.on_tick(market, tick)

                # Evaluar salida
                exit_signal = await strategy_engine.should_exit(market, tick)
                if exit_signal.is_actionable():
                    signals_generated += 1
                    await trading_svc._execute_exit(market, exit_signal)
                    orders_executed += 1
                    market_latencies.append(time.monotonic() - market_start)
                    await strategy_engine.on_exit(market)
                    continue

                # Evaluar entrada
                entry_signal = await strategy_engine.should_enter(market, tick)
                if entry_signal.is_actionable():
                    signals_generated += 1
                    await trading_svc._evaluate_risk_and_execute(
                        market, entry_signal, tick
                    )
                    orders_executed += 1

                market_cycles += 1
                market_latencies.append(time.monotonic() - market_start)

            except Exception as e:
                errors_this_cycle += 1
                logger.error(
                    "marathon_market_cycle_error",
                    cycle=cycle_num,
                    market_id=market.id[:16] if market.id else "unknown",
                    error=str(e)[:200],
                )
            finally:
                # Siempre finalizar el ciclo de estrategia
                try:
                    await strategy_engine.on_exit(market)
                except Exception:
                    pass

        # ── 3. Recolectar métricas del ciclo ───────────────────────────
        cycle_duration = time.monotonic() - cycle_start_ts
        avg_latency = (
            sum(market_latencies) / len(market_latencies)
            if market_latencies else 0.0
        )
        max_latency = max(market_latencies) if market_latencies else 0.0

        # Balance y PnL
        balance = 0.0
        pnl = 0.0
        pnl_pct = 0.0
        if hasattr(execution_handler, "get_balance"):
            balance = execution_handler.get_balance()
        if hasattr(execution_handler, "get_total_pnl"):
            pnl = execution_handler.get_total_pnl()
        if hasattr(execution_handler, "get_total_pnl_pct"):
            pnl_pct = execution_handler.get_total_pnl_pct()

        # Posiciones abiertas
        open_positions = 0
        try:
            positions = await repository.get_positions(open_only=True)
            open_positions = len(positions)
        except Exception:
            pass

        # ── 4. Registrar ciclo ─────────────────────────────────────────
        cycle_record = {
            "cycle": cycle_num,
            "timestamp": cycle_start_dt.isoformat(),
            "duration_seconds": round(cycle_duration, 3),
            "active_markets": len(active_markets),
            "markets_processed": market_cycles,
            "signals_generated": signals_generated,
            "orders_executed": orders_executed,
            "avg_market_latency_ms": round(avg_latency * 1000, 2),
            "max_market_latency_ms": round(max_latency * 1000, 2),
            "errors": errors_this_cycle,
            "balance_usdc": round(balance, 2),
            "pnl_usdc": round(pnl, 4),
            "pnl_pct": round(pnl_pct, 4),
            "open_positions": open_positions,
        }
        cycles_log.append(cycle_record)

        # ── 5. Log ─────────────────────────────────────────────────────
        signal_word = "señal" if signals_generated == 1 else "señales"
        logger.info(
            "marathon_cycle_completed",
            cycle=f"{cycle_num}/{num_cycles}",
            duration=f"{cycle_duration:.1f}s",
            markets=f"{market_cycles}/{len(active_markets)}",
            signals=signals_generated,
            orders=orders_executed,
            errors=errors_this_cycle,
            balance=f"{balance:.2f}",
            pnl=pnl,
            open=open_positions,
        )

        # ── 6. Esperar intervalo entre ciclos ──────────────────────────
        if cycle_num < num_cycles and not _shutdown_requested:
            remaining = max(0.0, CYCLE_INTERVAL_SECONDS - cycle_duration)
            await asyncio.sleep(remaining)

    return cycles_log


# ══════════════════════════════════════════════════════════════════════════════
# CRASH RECOVERY
# ══════════════════════════════════════════════════════════════════════════════


async def run_marathon_with_recovery(
    num_cycles: int,
    no_backoff: bool = False,
) -> tuple[list[dict[str, Any]], bool, int]:
    """
    Ejecuta el marathon con auto-reinicio en caso de crash.

    Usa exponential backoff entre intentos (5s → 10s → 20s → ... → 5min max).
    Si no_backoff=True, falla al primer error sin reintentar.

    Returns:
        (cycles_log, success): Lista de registros de ciclos y flag de éxito.
    """
    all_cycles: list[dict[str, Any]] = []
    attempt = 0
    backoff_seconds = INITIAL_BACKOFF_SECONDS

    while attempt < MAX_RESTARTS and not _shutdown_requested:
        attempt += 1
        container = None

        try:
            logger.info(
                "marathon_attempt_starting",
                attempt=attempt,
                max_restarts=MAX_RESTARTS,
                target_cycles=num_cycles,
            )

            # ── Bootstrap ──────────────────────────────────────────────
            container = await bootstrap_marathon()

            # ── Run ────────────────────────────────────────────────────
            remaining = num_cycles - len(all_cycles)
            if remaining <= 0:
                break

            cycles = await run_marathon(container, remaining)
            all_cycles.extend(cycles)

            # Si completamos todos los ciclos, éxito
            if len(all_cycles) >= num_cycles or _shutdown_requested:
                logger.info(
                    "marathon_completed",
                    total_cycles=len(all_cycles),
                    attempts=attempt,
                )
                return all_cycles, True, attempt

        except Exception as e:
            logger.error(
                "marathon_crash",
                attempt=attempt,
                error=type(e).__name__,
                detail=str(e)[:300],
            )

            if no_backoff:
                logger.error("marathon_fail_fast", reason="no_backoff_enabled")
                return all_cycles, False, attempt

            if attempt < MAX_RESTARTS and not _shutdown_requested:
                logger.info(
                    "marathon_restarting",
                    backoff_seconds=round(backoff_seconds, 1),
                    next_attempt=attempt + 1,
                )
                await asyncio.sleep(backoff_seconds)
                backoff_seconds = min(
                    backoff_seconds * BACKOFF_MULTIPLIER,
                    MAX_BACKOFF_SECONDS,
                )
        finally:
            # Clean shutdown del container
            if container is not None:
                try:
                    await container.shutdown()
                except Exception as e:
                    logger.warning("marathon_shutdown_error", error=str(e)[:200])

    logger.warning(
        "marathon_exhausted_retries",
        total_cycles=len(all_cycles),
        attempts=attempt,
    )
    return all_cycles, False, attempt


# ══════════════════════════════════════════════════════════════════════════════
# REPORT GENERATION
# ══════════════════════════════════════════════════════════════════════════════


def build_report(
    cycles_log: list[dict[str, Any]],
    success: bool,
    elapsed_seconds: float,
    attempts: int,
    args: argparse.Namespace,
) -> dict[str, Any]:
    """Construye el reporte final en formato JSON."""

    if not cycles_log:
        return {
            "status": "no_data",
            "error": "No cycles were completed",
            "elapsed_seconds": round(elapsed_seconds, 1),
            "attempts": attempts,
            "cycles_completed": 0,
        }

    # ── Métricas agregadas ──────────────────────────────────────────────
    durations = [c["duration_seconds"] for c in cycles_log]
    latencies = [c["avg_market_latency_ms"] for c in cycles_log if c["avg_market_latency_ms"] > 0]
    balances = [c["balance_usdc"] for c in cycles_log]
    pnls = [c["pnl_usdc"] for c in cycles_log]
    signals = [c["signals_generated"] for c in cycles_log]
    errors = [c["errors"] for c in cycles_log]

    first_balance = balances[0] if balances else 0.0
    last_balance = balances[-1] if balances else 0.0
    final_pnl = last_balance - first_balance

    return {
        "status": "success" if success else "partial",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "elapsed_seconds": round(elapsed_seconds, 1),
        "elapsed_minutes": round(elapsed_seconds / 60, 1),
        "attempts": attempts,
        "config": {
            "target_cycles": args.cycles if args.cycles > 0 else "indefinite",
            "no_backoff": args.no_backoff,
        },
        "summary": {
            "cycles_completed": len(cycles_log),
            "cycles_attempted": args.cycles if args.cycles > 0 else "indefinite",
            "completion_pct": (
                round(len(cycles_log) / args.cycles * 100, 1)
                if args.cycles > 0 else None
            ),
            "total_errors": sum(errors),
            "total_signals": sum(signals),
        },
        "timing": {
            "total_elapsed_seconds": round(elapsed_seconds, 1),
            "avg_cycle_duration_seconds": round(sum(durations) / len(durations), 3),
            "min_cycle_duration_seconds": round(min(durations), 3),
            "max_cycle_duration_seconds": round(max(durations), 3),
            "avg_market_latency_ms": (
                round(sum(latencies) / len(latencies), 2)
                if latencies else None
            ),
            "max_market_latency_ms": (
                round(max(latencies), 2) if latencies else None
            ),
        },
        "pnl": {
            "initial_balance_usdc": round(first_balance, 2),
            "final_balance_usdc": round(last_balance, 2),
            "absolute_pnl_usdc": round(final_pnl, 4),
            "pnl_pct": (
                round(final_pnl / first_balance * 100, 4)
                if first_balance > 0 else 0.0
            ),
            "max_balance_usdc": round(max(balances), 2) if balances else 0.0,
            "min_balance_usdc": round(min(balances), 2) if balances else 0.0,
            "max_drawdown_usdc": (
                round(max(balances) - min(balances), 2)
                if balances and len(balances) > 1 else 0.0
            ),
        },
        "stability": {
            "crashes": max(0, attempts - 1),
            "max_consecutive_cycles": len(cycles_log),
            "errors_per_cycle_avg": (
                round(sum(errors) / max(len(errors), 1), 2)
            ),
            "cycles_with_errors": sum(1 for e in errors if e > 0),
            "cycles_with_signals": sum(1 for s in signals if s > 0),
            "crash_free": success and attempts == 1,
        },
        "cycles": cycles_log,
    }


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════


async def main() -> None:
    args = parse_args()
    setup_signal_handlers()

    # ── Logging ─────────────────────────────────────────────────────────
    configure_logging(log_level=args.log_level)

    # ── Tracing (best-effort, no bloquea si falla) ─────────────────────
    _tracing_enabled = init_tracing(
        service_name=os.environ.get("OTEL_SERVICE_NAME", "polybot-marathon"),
    )
    if _tracing_enabled:
        logger.info("tracing_enabled")

    # ── Validaciones ────────────────────────────────────────────────────
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    num_cycles = args.cycles if args.cycles > 0 else 10**9  # Prácticamente indefinido
    if args.cycles == 0:
        logger.info("marathon_indefinite_mode", note="Ctrl+C para detener")

    logger.info(
        "marathon_starting",
        target_cycles=args.cycles if args.cycles > 0 else "indefinite",
        output=str(output_path),
        no_backoff=args.no_backoff,
    )

    start_ts = time.monotonic()

    # ── Ejecutar marathon ───────────────────────────────────────────────
    cycles_log, success, attempts = await run_marathon_with_recovery(
        num_cycles=num_cycles,
        no_backoff=args.no_backoff,
    )

    elapsed = time.monotonic() - start_ts

    # ── Construir reporte ───────────────────────────────────────────────
    report = build_report(
        cycles_log=cycles_log,
        success=success,
        elapsed_seconds=elapsed,
        attempts=attempts,
        args=args,
    )

    # ── Guardar ─────────────────────────────────────────────────────────
    with open(output_path, "w") as f:
        json.dump(report, f, indent=2, default=str)

    # ── Resumen final ───────────────────────────────────────────────────
    summary = report["summary"]
    timing = report["timing"]
    pnl_summary = report["pnl"]
    stability = report["stability"]

    logger.info("══════════════════════════════════════════════════════════")
    logger.info("  MARATHON COMPLETADO")
    logger.info("══════════════════════════════════════════════════════════")
    logger.info("  Estado:            %s", report["status"])
    logger.info("  Ciclos:            %d/%s",
                summary["cycles_completed"], summary["cycles_attempted"])
    logger.info("  Duración total:    %.1f min", elapsed / 60)
    logger.info("  Señales totales:   %d", summary["total_signals"])
    logger.info("  Errores totales:   %d", summary["total_errors"])
    logger.info("  Avg ciclo:         %.1f s", timing["avg_cycle_duration_seconds"])
    logger.info("  Avg latencia:      %s ms",
                f"{timing['avg_market_latency_ms']:.1f}"
                if timing["avg_market_latency_ms"] else "N/A")
    logger.info("  ───────────────────────────────────────────────────────")
    logger.info("  Balance inicial:   %.2f USDC", pnl_summary["initial_balance_usdc"])
    logger.info("  Balance final:     %.2f USDC", pnl_summary["final_balance_usdc"])
    logger.info("  PnL absoluto:      %.4f USDC", pnl_summary["absolute_pnl_usdc"])
    logger.info("  PnL %%:             %.4f%%", pnl_summary["pnl_pct"])
    logger.info("  Max drawdown:      %.2f USDC", pnl_summary["max_drawdown_usdc"])
    logger.info("  ───────────────────────────────────────────────────────")
    logger.info("  Crashes:           %d", stability["crashes"])
    logger.info("  Crash-free:        %s", stability["crash_free"])
    logger.info("  Ciclos con signal: %d/%d",
                stability["cycles_with_signals"], summary["cycles_completed"])
    logger.info("══════════════════════════════════════════════════════════")
    logger.info("  Reporte guardado:  %s", output_path)

    # ── Cleanup ─────────────────────────────────────────────────────────
    shutdown_tracing()

    # ── Exit code ───────────────────────────────────────────────────────
    if not success and summary["cycles_completed"] == 0:
        sys.exit(1)  # Marathon completamente fallido → CI debe fallar


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[marathon] Interrumpido por el usuario")
        sys.exit(0)
