# src/interfaces/api/schemas/health_schema.py

from pydantic import BaseModel
from enum import Enum


class ServiceStatusEnum(str, Enum):
    OK       = "ok"
    DEGRADED = "degraded"
    DOWN     = "down"


class HealthResponse(BaseModel):
    """Estado de salud del sistema y sus dependencias."""
    status:    ServiceStatusEnum
    version:   str
    mode:      str              # "paper" o "real"
    services:  dict[str, ServiceStatusEnum]
    # Ejemplo:
    # {
    #   "database":   "ok",
    #   "redis":      "ok",
    #   "polymarket": "ok",
    #   "telegram":   "ok"
    # }