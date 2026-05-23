# src/risk/engine.py

from datetime import datetime, timezone

import structlog

from src.domain.value_objects.risk_decision import RiskDecision
from src.domain.value_objects.signal import Signal
from src.infrastructure.observability.metrics import (
    RISK_DECISIONS,
    RISK_DRAWDOWN_GAUGE,
    RISK_EXPOSURE_GAUGE,
    RISK_RULE_TRIGGERED,
)
from src.infrastructure.observability.tracing import get_tracer
from src.risk.base import IRule
from src.risk.context import RiskContext
from src.risk.rules.drawdown import DrawdownRule
from src.risk.rules.hedge import HedgeRule
from src.risk.rules.kelly_sizing import KellySizingRule
from src.risk.rules.max_exposure import MaxExposureRule
from src.risk.rules.max_positions import MaxPositionsRule
from src.risk.rules.min_balance import MinBalanceRule

logger = structlog.get_logger(__name__)


class RiskEngineConfig:
    """Configuración centralizada del RiskEngine."""

    def __init__(
        self,
        min_balance_usdc:       float = 50.0,
        max_daily_drawdown_pct: float = 0.10,
        max_exposure_pct:       float = 0.30,
        max_open_positions:     int   = 5,
        max_net_exposure_pct:   float = 0.50,
        kelly_cap:              float = 0.25,
        kelly_safety_factor:    float = 0.25,
        kelly_target_price:     float = 0.90,
        kelly_position_floor:   float = 5.0,
        kelly_position_cap:     float = 50.0,
    ):
        self.min_balance_usdc       = min_balance_usdc
        self.max_daily_drawdown_pct = max_daily_drawdown_pct
        self.max_exposure_pct       = max_exposure_pct
        self.max_open_positions     = max_open_positions
        self.max_net_exposure_pct   = max_net_exposure_pct
        self.kelly_cap              = kelly_cap
        self.kelly_safety_factor    = kelly_safety_factor
        self.kelly_target_price     = kelly_target_price
        self.kelly_position_floor   = kelly_position_floor
        self.kelly_position_cap     = kelly_position_cap


class RiskEngine:
    """
    Orquestador de reglas de riesgo.
    Evalúa señales contra las reglas en orden de precedencia estricto.
    Primera regla que DENY detiene la cadena (short-circuit en DENY).
    Primera regla que ALLOW con suggested_amount lo propaga al final.
    """

    def __init__(self, config: RiskEngineConfig | None = None):
        self._config = config or RiskEngineConfig()

        # Reglas ordenadas por prioridad (menor número = evalúa primero)
        self._rules: list[IRule] = sorted(
            [
                MinBalanceRule(self._config.min_balance_usdc),
                DrawdownRule(self._config.max_daily_drawdown_pct),
                KellySizingRule(
                    kelly_cap=self._config.kelly_cap,
                    safety_factor=self._config.kelly_safety_factor,
                    target_price=self._config.kelly_target_price,
                    position_floor=self._config.kelly_position_floor,
                    position_cap=self._config.kelly_position_cap,
                ),
                MaxExposureRule(self._config.max_exposure_pct),
                MaxPositionsRule(self._config.max_open_positions),
                HedgeRule(self._config.max_net_exposure_pct),
            ],
            key=lambda r: r.priority,
        )

        # Balance inicial del día — se actualiza a medianoche UTC
        self._initial_day_balance: float | None = None
        self._day_date: str | None = None  # Formato: "2024-01-15"

        logger.info(
            "risk_engine_initialized",
            rules=[r.name for r in self._rules],
            min_balance=self._config.min_balance_usdc,
            max_drawdown=self._config.max_daily_drawdown_pct,
            max_exposure=self._config.max_exposure_pct,
            max_positions=self._config.max_open_positions,
        )

    # ------------------------------------------------------------------
    # API PÚBLICA
    # ------------------------------------------------------------------

    async def evaluate(
        self,
        signal:               Signal,
        current_balance:      float,
        open_positions_count: int,
        market_exposure_usdc: float,
        total_exposure_usdc:  float,
        requested_amount:     float,
        market_id:            str,
        trading_mode:         str,
        market_yes_price:     float = 0.5,
        recent_volatility:    float | None = None,
    ) -> RiskDecision:
        """
        Punto de entrada principal del RiskEngine.
        Construye el contexto y evalúa todas las reglas en orden.

        Parámetros opcionales para Kelly Criterion (P2.1):
          - market_yes_price: precio YES actual del mercado (default 0.5)
          - recent_volatility: volatilidad reciente (None → dampener=1.0)

        Instrumentado con OpenTelemetry: span por regla evaluada.
        """
        tracer = get_tracer()

        # Actualiza el balance inicial del día si cambió la fecha
        self._refresh_day_balance(current_balance)

        context = RiskContext(
            current_balance=current_balance,
            initial_day_balance=self._initial_day_balance or current_balance,
            open_positions_count=open_positions_count,
            market_exposure_usdc=market_exposure_usdc,
            total_exposure_usdc=total_exposure_usdc,
            requested_amount=requested_amount,
            market_id=market_id,
            trading_mode=trading_mode,
            market_yes_price=market_yes_price,
            recent_volatility=recent_volatility,
        )

        log = logger.bind(
            market_id=market_id,
            signal_type=signal.type.value,
            requested_amount=requested_amount,
            current_balance=current_balance,
            open_positions=open_positions_count,
        )

        log.info("risk_evaluation_start")

        # Emite métricas de estado actual
        RISK_DRAWDOWN_GAUGE.labels(mode=trading_mode).set(context.drawdown_pct)
        RISK_EXPOSURE_GAUGE.labels(mode=trading_mode).set(context.exposure_pct)

        # Acumula el suggested_amount más restrictivo de las reglas ALLOW
        final_suggested_amount: float | None = None

        # Evalúa reglas en orden de precedencia
        for rule in self._rules:
            with tracer.start_as_current_span("risk_rule_evaluate") as rule_span:
                rule_span.set_attribute("risk.rule_name", rule.name)
                rule_span.set_attribute("risk.rule_priority", rule.priority)

                decision = rule.evaluate(signal, context)

                rule_span.set_attribute("risk.allowed", str(decision.allowed))
                rule_span.set_attribute("risk.reason", decision.reason[:200])
                if decision.suggested_amount is not None:
                    rule_span.set_attribute("risk.suggested_amount", str(decision.suggested_amount))

            log.debug(
                "rule_evaluated",
                rule=rule.name,
                allowed=decision.allowed,
                reason=decision.reason,
                suggested_amount=decision.suggested_amount,
            )

            if not decision.allowed:
                # DENY: short-circuit — no evalúa más reglas
                log.warning(
                    "risk_denied",
                    rule=rule.name,
                    reason=decision.reason,
                )
                RISK_DECISIONS.labels(
                    result="deny",
                    rule=rule.name,
                    mode=trading_mode,
                ).inc()
                RISK_RULE_TRIGGERED.labels(rule=rule.name).inc()
                return decision

            # ALLOW con ajuste de monto → toma el más restrictivo
            if decision.suggested_amount is not None:
                if final_suggested_amount is None:
                    final_suggested_amount = decision.suggested_amount
                else:
                    # Usa el monto más pequeño sugerido entre todas las reglas
                    final_suggested_amount = min(
                        final_suggested_amount,
                        decision.suggested_amount,
                    )

        # Todas las reglas pasaron → ALLOW
        final_decision = RiskDecision(
            allowed=True,
            reason="all_rules_passed",
            rule_triggered="RiskEngine",
            suggested_amount=final_suggested_amount,
        )

        log.info(
            "risk_allowed",
            suggested_amount=final_suggested_amount,
            rules_evaluated=len(self._rules),
        )

        RISK_DECISIONS.labels(
            result="allow",
            rule="RiskEngine",
            mode=trading_mode,
        ).inc()

        return final_decision

    def update_config(self, new_config: RiskEngineConfig) -> None:
        """
        Actualiza la configuración en caliente.
        Reconstruye las reglas con los nuevos parámetros.
        """
        self._config = new_config
        self._rules = sorted(
            [
                MinBalanceRule(self._config.min_balance_usdc),
                DrawdownRule(self._config.max_daily_drawdown_pct),
                KellySizingRule(
                    kelly_cap=self._config.kelly_cap,
                    safety_factor=self._config.kelly_safety_factor,
                    target_price=self._config.kelly_target_price,
                    position_floor=self._config.kelly_position_floor,
                    position_cap=self._config.kelly_position_cap,
                ),
                MaxExposureRule(self._config.max_exposure_pct),
                MaxPositionsRule(self._config.max_open_positions),
                HedgeRule(self._config.max_net_exposure_pct),
            ],
            key=lambda r: r.priority,
        )
        logger.info(
            "risk_engine_config_updated",
            min_balance=new_config.min_balance_usdc,
            max_drawdown=new_config.max_daily_drawdown_pct,
            max_exposure=new_config.max_exposure_pct,
            max_positions=new_config.max_open_positions,
        )

    def get_rules_summary(self) -> list[dict]:
        """Devuelve resumen de reglas activas para el endpoint /status."""
        return [
            {"rule": r.name, "priority": r.priority}
            for r in self._rules
        ]

    # ------------------------------------------------------------------
    # GESTIÓN DEL DÍA (drawdown diario)
    # ------------------------------------------------------------------

    def _refresh_day_balance(self, current_balance: float) -> None:
        """
        Si cambió la fecha UTC, resetea el balance inicial del día.
        Garantiza que el drawdown se calcule por día calendario UTC.
        """
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        if self._day_date != today:
            self._day_date            = today
            self._initial_day_balance = current_balance
            logger.info(
                "day_balance_reset",
                date=today,
                initial_balance=current_balance,
            )
