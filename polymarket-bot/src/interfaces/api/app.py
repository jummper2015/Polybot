# src/interfaces/api/app.py  (versión final completa)

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.core.lifecycle import lifespan
from src.interfaces.api.routers import health, metrics, markets, positions, orders


def create_app() -> FastAPI:
    """
    Factory que crea y configura la app FastAPI.
    El lifespan gestiona el container de dependencias.
    Los middlewares de observabilidad se añaden en bootstrap.py.
    """
    app = FastAPI(
        title="Polymarket Bot API",
        version="1.0.0",
        description="Algorithmic trading bot for Polymarket BTC/ETH markets",
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],    # Restringir en producción
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

    _register_routers(app)
    return app


def _register_routers(app: FastAPI) -> None:
    prefix = "/api/v1"
    app.include_router(health.router,    prefix=prefix, tags=["Health"])
    app.include_router(metrics.router,   prefix=prefix, tags=["Metrics"])
    app.include_router(markets.router,   prefix=prefix, tags=["Markets"])
    app.include_router(positions.router, prefix=prefix, tags=["Positions"])
    app.include_router(orders.router,    prefix=prefix, tags=["Orders"])
    # Añadir a src/interfaces/api/app.py

from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pathlib import Path

STATIC_DIR = Path(__file__).parent / "static"


def create_app() -> FastAPI:
    app = FastAPI(
        title="Polymarket Bot API",
        version="1.0.0",
        lifespan=lifespan,
    )

    app.add_middleware(CORSMiddleware,
        allow_origins=["*"], allow_methods=["GET","POST"], allow_headers=["*"])

    # Sirve archivos estáticos (CSS, JS)
    app.mount(
        "/static",
        StaticFiles(directory=str(STATIC_DIR)),
        name="static",
    )

    _register_routers(app)

    # Ruta raíz → dashboard
    @app.get("/", include_in_schema=False)
    async def root():
        return FileResponse(str(STATIC_DIR / "index.html"))

    return app


def _register_routers(app: FastAPI) -> None:
    from src.interfaces.api.routers import (
        health, metrics, markets, positions, orders, dashboard
    )
    prefix = "/api/v1"
    app.include_router(health.router,     prefix=prefix, tags=["Health"])
    app.include_router(metrics.router,    prefix=prefix, tags=["Metrics"])
    app.include_router(markets.router,    prefix=prefix, tags=["Markets"])
    app.include_router(positions.router,  prefix=prefix, tags=["Positions"])
    app.include_router(orders.router,     prefix=prefix, tags=["Orders"])
    app.include_router(dashboard.router,  prefix=prefix, tags=["Dashboard"])