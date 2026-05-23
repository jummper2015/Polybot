# src/domain/enums/order_side.py

from enum import Enum


class OrderSide(str, Enum):
    YES = "YES"
    NO  = "NO"
