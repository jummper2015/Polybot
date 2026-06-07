# src/strategies/event_detector.py

"""
P11.4 — Event-Driven Trading: detect market events and adjust trading behavior.

Detects external information arrival as manifested through market data
(price shocks, volume surges, expiry proximity, spread explosions) and
produces EventResponse decisions (HALT, REDUCE_SIZE, BOOST_CONFIDENCE, ALLOW).

Architecture:
    MarketTick, Market
        │
        ▼
    EventDetector.feed_tick()   → updates rolling price/volume/spread buffers
    EventDetector.detect()       → checks for events against thresholds
        │
        ▼
    list[MarketEvent] (type, severity, confidence, reason)
        │
        ▼
    EventDetector.respond()      → EventResponse (action, size_mult, conf_mult)

Integration:
    RegimeAwareOrchestrator.should_enter()
        → EventDetector.detect(tick, market)
        → EventDetector.respond(events, order_size, confidence)
        → if HALT → return HOLD
        → if REDUCE_SIZE → reduce requested_amount
        → if BOOST_CONFIDENCE → boost signal.confidence

Design rationale:
    Polymarket prediction markets don't have traditional news feeds.
    Instead, "events" are detected from market data itself — price jumps,
    volume spikes, and spread explosions are the market's reaction to
    external information. This is more robust than trying to parse news.

Usage:
    detector = EventDetector()
    detector.feed_tick(tick, market)
    events = detector.detect(tick, market)
    response = detector.respond(events, order_size=10.0, confidence=0.8)
    if response.action == "HALT":
        return HOLD  # safety-first
"""

from dataclasses import dataclass, field
from enum import Enum

import structlog

from src.domain.entities.market import Market
from src.domain.value_objects.market_tick import MarketTick

logger = structlog.get_logger(__name__)


# ══════════════════════════════════════════════════════════════════════════
# EVENT TYPES
# ══════════════════════════════════════════════════════════════════════════


class EventType(Enum):
    """Market event types detected from tick data."""

    PRICE_SHOCK = "price_shock"
    """Sudden large price movement indicating new information arrival."""

    VOLUME_SURGE = "volume_surge"
    """Abnormal 24h volume spike indicating increased market activity."""

    EXPIRY_PROXIMITY = "expiry_proximity"
    """Market approaching expiry — uncertainty rises, liquidity may dry up."""

    SPREAD_EXPLOSION = "spread_explosion"
    """Bid-ask spread suddenly widening — liquidity crisis or uncertainty."""


class EventSeverity(Enum):
    """Severity levels for market events."""

    LOW = "low"
    """Advisory — minor adjustment to position sizing."""

    MEDIUM = "medium"
    """Caution — significant size reduction recommended."""

    HIGH = "high"
    """Danger — halt new entries, exits still allowed."""

    CRITICAL = "critical"
    """Emergency — halt all trading activity."""


class ResponseAction(Enum):
    """Actions the trading system should take in response to events."""

    ALLOW = "allow"
    """Normal trading — no restrictions."""

    REDUCE_SIZE = "reduce_size"
    """Reduce position size by response.size_multiplier."""

    BOOST_CONFIDENCE = "boost_confidence"
    """Increase signal confidence by response.confidence_multiplier."""

    HALT = "halt"
    """Block new entries entirely — safety-first."""


# ══════════════════════════════════════════════════════════════════════════
# DATA CLASSES
# ══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class MarketEvent:
    """A detected market event with type, severity, and metadata."""

    event_type: EventType
    severity: EventSeverity
    confidence: float
    """Confidence in this event detection (0.0-1.0)."""

    reason: str
    """Human-readable reason for the event."""

    market_id: str = ""
    """Market where the event was detected."""

    tick_price: float = 0.0
    """Price at time of detection."""

    tick_spread: float = 0.0
    """Spread at time of detection."""

    tick_volume: float = 0.0
    """Volume at time of detection."""

    @property
    def is_actionable(self) -> bool:
        """Whether this event requires a response (MEDIUM severity or above)."""
        return self.severity in (
            EventSeverity.MEDIUM,
            EventSeverity.HIGH,
            EventSeverity.CRITICAL,
        )

    @property
    def is_blocking(self) -> bool:
        """Whether this event should block new entries (HIGH or CRITICAL)."""
        return self.severity in (EventSeverity.HIGH, EventSeverity.CRITICAL)


@dataclass(frozen=True)
class EventResponse:
    """Recommended trading response based on detected events."""

    action: ResponseAction
    """What the system should do."""

    size_multiplier: float = 1.0
    """Multiplier to apply to position size (only for REDUCE_SIZE)."""

    confidence_multiplier: float = 1.0
    """Multiplier to apply to signal confidence (only for BOOST_CONFIDENCE)."""

    reasons: list[str] = field(default_factory=list)
    """Reasons for the response (from triggering events)."""

    triggering_events: int = 0
    """Number of events that contributed to this response."""

    @property
    def should_halt(self) -> bool:
        return self.action == ResponseAction.HALT

    @property
    def should_reduce(self) -> bool:
        return self.action == ResponseAction.REDUCE_SIZE

    @property
    def should_boost(self) -> bool:
        return self.action == ResponseAction.BOOST_CONFIDENCE


# ══════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ══════════════════════════════════════════════════════════════════════════


@dataclass
class EventDetectorConfig:
    """Configuration for the EventDetector."""

    # Price shock: sudden price movement thresholds (fraction of price)
    price_shock_low: float = 0.05
    """Price change > 5% in window → LOW severity."""

    price_shock_high: float = 0.15
    """Price change > 15% in window → HIGH severity."""

    price_window_ticks: int = 3
    """Number of ticks to look back for price shock detection."""

    # Volume surge: spike relative to rolling average
    volume_surge_multiplier: float = 3.0
    """Volume > baseline × multiplier → surge detected."""

    volume_window_ticks: int = 20
    """Rolling window for volume baseline."""

    # Expiry proximity thresholds (minutes)
    expiry_low_minutes: float = 30.0
    """LOW severity when expiry within 30 minutes."""

    expiry_medium_minutes: float = 10.0
    """MEDIUM severity when expiry within 10 minutes."""

    expiry_high_minutes: float = 5.0
    """HIGH severity when expiry within 5 minutes."""

    # Spread explosion: widening relative to rolling average
    spread_explosion_multiplier: float = 3.0
    """Spread > baseline × multiplier → explosion detected."""

    spread_window_ticks: int = 20
    """Rolling window for spread baseline."""

    # Event cooldown: minimum seconds between emitting the same event type
    event_cooldown_seconds: float = 300.0
    """Seconds before the same event type can fire again per market."""

    # Response parameters
    halt_size_multiplier: float = 0.0
    """Effective size when HALT (0 = no trade)."""

    reduce_size_medium: float = 0.50
    """Size multiplier for MEDIUM severity events."""

    reduce_size_high: float = 0.25
    """Size multiplier for HIGH severity events."""

    boost_confidence_multiplier: float = 1.15
    """Confidence boost for favorable LOW-severity events."""

    def validate(self) -> None:
        """Validate configuration parameters."""
        if not 0.0 < self.price_shock_low < self.price_shock_high < 1.0:
            raise ValueError(
                "price_shock_low must be < price_shock_high, both in (0, 1)"
            )
        if self.price_window_ticks < 2:
            raise ValueError("price_window_ticks must be >= 2")
        if self.volume_surge_multiplier <= 1.0:
            raise ValueError("volume_surge_multiplier must be > 1.0")
        if self.spread_explosion_multiplier <= 1.0:
            raise ValueError("spread_explosion_multiplier must be > 1.0")
        if not 0.0 < self.reduce_size_high < self.reduce_size_medium <= 1.0:
            raise ValueError(
                "reduce_size_high must be < reduce_size_medium, both in (0, 1]"
            )
        if self.event_cooldown_seconds < 0:
            raise ValueError("event_cooldown_seconds must be >= 0")


# ══════════════════════════════════════════════════════════════════════════
# EVENT DETECTOR
# ══════════════════════════════════════════════════════════════════════════


class EventDetector:
    """Detects market events from tick data and recommends trading responses.

    Maintains rolling buffers per market for price, volume, and spread.
    On each tick, checks against configurable thresholds to detect
    price shocks, volume surges, expiry proximity, and spread explosions.

    Events have a cooldown period to prevent duplicate alerts on
    consecutive ticks during the same event.
    """

    def __init__(self, config: EventDetectorConfig | None = None):
        self._config = config or EventDetectorConfig()
        self._config.validate()

        # Rolling buffers per market
        self._price_history: dict[str, list[float]] = {}
        self._volume_history: dict[str, list[float]] = {}
        self._spread_history: dict[str, list[float]] = {}

        # Cooldown tracking: (market_id, event_type) → last emission timestamp
        self._cooldowns: dict[tuple[str, str], float] = {}

    # ── Public API ──────────────────────────────────────────────────────────

    def feed_tick(self, tick: MarketTick, market: Market) -> None:
        """Feed a new tick into the detector's rolling buffers.

        Must be called BEFORE detect() to ensure buffers are current.

        Args:
            tick: The latest market tick.
            market: The market entity (for expiry data).
        """
        mid = tick.market_id

        self._price_history.setdefault(mid, []).append(tick.yes_price)
        self._volume_history.setdefault(mid, []).append(tick.volume_24h)
        self._spread_history.setdefault(mid, []).append(tick.spread)

        # Trim buffers to window size
        cfg = self._config
        for hist, window in [
            (self._price_history[mid], cfg.price_window_ticks * 3),
            (self._volume_history[mid], cfg.volume_window_ticks),
            (self._spread_history[mid], cfg.spread_window_ticks),
        ]:
            while len(hist) > window:
                hist.pop(0)

    def detect(
        self, tick: MarketTick, market: Market, current_time: float | None = None
    ) -> list[MarketEvent]:
        """Detect market events for the current tick.

        Args:
            tick: The latest market tick.
            market: The market entity.
            current_time: Unix timestamp for cooldown tracking.
                          If None, uses tick.timestamp.timestamp().

        Returns:
            List of MarketEvent objects (may be empty if no events detected).
        """
        mid = tick.market_id
        now = current_time or tick.timestamp.timestamp()
        events: list[MarketEvent] = []

        # ── 1. Price shock detection ────────────────────────────────────
        price_event = self._detect_price_shock(tick, market, now)
        if price_event:
            events.append(price_event)

        # ── 2. Volume surge detection ───────────────────────────────────
        volume_event = self._detect_volume_surge(tick, market, now)
        if volume_event:
            events.append(volume_event)

        # ── 3. Expiry proximity detection ───────────────────────────────
        expiry_event = self._detect_expiry_proximity(tick, market, now)
        if expiry_event:
            events.append(expiry_event)

        # ── 4. Spread explosion detection ───────────────────────────────
        spread_event = self._detect_spread_explosion(tick, market, now)
        if spread_event:
            events.append(spread_event)

        if events:
            logger.info(
                "events_detected",
                market_id=mid,
                count=len(events),
                types=[e.event_type.value for e in events],
                severities=[e.severity.value for e in events],
            )

        return events

    def respond(
        self,
        events: list[MarketEvent],
        order_size: float,
        confidence: float,
    ) -> EventResponse:
        """Determine the appropriate trading response to detected events.

        Priority: HALT > REDUCE_SIZE > BOOST_CONFIDENCE > ALLOW.
        The most restrictive action wins when multiple events conflict.

        Args:
            events: Detected market events (from detect()).
            order_size: Intended position size in USDC.
            confidence: Intended signal confidence (0.0-1.0).

        Returns:
            EventResponse with recommended action and multipliers.
        """
        if not events:
            return EventResponse(action=ResponseAction.ALLOW)

        cfg = self._config
        reasons: list[str] = []

        # Check for blocking events first (HIGH/CRITICAL → HALT)
        blocking = [e for e in events if e.is_blocking]
        if blocking:
            reasons = [f"{e.event_type.value}({e.severity.value}): {e.reason}" for e in blocking]
            return EventResponse(
                action=ResponseAction.HALT,
                size_multiplier=cfg.halt_size_multiplier,
                reasons=reasons,
                triggering_events=len(blocking),
            )

        # Check for MEDIUM severity events → REDUCE_SIZE
        medium = [e for e in events if e.severity == EventSeverity.MEDIUM]
        if medium:
            # Expiry proximity gets the most aggressive reduction
            size_mult = (
                cfg.reduce_size_high
                if any(e.event_type == EventType.EXPIRY_PROXIMITY for e in medium)
                else cfg.reduce_size_medium
            )

            reasons = [
                f"{e.event_type.value}({e.severity.value}): {e.reason}"
                for e in medium
            ]
            return EventResponse(
                action=ResponseAction.REDUCE_SIZE,
                size_multiplier=size_mult,
                reasons=reasons,
                triggering_events=len(medium),
            )

        # LOW severity events → BOOST_CONFIDENCE (favorable information arrival)
        low_events = [e for e in events if e.severity == EventSeverity.LOW]
        if low_events:
            reasons = [f"{e.event_type.value}: {e.reason}" for e in low_events]
            # Only boost for price_shock or volume_surge (not expiry/spread)
            boostable = [
                e for e in low_events
                if e.event_type in (EventType.PRICE_SHOCK, EventType.VOLUME_SURGE)
            ]
            if boostable:
                return EventResponse(
                    action=ResponseAction.BOOST_CONFIDENCE,
                    confidence_multiplier=cfg.boost_confidence_multiplier,
                    reasons=reasons,
                    triggering_events=len(boostable),
                )

        # Default: allow with no modification
        return EventResponse(action=ResponseAction.ALLOW, reasons=reasons)

    def reset(self, market_id: str) -> None:
        """Reset all buffers and cooldowns for a market."""
        self._price_history.pop(market_id, None)
        self._volume_history.pop(market_id, None)
        self._spread_history.pop(market_id, None)
        # Clean cooldowns for this market
        self._cooldowns = {
            k: v for k, v in self._cooldowns.items() if k[0] != market_id
        }

    # ── Internal Detection Methods ──────────────────────────────────────────

    def _detect_price_shock(
        self,
        tick: MarketTick,
        market: Market,
        now: float,
    ) -> MarketEvent | None:
        """Detect sudden price movements."""
        cfg = self._config
        mid = tick.market_id
        prices = self._price_history.get(mid, [])

        if len(prices) < cfg.price_window_ticks:
            return None

        # Compare current price to price N ticks ago
        old_price = prices[-cfg.price_window_ticks]
        if old_price <= 0:
            return None

        change = abs(tick.yes_price - old_price) / old_price

        if change > cfg.price_shock_high:
            severity = EventSeverity.HIGH
        elif change > cfg.price_shock_low:
            severity = EventSeverity.LOW
        else:
            return None

        if not self._check_cooldown(mid, EventType.PRICE_SHOCK.value, now):
            return None

        direction = "up" if tick.yes_price > old_price else "down"
        return MarketEvent(
            event_type=EventType.PRICE_SHOCK,
            severity=severity,
            confidence=min(1.0, change / 0.20),
            reason=(
                f"price moved {direction} {change:.1%} in {cfg.price_window_ticks} ticks "
                f"({old_price:.4f} → {tick.yes_price:.4f})"
            ),
            market_id=mid,
            tick_price=tick.yes_price,
            tick_spread=tick.spread,
            tick_volume=tick.volume_24h,
        )

    def _detect_volume_surge(
        self,
        tick: MarketTick,
        market: Market,
        now: float,
    ) -> MarketEvent | None:
        """Detect abnormal volume spikes vs rolling average."""
        cfg = self._config
        mid = tick.market_id
        volumes = self._volume_history.get(mid, [])

        if len(volumes) < max(3, cfg.volume_window_ticks // 2):
            return None

        # Baseline: median of recent volumes (exclude current)
        recent = volumes[-cfg.volume_window_ticks:-1] if len(volumes) > 1 else volumes
        if not recent:
            return None

        baseline = self._median(recent)
        if baseline <= 0:
            return None

        surge_ratio = tick.volume_24h / baseline

        if surge_ratio < cfg.volume_surge_multiplier:
            return None

        severity = (
            EventSeverity.HIGH if surge_ratio > cfg.volume_surge_multiplier * 2
            else EventSeverity.LOW
        )

        if not self._check_cooldown(mid, EventType.VOLUME_SURGE.value, now):
            return None

        return MarketEvent(
            event_type=EventType.VOLUME_SURGE,
            severity=severity,
            confidence=min(1.0, (surge_ratio - 1.0) / 4.0),
            reason=(
                f"volume surged {surge_ratio:.1f}x vs baseline "
                f"({baseline:.0f} → {tick.volume_24h:.0f} USDC)"
            ),
            market_id=mid,
            tick_price=tick.yes_price,
            tick_spread=tick.spread,
            tick_volume=tick.volume_24h,
        )

    def _detect_expiry_proximity(
        self,
        tick: MarketTick,
        market: Market,
        now: float,
    ) -> MarketEvent | None:
        """Detect markets approaching expiry."""
        cfg = self._config
        mid = tick.market_id

        minutes_left = market.minutes_to_expiry()

        if minutes_left <= cfg.expiry_high_minutes:
            severity = EventSeverity.HIGH
        elif minutes_left <= cfg.expiry_medium_minutes:
            severity = EventSeverity.MEDIUM
        elif minutes_left <= cfg.expiry_low_minutes:
            severity = EventSeverity.LOW
        else:
            return None

        if not self._check_cooldown(mid, EventType.EXPIRY_PROXIMITY.value, now):
            return None

        return MarketEvent(
            event_type=EventType.EXPIRY_PROXIMITY,
            severity=severity,
            confidence=min(1.0, 30.0 / max(minutes_left, 1.0)),
            reason=(
                f"market expiring in {minutes_left:.0f} min "
                f"(expiry={market.expiry.isoformat()})"
            ),
            market_id=mid,
            tick_price=tick.yes_price,
            tick_spread=tick.spread,
            tick_volume=tick.volume_24h,
        )

    def _detect_spread_explosion(
        self,
        tick: MarketTick,
        market: Market,
        now: float,
    ) -> MarketEvent | None:
        """Detect sudden spread widening vs rolling average."""
        cfg = self._config
        mid = tick.market_id
        spreads = self._spread_history.get(mid, [])

        if len(spreads) < max(3, cfg.spread_window_ticks // 2):
            return None

        # Baseline: median of recent spreads (exclude current)
        recent = spreads[-cfg.spread_window_ticks:-1] if len(spreads) > 1 else spreads
        if not recent:
            return None

        baseline = self._median(recent)
        if baseline <= 0:
            return None

        spread_ratio = tick.spread / baseline

        if spread_ratio < cfg.spread_explosion_multiplier:
            return None

        severity = (
            EventSeverity.HIGH if spread_ratio > cfg.spread_explosion_multiplier * 2.5
            else EventSeverity.MEDIUM
        )

        if not self._check_cooldown(mid, EventType.SPREAD_EXPLOSION.value, now):
            return None

        return MarketEvent(
            event_type=EventType.SPREAD_EXPLOSION,
            severity=severity,
            confidence=min(1.0, (spread_ratio - 1.0) / 5.0),
            reason=(
                f"spread widened {spread_ratio:.1f}x vs baseline "
                f"({baseline:.4f} → {tick.spread:.4f})"
            ),
            market_id=mid,
            tick_price=tick.yes_price,
            tick_spread=tick.spread,
            tick_volume=tick.volume_24h,
        )

    # ── Internal Helpers ────────────────────────────────────────────────────

    def _check_cooldown(self, market_id: str, event_key: str, now: float) -> bool:
        """Check if an event type is still on cooldown for this market.

        Returns True if the event can fire (cooldown expired or first time).
        """
        key = (market_id, event_key)
        last = self._cooldowns.get(key, 0.0)
        if now - last < self._config.event_cooldown_seconds:
            return False
        self._cooldowns[key] = now
        return True

    @staticmethod
    def _median(values: list[float]) -> float:
        """Compute median of a list (robust to outliers)."""
        if not values:
            return 0.0
        s = sorted(values)
        n = len(s)
        if n % 2 == 0:
            return (s[n // 2 - 1] + s[n // 2]) / 2.0
        return s[n // 2]
