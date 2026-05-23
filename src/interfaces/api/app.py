# src/interfaces/api/app.py

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, ORJSONResponse
from fastapi.staticfiles import StaticFiles

from src.core.lifecycle import lifespan
from src.interfaces.api.routers import health, markets, metrics, orders, positions, dashboard

STATIC_DIR = Path(__file__).parent / "static"


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
        default_response_class=ORJSONResponse,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],    # Restringir en producción
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

    # Sirve archivos estáticos (CSS, JS, assets del build React)
    app.mount(
        "/assets",
        StaticFiles(directory=str(STATIC_DIR / "assets")),
        name="assets",
    )

    _register_routers(app)

    # Ruta raíz → dashboard React
    @app.get("/", include_in_schema=False)
    async def root():
        return FileResponse(str(STATIC_DIR / "index.html"))

    # Catch-all para React SPA: todas las rutas no-API sirven index.html
    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str):
        """Sirve el index.html de React para client-side routing."""
        # Excluir rutas de API, docs y assets
        if full_path.startswith(("api/", "docs", "redoc", "openapi.json", "assets/")):
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        index_path = STATIC_DIR / "index.html"
        if index_path.exists():
            return FileResponse(str(index_path))
        return JSONResponse({"detail": "Not Found"}, status_code=404)

    return app


def _register_routers(app: FastAPI) -> None:
    prefix = "/api/v1"
    app.include_router(health.router,    prefix=prefix, tags=["Health"])
    app.include_router(metrics.router,   prefix=prefix, tags=["Metrics"])
    app.include_router(markets.router,   prefix=prefix, tags=["Markets"])
    app.include_router(positions.router, prefix=prefix, tags=["Positions"])
    app.include_router(orders.router,    prefix=prefix, tags=["Orders"])
    app.include_router(dashboard.router, prefix=prefix, tags=["Dashboard"])
