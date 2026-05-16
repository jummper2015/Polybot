# src/interfaces/telegram/handlers/alerts.py

import structlog
from aiogram import Bot
from aiogram.enums import ParseMode

from src.application.ports.notification_port import INotificationPort

logger = structlog.get_logger(__name__)


class TelegramNotifier(INotificationPort):
    """
    Implementación de INotificationPort usando el Bot de Telegram.
    Envía alertas automáticas al chat autorizado.
    Implementa el contrato definido en B5 — la capa de aplicación
    solo conoce INotificationPort, nunca esta clase directamente.
    """

    def __init__(self, bot: Bot, chat_id: int):
        self._bot     = bot
        self._chat_id = chat_id

    async def send_trade_alert(
        self,
        market_id: str,
        side:      str,
        amount:    float,
        price:     float,
        mode:      str,
    ) -> None:
        """
        Alerta de nueva posición abierta.
        Diferencia visualmente paper vs real.
        """
        mode_emoji = "🔴 REAL" if mode == "real" else "📋 PAPER"
        side_emoji = "📈 YES"  if side == "YES"  else "📉 NO"

        text = (
            f"{mode_emoji} *Nueva Posición*\n\n"
            f"Mercado: `{self._escape(market_id[:20])}\\.\\.\\. `\n"
            f"Lado: {side_emoji}\n"
            f"Monto: `{amount:.2f} USDC`\n"
            f"Precio: `{price:.4f}`\n"
        )
        await self._send(text)

    async def send_exit_alert(
        self,
        market_id: str,
        reason:    str,
        pnl:       float,
        pnl_pct:   float,
    ) -> None:
        """
        Alerta de posición cerrada con PnL.
        Usa emoji verde/rojo según el resultado.
        """
        pnl_emoji = "✅" if pnl >= 0 else "❌"
        sign      = "\\+" if pnl >= 0 else ""

        text = (
            f"{pnl_emoji} *Posición Cerrada*\n\n"
            f"Mercado: `{self._escape(market_id[:20])}\\.\\.\\. `\n"
            f"Razón: `{self._escape(reason)}`\n"
            f"PnL: `{sign}{pnl:.4f} USDC`\n"
            f"PnL: `{sign}{pnl_pct:.2%}`\n"
        )
        await self._send(text)

    async def send_risk_alert(
        self,
        rule_triggered: str,
        reason:         str,
    ) -> None:
        """
        Alerta cuando el RiskEngine bloquea una operación.
        Solo para reglas de alta prioridad (MinBalance, Drawdown).
        """
        text = (
            f"⚠️ *Riesgo Bloqueado*\n\n"
            f"Regla: `{self._escape(rule_triggered)}`\n"
            f"Motivo: _{self._escape(reason)}_\n"
        )
        await self._send(text)

    async def send_error_alert(self, error: str) -> None:
        """Alerta de error crítico del sistema."""
        text = (
            f"🚨 *Error Crítico*\n\n"
            f"`{self._escape(error[:300])}`\n\n"
            f"_Revisa los logs para más detalles\\._"
        )
        await self._send(text)

    async def send_daily_summary(
        self,
        pnl:       float,
        pnl_pct:   float,
        trades:    int,
        win_rate:  float,
        balance:   float,
    ) -> None:
        """
        Resumen diario automático enviado a medianoche UTC.
        """
        pnl_emoji = "📈" if pnl >= 0 else "📉"
        sign      = "\\+" if pnl >= 0 else ""

        text = (
            f"{pnl_emoji} *Resumen Diario*\n\n"
            f"💵 Balance: `{balance:.2f} USDC`\n"
            f"📊 PnL: `{sign}{pnl:.4f} USDC \\({sign}{pnl_pct:.2%}\\)`\n"
            f"🔢 Trades: `{trades}`\n"
            f"🎯 Win Rate: `{win_rate:.1%}`\n"
        )
        await self._send(text)

    # ------------------------------------------------------------------
    # HELPERS
    # ------------------------------------------------------------------

    async def _send(self, text: str) -> None:
        """
        Envía un mensaje al chat autorizado.
        Captura errores de Telegram sin propagar — las alertas
        no deben interrumpir el ciclo de trading.
        """
        try:
            await self._bot.send_message(
                chat_id=self._chat_id,
                text=text,
                parse_mode=ParseMode.MARKDOWN_V2,
            )
        except Exception as e:
            logger.error(
                "telegram_send_failed",
                error=str(e),
                chat_id=self._chat_id,
            )

    @staticmethod
    def _escape(text: str) -> str:
        """
        Escapa caracteres especiales para MarkdownV2 de Telegram.
        Obligatorio para evitar errores de parsing.
        """
        special = r"\_*[]()~`>#+-=|{}.!"
        return "".join(f"\\{c}" if c in special else c for c in str(text))