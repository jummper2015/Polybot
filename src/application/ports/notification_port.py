# src/application/ports/notification_port.py

from abc import ABC, abstractmethod


class INotificationPort(ABC):
    """
    Contrato para enviar notificaciones al usuario.
    Implementado por el bot de Telegram.
    """

    @abstractmethod
    async def send_trade_alert(
        self,
        market_id: str,
        side: str,
        amount: float,
        price: float,
        mode: str,
    ) -> None: ...

    @abstractmethod
    async def send_exit_alert(
        self,
        market_id: str,
        reason: str,
        pnl: float,
        pnl_pct: float,
    ) -> None: ...

    @abstractmethod
    async def send_risk_alert(
        self,
        rule_triggered: str,
        reason: str,
    ) -> None: ...

    @abstractmethod
    async def send_error_alert(self, error: str) -> None: ...
