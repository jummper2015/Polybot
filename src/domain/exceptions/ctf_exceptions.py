"""
Custom exceptions para el flujo CTF on-chain redeem (R2.0-redeem-impl F1).

Jerarquía:
    CTFRedeemError (base)
    ├── CTFMarketNotResolvedError   → mercado aún no resuelve on-chain
    ├── InsufficientGasError        → POLY_PROXY sin MATIC
    ├── DuplicateRedeemError        → idempotencia (otra op PENDING para misma condición)
    ├── FinalityTimeoutError        → no alcanzó N confirmaciones en timeout
    ├── RedeemExecutionError        → tx ejecutada pero revertida
    ├── CTFAdapterPausedError       → adapter onramp sin código
    └── CTFNonceError               → conflicto de nonce con tx pendiente
"""

from __future__ import annotations


class CTFRedeemError(Exception):
    """Base para todos los errores del flujo CTF redeem."""


class CTFMarketNotResolvedError(CTFRedeemError):
    """El mercado aún no está resuelto on-chain; no se puede redimir."""


class InsufficientGasError(CTFRedeemError):
    """El POLY_PROXY no tiene MATIC suficiente para pagar el gas del redeem."""


class DuplicateRedeemError(CTFRedeemError):
    """Ya existe una operación redeem PENDING/CONFIRMED para este (condition_id, outcome)."""


class FinalityTimeoutError(CTFRedeemError):
    """La tx no alcanzó N confirmaciones dentro del timeout configurado."""


class RedeemExecutionError(CTFRedeemError):
    """La tx ejecutó pero la llamada al contrato revirtió on-chain."""


class CTFAdapterPausedError(CTFRedeemError):
    """El adapter onramp (0x93070a...) está pausado o deprecado; sin código en 'latest'."""


class CTFNonceError(CTFRedeemError):
    """Conflicto de nonce: otra tx pendiente del EOA usa el mismo nonce."""
