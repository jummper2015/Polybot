# src/application/services/market_service.py

import re as _re
from datetime import datetime, timezone

import structlog

from src.application.ports.market_data_port import IMarketDataPort
from src.application.ports.repository_port import IRepositoryPort
from src.domain.entities.market import Market
from src.domain.enums.asset import Asset
from src.domain.enums.market_status import MarketStatus
from src.domain.enums.window import Window
from src.infrastructure.cache.redis_client import RedisClient
from src.infrastructure.observability.metrics import MARKETS_ACTIVE, MARKETS_DISCOVERED

logger = structlog.get_logger(__name__)

# Keywords que identifican cada asset en el título del mercado
ASSET_KEYWORDS: dict[Asset, list[str]] = {
    Asset.BTC: ["BTC", "Bitcoin", "bitcoin"],
    Asset.ETH: ["ETH", "Ethereum", "ethereum"],
}

# Duración esperada en segundos para cada ventana (con tolerancia)
WINDOW_DURATION: dict[Window, tuple[int, int]] = {
    Window.M5:  (270, 330),    # 5m ± 30 segundos
    Window.M15: (840, 960),    # 15m ± 60 segundos
}

# B5 fix: patrones para detectar markets live de Polymarket (Up/Down crypto)
# que rotan cada 5 o 15 minutos. Ejemplos:
#   "Bitcoin Up or Down on June 14, 3:35PM ET"
#   "Ethereum Up or Down on June 14, 3:35PM ET"
#   "Bitcoin Price - June 14 3:35PM ET"
#   "Ethereum Price - June 14 3:35PM ET"
LIVE_UP_DOWN_CRYPTO_PATTERN = _re.compile(
    r"\b(bitcoin|btc|ethereum|eth)\b\s+up\s+or\s+down\b",
    _re.IGNORECASE,
)

LIVE_PRICE_ET_PATTERN = _re.compile(
    r"\b(bitcoin|btc|ethereum|eth)\b[\s\w-]*?\bprice\b[\s\w-]*?"
    r"(?:\d{1,2}(?::\d{2})?\s*(?:am|pm)?\s*et\b)",
    _re.IGNORECASE,
)


def _is_live_crypto_market(raw: dict) -> bool:
    """Detecta markets live crypto (Up/Down o Price crypto) de Polymarket.

    B5: estos markets tienen el formato "Bitcoin Up or Down on [date] [time] ET"
    y rotan cada 5 o 15 minutos. El filtro previo (slug con -5m-/H:MM-H:MM)
    no los capturaba.
    """
    title = raw.get("title", "") or raw.get("question", "")
    slug = raw.get("slug", "")
    text = f"{title} {slug}"
    return bool(
        LIVE_UP_DOWN_CRYPTO_PATTERN.search(text)
        or LIVE_PRICE_ET_PATTERN.search(text)
    )


class MarketService:
    """
    Caso de uso: descubrir, filtrar y mantener mercados BTC/ETH activos.
    Orquesta HTTP client, DB y Redis. No conoce SQLAlchemy directamente.
    """

    def __init__(
        self,
        market_data_port: IMarketDataPort,
        repository:       IRepositoryPort,
        redis:            RedisClient,
    ):
        self._market_data = market_data_port
        self._repo        = repository
        self._redis       = redis

    # ------------------------------------------------------------------
    # DISCOVERY
    # ------------------------------------------------------------------

    async def discover_markets(self) -> list[Market]:
        """
        Consulta Polymarket, filtra por BTC/ETH y ventanas 5m/15m,
        persiste en DB y cachea en Redis.
        Llamado al arranque y cada 60 minutos por el scheduler.

        Optimización: hace UNA sola llamada a la API (no 4) y luego
        filtra los mercados por asset y window.
        """
        log = logger.bind(action="discover_markets")
        log.info("starting_market_discovery")

        discovered: list[Market] = []

        try:
            # 1. Obtiene TODOS los mercados crypto en UNA sola llamada API
            #    (los parámetros asset/window son ignorados por el endpoint /events,
            #     el filtrado se hace localmente para evitar 4 llamadas redundantes)
            raw_markets = await self._market_data.get_active_markets(
                asset="all",
                window="all",
            )

            if not raw_markets:
                log.warning("no_markets_fetched_from_api")
                return []

            log.info("raw_markets_fetched", count=len(raw_markets))

            # 2. Itera sobre cada combinación asset×window y filtra
            for asset in Asset:
                for window in Window:
                    try:
                        markets = self._filter_and_parse(raw_markets, asset, window)

                        for market in markets:
                            await self._repo.save_market(market)
                            await self._redis.set_market(market, ttl_seconds=3900)
                            # ── Fetch y cachear market_info (tick_size, MOS, fees) ──
                            await self._cache_market_info(market)
                            discovered.append(market)

                        log.info(
                            "markets_discovered",
                            asset=asset.value,
                            window=window.value,
                            count=len(markets),
                        )
                        MARKETS_DISCOVERED.labels(
                            asset=asset.value, window=window.value
                        ).inc(len(markets))

                    except Exception as e:
                        log.error(
                            "discovery_filter_failed",
                            asset=asset.value,
                            window=window.value,
                            error=str(e),
                        )

        except Exception as e:
            log.error("discovery_fetch_failed", error=str(e))

        log.info("discovery_complete", total=len(discovered))
        return discovered

    # ------------------------------------------------------------------
    # QUERIES
    # ------------------------------------------------------------------

    async def get_active_markets(
        self,
        asset:  str | None = None,
        window: str | None = None,
    ) -> list[Market]:
        """
        Devuelve mercados activos.
        Primero intenta Redis (rápido), si falla cae a DB.
        Cuando cae a DB, repopula Redis para futuras consultas.
        """
        # Intenta desde caché primero
        cached = await self._redis.get_active_markets(asset=asset, window=window)
        if cached:
            return cached

        # Fallback a DB
        db_markets = await self._repo.get_active_markets(asset=asset, window=window)

        # Repopula Redis desde DB para que subsistemas (WS, orderbook)
        # que solo consultan Redis tengan los datos disponibles
        if db_markets:
            for market in db_markets:
                await self._redis.set_market(market, ttl_seconds=3900)
            logger.debug(
                "markets_synced_db_to_redis",
                count=len(db_markets),
            )

        return db_markets

    async def get_market_by_id(self, market_id: str) -> Market | None:
        """Busca un mercado por ID. Redis primero, DB como fallback."""
        cached = await self._redis.get_market(market_id)
        if cached:
            return cached
        return await self._repo.get_market_by_id(market_id)

    async def get_market_tick(self, market_id: str) -> "MarketTick | None":  # noqa: F821
        """
        Obtiene el tick más reciente del mercado. Delega en el market data port.
        Thin wrapper para que TradingService no acceda a atributos privados.
        """
        return await self._market_data.get_market_tick(market_id)

    # ------------------------------------------------------------------
    # WEBSOCKET SUBSCRIPTIONS
    # ------------------------------------------------------------------

    async def subscribe_all_to_orderbook(
        self, callback
    ) -> None:
        """
        Subscribe todos los mercados activos al order book via WebSocket.
        El callback recibe MarketTick en cada actualización.
        Llamado desde TradingService.start() después del discovery.

        Pasa yes_token_id (asset_id del CLOB) para la suscripción WS,
        que es lo que la API de Polymarket espera (no el condition_id).
        """
        markets = await self.get_active_markets()
        for market in markets:
            await self._market_data.subscribe_order_book(
                market.id, callback, token_id=market.yes_token_id,
            )
        logger.info(
            "ws_subscriptions_started",
            count=len(markets),
        )

    async def update_market_prices(
        self,
        market_id: str,
        yes_price: float,
        no_price:  float,
        volume:    float,
    ) -> None:
        """
        Actualiza precios de un mercado después de recibir un tick.
        Actualiza DB y Redis simultáneamente.
        """
        market = await self.get_market_by_id(market_id)
        if not market:
            return

        market.update_prices(yes_price, no_price, volume)
        await self._repo.save_market(market)
        await self._redis.set_market(market, ttl_seconds=3900)

        MARKETS_ACTIVE.labels(
            asset=market.asset.value,
            window=market.window.value,
        ).set(1)

    # ------------------------------------------------------------------
    # MARKET INFO CACHING (tick_size, MOS, neg_risk)
    # ------------------------------------------------------------------

    async def _cache_market_info(self, market: Market) -> None:
        """
        Fetch y cachea metadata del CLOB para este mercado.

        Usa el /book endpoint (via http_client) para obtener tick_size,
        min_order_size, y neg_risk. Los guarda en Redis como metadata
        para que el execution handler use los valores correctos.

        Esto evita usar tick_size="0.01" hardcodeado — cada mercado
        puede tener su propio tick_size.
        """
        try:
            _ = await self._market_data.get_market_tick(market.id)
            # El tick en sí se descarta; los metadatos ya se cachearon
            # en Redis por http_client._fetch_tick_rest() via set_market_metadata().
            # Pero también actualizamos la entidad Market con los metadatos.
            meta = await self._redis.get_market_metadata(market.id)
            if meta:
                if "tick_size" in meta:
                    market.tick_size = meta["tick_size"]
                if "neg_risk" in meta:
                    market.neg_risk = meta["neg_risk"]
                if "min_order_size" in meta:
                    market.min_order_size = meta["min_order_size"]
                # Actualizar en Redis con los metadatos merged
                await self._redis.set_market(market, ttl_seconds=3900)
                logger.debug(
                    "market_info_cached",
                    market_id=market.id[:20],
                    tick_size=market.tick_size,
                    neg_risk=market.neg_risk,
                    min_order_size=market.min_order_size,
                )
        except Exception as e:
            logger.debug(
                "market_info_cache_skipped",
                market_id=market.id[:20],
                error=str(e),
            )

    # ------------------------------------------------------------------
    # FILTROS INTERNOS
    # ------------------------------------------------------------------

    def _filter_and_parse(
        self,
        raw_markets: list[dict],
        asset:  Asset,
        window: Window,
    ) -> list[Market]:
        """
        Aplica filtros de asset y ventana temporal a los datos raw de la API.
        Devuelve solo el mercado de mayor volumen que cumple ambos criterios
        (máximo 1 por combinación asset × window).
        """
        candidates = []

        for raw in raw_markets:
            # Filtro 1: el título debe contener keywords del asset
            if not self._matches_asset(raw.get("question", ""), asset):
                continue

            # Filtro 2: la duración debe corresponder a la ventana
            if not self._matches_window(raw, window):
                continue

            # Filtro 3: debe estar activo (no resuelto ni expirado)
            if raw.get("active") is False:
                continue

            try:
                market = self._parse_market(raw, asset, window)
                candidates.append(market)
            except Exception as e:
                logger.warning(
                    "market_parse_failed",
                    market_id=raw.get("condition_id"),
                    error=str(e),
                )

        # Keep only the highest-volume market per (asset, window)
        if not candidates:
            return []

        candidates.sort(key=lambda m: m.volume_24h, reverse=True)
        best = candidates[0]

        if len(candidates) > 1:
            logger.debug(
                "market_dedup_applied",
                asset=asset.value,
                window=window.value,
                total_candidates=len(candidates),
                selected_volume=round(best.volume_24h, 0),
                selected_id=best.id[:20] + "...",
            )

        return [best]

    def _matches_asset(self, question: str, asset: Asset) -> bool:
        """Verifica si la pregunta del mercado corresponde al asset."""
        keywords = ASSET_KEYWORDS[asset]
        return any(kw in question for kw in keywords)

    def _matches_window(self, raw: dict, window: Window) -> bool:
        """
        Verifica si la ventana temporal del mercado corresponde a la esperada.

        Estrategia en 2 niveles:
          1. Slug: busca "5m" o "15m" en el slug del mercado (más fiable).
          2. Question: parsea el rango horario (ej: "9:30AM-9:35AM") para
             calcular la duración y comparar con la ventana esperada.
          3. Fallback: intenta usar start_date_iso/end_date_iso (solo para
             mercados que no son "Up or Down", donde las fechas sí reflejan
             la ventana real).
        """
        slug = raw.get("slug", "")
        question = raw.get("question", "")

        # ── Nivel 1: Slug contiene el timeframe explícito ──────────────
        if window == Window.M5 and "-5m-" in slug:
            return True
        if window == Window.M15 and "-15m-" in slug:
            return True

        # ── Nivel 2: Parsear rango horario en la pregunta ──────────
        # Formato: "9:30AM-9:35AM ET" o "9:30-9:35"
        time_range_pattern = _re.compile(
            r"(\d{1,2}):(\d{2})\s*(?:AM|PM)?\s*-"
            r"\s*(\d{1,2}):(\d{2})\s*(?:AM|PM)?"
        )
        match = time_range_pattern.search(question)
        if match:
            h1 = int(match.group(1))
            m1 = int(match.group(2))
            h2 = int(match.group(3))
            m2 = int(match.group(4))
            start_mins = h1 * 60 + m1
            end_mins = h2 * 60 + m2
            if end_mins <= start_mins:
                end_mins += 12 * 60
            duration_mins = end_mins - start_mins

            if window == Window.M5 and 2 <= duration_mins <= 7:
                return True
            if window == Window.M15 and 12 <= duration_mins <= 18:
                return True

        # ── Nivel 3: Fallback con fechas del mercado ────────────────────
        try:
            start = self._parse_datetime_safe(raw.get("start_date_iso", ""))
            end   = self._parse_datetime_safe(raw.get("end_date_iso", ""))
            if start is None or end is None:
                # B5: sin fechas puede ser un market live crypto (Up/Down).
                return self._matches_live_crypto_window(raw, window)
            duration_secs = (end - start).total_seconds()

            min_secs, max_secs = WINDOW_DURATION[window]
            if min_secs <= duration_secs <= max_secs:
                return True
            # B5: fechas fuera de rango puede ser live crypto.
            return self._matches_live_crypto_window(raw, window)

        except (KeyError, ValueError, TypeError):
            return self._matches_live_crypto_window(raw, window)

    def _matches_live_crypto_window(self, raw: dict, window: Window) -> bool:
        """
        B5: Verifica si un market live crypto (Up/Down o Price crypto)
        corresponde a la ventana solicitada. Estos markets rotan cada 5 o
        15 minutos, pero NO usan el patrón de slug -5m-/-15m- ni rangos
        H:MM-H:MM en el título.

        Acepta M5 por defecto (los markets live crypto más comunes en
        Polymarket son de 5 min). Para M15 sólo se acepta si el slug
        contiene marcador explícito.
        """
        if not _is_live_crypto_market(raw):
            return False
        slug = raw.get("slug", "")
        if window == Window.M5:
            return True
        if window == Window.M15:
            return "-15m-" in slug or "-15-minute-" in slug
        return False

    @staticmethod
    def _parse_datetime_safe(date_str: str) -> datetime | None:
        """
        Parsea una fecha ISO de forma segura, normalizando siempre a UTC naive.
        Evita errores de resta entre offset-aware y offset-naive datetimes.
        """
        if not date_str:
            return None
        try:
            dt = datetime.fromisoformat(date_str)
            # Normalizar a UTC naive para comparaciones consistentes
            if dt.tzinfo is not None:
                dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
            return dt
        except (ValueError, TypeError):
            return None

    def _parse_market(self, raw: dict, asset: Asset, window: Window) -> Market:
        """
        Convierte el dict raw de la API de Polymarket en una entidad Market.

        Soporta tanto outcomes "Yes"/"No" (mercados tradicionales) como
        "Up"/"Down" (mercados up-down de Polymarket).
        """
        tokens = raw.get("tokens", [])
        # Busca el token "positivo": Yes o Up
        yes_token = next(
            (t for t in tokens if t.get("outcome") in ("Yes", "Up")), {}
        )
        # Busca el token "negativo": No o Down
        no_token = next(
            (t for t in tokens if t.get("outcome") in ("No", "Down")), {}
        )

        # Si no encontramos por nombre, asumimos índice 0 = positivo, índice 1 = negativo
        if not yes_token and not no_token and len(tokens) >= 2:
            yes_token = tokens[0]
            no_token  = tokens[1]

        expiry = self._parse_datetime_safe(raw.get("end_date_iso", ""))

        return Market(
            id           = raw["condition_id"],
            asset        = asset,
            window       = window,
            question     = raw["question"],
            status       = MarketStatus.ACTIVE,
            yes_token_id = str(yes_token.get("token_id", "")),
            no_token_id  = str(no_token.get("token_id",  "")),
            yes_price    = float(yes_token.get("price", 0.5)),
            no_price     = float(no_token.get("price",  0.5)),
            volume_24h   = float(raw.get("volume24hr", 0.0)),
            expiry       = expiry or datetime.now(timezone.utc).replace(tzinfo=None),
        )
