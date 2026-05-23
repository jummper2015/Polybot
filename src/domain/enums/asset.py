# src/domain/enums/asset.py

from enum import Enum


class Asset(str, Enum):
    BTC = "BTC"
    ETH = "ETH"
