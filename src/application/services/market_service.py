# src/application/services/market_service.py

from datetime import datetime

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
        """
        log = logger.bind(action="discover_markets")
        log.info("starting_market_discovery")

        discovered: list[Market] = []

        for asset in Asset:
            for window in Window:
                try:
                    # 1. Obtiene mercados raw de Polymarket API
                    raw_markets = await self._market_data.get_active_markets(
                        asset=asset.value,
                        window=window.value,
                    )

                    # 2. Filtra y convierte a entidades de dominio
                    markets = self._filter_and_parse(raw_markets, asset, window)

                    # 3. Persiste cada mercado en DB
                    for market in markets:
                        await self._repo.save_market(market)
                        # 4. Cachea en Redis con TTL de 65 minutos
                        await self._redis.set_market(market, ttl_seconds=3900)
                        discovered.append(market)

                    log.info(
                        "markets_discovered",
                        asset=asset.value,
                        window=window.value,
                        count=len(markets),
                    )
                    # Métrica Prometheus
                    MARKETS_DISCOVERED.labels(
                        asset=asset.value, window=window.value
                    ).inc(len(markets))

                except Exception as e:
                    log.error(
                        "discovery_failed",
                        asset=asset.value,
                        window=window.value,
                        error=str(e),
                    )

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
        """
        # Intenta desde caché primero
        cached = await self._redis.get_active_markets(asset=asset, window=window)
        if cached:
            return cached

        # Fallback a DB
        return await self._repo.get_active_markets(asset=asset, window=window)

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
        Verifica si la duración del mercado corresponde a la ventana.
        Calcula segundos entre start_time y end_time del mercado.
        """
        try:
            start = datetime.fromisoformat(raw["start_date_iso"])
            end   = datetime.fromisoformat(raw["end_date_iso"])
            duration_secs = (end - start).total_seconds()

            min_secs, max_secs = WINDOW_DURATION[window]
            return min_secs <= duration_secs <= max_secs

        except (KeyError, ValueError):
            return False

    def _parse_market(self, raw: dict, asset: Asset, window: Window) -> Market:
        """
        Convierte el dict raw de la API de Polymarket en una entidad Market.
        Puede lanzar KeyError/ValueError si el formato es inesperado.
        """
        tokens = raw.get("tokens", [])
        yes_token = next((t for t in tokens if t["outcome"] == "Yes"), {})
        no_token  = next((t for t in tokens if t["outcome"] == "No"),  {})

        return Market(
            id           = raw["condition_id"],
            asset        = asset,
            window       = window,
            question     = raw["question"],
            status       = MarketStatus.ACTIVE,
            yes_token_id = yes_token.get("token_id", ""),
            no_token_id  = no_token.get("token_id",  ""),
            yes_price    = float(yes_token.get("price", 0.5)),
            no_price     = float(no_token.get("price",  0.5)),
            volume_24h   = float(raw.get("volume24hr", 0.0)),
            expiry       = datetime.fromisoformat(raw["end_date_iso"]),
        )
