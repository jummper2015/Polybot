"""
Shared filters for Polymarket market discovery (B5 fix).

Single source of truth for the regex patterns and helper functions used
to identify Polymarket live crypto markets (Up/Down, Price crypto) and
to disambiguate BTC vs ETH avoiding false positives like "Ethiopia".

Both ``MarketService`` (production discovery) and ``record_live_data.py``
(recording) import from here to guarantee consistent filtering rules.
"""

from __future__ import annotations

import re
from typing import Optional

# ── Pattern: "Bitcoin Up or Down on [date]" / "Ethereum Up or Down on [date]" ──
# Polymarket publishes Up/Down crypto markets every 5/15 minutes.
UP_DOWN_CRYPTO_PATTERN = re.compile(
    r"\b(bitcoin|btc|ethereum|eth)\b\s+up\s+or\s+down\b",
    re.IGNORECASE,
)

# ── Pattern: "Bitcoin Price - [date] [time] ET" / "Ethereum Price - ..." ──
# The "ET" suffix at the end is required to avoid matching generic "price" mentions.
PRICE_ET_PATTERN = re.compile(
    r"\b(bitcoin|btc|ethereum|eth)\b[\s\w-]*?\bprice\b[\s\w-]*?"
    r"(?:\d{1,2}(?::\d{2})?\s*(?:am|pm)?\s*et\b)",
    re.IGNORECASE,
)

# ── Asset detection (avoids "Ethiopia" false positive) ──
ASSET_PATTERN_BTC = re.compile(r"\b(bitcoin|btc)\b", re.IGNORECASE)
ASSET_PATTERN_ETH = re.compile(r"\b(ethereum|eth)\b", re.IGNORECASE)


def detect_asset(raw: dict) -> Optional[str]:
    """
    Returns ``"BTC"`` or ``"ETH"`` based on the market text, or ``None`` if
    neither asset is mentioned. Avoids false positives like "Ethiopia" by
    requiring word boundaries on both sides.
    """
    title = raw.get("title", "") or raw.get("question", "")
    slug = raw.get("slug", "")
    text = f"{title} {slug}"
    has_btc = bool(ASSET_PATTERN_BTC.search(text))
    has_eth = bool(ASSET_PATTERN_ETH.search(text))
    if has_btc and not has_eth:
        return "BTC"
    if has_eth and not has_btc:
        return "ETH"
    if has_btc and has_eth:
        # Ambiguous (e.g. "BTC vs ETH" comparison): pick whichever appears first.
        m_btc = ASSET_PATTERN_BTC.search(text)
        m_eth = ASSET_PATTERN_ETH.search(text)
        if m_btc and m_eth:
            return "BTC" if m_btc.start() < m_eth.start() else "ETH"
    return None


def is_live_crypto_market(raw: dict) -> bool:
    """
    Returns True if the raw market dict matches the Polymarket live crypto
    pattern (Up/Down or Price crypto) for either BTC or ETH.
    """
    title = raw.get("title", "") or raw.get("question", "")
    slug = raw.get("slug", "")
    text = f"{title} {slug}"
    if not (UP_DOWN_CRYPTO_PATTERN.search(text) or PRICE_ET_PATTERN.search(text)):
        return False
    return detect_asset(raw) is not None


def live_crypto_window(raw: dict) -> Optional[str]:
    """
    Heuristic to identify the window (5m/15m) of a live crypto market based
    on slug markers. Returns ``"5m"`` by default since most live crypto
    markets on Polymarket cycle every 5 minutes; ``"15m"`` only when the
    slug explicitly contains a 15m marker.
    """
    if not is_live_crypto_market(raw):
        return None
    slug = raw.get("slug", "")
    if "-15m-" in slug or "-15-minute-" in slug:
        return "15m"
    return "5m"
