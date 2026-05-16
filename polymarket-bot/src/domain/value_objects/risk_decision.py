# src/domain/value_objects/risk_decision.py

from dataclasses import dataclass


@dataclass(frozen=True)
class RiskDecision:
    """
    Respuesta del RiskEngine ante una señal.
    Siempre incluye el motivo y la regla que tomó la decisión.
    """
    allowed:         bool
    reason:          str            # Descripción legible
    rule_triggered:  str            # Nombre de la regla (ej: "MinBalanceRule")
    suggested_amount: float | None = None  # Monto ajustado si el riesgo lo redujo