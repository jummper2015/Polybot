"""Enum con los estados posibles del flujo de finality de una redeem tx."""

from __future__ import annotations

from enum import Enum


class FinalityStatus(str, Enum):
    """Estado de finality de una transacción CTF redeem.

    Cadena esperada:
        PENDING → SUBMITTED → MINED → CONFIRMED
                                  ↘     ↘
                                   REORGED / FAILED / TIMEOUT
    """

    PENDING     = "pending"      # tx aún no en mempool
    SUBMITTED   = "submitted"    # tx en mempool, no minada
    MINED       = "mined"        # tx minada pero < N confirmaciones (default N=64)
    CONFIRMED   = "confirmed"    # ≥ N confirmaciones (finality práctica Polygon)
    REORGED     = "reorged"      # reorg detectado post-confirm; requiere re-call
    TIMEOUT     = "timeout"      # no alcanzó finality dentro del timeout (default 600s)
    FAILED      = "failed"       # tx minada pero revertida on-chain
