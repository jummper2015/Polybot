# src/domain/enums/market_status.py

from enum import Enum


class MarketStatus(str, Enum):
    ACTIVE   = "active"
    EXPIRED  = "expired"
    RESOLVED = "resolved"
    UNKNOWN  = "unknown"
