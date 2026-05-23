# src/domain/enums/order_status.py

from enum import Enum


class OrderStatus(str, Enum):
    PENDING   = "pending"
    FILLED    = "filled"
    CANCELLED = "cancelled"
    FAILED    = "failed"
