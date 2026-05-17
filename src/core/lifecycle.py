# src/core/lifecycle.py

import asyncio
import structlog
from contextlib import asynccontextmanager
from fastapi import FastAPI

logger = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Lifespan de FastAPI.
    En modo bootstrap (bootstrap.py), el container ya está inicializado
    y asignado a app.state.container antes de que uvicorn arranque.
    Este lifespan solo verifica que el container existe.
    """
    container = getattr(app.state, "container", None)

    if container is None:
        # Solo en modo standalone (sin bootstrap.py) — crea el container
        logger.warning(
            "lifespan_creating_container",
            note="Modo standalone — usar bootstrap.py en producción",
        )
        from src.core.config import load_config
        from src.core.container import Container
        config    = load_config()
        container = Container(config=config)
        await container.init()
        app.state.container = container
        standalone = True
    else:
        standalone = False
        logger.info("lifespan_container_already_initialized")

    logger.info("fastapi_lifespan_startup_complete")

    yield  # ← FastAPI sirve requests entre aquí y el shutdown

    logger.info("fastapi_lifespan_shutting_down")

    if standalone:
        # Solo apaga si lo creamos nosotros
        await container.shutdown()