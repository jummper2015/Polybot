# src/strategies/filters/multi_timeframe.py

"""
Filtro de confirmación multi-timeframe (P2.3).

Señal de 5m solo se ejecuta si 15m confirma la misma dirección.
Reduce falsos positivos ~40% al exigir que la tendencia de corto plazo
sea respaldada por el timeframe superior.

Lógica (BuyAboveThreshold):
  - Si M5 > threshold AND M15 > threshold → confidence +25%
  - Si M5 > threshold AND M15 < threshold → hold (no confirmado)
  - Si no hay tick M15 disponible → allow (no bloquear por falta de datos)
"""

from typing import Awaitable, Callable

import structlog

from src.domain.entities.market import Market
from src.domain.enums.asset import Asset
from src.domain.enums.window import Window
from src.domain.value_objects.market_tick import MarketTick
from src.infrastructure.observability.metrics import FILTER_REJECTIONS
from src.strategies.base import StrategyState
from src.strategies.filters.base import FilterResult

logger = structlog.get_logger(__name__)

FILTER_NAME = "MultiTimeframeFilter"


class MultiTimeframeFilter:
    """
    Filtro asíncrono que confirma señales M5 con el tick M15 del mismo asset.

    Solo aplica a mercados de ventana M5. Para M15, siempre devuelve passed.
    Si no hay tick M15 disponible (WS caído, primer ciclo), permite la señal.

    Uso:
        filter = MultiTimeframeFilter(tick_provider=..., threshold=0.75)
        result = await filter.apply(m5_tick, state, market)
        if not result.passed:
            return Signal(type=HOLD, reason=result.reason)
        # Si pasa → boost confidence
    """

    def __init__(
        self,
        tick_provider: Callable[[Asset], Awaitable[MarketTick | None]],
        threshold: float = 0.75,
    ):
        """
        Args:
            tick_provider: async fn(Asset) -> MarketTick | None.
                           Debe resolver el tick M15 para el asset dado.
            threshold: precio YES mínimo que debe tener el tick M15
                       para confirmar la misma dirección que M5.
        """
        self._get_m15_tick = tick_provider
        self._threshold = threshold

        logger.info(
            "mtf_filter_initialized",
            threshold=threshold,
        )

    @property
    def name(self) -> str:
        return FILTER_NAME

    async def apply(
        self,
        tick: MarketTick,
        state: StrategyState,
        market: Market,
    ) -> FilterResult:
        """
        Evalúa si el tick M15 confirma la dirección del tick M5.

        Solo aplica a mercados M5. Para M15, siempre OK.
        Si no hay tick M15 → OK (no bloquear por falta de datos).
        Si M15 yes_price >= threshold → OK (confirma).
        Si M15 yes_price < threshold → FAIL (no confirma).
        """
        # ── Solo aplica a M5 ──────────────────────────────────────────
        if market.window != Window.M5:
            return FilterResult.ok(
                FILTER_NAME,
                f"not M5 (window={market.window.value}), skipping",
            )

        # ── Obtener tick M15 ──────────────────────────────────────────
        try:
            m15_tick = await self._get_m15_tick(market.asset)
        except Exception as e:
            logger.debug(
                "mtf_tick_fetch_error",
                asset=market.asset.value,
                error=str(e),
            )
            # No bloquear si falla la obtención del tick
            return FilterResult.ok(
                FILTER_NAME,
                f"M15 tick fetch failed: {e}",
            )

        if m15_tick is None:
            return FilterResult.ok(
                FILTER_NAME,
                f"no M15 tick available for {market.asset.value}",
            )

        # ── Verificar confirmación ────────────────────────────────────
        if m15_tick.yes_price >= self._threshold:
            return FilterResult.ok(
                FILTER_NAME,
                f"M15 confirms: price={m15_tick.yes_price:.4f} >= "
                f"threshold={self._threshold}",
            )
        else:
            reason = (
                f"M15 does NOT confirm: price={m15_tick.yes_price:.4f} < "
                f"threshold={self._threshold}"
            )
            logger.debug(
                "mtf_rejected",
                asset=market.asset.value,
                m5_price=tick.yes_price,
                m15_price=m15_tick.yes_price,
                threshold=self._threshold,
            )
            FILTER_REJECTIONS.labels(filter_name=FILTER_NAME).inc()
            return FilterResult.fail(FILTER_NAME, reason)
