# src/interfaces/api/routers/metrics.py

from fastapi import APIRouter, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

router = APIRouter()


@router.get("/metrics", summary="Métricas Prometheus", include_in_schema=False)
async def prometheus_metrics() -> Response:
    """
    Endpoint scrapeado por Prometheus.
    Devuelve métricas en formato text/plain estándar.
    No aparece en la documentación Swagger.
    """
    return Response(
        content=generate_latest(),
        media_type=CONTENT_TYPE_LATEST,
    )
