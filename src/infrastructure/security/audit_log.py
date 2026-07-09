# src/infrastructure/security/audit_log.py

from datetime import datetime, timezone
from enum import Enum

import structlog

from src.application.ports.repository_port import IRepositoryPort

logger = structlog.get_logger(__name__)


class AuditAction(str, Enum):
    REAL_ORDER_ATTEMPT  = "real_order_attempt"
    REAL_ORDER_SUCCESS  = "real_order_success"
    REAL_ORDER_FAILED   = "real_order_failed"
    REAL_ORDER_RETRY    = "real_order_retry"
    REAL_EXIT_ATTEMPT   = "real_exit_attempt"
    REAL_EXIT_SUCCESS   = "real_exit_success"
    REAL_REDEEM_ATTEMPT = "real_redeem_attempt"
    REAL_REDEEM_SUCCESS = "real_redeem_success"
    REAL_REDEEM_FAILED  = "real_redeem_failed"
    REAL_TRADING_ENABLED  = "real_trading_enabled"
    REAL_TRADING_DISABLED = "real_trading_disabled"
    GUARDRAIL_TRIGGERED = "guardrail_triggered"
    # ── R2.0-redeem-impl F1: CTF on-chain redeem flow ───────────────
    CTF_REDEEM_TX_SUBMITTED = "ctf_redeem_tx_submitted"
    CTF_REDEEM_TX_MINED     = "ctf_redeem_tx_mined"
    CTF_REDEEM_TX_CONFIRMED = "ctf_redeem_tx_confirmed"
    CTF_REDEEM_FAILED       = "ctf_redeem_failed"
    CTF_REDEEM_REPLACED     = "ctf_redeem_replaced"
    CTF_REDEEM_RECONCILED   = "ctf_redeem_reconciled"
    CTF_REDEEM_MATIC_FUNDED = "ctf_redeem_matic_funded"


class AuditLogger:
    """
    Genera entradas de auditoría inmutables para todas las operaciones reales.
    Escribe en structlog (archivo + stdout) Y persiste en DB para trazabilidad.
    Las entradas de auditoría NUNCA se borran ni modifican.
    """

    def __init__(self, repository: IRepositoryPort):
        self._repo = repository

    async def log(
        self,
        action:    AuditAction,
        details:   dict,
        order_id:  str | None = None,
        market_id: str | None = None,
        amount:    float | None = None,
    ) -> None:
        """
        Registra una acción de auditoría.
        Doble escritura: structlog (stdout/archivo) + DB.
        """
        entry = {
            "audit_action": action.value,
            "order_id":     order_id,
            "market_id":    market_id,
            "amount":       amount,
            "timestamp":    datetime.now(timezone.utc).isoformat(),
            **details,
        }

        # Escritura en structlog (siempre, incluso si DB falla)
        logger.info("AUDIT", **entry)

        # Persistencia en DB (best-effort, no bloquea la operación)
        try:
            await self._repo.save_audit_log(entry)
        except Exception as e:
            # Si la DB falla, el log de structlog ya está guardado
            logger.error(
                "audit_db_write_failed",
                error=str(e),
                audit_action=action.value,
            )
