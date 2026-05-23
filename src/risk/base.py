# src/risk/base.py

from abc import ABC, abstractmethod

from src.domain.value_objects.risk_decision import RiskDecision
from src.domain.value_objects.signal import Signal
from src.risk.context import RiskContext


class IRule(ABC):
    """
    Contrato que toda regla de riesgo debe implementar.
    Las reglas son stateless — todo el estado viene en RiskContext.
    Método síncrono: las reglas no hacen I/O.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Nombre único de la regla. Usado en logs, métricas y RiskDecision."""
        ...

    @property
    @abstractmethod
    def priority(self) -> int:
        """
        Prioridad de evaluación (menor número = mayor prioridad).
        MinBalance=1, Drawdown=2, KellySizing=3, MaxExposure=3,
        MaxPositions=4, Hedge=5
        """
        ...

    @abstractmethod
    def evaluate(
        self,
        signal:  Signal,
        context: RiskContext,
    ) -> RiskDecision:
        """
        Evalúa si la señal debe ser permitida según esta regla.
        SIEMPRE devuelve un RiskDecision — nunca None.
        Si la regla no aplica a esta señal → devuelve ALLOW.
        """
        ...

    def _allow(
        self,
        reason: str,
        suggested_amount: float | None = None,
    ) -> RiskDecision:
        """Factory para decisiones de permiso."""
        return RiskDecision(
            allowed=True,
            reason=reason,
            rule_triggered=self.name,
            suggested_amount=suggested_amount,
        )

    def _deny(self, reason: str) -> RiskDecision:
        """Factory para decisiones de rechazo."""
        return RiskDecision(
            allowed=False,
            reason=reason,
            rule_triggered=self.name,
            suggested_amount=None,
        )
