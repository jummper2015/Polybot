# src/domain/enums/signal_type.py

from enum import Enum


class SignalType(str, Enum):
    BUY_YES = "BUY_YES"
    BUY_NO  = "BUY_NO"
    HOLD    = "HOLD"
    EXIT    = "EXIT"
