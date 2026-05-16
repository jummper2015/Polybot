# src/infrastructure/security/security_guard.py

import structlog
from dataclasses import dataclass
from datetime import datetime, timezone

from src.infrastructure.security.key_manager import KeyManager
from src.infrastructure.security.rate_limiter import RateLimiter
from src.infrastructure.security.secure_config import SecureConfig
from src.infrastructure.security.audit_log import AuditLogger, AuditAction
from src.infrastructure.observability.metrics import (
    SECURITY_GUARDRAIL_TRIGGERED,
    SECURITY_RATE_LIMIT_BLOCKED,
)

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class SecurityCheckResult:
    """Resultado de una verificación de seguridad pre-operación."""
    passed:   bool
    reason:   str
    checks:   dict[str, bool]   # Resultado de cada check individual


class SecurityGuard:
    """
    Punto central de validación de seguridad.
    Se invoca ANTES de cualquier operación real.
    Combina: guardrails + rate limit + key validation + modo check.

    Jerarquía de checks (orden de evaluación):
    1. Modo de trading verificado (no operar real en modo paper)
    2. Key Manager disponible y claves presentes
    3. Rate limit no excedido
    4. Amount dentro de guardrails hardcoded
    5. Market ID válido
    """

    # Guardrails absolutos — NO modificar
    HARD_MAX_AMOUNT_USDC = 500.0
    HARD_MIN_AMOUNT_USDC = 1.0

    def __init__(
        self,
        config:       SecureConfig,
        key_manager:  KeyManager | None,   # None en paper trading
        rate_limiter: RateLimiter,
        audit_logger: AuditLogger,
    ):
        self._config       = config
        self._keys         = key_manager
        self._rate_limiter = rate_limiter
        self._audit        = audit_logger

    # ------------------------------------------------------------------
    # CHECK PRE-OPERACIÓN REAL
    # ------------------------------------------------------------------

    async def check_real_order(
        self,
        order_id:  str,
        market_id: str,
        amount:    float,
        side:      str,
    ) -> SecurityCheckResult:
        """
        Ejecuta todos los checks de seguridad antes de una orden real.
        Si cualquier check falla, la operación es bloqueada.
        """
        checks = {}

        # ── Check 1: Modo de trading ──────────────────────────────────
        checks["trading_mode_real"] = self._config.trading_mode == "real"
        if not checks["trading_mode_real"]:
            reason = "SECURITY: intento de orden real en modo paper"
            await self._audit.log(
                action=AuditAction.GUARDRAIL_TRIGGERED,
                details={"check": "trading_mode", "reason": reason},
                order_id=order_id,
                market_id=market_id,
                amount=amount,
            )
            SECURITY_GUARDRAIL_TRIGGERED.labels(check="trading_mode").inc()
            return SecurityCheckResult(passed=False, reason=reason, checks=checks)

        # ── Check 2: Key Manager disponible ──────────────────────────
        checks["keys_available"] = (
            self._keys is not None and self._keys.is_real_trading_ready()
        )
        if not checks["keys_available"]:
            reason = "SECURITY: claves de wallet no disponibles o incompletas"
            SECURITY_GUARDRAIL_TRIGGERED.labels(check="keys_available").inc()
            return SecurityCheckResult(passed=False, reason=reason, checks=checks)

        # ── Check 3: Rate limit ───────────────────────────────────────
        rate_ok, rate_reason = await self._rate_limiter.check_and_record(
            order_id=order_id,
            market_id=market_id,
        )
        checks["rate_limit"] = rate_ok
        if not rate_ok:
            SECURITY_RATE_LIMIT_BLOCKED.inc()
            return SecurityCheckResult(
                passed=False, reason=rate_reason, checks=checks
            )

        # ── Check 4: Amount guardrails ────────────────────────────────
        checks["amount_valid"] = (
            self.HARD_MIN_AMOUNT_USDC <= amount <= self.HARD_MAX_AMOUNT_USDC
        )
        if not checks["amount_valid"]:
            reason = (
                f"SECURITY: amount={amount:.2f} USDC fuera de guardrails "
                f"[{self.HARD_MIN_AMOUNT_USDC:.0f}, {self.HARD_MAX_AMOUNT_USDC:.0f}]"
            )
            await self._audit.log(
                action=AuditAction.GUARDRAIL_TRIGGERED,
                details={"check": "amount_bounds", "amount": amount},
                order_id=order_id,
                market_id=market_id,
                amount=amount,
            )
            SECURITY_GUARDRAIL_TRIGGERED.labels(check="amount_bounds").inc()
            return SecurityCheckResult(passed=False, reason=reason, checks=checks)

        # ── Check 5: Market ID válido ─────────────────────────────────
        checks["market_id_valid"] = (
            bool(market_id) and len(market_id) >= 10
        )
        if not checks["market_id_valid"]:
            reason = f"SECURITY: market_id inválido: '{market_id}'"
            SECURITY_GUARDRAIL_TRIGGERED.labels(check="market_id").inc()
            return SecurityCheckResult(passed=False, reason=reason, checks=checks)

        # ── Todos los checks pasaron ──────────────────────────────────
        logger.info(
            "security_check_passed",
            order_id=order_id,
            market_id=market_id,
            amount=amount,
            side=side,
            rate_remaining=await self._rate_limiter.get_remaining(),
        )

        return SecurityCheckResult(
            passed=True,
            reason="all_security_checks_passed",
            checks=checks,
        )

    # ------------------------------------------------------------------
    # CHECKLIST PRE-ACTIVACIÓN DE REAL TRADING
    # ------------------------------------------------------------------

    async def run_activation_checklist(self) -> dict[str, bool]:
        """
        Checklist completo que se ejecuta antes de activar real trading.
        Devuelve un dict con el resultado de cada verificación.
        El Telegram bot muestra este checklist al usuario.
        """
        checklist = {}

        # 1. Variables de entorno presentes
        try:
            from src.infrastructure.security.secure_config import REQUIRED_REAL
            import os
            checklist["env_vars_present"] = all(
                os.environ.get(k) for k in REQUIRED_REAL
            )
        except Exception:
            checklist["env_vars_present"] = False

        # 2. Key Manager funcional
        checklist["key_manager_ready"] = (
            self._keys is not None and self._keys.is_real_trading_ready()
        )

        # 3. Rate limit no agotado
        remaining = await self._rate_limiter.get_remaining()
        checklist["rate_limit_available"] = remaining > 0

        # 4. Modo configurado como real
        checklist["mode_is_real"] = self._config.trading_mode == "real"

        # 5. Balance mínimo configurado razonablemente
        checklist["min_balance_configured"] = (
            self._config.risk_min_balance_usdc >= 10.0
        )

        # 6. Position size dentro de guardrails
        checklist["position_size_safe"] = (
            self.HARD_MIN_AMOUNT_USDC
            <= self._config.bat_position_size_usdc
            <= self.HARD_MAX_AMOUNT_USDC
        )

        all_passed = all(checklist.values())

        await self._audit.log(
            action=AuditAction.REAL_TRADING_ENABLED if all_passed
                   else AuditAction.GUARDRAIL_TRIGGERED,
            details={
                "checklist": checklist,
                "all_passed": all_passed,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )

        logger.info(
            "activation_checklist_result",
            all_passed=all_passed,
            checklist=checklist,
        )

        return checklist

    # ------------------------------------------------------------------
    # UTILIDADES DE ESTADO
    # ------------------------------------------------------------------

    async def get_security_status(self) -> dict:
        """
        Estado de seguridad para el health check y Telegram /status.
        Sin valores sensibles.
        """
        return {
            "trading_mode":         self._config.trading_mode,
            "keys_ready":           self._keys.is_real_trading_ready()
                                    if self._keys else False,
            "rate_limit_remaining": await self._rate_limiter.get_remaining(),
            "rate_limit_max":       10,
            "guardrail_max_usdc":   self.HARD_MAX_AMOUNT_USDC,
            "guardrail_min_usdc":   self.HARD_MIN_AMOUNT_USDC,
        }