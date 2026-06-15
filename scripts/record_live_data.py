#!/usr/bin/env python3
"""
Live market data recorder for Polymarket.

Records real-time tick data to Parquet (default) or CSV format.

Architecture:
    WebSocket → Tick Parser → MultiAssetRecorder (buffered) → Parquet files
                                                                  ↓
                                                            Partitioned by
                                                            asset/date

Usage:
    python scripts/record_live_data.py --asset BTC --duration-hours 24
    python scripts/record_live_data.py --asset ETH --format csv    # backward compat
    python scripts/record_live_data.py --all
    python scripts/record_live_data.py --market-id 0xabc...

Output (Parquet mode, default):
    data/parquet/
        asset=BTC/year=2026/month=05/day=26/ticks_HHMMSS_ffffff.parquet
        asset=ETH/year=2026/month=05/day=26/ticks_HHMMSS_ffffff.parquet
        manifest.json

Output (CSV mode, legacy):
    data/historical/live_{asset}_{market_id}_{date}.csv
    live_manifest.json
"""

import argparse
import asyncio
import csv
import json
import os
import re
import signal
import sys
from datetime import datetime, timezone
from pathlib import Path

# Ensure project root is on sys.path for src.* imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import httpx
import structlog
import websockets

from src.infrastructure.data.schema import datetime_to_ns
from src.infrastructure.data.storage import MultiAssetRecorder
from src.infrastructure.polymarket.market_filters import (
    detect_asset as _detect_asset,
    is_live_crypto_market,
    live_crypto_window,
)

logger = structlog.get_logger(__name__)

# Polymarket endpoints
GAMMA_BASE_URL = "https://gamma-api.polymarket.com"
WS_BASE_URL    = "wss://ws-subscriptions-clob.polymarket.com/ws/market"

# Correct subscription format for Polymarket CLOB WebSocket API
# Docs: https://docs.polymarket.com/market-data/websocket/market-channel
# Uses "type": "market" with "assets_ids" (token IDs, NOT condition IDs)
WS_SUBSCRIBE_TYPE = "market"

# Defaults
DEFAULT_OUTPUT_DIR   = Path("data/historical")
DEFAULT_PARQUET_DIR  = Path("data/parquet")
FLUSH_EVERY_N_TICKS  = 100
WS_PING_INTERVAL     = 20

# Global flag for graceful shutdown
_shutdown_requested = False


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Record live Polymarket market data",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/record_live_data.py --asset BTC --duration-hours 24
  python scripts/record_live_data.py --asset ETH --format csv
  python scripts/record_live_data.py --market-id 0xabc...
  python scripts/record_live_data.py --all --format parquet --batch-size 16384
        """,
    )
    parser.add_argument("--asset", choices=["BTC", "ETH"],
                        help="Asset to record markets for")
    parser.add_argument("--all", action="store_true",
                        help="Record both BTC and ETH")
    parser.add_argument("--market-id",
                        help="Record a specific condition_id")
    parser.add_argument("--duration-hours", type=float, default=24.0,
                        help="Hours to record (default: 24, 0=indefinite)")
    parser.add_argument("--format", choices=["parquet", "csv"], default="parquet",
                        help="Output format (default: parquet)")
    parser.add_argument("--output-dir",
                        help="Output directory (default: parquet=data/parquet, csv=data/historical)")
    parser.add_argument("--batch-size", type=int, default=1000,
                        help="Parquet buffer batch size (default: 1000)")
    parser.add_argument("--verbose", action="store_true",
                        help="Print every tick received")
    parser.add_argument("--auto-rotate", action="store_true", default=True,
                        help="Auto-rotate to the next live crypto market when "
                             "the current one expires (B5 default: ON)")
    parser.add_argument("--no-auto-rotate", dest="auto_rotate",
                        action="store_false",
                        help="Disable auto-rotation (use one-shot discovery)")
    return parser.parse_args()


# ── Market Discovery ─────────────────────────────────────────────────────────

# ── Window matchers (alineado con MarketService._matches_window) ─────────────
# B4 fix (R1.2-bis): el filtro previo sólo verificaba el keyword del asset
# y aceptaba binarios longevos (p. ej. "Will bitcoin hit $1m before GTA VI?")
# que no corresponden a las ventanas M5/M15 del bot. Replicamos aquí la lógica
# canónica de src/application/services/market_service.py para mantener un único
# criterio de filtrado entre discovery online (MarketService) y recording offline.

_TIME_RANGE_PATTERN = re.compile(
    r"(\d{1,2}):(\d{2})\s*(?:AM|PM)?\s*-\s*(\d{1,2}):(\d{2})\s*(?:AM|PM)?"
)

# B5 fix: los markets live de Polymarket (ciclo cada 5/15 min) usan el patrón
# "Bitcoin Up or Down on June 14, 3:35PM ET" o
# "Ethereum Price - June 14 3:35PM ET", NO "-5m-" en slug ni rangos H:MM-H:MM.
# Estos markets se crean continuamente y rotan cuando el anterior termina.
# Se acepta cualquier "Up or Down" sobre BTC/ETH como M5 o M15 por su
# naturaleza: Polymarket sólo publica ventanas cortas para Up/Down crypto.

_UP_DOWN_CRYPTO_PATTERN = re.compile(
    r"\b(bitcoin|btc|ethereum|eth)\b\s+up\s+or\s+down\b",
    re.IGNORECASE,
)

_PRICE_TIME_ET_PATTERN = re.compile(
    r"\b(bitcoin|btc|ethereum|eth)\b[\s\w-]*?\bprice\b[\s\w-]*?"
    r"(?:\d{1,2}(?::\d{2})?\s*(?:am|pm)?\s*et\b)",
    re.IGNORECASE,
)

# Asset canónico de un market en base a su texto
_ASSET_PATTERN_BTC = re.compile(r"\b(bitcoin|btc)\b", re.IGNORECASE)
_ASSET_PATTERN_ETH = re.compile(r"\b(ethereum|eth)\b", re.IGNORECASE)


def _detect_asset(raw: dict) -> str | None:
    """Devuelve 'BTC' o 'ETH' según el texto del market, o None si no es
    ninguno de los dos. Evita falsos positivos como 'Ethiopia' o
    'Eth0x...' exigiendo que NO sea un asset distinto."""
    title = raw.get("title", "") or raw.get("question", "")
    slug = raw.get("slug", "")
    text = f"{title} {slug}"
    has_btc = bool(_ASSET_PATTERN_BTC.search(text))
    has_eth = bool(_ASSET_PATTERN_ETH.search(text))
    if has_btc and not has_eth:
        return "BTC"
    if has_eth and not has_btc:
        return "ETH"
    if has_btc and has_eth:
        # Empate ambiguo: priorizar el que aparezca primero
        m_btc = _ASSET_PATTERN_BTC.search(text)
        m_eth = _ASSET_PATTERN_ETH.search(text)
        if m_btc and m_eth:
            return "BTC" if m_btc.start() < m_eth.start() else "ETH"
    return None


def _is_live_crypto_market(raw: dict) -> tuple[bool, str | None, str | None]:
    """
    Detecta markets live de Polymarket (Up/Down o Price crypto).

    Polymarket publica estos markets en ciclos de 5 o 15 minutos. El formato
    del título es:
      - "Bitcoin Up or Down on June 14, 3:35PM ET"
      - "Ethereum Up or Down on June 14, 3:35PM ET"
      - "Bitcoin Price - June 14 3:35PM ET"
      - "Ethereum Price - June 14 3:35PM ET"

    Returns:
        (is_live, window, asset) donde:
          - is_live: True si es un market live crypto
          - window: "5m" o "15m" (heurística basada en duración hasta endDate)
          - asset: "BTC" o "ETH"
    """
    title = raw.get("title", "") or raw.get("question", "")
    slug = raw.get("slug", "")
    text = f"{title} {slug}"

    is_up_down = bool(_UP_DOWN_CRYPTO_PATTERN.search(text))
    is_price_et = bool(_PRICE_TIME_ET_PATTERN.search(text))
    if not (is_up_down or is_price_et):
        return False, None, None

    asset = _detect_asset(raw)
    if not asset:
        return False, None, None

    # Heurística de ventana: los markets Up/Down se publican tanto en M5
    # como en M15. Si no podemos distinguir por el slug, lo etiquetamos
    # conservadoramente como ambos para que la estrategia decida.
    if "-5m-" in slug or "-5-minute-" in slug:
        return True, "5m", asset
    if "-15m-" in slug or "-15-minute-" in slug:
        return True, "15m", asset

    # Sin marcador explícito: aceptar como 5m (los markets live crypto
    # más comunes en Polymarket son de 5 minutos; el ciclo de 15m
    # también existe pero suele etiquetarse con "15m" en el slug).
    # Devolver ambos casos requiere dos entradas — aquí simplificamos
    # a "5m" como ventana por defecto, y el caller puede pedir ambas.
    return True, "5m", asset


def _matches_window(raw: dict, *, window: str) -> bool:
    """True si el market corresponde a M5 (5m) o M15 (15m). Nunca acepta
    longevos. Acepta:
      1. slug con ``-5m-`` / ``-15m-``.
      2. question con rango horario que cuadre con la duración esperada.
      3. markets live crypto (Up or Down / Price crypto) — solo en su
         ventana explícita si está marcada, o ambos si no.
    Rechaza el resto."""
    slug = raw.get("slug", "")
    # Aceptar tanto "question" (markets) como "title" (events) — Polymarket
    # devuelve el título en uno u otro campo según el endpoint.
    question = raw.get("question", "") or raw.get("title", "")

    if window == "5m" and "-5m-" in slug:
        return True
    if window == "15m" and "-15m-" in slug:
        return True

    match = _TIME_RANGE_PATTERN.search(question)
    if match:
        h1, m1, h2, m2 = (int(g) for g in match.groups())
        start_mins = h1 * 60 + m1
        end_mins = h2 * 60 + m2
        if end_mins <= start_mins:
            end_mins += 12 * 60
        duration = end_mins - start_mins
        if window == "5m" and 2 <= duration <= 7:
            return True
        if window == "15m" and 12 <= duration <= 18:
            return True

    # B5: aceptar markets live crypto (Up or Down / Price crypto) cuya
    # ventana explícita coincida. Sin marcador, sólo aceptamos 5m por
    # ser la ventana más común.
    is_live, live_window, _ = _is_live_crypto_market(raw)
    if is_live and live_window == window:
        return True

    return False


async def find_markets_for_asset(
    asset: str,
    windows: tuple[str, ...] = ("5m", "15m"),
    max_per_window: int = 5,
) -> list[dict]:
    """Find active M5/M15 markets for a given asset via Gamma API.

    Rechaza binarios longevos (ej. *Will bitcoin hit $1m before GTA VI?*)
    aplicando el mismo filtro de ventana que el ``MarketService`` de
    producción. Solo retorna markets con `active=True` y `closed=False`.
    """
    async with httpx.AsyncClient(timeout=30) as client:
        keywords = ["bitcoin", "btc"] if asset == "BTC" else ["ethereum", "eth"]
        response = await client.get(
            f"{GAMMA_BASE_URL}/markets",
            params={"active": "true", "closed": "false", "_limit": "500"},
        )
        response.raise_for_status()
        all_markets = response.json()

        # Group by window, keep highest-volume markets per window.
        by_window: dict[str, list[dict]] = {w: [] for w in windows}
        seen: set[str] = set()

        for m in all_markets:
            cid = m.get("conditionId", "")
            if not cid or cid in seen:
                continue
            q = m.get("question", "").lower()
            if not any(k in q for k in keywords):
                continue
            for w in windows:
                if _matches_window(m, window=w):
                    by_window[w].append(m)
                    seen.add(cid)
                    break

        # Sort each window bucket by volume descending and pick top N.
        result: list[dict] = []
        for w in windows:
            bucket = sorted(
                by_window[w],
                key=lambda m: float(m.get("volume24hr", m.get("volume", 0)) or 0),
                reverse=True,
            )
            result.extend(bucket[:max_per_window])

        return result


async def find_live_crypto_markets(asset: str) -> list[dict]:
    """Find Polymarket live crypto markets (Up/Down o Price crypto) que
    están por abrir o acaban de abrir.

    A diferencia de ``find_markets_for_asset``, que solo busca markets
    actualmente activos, esta función busca los markets programados
    para resolverse en los próximos minutos (mercado "Bitcoin Up or
    Down on [date] [time] ET" o similar).

    Returns:
        Lista de markets ordenados por ``endDate`` ascendente (el más
        próximo a expirar primero). El primero es el que debería estar
        activo AHORA. Cuando expire, el siguiente de la lista toma el
        relevo en la rotación automática.
    """
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(
            f"{GAMMA_BASE_URL}/markets",
            params={
                "active": "true",
                "closed": "false",
                "order": "endDate",
                "ascending": "true",
                "_limit": "500",
            },
        )
        response.raise_for_status()
        all_markets = response.json()

        candidates: list[dict] = []
        seen: set[str] = set()
        for m in all_markets:
            cid = m.get("conditionId", "")
            if not cid or cid in seen:
                continue
            if not is_live_crypto_market(m):
                continue
            if _detect_asset(m) != asset:
                continue
            seen.add(cid)
            candidates.append(m)

        def _end_key(m: dict) -> float:
            end_str = m.get("endDate") or ""
            try:
                end_dt = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
                return end_dt.timestamp()
            except (ValueError, TypeError):
                return float("inf")  # Sin fecha válida → al final

        candidates.sort(key=_end_key)
        return candidates


def select_next_market_for_rotation(
    markets: list[dict],
    current_market_id: str | None,
) -> dict | None:
    """Selecciona el siguiente market para la rotación automática.

    Lógica:
      1. Si no hay mercado actual, devuelve el primero de la lista
         (el más próximo a expirar).
      2. Si hay un mercado actual y aún no expiró, lo mantiene.
      3. Si el mercado actual expiró, devuelve el siguiente disponible
         (que aún no haya expirado).

    Args:
        markets: Lista ordenada por endDate ascendente.
        current_market_id: condition_id del mercado actual (puede ser None).

    Returns:
        El mercado a procesar a continuación, o None si la lista está
        vacía o todos han expirado.
    """
    if not markets:
        return None

    now = datetime.now(timezone.utc)

    def _is_expired(m: dict) -> bool:
        end_str = m.get("endDate") or ""
        try:
            end_dt = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
            return end_dt <= now
        except (ValueError, TypeError):
            return False

    # Si no hay mercado actual, devolver el primero no expirado
    if not current_market_id:
        for m in markets:
            if not _is_expired(m):
                return m
        return None

    # Buscar el mercado actual
    current_idx = None
    for i, m in enumerate(markets):
        if m.get("conditionId") == current_market_id:
            current_idx = i
            break

    if current_idx is None:
        # El mercado actual ya no está en la lista (cerrado y desplazado)
        for m in markets:
            if not _is_expired(m):
                return m
        return None

    current = markets[current_idx]
    if not _is_expired(current):
        # El mercado actual sigue vigente
        return current

    # El mercado actual expiró — buscar el siguiente no expirado
    for m in markets[current_idx + 1:]:
        if not _is_expired(m):
            return m

    return None


def parse_market(m: dict) -> dict | None:
    """Extract market info from Gamma API response."""
    cid = m.get("conditionId", "")
    if not cid:
        return None

    # Primary: clobTokenIds (list of strings like ["123", "456"])
    clob_tokens = m.get("clobTokenIds", [])
    if isinstance(clob_tokens, str):
        try:
            clob_tokens = json.loads(clob_tokens)
        except (json.JSONDecodeError, TypeError):
            clob_tokens = []

    # Fallback: tokens field (list of objects with token_id key)
    tokens_objs = m.get("tokens", [])
    if isinstance(tokens_objs, str):
        try:
            tokens_objs = json.loads(tokens_objs)
        except (json.JSONDecodeError, TypeError):
            tokens_objs = []

    yes_tid = None
    no_tid = None

    # Prefer clobTokenIds first (direct string IDs)
    if clob_tokens:
        yes_tid = str(clob_tokens[0]) if clob_tokens else None
        no_tid = str(clob_tokens[1]) if len(clob_tokens) > 1 else None
    # Fallback: extract from tokens objects
    elif tokens_objs:
        for t in tokens_objs:
            outcome = str(t.get("outcome", "")).lower()
            tid = str(t.get("token_id", "")) if t.get("token_id") else None
            if tid:
                if outcome == "yes" and not yes_tid:
                    yes_tid = tid
                elif outcome == "no" and not no_tid:
                    no_tid = tid
        # If outcomes aren't labeled, use position 0=Yes, 1=No
        if not yes_tid and tokens_objs:
            yes_tid = str(tokens_objs[0].get("token_id", "")) or None
        if not no_tid and len(tokens_objs) > 1:
            no_tid = str(tokens_objs[1].get("token_id", "")) or None

    return {
        "condition_id": cid,
        "question": m.get("question", "")[:120],
        "yes_token_id": yes_tid,
        "no_token_id": no_tid,
        "active": m.get("active", False),
    }


# ── Stateful Tick Parsing ─────────────────────────────────────────────────────
#
# The Polymarket CLOB WebSocket sends two types of messages:
#   1. Initial snapshot: list containing full order book (bids + asks)
#   2. Price change events: incremental updates (price_changes array, NO bids/asks)
#
# We maintain a module-level cache of the last known order book state per market
# so that price_change events can be converted into complete tick records.

_book_cache: dict[str, dict] = {}


def init_book_state(market_id: str, yes_token_id: str) -> None:
    """Initialize/reinitialize order book state for a market before WS connect."""
    _book_cache[market_id] = {
        "best_bid": 0.0,
        "best_ask": 0.0,
        "bids_vols": [0.0, 0.0, 0.0],
        "asks_vols": [0.0, 0.0, 0.0],
        "volume": 0.0,
        "yes_token_id": yes_token_id,
        "initialized": False,
    }


def _build_tick(
    market_id: str,
    asset: str,
    best_bid: float,
    best_ask: float,
    state: dict,
    timestamp_raw=None,
) -> dict:
    """Build a normalized tick dict from book state."""
    yes_price = (best_bid + best_ask) / 2
    no_price = 1.0 - yes_price
    spread = best_ask - best_bid
    mid_price = yes_price
    volume = state.get("volume", 0.0)

    liquidity_score = None
    if volume > 0 and spread > 0:
        liquidity_score = round(volume / (1.0 + spread * 100), 2)

    # Timestamp: WS may send seconds (10-digit), ms (13-digit), or finer.
    # Normalise to seconds for datetime.fromtimestamp().
    if timestamp_raw:
        ts_int = int(timestamp_raw)
        while ts_int > 10_000_000_000:       # seconds fit in 10 digits
            ts_int = ts_int // 1000
        ts_dt = datetime.fromtimestamp(ts_int, tz=timezone.utc)
    else:
        ts_dt = datetime.now(timezone.utc)
    ts_ns = datetime_to_ns(ts_dt)

    bv = state.get("bids_vols", [0.0, 0.0, 0.0])
    av = state.get("asks_vols", [0.0, 0.0, 0.0])

    return {
        "timestamp_ns":    ts_ns,
        "market_id":       market_id,
        "asset":           asset,
        "yes_price":       round(yes_price, 4),
        "no_price":        round(no_price, 4),
        "mid_price":       round(mid_price, 4),
        "best_bid":        round(best_bid, 4),
        "best_ask":        round(best_ask, 4),
        "spread":          round(spread, 4),
        "volume_24h":      round(volume, 2),
        "liquidity_score": liquidity_score,
        "bids_vol_1":      round(bv[0], 2),
        "asks_vol_1":      round(av[0], 2),
        "bids_vol_2":      round(bv[1], 2),
        "asks_vol_2":      round(av[1], 2),
        "bids_vol_3":      round(bv[2], 2),
        "asks_vol_3":      round(av[2], 2),
    }


def _apply_book_snapshot(market_id: str, data: dict, asset: str) -> dict | None:
    """Parse a full order book snapshot and update the cache."""
    bids = data.get("bids", [])
    asks = data.get("asks", [])
    if not bids or not asks:
        return None

    best_bid = max(float(b["price"]) for b in bids)
    best_ask = min(float(a["price"]) for a in asks)
    volume = sum(float(b.get("size", 0)) for b in bids)

    # Order book depth (top 3 levels)
    bids_sorted = sorted(bids, key=lambda b: float(b["price"]), reverse=True)
    asks_sorted = sorted(asks, key=lambda a: float(a["price"]))

    def _vol(items, idx):
        return float(items[idx].get("size", 0)) if idx < len(items) else 0.0

    state = _book_cache.get(market_id, {})
    state["best_bid"] = best_bid
    state["best_ask"] = best_ask
    state["volume"] = volume
    state["bids_vols"] = [_vol(bids_sorted, 0), _vol(bids_sorted, 1), _vol(bids_sorted, 2)]
    state["asks_vols"] = [_vol(asks_sorted, 0), _vol(asks_sorted, 1), _vol(asks_sorted, 2)]
    state["initialized"] = True
    _book_cache[market_id] = state

    return _build_tick(market_id, asset, best_bid, best_ask, state, data.get("timestamp"))


def _apply_price_changes(market_id: str, data: dict, asset: str) -> dict | None:
    """Apply price_change events using best_bid/best_ask from the event itself.

    Each price_change object in Polymarket's WS already carries the current
    best_bid and best_ask, so we don't need to track incremental state.
    We update the cache and produce a tick from the first YES-token change.
    """
    state = _book_cache.get(market_id)
    if not state or not state.get("initialized"):
        return None

    yes_token_id = str(state.get("yes_token_id", ""))
    price_changes = data.get("price_changes", [])

    if not price_changes:
        return None

    # Find the first price_change for the YES token
    best_pc = None
    for pc in price_changes:
        pc_asset = str(pc.get("asset_id", ""))
        if pc_asset == yes_token_id:
            best_pc = pc
            break
    # No YES-token change found — skip (NO-token prices would need inversion)
    if not best_pc:
        return None

    # Use best_bid/best_ask from the price_change directly
    raw_bid = best_pc.get("best_bid")
    raw_ask = best_pc.get("best_ask")
    if not raw_bid or not raw_ask:
        return None

    best_bid = float(raw_bid)
    best_ask = float(raw_ask)

    if best_bid <= 0 or best_ask <= 0 or best_bid >= best_ask:
        return None

    # Update cache
    state["best_bid"] = best_bid
    state["best_ask"] = best_ask
    _book_cache[market_id] = state

    return _build_tick(market_id, asset, best_bid, best_ask, state, data.get("timestamp"))


def parse_ws_message(
    market_id: str,
    raw_message: str,
    asset: str = "",
) -> dict | None:
    """
    Parse a WebSocket message into a normalized tick dict.

    Uses a module-level order book cache to handle incremental price_change
    events that lack full bids/asks. The initial snapshot seeds the cache.

    Returns None if the message cannot produce a valid tick.
    """
    try:
        data = json.loads(raw_message)

        # ── Initial snapshot (list) ─────────────────────────────────
        if isinstance(data, list):
            for item in data:
                event_type = item.get("event_type", item.get("type", ""))
                if event_type in ("book", ""):
                    result = _apply_book_snapshot(market_id, item, asset)
                    if result:
                        return result
            # Empty list or no book items — expected for illiquid markets
            return None

        event_type = data.get("event_type", data.get("type", ""))

        # ── Full book snapshot ─────────────────────────────────────
        if event_type == "book":
            return _apply_book_snapshot(market_id, data, asset)

        # ── Price change event (carries best_bid/best_ask natively) ─
        if event_type == "price_change":
            return _apply_price_changes(market_id, data, asset)

        return None

    except (json.JSONDecodeError, KeyError, ValueError, TypeError) as e:
        print(f"  ⚠️  [{market_id[:12]}] Parse error: {type(e).__name__}: {e}")
        return None


# ── CSV TickRecorder (legacy) ────────────────────────────────────────────────

class CsvTickRecorder:
    """Legacy CSV-based tick recorder (backward compatibility)."""

    def __init__(self, output_dir: Path, verbose: bool = False):
        self._dir = output_dir
        self._dir.mkdir(parents=True, exist_ok=True)
        self._verbose = verbose
        self._writers: dict[str, dict] = {}

    def _open_writer(self, market_id: str, asset: str) -> None:
        short_id = market_id[:10] + ".." + market_id[-6:]
        date_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        filename = f"live_{asset}_{short_id}_{date_str}.csv"
        path = self._dir / filename

        f = open(path, "w", newline="")
        writer = csv.DictWriter(f, fieldnames=[
            "timestamp", "yes_price", "no_price", "best_bid", "best_ask",
            "spread", "volume_24h",
        ])
        writer.writeheader()
        self._writers[market_id] = {
            "file": f, "writer": writer, "count": 0, "path": path,
        }
        print(f"  📝 Recording to {path}")

    def record(self, market_id: str, tick_data: dict) -> None:
        w = self._writers.get(market_id)
        if not w:
            return

        from datetime import datetime, timezone
        ts_ns = tick_data.get("timestamp_ns", 0)
        ts_dt = datetime.fromtimestamp(ts_ns / 1_000_000_000, tz=timezone.utc)

        row = {
            "timestamp": ts_dt.isoformat(),
            "yes_price": tick_data["yes_price"],
            "no_price":  tick_data["no_price"],
            "best_bid":  tick_data["best_bid"],
            "best_ask":  tick_data["best_ask"],
            "spread":    tick_data["spread"],
            "volume_24h": tick_data["volume_24h"],
        }
        w["writer"].writerow(row)
        w["count"] += 1
        if w["count"] % FLUSH_EVERY_N_TICKS == 0:
            w["file"].flush()
        if self._verbose:
            print(f"  [{market_id[:12]}..] yes={tick_data['yes_price']:.4f} spread={tick_data['spread']:.4f} (#{w['count']})")

    def close_all(self) -> list[dict]:
        summaries = []
        for market_id, w in self._writers.items():
            w["file"].flush()
            w["file"].close()
            summaries.append({
                "market_id": market_id,
                "ticks": w["count"],
                "path": str(w["path"]),
            })
        self._writers.clear()
        return summaries


# ── WebSocket Listener ───────────────────────────────────────────────────────

async def listen_market_parquet(
    token_id: str,
    market_id: str,
    asset: str,
    recorder: MultiAssetRecorder,
    duration_hours: float,
    verbose: bool = False,
) -> int:
    """
    Connect to WebSocket and record ticks via MultiAssetRecorder (Parquet).

    token_id:  CLOB asset_id (numeric token ID) used for WS subscription
    market_id: condition_id (0x...) used for tick recording labels

    Returns number of ticks recorded.
    """
    url = WS_BASE_URL
    start_time = asyncio.get_event_loop().time()
    tick_count = 0
    short_id = market_id[:16]
    last_heartbeat = start_time

    # Initialize the stateful book cache before connecting
    init_book_state(market_id, token_id)

    while not _shutdown_requested:
        try:
            async with websockets.connect(
                url,
                ping_interval=WS_PING_INTERVAL,
                ping_timeout=30,
                close_timeout=10,
            ) as ws:
                # Polymarket CLOB WS API v2: "type": "market" with "assets_ids"
                sub_msg = json.dumps({
                    "assets_ids": [token_id],
                    "type": "market",
                })
                await ws.send(sub_msg)
                print(f"  🔗 Connected to {short_id}... (asset_id={token_id[:20]}...)")

                while not _shutdown_requested:
                    now = asyncio.get_event_loop().time()
                    elapsed = (now - start_time) / 3600
                    if duration_hours > 0 and elapsed >= duration_hours:
                        print(f"  ⏰ Duration reached ({duration_hours}h)")
                        return tick_count

                    # Heartbeat every 5 minutes
                    if now - last_heartbeat >= 300:
                        rate = tick_count / max(1, (now - start_time) / 3600)
                        print(f"  💓 [{short_id}..{asset}] {tick_count} ticks | "
                              f"{elapsed:.1f}h elapsed | {rate:.0f} ticks/h")
                        last_heartbeat = now

                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=30.0)
                    except asyncio.TimeoutError:
                        continue

                    tick = parse_ws_message(market_id, raw, asset)
                    if tick:
                        recorder.record_tick(asset, tick)
                        tick_count += 1

                        if verbose and tick_count % 100 == 0:
                            print(f"  [{short_id}..] yes={tick['yes_price']:.4f} spread={tick['spread']:.4f} (#{tick_count})")

        except asyncio.CancelledError:
            break
        except websockets.ConnectionClosed:
            print(f"  ⚠️  WS disconnected for {short_id}..., reconnecting...")
            await asyncio.sleep(5)
        except Exception as e:
            print(f"  ❌ Error on {short_id}...: {e}")
            await asyncio.sleep(10)

    return tick_count


async def listen_market_csv(
    token_id: str,
    market_id: str,
    asset: str,
    recorder: CsvTickRecorder,
    duration_hours: float,
    verbose: bool = False,
) -> int:
    """
    Legacy CSV listener (backward compatible).
    Returns number of ticks recorded.

    token_id:  CLOB asset_id (numeric) for WS subscription
    market_id: condition_id (0x...) for tick labelling
    """
    url = WS_BASE_URL
    start_time = asyncio.get_event_loop().time()
    tick_count = 0

    # Initialize the stateful book cache before connecting
    init_book_state(market_id, token_id)

    while not _shutdown_requested:
        try:
            async with websockets.connect(
                url,
                ping_interval=WS_PING_INTERVAL,
                ping_timeout=30,
                close_timeout=10,
            ) as ws:
                # Polymarket CLOB WS API v2: "type": "market" with "assets_ids"
                sub_msg = json.dumps({
                    "assets_ids": [token_id],
                    "type": "market",
                })
                await ws.send(sub_msg)
                print(f"  🔗 Connected to {market_id[:20]}... (token={token_id[:16]}...)")
                recorder._open_writer(market_id, asset)

                while not _shutdown_requested:
                    elapsed = (asyncio.get_event_loop().time() - start_time) / 3600
                    if duration_hours > 0 and elapsed >= duration_hours:
                        print(f"  ⏰ Duration reached ({duration_hours}h)")
                        return tick_count

                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=30.0)
                    except asyncio.TimeoutError:
                        continue

                    tick = parse_ws_message(market_id, raw, asset)
                    if tick:
                        recorder.record(market_id, tick)
                        tick_count += 1

        except asyncio.CancelledError:
            break
        except websockets.ConnectionClosed:
            print(f"  ⚠️  WS disconnected for {market_id[:20]}..., reconnecting...")
            await asyncio.sleep(5)
        except Exception as e:
            print(f"  ❌ Error on {market_id[:20]}...: {e}")
            await asyncio.sleep(10)

    return tick_count


# ── Signal Handling ──────────────────────────────────────────────────────────

def setup_signal_handlers():
    def handler(sig, frame):
        global _shutdown_requested
        print("\n  🛑 Shutdown requested...")
        _shutdown_requested = True
    signal.signal(signal.SIGINT, handler)
    signal.signal(signal.SIGTERM, handler)


# ── Main ─────────────────────────────────────────────────────────────────────

async def run_with_auto_rotation(
    assets: list[str],
    recorder,                       # MultiAssetRecorder o CsvTickRecorder
    use_parquet: bool,
    duration_hours: float,
    batch_size: int,
    verbose: bool,
) -> None:
    """
    B5: loop continuo de grabación con auto-rotación entre markets live.

    Cada ``rotation_interval`` segundos:
      1. Llama ``find_live_crypto_markets(asset)`` para refrescar la lista.
      2. Compara el primer mercado no expirado con el actual.
      3. Si es distinto, cancela la task WS del anterior y arranca una
         nueva con el siguiente market.

    Así el bot transiciona automáticamente del market "Bitcoin Up or
    Down on June 14 3:35PM ET" al siguiente ("3:40PM ET", etc.) sin
    intervención humana.
    """
    import asyncio
    rotation_interval = 30  # segundos entre polls de discovery

    start_time = asyncio.get_event_loop().time()
    all_tasks: list = []
    current_ids: dict[str, str | None] = {a: None for a in assets}

    try:
        while not _shutdown_requested:
            elapsed = (asyncio.get_event_loop().time() - start_time) / 3600
            if duration_hours > 0 and elapsed >= duration_hours:
                print(f"  ⏰ Duration reached ({duration_hours}h)")
                return

            for asset in assets:
                # 1) Refrescar lista de markets
                markets = await find_live_crypto_markets(asset)
                next_market = select_next_market_for_rotation(
                    markets, current_ids[asset],
                )
                if next_market is None:
                    continue
                next_id = next_market.get("conditionId", "")
                if next_id == current_ids[asset]:
                    # Mismo market, no rotar
                    continue

                # 2) Cancelar la task del market anterior (si existe)
                for t, cid, _q, _a in list(all_tasks):
                    if cid == current_ids[asset] and not t.done():
                        t.cancel()
                        all_tasks.remove((t, cid, _q, _a))

                # 3) Parsear el nuevo market
                info = parse_market(next_market)
                if not info:
                    continue
                ws_token_id = info.get("yes_token_id") or info.get("no_token_id")
                if not ws_token_id:
                    print(f"  ⚠️  No clobTokenIds for {info['condition_id'][:20]}")
                    continue

                print(
                    f"  🔄 [{asset}] Rotating to {info['question'][:60]} "
                    f"(end={next_market.get('endDate', '?')})"
                )

                if use_parquet:
                    recorder.start_session(
                        asset=asset,
                        market_id=info["condition_id"],
                        question=info["question"],
                    )
                    task = asyncio.create_task(
                        listen_market_parquet(
                            token_id=ws_token_id,
                            market_id=info["condition_id"],
                            asset=asset,
                            recorder=recorder,
                            duration_hours=duration_hours,
                            verbose=verbose,
                        ),
                        name=f"ws_{info['condition_id'][:20]}",
                    )
                else:
                    task = asyncio.create_task(
                        listen_market_csv(
                            token_id=ws_token_id,
                            market_id=info["condition_id"],
                            asset=asset,
                            recorder=recorder,
                            duration_hours=duration_hours,
                            verbose=verbose,
                        ),
                        name=f"ws_{info['condition_id'][:20]}",
                    )

                all_tasks.append(
                    (task, info["condition_id"], info["question"], asset)
                )
                current_ids[asset] = next_id

            # Esperar antes del próximo poll
            await asyncio.sleep(rotation_interval)

    except asyncio.CancelledError:
        pass
    finally:
        for t, *_ in all_tasks:
            if not t.done():
                t.cancel()
        if all_tasks:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*[t for t, *_ in all_tasks], return_exceptions=True),
                    timeout=5,
                )
            except asyncio.TimeoutError:
                pass


async def main() -> None:
    args = parse_args()
    setup_signal_handlers()

    use_parquet = args.format == "parquet"

    # ── Output directory ───────────────────────────────────────────────
    if args.output_dir:
        output_dir = Path(args.output_dir)
    elif use_parquet:
        output_dir = DEFAULT_PARQUET_DIR
    else:
        output_dir = DEFAULT_OUTPUT_DIR

    output_dir.mkdir(parents=True, exist_ok=True)

    print("═" * 65)
    print("  POLYBOT — Live Market Data Recorder")
    print(f"  Format:      {'Parquet (compressed)' if use_parquet else 'CSV (legacy)'}")
    print(f"  Output:      {output_dir.absolute()}")
    print(f"  Duration:    {args.duration_hours}h {'(indefinite)' if args.duration_hours == 0 else ''}")
    print(f"  Batch size:  {args.batch_size} ticks")
    print("═" * 65)

    # ── Initialize recorder ────────────────────────────────────────────
    if use_parquet:
        recorder = MultiAssetRecorder(
            base_dir=output_dir,
            batch_size=args.batch_size,
            verbose=args.verbose,
        )
    else:
        csv_recorder = CsvTickRecorder(output_dir, verbose=args.verbose)

    # ── Single market mode ──────────────────────────────────────────────
    if args.market_id:
        print(f"\n  Recording market: {args.market_id}")
        print("  ⚠️  Single-market mode requires a token_id (clobTokenId) for WS subscription.")
        print("     Use --asset or --all mode to auto-discover markets with token IDs.")
        sys.exit(1)

    # ── Asset mode ─────────────────────────────────────────────────────
    assets = []
    if args.all:
        assets = ["BTC", "ETH"]
    elif args.asset:
        assets = [args.asset]
    else:
        print("❌ Specify --asset, --all, or --market-id")
        sys.exit(1)

    all_market_infos: list = []  # placeholder; populated by inner loop
    all_tasks: list = []

    for asset in assets:
        print(f"\n{'─' * 65}")
        print(f"  ASSET: {asset}")
        print(f"{'─' * 65}")

        markets = await find_markets_for_asset(asset)
        if not markets:
            print(f"  ⚠️  No markets found for {asset}")
            continue

        for m in markets:
            info = parse_market(m)
            if not info:
                continue
            print(f"  📊 {info['question']}")
            print(f"     ID: {info['condition_id'][:30]}...")

            # Get token_id for WS subscription (clobTokenIds from Gamma API)
            ws_token_id = info.get("yes_token_id") or info.get("no_token_id")
            if not ws_token_id:
                print("     ⚠️  No clobTokenIds found — skipping")
                continue

            if use_parquet:
                recorder.start_session(
                    asset=asset,
                    market_id=info["condition_id"],
                    question=info["question"],
                )
                task = asyncio.create_task(
                    listen_market_parquet(
                        token_id=ws_token_id,
                        market_id=info["condition_id"],
                        asset=asset,
                        recorder=recorder,
                        duration_hours=args.duration_hours,
                        verbose=args.verbose,
                    ),
                    name=f"ws_{info['condition_id'][:20]}",
                )
            else:
                task = asyncio.create_task(
                    listen_market_csv(
                        token_id=ws_token_id,
                        market_id=info["condition_id"],
                        asset=asset,
                        recorder=csv_recorder,
                        duration_hours=args.duration_hours,
                        verbose=args.verbose,
                    ),
                    name=f"ws_{info['condition_id'][:20]}",
                )

            all_tasks.append((task, info["condition_id"], info["question"], asset))

    # ── Mode: B5 auto-rotation entre markets live crypto ────────────
    if args.auto_rotate and assets:
        print(f"\n  🔁 Auto-rotation ON (B5): polling every 30s for next live market")
        print(f"  📡 Recording {len(assets)} asset(s) with continuous rotation...")
        print("  Press Ctrl+C to stop early\n")
        # Usa el recorder (MultiAssetRecorder o CsvTickRecorder)
        active_recorder = recorder if use_parquet else csv_recorder
        await run_with_auto_rotation(
            assets=assets,
            recorder=active_recorder,
            use_parquet=use_parquet,
            duration_hours=args.duration_hours,
            batch_size=args.batch_size,
            verbose=args.verbose,
        )
        # Después de rotar, finalizar
        if use_parquet:
            manifest = recorder.finalize_all()
            total_ticks = manifest.get("total_ticks", 0)
            print(f"\n  ✅ {total_ticks} total ticks (auto-rotation mode)")
        else:
            summaries = csv_recorder.close_all()
            total_ticks = sum(s["ticks"] for s in summaries)
            print(f"\n  ✅ {total_ticks} total ticks (auto-rotation mode)")
        return

    if not all_tasks:
        print("\n  ❌ No markets found to record.")
        sys.exit(1)

    print(f"\n  📡 Recording {len(all_tasks)} markets...")
    print("  Press Ctrl+C to stop early\n")

    # ── Wait for all tasks ────────────────────────────────────────────
    try:
        results = await asyncio.gather(*[t for t, _, _, _ in all_tasks])
    except asyncio.CancelledError:
        results = []

    # ── Summary ────────────────────────────────────────────────────────
    print(f"\n{'═' * 65}")
    print("  RECORDING SUMMARY")
    print(f"{'═' * 65}")

    if use_parquet:
        manifest = recorder.finalize_all()
        total_ticks = manifest.get("total_ticks", 0)

        # Build per-market session summaries from listener results
        sessions = []
        for idx, (_, cid, question, asset) in enumerate(all_tasks):
            ticks = results[idx] if idx < len(results) else 0
            print(f"  {asset:>4} | {ticks:>8} ticks | {question[:60]}")
            sessions.append({
                "asset": asset,
                "market_id": cid,
                "question": question,
                "ticks": ticks,
            })

        print(f"\n  ✅ {total_ticks} total ticks across {len(sessions)} markets")
        print(f"  📋 Parquet: {output_dir.absolute()}/")
        print(f"  📋 Manifest: {output_dir.absolute()}/manifest.json")

        # Write manifest with correct session data
        manifest_path = output_dir / "manifest.json"
        manifest["sessions"] = sessions
        with open(manifest_path, "w") as f:
            json.dump(manifest, f, indent=2, default=str)

        # Quick size estimate
        total_size = sum(
            f.stat().st_size for f in output_dir.rglob("*.parquet")
        ) if output_dir.exists() else 0
        if total_size > 0:
            print(f"  💾 Total size: {total_size / 1024 / 1024:.1f} MB (zstd compressed)")

    else:
        summaries = csv_recorder.close_all()
        total_ticks = 0
        for s in summaries:
            print(f"  {s['ticks']:>6} ticks → {s['path']}")
            total_ticks += s["ticks"]
        print(f"\n  ✅ {total_ticks} total ticks across {len(summaries)} markets")

        manifest_path = output_dir / "live_manifest.json"
        with open(manifest_path, "w") as f:
            json.dump({
                "recorded_at": datetime.now(timezone.utc).isoformat(),
                "duration_hours": args.duration_hours,
                "total_ticks": total_ticks,
                "markets": summaries,
            }, f, indent=2)
        print(f"  📋 Manifest: {manifest_path}")


if __name__ == "__main__":
    asyncio.run(main())
