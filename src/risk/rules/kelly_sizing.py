# src/risk/rules/kelly_sizing.py

from src.domain.value_objects.risk_decision import RiskDecision
from src.domain.value_objects.signal import Signal
from src.risk.base import IRule
from src.risk.context import RiskContext


class KellySizingRule(IRule):
    """
    Ajusta el tamaño de posición usando Kelly Criterion fraccional.

    Fórmula (según PLAN_MEJORAS.txt P2.1):
      edge = confidence - market_implied_probability
      b    = target_price / (1.0 - target_price)
      kelly_fraction = (b * edge - (1 - edge)) / b
      kelly_fraction = max(0.0, min(kelly_fraction, cap))

      position_size = balance * kelly_fraction * safety_factor * volatility_dampener

    Guardrails hardcoded:
      - Floor: nunca menos de $5 USDC
      - Cap:   nunca más de $50 USDC
      - Kelly nunca puede aumentar el position size por encima del configurado

    La volatilidad se calcula externamente (en la estrategia) y se pasa vía
    RiskContext.recent_volatility. Si no está disponible, el dampener = 1.0.
    """

    def __init__(
        self,
        kelly_cap:         float = 0.25,   # Fracción máxima de Kelly (25%)
        safety_factor:     float = 0.25,   # Factor de seguridad (25% del Kelly)
        target_price:      float = 0.90,   # Precio objetivo de la estrategia
        position_floor:    float = 5.0,    # Monto mínimo absoluto (USDC)
        position_cap:      float = 50.0,   # Monto máximo absoluto (USDC)
    ):
        self._kelly_cap      = kelly_cap
        self._safety_factor  = safety_factor
        self._target_price   = target_price
        self._position_floor = position_floor
        self._position_cap   = position_cap

    @property
    def name(self) -> str:
        return "KellySizingRule"

    @property
    def priority(self) -> int:
        # Se evalúa después de MinBalance y Drawdown, pero antes de MaxExposure
        return 3

    def evaluate(self, signal: Signal, context: RiskContext) -> RiskDecision:
        confidence = signal.confidence

        # ── 1. Calcular edge (ventaja sobre el mercado) ────────────────
        market_prob = context.market_yes_price
        edge = confidence - market_prob

        # Sin ventaja → usar el floor (o denegar si ni siquiera el floor es viable)
        if edge <= 0.0:
            floor = max(self._position_floor, 0.0)
            # Kelly nunca puede aumentar el position size por encima del configurado
            floor = min(floor, context.requested_amount)
            if context.current_balance < floor:
                return self._deny(
                    f"kelly_edge_zero_or_negative: edge={edge:.4f} "
                    f"(confidence={confidence:.2f}, market_price={market_prob:.2f}), "
                    f"balance={context.current_balance:.2f} < floor={floor:.2f}"
                )
            return self._allow(
                reason=(
                    f"kelly_edge_zero: edge={edge:.4f} ≤ 0 → "
                    f"using floor={floor:.2f} USDC"
                ),
                suggested_amount=round(floor, 2),
            )

        # ── 2. Calcular Kelly fraction ─────────────────────────────────
        # Odds ratio: b = target_price / (1 - target_price)
        b = self._target_price / (1.0 - self._target_price)

        # Kelly fraction clásica: f* = (b * edge - (1 - edge)) / b
        kelly_raw = (b * edge - (1.0 - edge)) / b

        # Cap al máximo permitido y floor a 0
        kelly_fraction = max(0.0, min(kelly_raw, self._kelly_cap))

        # ── 3. Volatility dampener ─────────────────────────────────────
        dampener = self._compute_dampener(context.recent_volatility)

        # ── 4. Calcular position size final ────────────────────────────
        kelly_amount = (
            context.current_balance
            * kelly_fraction
            * self._safety_factor
            * dampener
        )

        # ── 5. Aplicar guardrails hardcoded ────────────────────────────
        final_amount = max(self._position_floor, min(kelly_amount, self._position_cap))

        # Kelly NUNCA puede aumentar el monto por encima del configurado
        final_amount = min(final_amount, context.requested_amount)

        # Si el monto final es mayor que el balance disponible, denegar
        if final_amount > context.current_balance:
            return self._deny(
                f"kelly_amount_exceeds_balance: "
                f"kelly={final_amount:.2f} > balance={context.current_balance:.2f}"
            )

        # ── 6. Decisión ────────────────────────────────────────────────
        return self._allow(
            reason=(
                f"kelly_sized: requested={context.requested_amount:.2f} → "
                f"kelly={final_amount:.2f} USDC "
                f"(edge={edge:.4f}, confidence={confidence:.2f}, "
                f"kelly_pct={kelly_fraction:.2%}, dampener={dampener:.2f})"
            ),
            suggested_amount=round(final_amount, 2),
        )

    # ------------------------------------------------------------------
    # HELPERS
    # ------------------------------------------------------------------

    def _compute_dampener(self, volatility: float | None) -> float:
        """
        Calcula el volatility dampener.

        realized_vol = std / mean de los últimos 10 ticks.
        dampener = min(1.0, 0.02 / realized_vol) si realized_vol > 0.

        Si volatility es None → dampener = 1.0 (sin ajuste).
        """
        if volatility is None or volatility <= 0.0:
            return 1.0

        dampener = min(1.0, 0.02 / volatility)
        return dampener
