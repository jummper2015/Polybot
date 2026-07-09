# src/execution/real_handler.py

import asyncio
import hashlib
import uuid
from datetime import datetime, timezone

import httpx
import structlog

from src.application.ports.notification_port import INotificationPort
from src.application.ports.repository_port import IRepositoryPort
from src.domain.entities.order import Order
from src.domain.entities.position import Position
from src.domain.enums.finality_status import FinalityStatus
from src.domain.enums.order_side import OrderSide
from src.domain.enums.order_status import OrderStatus
from src.domain.enums.trading_mode import TradingMode
from src.domain.exceptions.ctf_exceptions import CTFRedeemError
from src.domain.value_objects.redeem_receipt import RedeemReceipt
from src.domain.value_objects.signal import Signal, SignalType
from src.domain.value_objects.trade_result import TradeResult
from src.execution.base import IExecutionHandler
from src.infrastructure.cache.redis_client import RedisClient
from src.infrastructure.observability.metrics import (
    ORDERS_EXECUTED,
    PNL_GAUGE,
    REAL_ORDER_ERRORS,
    REAL_ORDER_RETRIES,
)
from src.infrastructure.observability.tracing import get_tracer
from src.infrastructure.polymarket.clob_client import (
    CLOBRedeemNotSupportedError,
    PolymarketCLOBClient,
)
from src.infrastructure.security.audit_log import AuditAction, AuditLogger
from src.infrastructure.security.circuit_breaker import (
    CLOBCircuitBreaker,
)
from src.infrastructure.security.security_guard import SecurityGuard

logger = structlog.get_logger(__name__)

# ── Guardrails hardcoded (NO son configurables) ───────────────────────
# CLOB V2 (abril 2026): colateral migrado de USDC.e → pUSD (Polymarket USD)
MAX_ORDER_AMOUNT_PUSD = 500.0    # Nunca más de 500 pUSD por orden
MIN_ORDER_AMOUNT_PUSD = 1.0     # Nunca menos de 1 pUSD por orden
DEFAULT_TICK_SIZE     = "0.01"   # Tick size default (se pisa con market_info)

# ── Configuración de retry ────────────────────────────────────────────
MAX_RETRIES       = 3
RETRY_BACKOFF     = [1.0, 2.0, 4.0]  # Segundos entre intentos

# Errores HTTP que son retryables:
# - 408 Request Timeout, 425 Too Early (matching engine restart),
# - 429 Rate Limited, 5xx (server errors)
RETRYABLE_STATUS  = {408, 425, 429, 500, 502, 503, 504}

TRADING_MODE = TradingMode.REAL


class RealTradingHandler(IExecutionHandler):
    """
    Ejecuta órdenes reales on-chain en Polymarket via CLOB V2 API.
    Implementa el mismo contrato IExecutionHandler que PaperTradingHandler.

    CLOB V2 (abril 2026): colateral pUSD, EIP-712 domain V2, sin nonces.

    GARANTÍAS DE SEGURIDAD:
    - Guardrails hardcoded que no pueden superarse por config
    - Toda operación genera audit log inmutable
    - Idempotency key SHA256 determinista: mismo signal en el mismo minuto = misma key
    - Circuit breaker: 5 fallos en 60s → bloquea llamadas al CLOB por 60s
    - Retry solo en errores de red (no en 4xx de lógica)
    - Doble confirmación requerida antes de activar este handler
    """

    def __init__(
        self,
        clob_client:      PolymarketCLOBClient,
        repository:       IRepositoryPort,
        redis:            RedisClient,
        notifier:         INotificationPort,
        audit_logger:     AuditLogger,
        security_guard:   SecurityGuard | None = None,
        circuit_breaker:  CLOBCircuitBreaker | None = None,
    ):
        self._clob      = clob_client
        self._repo      = repository
        self._redis     = redis
        self._notifier  = notifier
        self._audit     = audit_logger
        self._security  = security_guard
        self._circuit   = circuit_breaker

        logger.info(
            "real_handler_initialized",
            max_order_usdc=MAX_ORDER_AMOUNT_PUSD,
            max_retries=MAX_RETRIES,
            circuit_breaker=circuit_breaker is not None,
        )

    # ------------------------------------------------------------------
    # IExecutionHandler — Implementación Real
    # ------------------------------------------------------------------

    async def execute_entry(
        self,
        signal:    Signal,
        market_id: str,
        amount:    float,
    ) -> TradeResult:
        """
        Ejecuta una compra real en Polymarket.
        Flujo: Guardrails → Audit → API call con retry → Persistencia

        Instrumentado con OpenTelemetry: span raíz con atributos de orden.
        """
        log = logger.bind(
            market_id=market_id,
            signal_type=signal.type.value,
            amount=amount,
            mode="real",
        )
        tracer = get_tracer()

        with tracer.start_as_current_span("execution.entry") as entry_span:
            entry_span.set_attribute("execution.mode", "real")
            entry_span.set_attribute("execution.market_id", market_id)
            entry_span.set_attribute("execution.amount", str(amount))
            entry_span.set_attribute("execution.side", signal.type.value)

        # ── SecurityGuard ANTES de cualquier operación ────────────────
        if self._security is not None:
            security_result = await self._security.check_real_order(
                order_id=str(uuid.uuid4()),
                market_id=market_id,
                amount=amount,
                side=signal.type.value,
            )

            if not security_result.passed:
                logger.warning(
                    "real_order_blocked_by_security",
                    reason=security_result.reason,
                    checks=security_result.checks,
                )
                entry_span.set_attribute("error", security_result.reason[:200])
                entry_span.set_attribute("execution.success", "false")
                return self._failed_result(
                    market_id, signal, amount, 0.0,
                    security_result.reason
                )

        # ── GUARDRAILS (hardcoded, inamovibles) ───────────────────────
        guardrail_result = self._apply_guardrails(amount, market_id)
        if guardrail_result is not None:
            # Guardrail disparado → denegar y auditar
            await self._audit.log(
                action=AuditAction.GUARDRAIL_TRIGGERED,
                details={"reason": guardrail_result, "signal": signal.type.value},
                market_id=market_id,
                amount=amount,
            )
            entry_span.set_attribute("error", guardrail_result[:200])
            entry_span.set_attribute("execution.success", "false")
            return self._failed_result(
                market_id, signal, amount, 0.0, guardrail_result
            )

        # Ajusta al máximo permitido si excede (no debería por RiskEngine)
        safe_amount = min(amount, MAX_ORDER_AMOUNT_PUSD)

        # ── Obtiene token ID del mercado ──────────────────────────────
        token_id, target_price = await self._get_token_and_price(
            market_id, signal.type
        )

        # ── Genera order_id determinista ANTES de llamar a la API ────
        # Idempotencia SHA256: mismo signal en el mismo minuto = misma key
        # Si el request falla a mitad, el reintento enviará la misma key
        # y Polymarket no duplicará la orden
        order_id = self._generate_idempotency_key(
            strategy_name=signal.source_strategy,
            market_id=market_id,
        )

        # ── Audit: intento de orden ───────────────────────────────────
        await self._audit.log(
            action=AuditAction.REAL_ORDER_ATTEMPT,
            details={
                "signal_type":   signal.type.value,
                "target_price":  target_price,
                "strategy":      signal.source_strategy,
                "reason":        signal.reason,
            },
            order_id=order_id,
            market_id=market_id,
            amount=safe_amount,
        )

        # ── Resolver tick_size y neg_risk desde Redis metadata ────────
        market_meta = await self._redis.get_market_metadata(market_id)
        order_tick_size = str(
            market_meta.get("tick_size", DEFAULT_TICK_SIZE)
        )
        order_neg_risk = bool(market_meta.get("neg_risk", False))
        market_mos = float(
            market_meta.get("min_order_size", MIN_ORDER_AMOUNT_PUSD)
        )

        # ── MOS guardrail: usar el max del hardcoded y el del mercado ──
        effective_min = max(MIN_ORDER_AMOUNT_PUSD, market_mos)
        mos_result = self._apply_mos_guardrail(safe_amount, effective_min, market_id)
        if mos_result is not None:
            await self._audit.log(
                action=AuditAction.GUARDRAIL_TRIGGERED,
                details={"reason": mos_result, "signal": signal.type.value},
                market_id=market_id,
                amount=safe_amount,
            )
            entry_span.set_attribute("error", mos_result[:200])
            entry_span.set_attribute("execution.success", "false")
            return self._failed_result(
                market_id, signal, safe_amount, 0.0, mos_result
            )

        # ── Crea la orden en DB (estado PENDING antes de la API call) ─
        order = Order(
            id              = str(uuid.uuid4()),
            market_id       = market_id,
            side            = OrderSide.YES if signal.type == SignalType.BUY_YES
                              else OrderSide.NO,
            amount          = safe_amount,
            target_price    = target_price,
            fill_price      = None,
            slippage        = None,
            status          = OrderStatus.PENDING,
            mode            = TRADING_MODE,
            strategy        = signal.source_strategy,
            reason          = signal.reason,
            idempotency_key = order_id,
        )
        await self._repo.save_order(order)
        log.info("real_order_pending", order_id=order_id)

        # ── Llama a la API con retry ──────────────────────────────────
        api_response, error = await self._call_with_retry(
            operation="create_order",
            order_id=order_id,
            fn=lambda: self._clob.create_order(
                token_id=token_id,
                side="BUY",
                price=target_price,
                size=safe_amount,
                order_id=order_id,
                tick_size=order_tick_size,
                neg_risk=order_neg_risk,
            ),
            log=log,
        )

        if error:
            # Fallo definitivo después de todos los reintentos
            order.mark_failed(error)
            await self._repo.save_order(order)

            await self._audit.log(
                action=AuditAction.REAL_ORDER_FAILED,
                details={"error": error},
                order_id=order_id,
                market_id=market_id,
                amount=safe_amount,
            )
            REAL_ORDER_ERRORS.labels(market_id=market_id).inc()
            entry_span.set_attribute("error", error[:200])
            entry_span.set_attribute("execution.success", "false")
            return self._failed_result(
                market_id, signal, safe_amount, target_price, error
            )

        # ── Procesa respuesta exitosa ─────────────────────────────────
        fill_price = float(api_response.get("price", target_price))
        slippage   = round(fill_price - target_price, 4)

        order.mark_filled(fill_price=fill_price, slippage=slippage)
        await self._repo.save_order(order)

        # ── Crea posición en DB ───────────────────────────────────────
        position = await self._create_real_position(order, market_id)
        await self._repo.save_position(position)

        # ── Audit: orden exitosa ──────────────────────────────────────
        await self._audit.log(
            action=AuditAction.REAL_ORDER_SUCCESS,
            details={
                "fill_price": fill_price,
                "slippage":   slippage,
                "shares":     order.shares,
            },
            order_id=order_id,
            market_id=market_id,
            amount=safe_amount,
        )

        ORDERS_EXECUTED.labels(mode="real", side=order.side.value).inc()

        entry_span.set_attribute("execution.success", "true")
        entry_span.set_attribute("execution.order_id", order_id)
        entry_span.set_attribute("execution.fill_price", str(fill_price))
        entry_span.set_attribute("execution.slippage", str(slippage))

        log.info(
            "real_order_filled",
            order_id=order_id,
            fill_price=fill_price,
            slippage=slippage,
            shares=round(order.shares, 4),
        )

        # ── Notifica al usuario ───────────────────────────────────────
        if self._notifier is not None:
            await self._notifier.send_trade_alert(
                market_id=market_id,
                side=order.side.value,
                amount=safe_amount,
                price=fill_price,
                mode="real",
            )

        return TradeResult(
            order_id     = order_id,
            market_id    = market_id,
            side         = order.side.value,
            amount       = safe_amount,
            target_price = target_price,
            fill_price   = fill_price,
            slippage     = slippage,
            pnl          = None,
            success      = True,
            mode         = "real",
            timestamp    = datetime.now(timezone.utc),
        )

    async def execute_exit(
        self,
        position: Position,
        reason:   str,
    ) -> TradeResult:
        """
        Vende la posición existente en Polymarket.
        En mercados de predicción: vender = crear orden opuesta al precio actual.

        Instrumentado con OpenTelemetry: span con atributos de salida.
        """
        log = logger.bind(
            position_id=position.id,
            market_id=position.market_id,
            reason=reason,
            mode="real",
        )
        tracer = get_tracer()

        with tracer.start_as_current_span("execution.exit") as exit_span:
            exit_span.set_attribute("execution.mode", "real")
            exit_span.set_attribute("execution.market_id", position.market_id)
            exit_span.set_attribute("execution.position_id", position.id)
            exit_span.set_attribute("execution.exit_reason", reason)

        current_price = await self._get_current_price(position.market_id)
        order_id      = self._generate_idempotency_key(
            strategy_name=position.strategy,
            market_id=position.market_id,
        )

        await self._audit.log(
            action=AuditAction.REAL_EXIT_ATTEMPT,
            details={
                "reason":        reason,
                "current_price": current_price,
                "entry_price":   position.entry_price,
                "shares":        position.shares,
            },
            order_id=order_id,
            market_id=position.market_id,
            amount=position.amount,
        )

        # Obtiene el token ID del lado opuesto para vender
        token_id, _ = await self._get_token_and_price(
            position.market_id,
            # Para salir de YES: vendemos creando orden SELL YES
            SignalType.BUY_YES if position.side == "YES" else SignalType.BUY_NO,
        )

        # ── Resolver tick_size y neg_risk para exit ───────────────────
        market_meta = await self._redis.get_market_metadata(position.market_id)
        exit_tick_size = market_meta.get("tick_size", DEFAULT_TICK_SIZE)
        exit_neg_risk = market_meta.get("neg_risk", False)

        api_response, error = await self._call_with_retry(
            operation="exit_order",
            order_id=order_id,
            fn=lambda: self._clob.create_order(
                token_id=token_id,
                side="SELL",
                price=current_price,
                size=position.shares,  # Vendemos todas las shares
                order_id=order_id,
                tick_size=exit_tick_size,
                neg_risk=exit_neg_risk,
            ),
            log=log,
        )

        if error:
            await self._audit.log(
                action=AuditAction.REAL_ORDER_FAILED,
                details={"error": error, "operation": "exit"},
                order_id=order_id,
                market_id=position.market_id,
            )
            log.error("real_exit_failed", error=error)
            return self._failed_result(
                position.market_id, None, position.amount,
                current_price, error
            )

        exit_price = float(api_response.get("price", current_price))
        slippage   = round(exit_price - current_price, 4)

        position.close(exit_price=exit_price, reason=reason)
        await self._repo.save_position(position)

        await self._audit.log(
            action=AuditAction.REAL_EXIT_SUCCESS,
            details={
                "exit_price": exit_price,
                "pnl":        position.pnl,
                "pnl_pct":    position.pnl_pct,
                "reason":     reason,
            },
            order_id=order_id,
            market_id=position.market_id,
            amount=position.amount,
        )

        PNL_GAUGE.labels(mode="real").inc(position.pnl or 0)
        ORDERS_EXECUTED.labels(mode="real", side="EXIT").inc()

        exit_span.set_attribute("execution.success", "true")
        exit_span.set_attribute("execution.exit_price", str(exit_price))
        if position.pnl is not None:
            exit_span.set_attribute("execution.pnl", str(position.pnl))

        log.info(
            "real_exit_success",
            exit_price=exit_price,
            pnl=round(position.pnl, 4),
            pnl_pct=f"{position.pnl_pct:.2%}",
        )

        if self._notifier is not None:
            await self._notifier.send_exit_alert(
                market_id=position.market_id,
                reason=reason,
                pnl=position.pnl,
                pnl_pct=position.pnl_pct,
            )

        return TradeResult(
            order_id     = order_id,
            market_id    = position.market_id,
            side         = position.side,
            amount       = position.amount,
            target_price = current_price,
            fill_price   = exit_price,
            slippage     = slippage,
            pnl          = position.pnl,
            success      = True,
            mode         = "real",
            timestamp    = datetime.now(timezone.utc),
        )

    async def execute_hedge(
        self,
        position:     Position,
        hedge_amount: float,
    ) -> TradeResult:
        """
        Compra tokens NO como cobertura parcial de una posición YES.
        Mismo flujo que execute_entry pero con SignalType.BUY_NO.
        """
        from src.domain.value_objects.signal import Signal

        hedge_signal = Signal(
            type=SignalType.BUY_NO,
            market_id=position.market_id,
            confidence=1.0,
            source_strategy=position.strategy,
            reason=f"hedge for position {position.id}",
            timestamp=datetime.now(timezone.utc),
        )
        return await self.execute_entry(
            signal=hedge_signal,
            market_id=position.market_id,
            amount=min(hedge_amount, MAX_ORDER_AMOUNT_PUSD),
        )

    # ------------------------------------------------------------------
    # REDEEM AL VENCIMIENTO
    # ------------------------------------------------------------------

    async def redeem_resolved_position(
        self,
        position:  Position,
        token_id:  str,
    ) -> TradeResult:
        """
        Redime tokens ganadores después de la resolución del mercado.
        Llamado por un job externo que detecta mercados resueltos.
        Solo tiene efecto si ganamos — si perdemos, los tokens valen 0.

        CLOB V2: redeem es on-chain via CTF. Si `ctf_redeemer` disponible,
        ejecuta redeem atómico on-chain con auditoría CTF_REDEEM_TX_*.
        Si no disponible, falla rápido con REAL_REDEEM_FAILED reason=ctf_onchain_required.
        """
        log = logger.bind(
            position_id=position.id,
            market_id=position.market_id,
            mode="real",
        )

        await self._audit.log(
            action=AuditAction.REAL_REDEEM_ATTEMPT,
            details={"token_id": token_id, "shares": position.shares},
            market_id=position.market_id,
            amount=position.amount,
        )

        # Genera order_id determinista para redeem (mismo patrón idempotencia)
        redeem_order_id = self._generate_idempotency_key(
            strategy_name="redeem",
            market_id=position.market_id,
        )

        # ── Path CTF on-chain (R2.0-redeem-impl F1) ────────────────────
        if self._clob.ctf_redeemer is not None:
            return await self._redeem_via_ctf(
                position=position,
                token_id=token_id,
                redeem_order_id=redeem_order_id,
                log=log,
            )

        # ── Path legacy (fail-fast sin CTF) ────────────────────────────
        try:
            api_response, error = await self._call_with_retry(
                operation="redeem",
                order_id=redeem_order_id,
                fn=lambda: self._clob.redeem_position(
                    token_id=token_id,
                    market_id=position.market_id,
                ),
                log=log,
            )
        except CLOBRedeemNotSupportedError as exc:
            # Defensa en profundidad: si por cualquier razón
            # CLOBRedeemNotSupportedError escapa del _call_with_retry,
            # la capturamos aquí. El camino normal la convierte en
            # (None, error_msg) via el `except NotImplementedError`.
            error = str(exc) or type(exc).__name__
            api_response = None

        if error:
            reason = (
                "ctf_onchain_required"
                if "CTF" in error or "redeemPositions" in error
                else "redeem_failed"
            )
            log.error("redeem_failed", error=error, reason=reason)
            await self._audit.log(
                action=AuditAction.REAL_REDEEM_FAILED,
                details={"reason": reason, "error": error[:200]},
                market_id=position.market_id,
                amount=position.amount,
            )
            return self._failed_result(
                position.market_id, None,
                position.amount, 0.0, error
            )

        # Valor redimido: shares * 1.0 si ganamos (cada token ganador = 1 pUSD)
        redeemed_pusd = float(api_response.get("redeemed_amount", 0.0))

        position.close(
            exit_price=redeemed_pusd / position.shares if position.shares > 0 else 0,
            reason="market_resolved_redeemed",
        )
        await self._repo.save_position(position)

        await self._audit.log(
            action=AuditAction.REAL_REDEEM_SUCCESS,
            details={"redeemed_pusd": redeemed_pusd},
            market_id=position.market_id,
            amount=redeemed_pusd,
        )

        log.info(
            "redeem_success",
            redeemed_pusd=redeemed_pusd,
            pnl=position.pnl,
        )

        return TradeResult(
            order_id     = redeem_order_id,
            market_id    = position.market_id,
            side         = position.side,
            amount       = redeemed_pusd,
            target_price = 1.0,
            fill_price   = 1.0,
            slippage     = 0.0,
            pnl          = position.pnl,
            success      = True,
            mode         = "real",
            timestamp    = datetime.now(timezone.utc),
        )

    async def _redeem_via_ctf(
        self,
        position:        Position,
        token_id:        str,
        redeem_order_id: str,
        log,
    ) -> TradeResult:
        """
        Ejecuta redeem on-chain via CTFRedeemer (R2.0-redeem-impl F1).

        Mapea Position.side ("YES"/"NO") + shares → shares_yes/shares_no,
        llama CTFRedeemer.redeem, espera finality, emite audits CTF_REDEEM_TX_*.
        """
        redeemer = self._clob.ctf_redeemer
        if redeemer is None:
            # Defensive — el caller ya debería haber chequeado esto
            raise CTFRedeemError("ctf_redeemer not available")

        # ── Mapeo side → shares_yes/shares_no ─────────────────────────
        shares_yes = int(position.shares) if position.side == "YES" else 0
        shares_no  = int(position.shares) if position.side == "NO"  else 0

        # ── Redeem on-chain (preflight + tx + finality) ───────────────
        try:
            receipt: RedeemReceipt = await redeemer.redeem(
                condition_id=position.market_id,
                shares_yes=shares_yes,
                shares_no=shares_no,
                redeem_op_id=redeem_order_id,
            )
        except CTFRedeemError as e:
            log.error("ctf_redeem_error", error=str(e), redeem_op_id=redeem_order_id)
            await self._audit.log(
                action=AuditAction.CTF_REDEEM_FAILED,
                details={"error": str(e)[:200], "redeem_op_id": redeem_order_id},
                market_id=position.market_id,
                amount=position.amount,
            )
            return self._failed_result(
                position.market_id, None,
                position.amount, 0.0, str(e)
            )

        # ── Audit según el status de finality ─────────────────────────
        if receipt.tx_hash:
            await self._audit.log(
                action=AuditAction.CTF_REDEEM_TX_SUBMITTED,
                details={
                    "tx_hash": receipt.tx_hash,
                    "redeem_op_id": redeem_order_id,
                    "condition_id": receipt.condition_id[-12:],
                },
                market_id=position.market_id,
                amount=position.amount,
            )

        if receipt.status == FinalityStatus.CONFIRMED.value and receipt.confirmed_at:
            await self._audit.log(
                action=AuditAction.CTF_REDEEM_TX_CONFIRMED,
                details={
                    "tx_hash": receipt.tx_hash,
                    "pusd_received": receipt.pusd_received,
                    "gas_used": receipt.gas_used,
                    "gas_fee_matic": receipt.gas_fee_matic,
                },
                market_id=position.market_id,
                amount=receipt.pusd_received,
            )
        elif receipt.status in (FinalityStatus.MINED.value, FinalityStatus.SUBMITTED.value):
            await self._audit.log(
                action=AuditAction.CTF_REDEEM_TX_MINED,
                details={
                    "tx_hash": receipt.tx_hash,
                    "status": receipt.status,
                    "gas_used": receipt.gas_used,
                },
                market_id=position.market_id,
                amount=position.amount,
            )
        elif receipt.status in (FinalityStatus.FAILED.value, FinalityStatus.TIMEOUT.value, FinalityStatus.REORGED.value):
            await self._audit.log(
                action=AuditAction.CTF_REDEEM_FAILED,
                details={
                    "tx_hash": receipt.tx_hash,
                    "status": receipt.status,
                    "redeem_op_id": redeem_order_id,
                },
                market_id=position.market_id,
                amount=position.amount,
            )
            return self._failed_result(
                position.market_id, None,
                position.amount, 0.0, f"Redeem {receipt.status}"
            )

        # ── Cierra position con el pUSD repatriado ────────────────────
        redeemed_pusd = receipt.pusd_received
        position.close(
            exit_price=redeemed_pusd / position.shares if position.shares > 0 else 0,
            reason="market_resolved_ctf_redeemed",
        )
        await self._repo.save_position(position)

        await self._audit.log(
            action=AuditAction.REAL_REDEEM_SUCCESS,
            details={
                "redeemed_pusd": redeemed_pusd,
                "tx_hash": receipt.tx_hash,
                "finality_status": receipt.status,
            },
            market_id=position.market_id,
            amount=redeemed_pusd,
        )

        log.info(
            "ctf_redeem_success",
            redeemed_pusd=redeemed_pusd,
            tx_hash=receipt.tx_hash,
            pnl=position.pnl,
        )

        return TradeResult(
            order_id     = redeem_order_id,
            market_id    = position.market_id,
            side         = position.side,
            amount       = redeemed_pusd,
            target_price = 1.0,
            fill_price   = 1.0,
            slippage     = 0.0,
            pnl          = position.pnl,
            success      = True,
            mode         = "real",
            timestamp    = datetime.now(timezone.utc),
        )

    # ------------------------------------------------------------------
    # RETRY CON BACKOFF EXPONENCIAL
    # ------------------------------------------------------------------

    @staticmethod
    def _generate_idempotency_key(
        strategy_name: str,
        market_id:     str,
    ) -> str:
        """
        Genera una idempotency key determinista basada en SHA256.

        Fórmula (según PLAN_MEJORAS.txt P1.4):
          key = SHA256(strategy_name + market_id + timestamp_truncado_a_minuto)[:16]

        Mismo signal en el mismo minuto = misma key.
        Polymarket desduplica automáticamente basado en order_id.
        """
        now = datetime.now(timezone.utc)
        minute_bucket = now.strftime("%Y%m%d%H%M")  # Truncado al minuto
        raw = f"{strategy_name}{market_id}{minute_bucket}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    async def _call_with_retry(
        self,
        operation: str,
        order_id:  str,
        fn,
        log,
    ) -> tuple[dict | None, str | None]:
        """
        Ejecuta una función async con retry y backoff exponencial.
        Retorna (response, None) si éxito o (None, error_msg) si fallo definitivo.
        Solo reintenta en errores de red o 5xx — nunca en 4xx (errores de lógica).

        Protegido por Circuit Breaker: si el circuito está abierto,
        retorna fallo inmediato sin intentar llamadas a la API.
        """
        # ── Circuit Breaker: verificar antes de cualquier llamada ─────
        if self._circuit is not None and self._circuit.is_open():
            error_msg = (
                f"Circuit breaker open: {self._circuit.state.value} "
                f"— API calls blocked"
            )
            log.warning(
                "circuit_breaker_blocked",
                operation=operation,
                state=self._circuit.state.value,
            )
            await self._audit.log(
                action=AuditAction.GUARDRAIL_TRIGGERED,
                details={
                    "operation": operation,
                    "reason": "circuit_breaker_open",
                    "state": self._circuit.state.value,
                },
                order_id=order_id,
            )
            return None, error_msg

        last_error = None

        for attempt in range(MAX_RETRIES):
            try:
                response = await fn()

                if attempt > 0:
                    log.info(
                        "retry_succeeded",
                        operation=operation,
                        attempt=attempt + 1,
                    )

                # ── Registrar éxito en circuit breaker ────────────────
                if self._circuit is not None:
                    try:
                        await self._circuit.record_success()
                    except Exception:
                        pass

                return response, None

            except NotImplementedError as e:
                # Operación no soportada por el camino actual (ej. redeem
                # en CLOB V2 requiere CTF on-chain). No tiene sentido
                # reintentar — falla rápido con mensaje preservado.
                error_msg = str(e) or type(e).__name__
                log.error(
                    "operation_not_supported",
                    operation=operation,
                    error=error_msg,
                )
                return None, error_msg

            except httpx.HTTPStatusError as e:
                status = e.response.status_code

                # ── Post-only mode detection (matching engine restart) ──
                # CLOB V2 returns 503 with code="post_only_mode" after restarts.
                # Parse retry_after_seconds from body or Retry-After header.
                if status == 503:
                    post_only_info = self._parse_post_only_response(e.response)
                    if post_only_info:
                        wait = post_only_info["retry_after"]
                        log.warning(
                            "post_only_mode_detected",
                            operation=operation,
                            retry_after_seconds=wait,
                            attempt=attempt + 1,
                        )
                        last_error = f"Post-only mode (attempt {attempt + 1}/{MAX_RETRIES})"
                        REAL_ORDER_RETRIES.labels(
                            market_id="unknown", operation=operation
                        ).inc()
                        if attempt < MAX_RETRIES - 1:
                            await asyncio.sleep(wait)
                        continue

                # 4xx: error de lógica, no reintentamos
                if status < 500 and status != 429 and status != 425:
                    error_msg = (
                        f"HTTP {status}: {e.response.text[:200]}"
                    )
                    log.error(
                        "non_retryable_http_error",
                        operation=operation,
                        status=status,
                        error=error_msg,
                    )
                    return None, error_msg

                # 425 (matching engine restart), 429, 5xx: reintentamos
                last_error = f"HTTP {status} (attempt {attempt + 1}/{MAX_RETRIES})"
                REAL_ORDER_RETRIES.labels(
                    market_id="unknown", operation=operation
                ).inc()

            except (httpx.RequestError, asyncio.TimeoutError) as e:
                # Errores de red: siempre reintentamos
                last_error = f"NetworkError: {type(e).__name__}: {str(e)[:100]}"
                REAL_ORDER_RETRIES.labels(
                    market_id="unknown", operation=operation
                ).inc()

            if attempt < MAX_RETRIES - 1:
                wait = RETRY_BACKOFF[attempt]
                log.warning(
                    "retrying",
                    operation=operation,
                    attempt=attempt + 1,
                    wait_seconds=wait,
                    error=last_error,
                )
                await self._audit.log(
                    action=AuditAction.REAL_ORDER_RETRY,
                    details={
                        "attempt": attempt + 1,
                        "error":   last_error,
                        "wait":    wait,
                    },
                    order_id=order_id,
                )
                await asyncio.sleep(wait)

        log.error(
            "all_retries_exhausted",
            operation=operation,
            attempts=MAX_RETRIES,
            last_error=last_error,
        )

        # ── Registrar fallo en circuit breaker ────────────────────────
        if self._circuit is not None:
            try:
                await self._circuit.record_failure()
            except Exception:
                pass  # No queremos que el circuit breaker rompa el flujo

        return None, f"All {MAX_RETRIES} attempts failed. Last: {last_error}"

    # ------------------------------------------------------------------
    # GUARDRAILS (hardcoded — NO modificar)
    # ------------------------------------------------------------------

    def _apply_guardrails(
        self, amount: float, market_id: str
    ) -> str | None:
        """
        Verifica guardrails hardcoded antes de cualquier orden real.
        Devuelve None si todo OK, o string con el motivo si falla.
        ESTOS LÍMITES NO SON CONFIGURABLES — son límites de seguridad.
        """
        if amount > MAX_ORDER_AMOUNT_PUSD:
            return (
                f"GUARDRAIL: amount={amount:.2f} pUSD > "
                f"max_allowed={MAX_ORDER_AMOUNT_PUSD:.2f} pUSD (hardcoded limit)"
            )

        if amount < MIN_ORDER_AMOUNT_PUSD:
            return (
                f"GUARDRAIL: amount={amount:.2f} pUSD < "
                f"min_allowed={MIN_ORDER_AMOUNT_PUSD:.2f} pUSD"
            )

        if not market_id or len(market_id) < 10:
            return f"GUARDRAIL: market_id inválido: '{market_id}'"

        return None  # Todos los guardrails pasaron

    @staticmethod
    def _apply_mos_guardrail(
        amount: float, mos: float, market_id: str
    ) -> str | None:
        """
        Verifica que el monto de la orden cumpla con el minimum order
        size (MOS) del mercado. El MOS puede variar por mercado y se
        obtiene del /book endpoint (cacheado en Redis).

        Devuelve None si OK, o string con el motivo si la orden es
        demasiado pequeña para este mercado.
        """
        if amount < mos:
            return (
                f"GUARDRAIL: amount={amount:.2f} pUSD < "
                f"market_min_order_size={mos:.2f} pUSD "
                f"(market_id={market_id[:20]}...)"
            )
        return None

    # ------------------------------------------------------------------
    # HELPERS INTERNOS
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_post_only_response(response: httpx.Response) -> dict | None:
        """
        Detecta el modo post-only en respuestas 503 del CLOB V2.

        Polymarket activa post-only mode tras reinicios del matching engine.
        La respuesta incluye:
          {"error": "...", "code": "post_only_mode", "retry_after_seconds": N}

        También se envía el header HTTP Retry-After.

        Returns:
            {"retry_after": float} si es post-only mode, None si no.
            El valor se limita a [1, 120] segundos.
        """
        try:
            import json as _json
            body = _json.loads(response.text)
        except Exception:
            return None

        if body.get("code") != "post_only_mode":
            return None

        # Prioridad: retry_after_seconds del body > Retry-After header > default 30s
        retry = float(body.get("retry_after_seconds", 0))
        if retry <= 0:
            retry_header = response.headers.get("Retry-After", "")
            try:
                retry = float(retry_header)
            except (ValueError, TypeError):
                retry = 30.0  # Default conservador

        # Limitar a rango seguro: 1-120 segundos
        return {"retry_after": max(1.0, min(retry, 120.0))}

    async def _get_token_and_price(
        self,
        market_id:   str,
        signal_type: SignalType,
    ) -> tuple[str, float]:
        """
        Obtiene el token_id y precio actual del lado correcto (YES/NO).
        Primero intenta Redis, luego DB.
        """
        market = await self._redis.get_market(market_id)
        if not market:
            raise ValueError(f"Market {market_id} no encontrado en Redis/DB")

        ws_state = await self._redis.get_ws_state(market_id)

        if signal_type == SignalType.BUY_YES:
            token_id = market.yes_token_id
            price    = float(ws_state.get("last_yes_price", 0.5)) if ws_state else 0.5
        else:
            token_id = market.no_token_id
            price    = 1.0 - (
                float(ws_state.get("last_yes_price", 0.5)) if ws_state else 0.5
            )

        return token_id, round(price, 4)

    async def _get_current_price(self, market_id: str) -> float:
        """Precio YES actual desde Redis."""
        ws_state = await self._redis.get_ws_state(market_id)
        return float(ws_state.get("last_yes_price", 0.5)) if ws_state else 0.5

    async def _create_real_position(
        self,
        order:     Order,
        market_id: str,
    ) -> Position:
        """Crea una Position desde una orden filled real."""
        market = await self._redis.get_market(market_id)
        return Position(
            id          = str(uuid.uuid4()),
            market_id   = market_id,
            asset       = market.asset.value if market else "UNKNOWN",
            window      = market.window.value if market else "UNKNOWN",
            side        = order.side.value,
            amount      = order.amount,
            shares      = order.shares,
            entry_price = order.fill_price,
            exit_price  = None,
            pnl         = None,
            pnl_pct     = None,
            mode        = "real",
            strategy    = order.strategy,
            exit_reason = None,
        )

    def _failed_result(
        self,
        market_id:    str,
        signal:       Signal | None,
        amount:       float,
        target_price: float,
        error:        str,
    ) -> TradeResult:
        """Construye un TradeResult de fallo para real trading."""
        return TradeResult(
            order_id     = str(uuid.uuid4()),
            market_id    = market_id,
            side         = signal.type.value if signal else "UNKNOWN",
            amount       = amount,
            target_price = target_price,
            fill_price   = target_price,
            slippage     = 0.0,
            pnl          = None,
            success      = False,
            mode         = "real",
            timestamp    = datetime.now(timezone.utc),
            error        = error,
        )
