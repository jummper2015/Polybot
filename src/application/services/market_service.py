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

    async def get_market_tick(self, market_id: str) -> "MarketTick | None":
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
        """
        markets = await self.get_active_markets()
        for market in markets:
            await self._market_data.subscribe_order_book(
                market.id, callback
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
        Devuelve solo los mercados que cumplen ambos criterios.
        """
        result = []

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
                result.append(market)
            except Exception as e:
                logger.warning(
                    "market_parse_failed",
                    market_id=raw.get("condition_id"),
                    error=str(e),
                )

        return result

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

        # ── Nivel 2: Parsear rango horario en la pregunta ──────────────
        # Formato: "9:30AM-9:35AM ET" o "9:30-9:35"
        time_range_pattern = _re.compile(
            r"(\d{1,2}):(\d{2})\s*(?:AM|PM)?\s*-\s*(\d{1,2}):(\d{2})\s*(?:AM|PM)?"
        )
        match = time_range_pattern.search(question)
        if match:
            h1, m1, h2, m2 = int(match.group(1)), int(match.group(2)), int(match.group(3)), int(match.group(4))
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
                return False
            duration_secs = (end - start).total_seconds()

            min_secs, max_secs = WINDOW_DURATION[window]
            return min_secs <= duration_secs <= max_secs

        except (KeyError, ValueError, TypeError):
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
