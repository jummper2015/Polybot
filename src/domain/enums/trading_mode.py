# src/domain/enums/trading_mode.py

from enum import Enum


class TradingMode(str, Enum):
    PAPER = "paper"
    REAL  = "real"
