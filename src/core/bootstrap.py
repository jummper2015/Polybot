# src/core/bootstrap.py

import asyncio
import os
import signal

import structlog

from src.core.config import load_config
from src.core.container import Container
from src.infrastructure.observability.logging import configure_logging
from src.infrastructure.observability.tracing import init_tracing, shutdown_tracing

logger = structlog.get_logger(__name__)


async def run_telegram(container: Container) -> None:
    """
    Corre el polling de Telegram en loop.
    Se cancela limpiamente cuando el bot se detiene.
    """
    try:
        logger.info("telegram_polling_starting")
        await container.telegram_dp.start_polling(
            container.telegram_bot,
            allowed_updates=["message", "callback_query"],
        )
    except asyncio.CancelledError:
        logger.info("telegram_polling_cancelled")
    except Exception as e:
        logger.error("telegram_polling_error", error=str(e))


async def run_fastapi(container: Container) -> None:
    """
    Corre el servidor FastAPI con uvicorn programáticamente.
    El container ya está inicializado — solo lo pasa a la app.
    """
    import uvicorn

    from src.interfaces.api.app import create_app
    from src.interfaces.api.middleware import (
        ObservabilityMiddleware,
        RequestContextMiddleware,
    )

    app = create_app()

    # Inyecta el container ya inicializado (no usa lifespan para re-inicializar)
    app.state.container = container

    # Registra middlewares de observabilidad
    app.add_middleware(ObservabilityMiddleware)
    app.add_middleware(RequestContextMiddleware)

    config = uvicorn.Config(
        app=app,
        host=os.environ.get("API_HOST", "0.0.0.0"),  # nosec: bind all in container/K8s is expected
        port=int(os.environ.get("API_PORT", "8000")),
        log_level="warning",   # structlog maneja el logging — uvicorn solo errores
        access_log=False,       # structlog ya loguea los requests via middleware
    )
    server = uvicorn.Server(config)

    try:
        logger.info("fastapi_server_starting", port=8000)
        await server.serve()
    except asyncio.CancelledError:
        logger.info("fastapi_server_cancelled")


async def run_trading(container: Container) -> None:
    """
    Corre el bot de trading en loop.
    """
    try:
        logger.info("trading_service_starting")
        await container.trading_service.start()

        # Mantiene la tarea viva hasta cancelación
        while True:
            await asyncio.sleep(3600)

    except asyncio.CancelledError:
        logger.info("trading_service_cancelled")
        await container.trading_service.stop()


async def bootstrap() -> None:
    """
    Punto de entrada principal del sistema.
    Inicializa todo y lanza los tres loops en paralelo:
    1. FastAPI (REST API + /health + /metrics)
    2. Telegram (bot UI)
    3. TradingService (ciclos de mercado)

    Gestiona SIGTERM/SIGINT para apagado limpio.
    """
    # ── Paso 1: Logging (primero de todo) ────────────────────────────
    log_level = os.environ.get("LOG_LEVEL", "INFO")
    configure_logging(log_level=log_level)

    logger.info("bootstrap_starting", version="1.0.0")

    # ── Paso 2: OpenTelemetry Tracing ─────────────────────────────────
    # Se inicializa ANTES del container para capturar spans de init
    tracing_enabled = init_tracing(
        service_name=os.environ.get("OTEL_SERVICE_NAME", "polybot"),
    )
    if tracing_enabled:
        logger.info("tracing_enabled")
    else:
        logger.info("tracing_disabled")

    # ── Paso 3: Config ────────────────────────────────────────────────
    config = load_config()

    # ── Paso 4: Container (DI + inicialización de todos los servicios) ─
    container = Container(config=config)
    await container.init()

    # ── Paso 5: Migraciones de DB ─────────────────────────────────────
    await _run_migrations()

    # ── Paso 5.5: Reconcile redeem operations pendientes ──────────────
    # Si hay operaciones PENDING/SUBMITTED/MINED tras restart, las reporta.
    # Reconciliación completa (retry automático) será implementada en F2.
    await _reconcile_pending_redeems(container)

    # ── Paso 6: Setup graceful shutdown ──────────────────────────────
    loop = asyncio.get_running_loop()
    shutdown_event = asyncio.Event()

    def _signal_handler(sig):
        logger.info("shutdown_signal_received", signal=sig.name)
        shutdown_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _signal_handler, sig)

    # ── Paso 7: Lanza los servicios en paralelo ──────────────────
    tasks = [
        asyncio.create_task(run_fastapi(container),  name="fastapi"),
        asyncio.create_task(run_trading(container),  name="trading"),
    ]

    # Telegram: solo si el bot se inicializó correctamente
    if container.telegram_bot is not None and container.telegram_dp is not None:
        tasks.append(
            asyncio.create_task(run_telegram(container), name="telegram")
        )
    else:
        logger.warning("telegram_skipped", reason="bot_token_missing_or_invalid")

    logger.info(
        "all_services_started",
        mode=config.trading_mode,
        tasks=[t.get_name() for t in tasks],
    )

    # ── Paso 8: Espera señal de shutdown o error en alguna tarea ──────
    done, pending = await asyncio.wait(
        [*tasks, asyncio.create_task(shutdown_event.wait())],
        return_when=asyncio.FIRST_COMPLETED,
    )

    # Loguea qué tarea terminó primero
    for task in done:
        if task.exception():
            logger.error(
                "task_failed_unexpectedly",
                task=task.get_name(),
                error=str(task.exception()),
            )

    # ── Paso 9: Cancela tareas pendientes ────────────────────────────
    logger.info("initiating_graceful_shutdown")
    for task in pending:
        task.cancel()

    await asyncio.gather(*pending, return_exceptions=True)

    # ── Paso 10: Apagado del container + tracing ─────────────────────
    await container.shutdown()
    shutdown_tracing()

    logger.info("bootstrap_shutdown_complete")


async def _reconcile_pending_redeems(container: Container) -> None:
    """
    Reconcilia operaciones redeem pendientes tras restart del bot.

    Lee redeem_operations con status IN (pending, submitted, mined) y reporta:
      - Cuántas hay pendientes
      - Sus tx_hash (si available) para verificación manual
      - Log warning si alguna lleva > 10 min pendiente

    Reconciliación automática (retry via wait_for_finality) será implementada
    en F2 cuando el CTFRedeemer.reconcile_on_startup esté completo.
    """
    try:
        pending_ops = await container.repository.get_pending_redeems(limit=100)

        if not pending_ops:
            logger.info("reconcile_pending_redeems_none")
            return

        logger.warning(
            "reconcile_pending_redeems_found",
            count=len(pending_ops),
            ops=[
                {
                    "redeem_op_id": op.redeem_op_id,
                    "condition_id": op.condition_id[-12:],
                    "status": op.status,
                    "tx_hash": op.tx_hash[-12:] if op.tx_hash else None,
                    "submitted_at": op.submitted_at.isoformat() if op.submitted_at else None,
                }
                for op in pending_ops
            ],
        )

        # TODO F2: implementar retry automático
        # for op in pending_ops:
        #     if op.tx_hash and op.status in (submitted, mined):
        #         await ctf_redeemer.reconcile_tx(op.tx_hash, op.redeem_op_id)

    except Exception as e:
        logger.error(
            "reconcile_pending_redeems_error",
            error=str(e)[:200],
        )
        # No falla el arranque — reconcile es best-effort


async def _run_migrations() -> None:
    """
    Ejecuta migraciones de Alembic antes de arrancar.
    Equivalente a `alembic upgrade head` pero desde Python.
    """
    import subprocess
    import sys

    logger.info("running_db_migrations")
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        logger.error(
            "migration_failed",
            stdout=result.stdout,
            stderr=result.stderr,
        )
        raise RuntimeError(f"Alembic migration failed: {result.stderr}")

    logger.info("migrations_complete", output=result.stdout.strip())
