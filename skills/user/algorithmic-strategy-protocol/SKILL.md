---
name: algorithmic-strategy-protocol
description: >
  Define el protocolo ABC obligatorio para todas las estrategias algorítmicas
  del bot: on_cycle_start / on_tick / should_enter / should_exit / on_exit.
  Activa cuando se implementa, modifica o revisa cualquier estrategia
  (incluyendo Buy Above Threshold), cuando se diseña el Strategy Engine,
  cuando se añade un nuevo filtro de entrada/salida, o cuando se discute
  cómo el ciclo tick → señal → decisión → orden debe fluir. También activa
  ante cualquier intento de añadir una nueva estrategia al proyecto.
---

# Skill: Algorithmic Strategy Protocol

## Overview

Este skill define el contrato común que TODA estrategia del bot debe
implementar, y el flujo exacto en que el Strategy Engine llama a ese contrato
durante cada ciclo de mercado. Garantiza que la arquitectura modular de
`strategies/` sea extensible sin romper el Strategy Engine ni el Risk Engine.

El protocolo está diseñado para el ciclo:

```
MarketCycle recibido
  │
  ├─ on_cycle_start(cycle)      → inicializa estado interno de la estrategia
  │
  ├─ [por cada MarketTick]
  │    on_tick(tick)            → actualiza indicadores internos
  │
  ├─ should_enter(cycle, tick)  → ¿generar señal de entrada?
  │    │
  │    ├─ NO  → nada, esperar siguiente tick
  │    └─ SÍ  → StrategySignal(direction=ENTER) al Risk Engine
  │
  ├─ should_exit(cycle, tick, position) → ¿generar señal de salida?
  │    │
  │    ├─ NO  → mantener posición
  │    └─ SÍ  → StrategySignal(direction=EXIT) al Risk Engine
  │
  └─ on_exit(cycle, result)     → limpia estado, registra resultado
```

---

## Cuándo usar este skill

Activa cuando:

- Se implementa o modifica cualquier archivo en `strategies/`.
- Se implementa `application/services/strategy_engine.py`.
- Se añaden nuevos filtros de entrada (spread, liquidez, confirmación por
  ticks, filtros temporales) a una estrategia existente.
- Se revisa el flujo completo tick → señal → decisión → orden.
- Se discute si una lógica pertenece a la estrategia o al Risk Engine.

NO activas para:
- Lógica de descubrimiento de mercados (usar `polymarket-market-discovery`).
- Lógica de ejecución de órdenes (usar `paper-vs-real-execution-mode`).
- Reglas de riesgo y allow/deny (esas viven en `risk/`, no en `strategies/`).

---

## Decisions Log del protocolo (inamovible)

Estas decisiones están fijadas. Claude Code no puede cambiarlas sin
aprobación explícita del humano y actualización del SPEC.md del proyecto.

| Decisión | Valor fijado |
|---|---|
| Estrategia inicial obligatoria | Buy Above Threshold |
| Interfaz de estrategia | ABC con 5 métodos obligatorios |
| Tipo de señal de salida | StrategySignal con campo `direction` |
| Separación estrategia/riesgo | Las estrategias NO toman decisiones de riesgo |
| Nombre del Engine | StrategyEngine (no StrategyManager, no StrategyRunner) |
| Registro de estrategias | Dict con key = nombre string, value = instancia |

---

## Contratos de dominio (inamovibles)

### BaseStrategy (ABC)

```python
# strategies/base.py
from abc import ABC, abstractmethod
from domain.models.market import MarketCycle, MarketTick
from domain.models.signal import StrategySignal
from domain.models.position import Position

class BaseStrategy(ABC):
    """Protocolo que toda estrategia debe implementar.
    No añadir métodos concretos aquí sin actualizar el Decisions Log."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Nombre único de la estrategia. Usado como clave de registro."""
        ...

    @abstractmethod
    def on_cycle_start(self, cycle: MarketCycle) -> None:
        """Se llama una vez al inicio de cada MarketCycle.
        Usar para resetear estado interno (ventanas de precio, contadores)."""
        ...

    @abstractmethod
    def on_tick(self, tick: MarketTick) -> None:
        """Se llama por cada MarketTick recibido durante el ciclo.
        Actualizar indicadores internos. NO generar señales aquí."""
        ...

    @abstractmethod
    def should_enter(self, cycle: MarketCycle, tick: MarketTick) -> StrategySignal | None:
        """Evalúa si se debe entrar en posición.
        Retorna StrategySignal si hay señal, None si no.
        Esta función debe ser PURA: mismo input → mismo output."""
        ...

    @abstractmethod
    def should_exit(
        self,
        cycle: MarketCycle,
        tick: MarketTick,
        position: Position,
    ) -> StrategySignal | None:
        """Evalúa si se debe salir de una posición abierta.
        Retorna StrategySignal si hay señal, None si no."""
        ...

    @abstractmethod
    def on_exit(self, cycle: MarketCycle, result: "TradeResult") -> None:
        """Se llama tras cerrar una posición. Limpiar estado interno."""
        ...
```

### StrategySignal

```python
# domain/models/signal.py
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

class SignalDirection(str, Enum):
    ENTER = "ENTER"
    EXIT  = "EXIT"

class Outcome(str, Enum):
    YES = "YES"
    NO  = "NO"

@dataclass(frozen=True)
class StrategySignal:
    strategy_name: str
    market_id: str
    direction: SignalDirection
    outcome: Outcome           # en qué outcome operar (YES o NO)
    confidence: float          # 0.0 – 1.0, calculado por la estrategia
    price_at_signal: float     # precio del outcome en el momento de la señal
    reason: str                # texto legible para logs y Telegram
    timestamp: datetime = None

    def __post_init__(self):
        object.__setattr__(self, "timestamp", self.timestamp or datetime.utcnow())
        assert 0.0 <= self.confidence <= 1.0, "confidence debe estar entre 0 y 1"
```

---

## Implementación de referencia: Buy Above Threshold

Esta es la estrategia inicial obligatoria. Implementarla exactamente así.
No modificar el algoritmo central sin actualizar el Decisions Log.

```python
# strategies/buy_above_threshold.py
from dataclasses import dataclass, field
from domain.models.market import MarketCycle, MarketTick
from domain.models.signal import StrategySignal, SignalDirection, Outcome
from domain.models.position import Position
from strategies.base import BaseStrategy

@dataclass
class BuyAboveThresholdConfig:
    threshold: float = 0.70       # precio YES mínimo para entrar
    min_liquidity: float = 100.0  # USDC mínimos en cada lado del book
    max_spread: float = 0.05      # spread máximo tolerado (5%)
    confirm_ticks: int = 2        # ticks consecutivos que deben confirmar
    stop_loss: float = 0.10       # bajar 10% del precio de entrada → salir
    stop_drop: float = 0.15       # precio cae 15 puntos absolutos → salir
    timeout_cycles: int = 3       # máximo de ciclos sin salida → salir igual

class BuyAboveThreshold(BaseStrategy):
    """Entra en YES cuando el precio supera el umbral con confirmación.
    Aplica filtros de liquidez, spread, stop-loss y timeout."""

    def __init__(self, config: BuyAboveThresholdConfig | None = None):
        self.cfg = config or BuyAboveThresholdConfig()
        self._ticks_above: int = 0
        self._entry_price: float | None = None
        self._cycles_open: int = 0

    @property
    def name(self) -> str:
        return "buy_above_threshold"

    def on_cycle_start(self, cycle: MarketCycle) -> None:
        self._ticks_above = 0
        if self._entry_price is None:
            self._cycles_open = 0
        else:
            self._cycles_open += 1

    def on_tick(self, tick: MarketTick) -> None:
        if tick.yes_price >= self.cfg.threshold:
            self._ticks_above += 1
        else:
            self._ticks_above = 0  # reiniciar si baja del umbral

    def should_enter(self, cycle: MarketCycle, tick: MarketTick) -> StrategySignal | None:
        if self._entry_price is not None:
            return None  # ya hay posición abierta

        if not self._passes_filters(tick):
            return None

        if self._ticks_above < self.cfg.confirm_ticks:
            return None  # confirmación insuficiente

        return StrategySignal(
            strategy_name  = self.name,
            market_id      = tick.market_id,
            direction      = SignalDirection.ENTER,
            outcome        = Outcome.YES,
            confidence     = min(tick.yes_price, 0.99),
            price_at_signal= tick.yes_price,
            reason         = (
                f"YES price {tick.yes_price:.3f} ≥ threshold {self.cfg.threshold} "
                f"por {self._ticks_above} ticks consecutivos"
            ),
        )

    def should_exit(
        self, cycle: MarketCycle, tick: MarketTick, position: Position
    ) -> StrategySignal | None:
        if self._entry_price is None:
            return None

        drop_pct = (self._entry_price - tick.yes_price) / self._entry_price
        drop_abs = self._entry_price - tick.yes_price

        reason = None
        if drop_pct >= self.cfg.stop_loss:
            reason = f"Stop-loss: caída {drop_pct:.1%} desde entrada"
        elif drop_abs >= self.cfg.stop_drop:
            reason = f"Stop-drop: caída absoluta {drop_abs:.3f}"
        elif self._cycles_open >= self.cfg.timeout_cycles:
            reason = f"Timeout: {self._cycles_open} ciclos sin salida"

        if reason:
            return StrategySignal(
                strategy_name  = self.name,
                market_id      = tick.market_id,
                direction      = SignalDirection.EXIT,
                outcome        = Outcome.YES,
                confidence     = 1.0,
                price_at_signal= tick.yes_price,
                reason         = reason,
            )
        return None

    def on_exit(self, cycle: MarketCycle, result) -> None:
        self._entry_price = None
        self._ticks_above = 0
        self._cycles_open = 0

    def _passes_filters(self, tick: MarketTick) -> bool:
        """Filtros de calidad: liquidez mínima + spread máximo."""
        if tick.yes_liquidity < self.cfg.min_liquidity:
            return False
        if tick.no_liquidity < self.cfg.min_liquidity:
            return False
        if tick.spread > self.cfg.max_spread:
            return False
        return True
```

---

## Strategy Engine: cómo orquesta el protocolo

```python
# application/services/strategy_engine.py
class StrategyEngine:
    """Orquesta las estrategias registradas durante un MarketCycle.
    No contiene lógica de negocio — delega en BaseStrategy."""

    def __init__(self, risk_engine: "RiskEngine"):
        self._strategies: dict[str, BaseStrategy] = {}
        self._risk = risk_engine

    def register(self, strategy: BaseStrategy) -> None:
        self._strategies[strategy.name] = strategy

    async def run_cycle(self, cycle: MarketCycle) -> None:
        for strategy in self._strategies.values():
            strategy.on_cycle_start(cycle)

    async def on_tick(self, tick: MarketTick, position: "Position | None") -> None:
        for strategy in self._strategies.values():
            strategy.on_tick(tick)
            signal = (
                strategy.should_exit(cycle=None, tick=tick, position=position)
                if position
                else strategy.should_enter(cycle=None, tick=tick)
            )
            if signal:
                decision = await self._risk.evaluate(signal)
                if decision.allowed:
                    await self._execution_port.execute(signal, decision)
```

---

## Reglas de separación obligatoria (estrategia vs riesgo)

| Responsabilidad | Dónde vive |
|---|---|
| Umbral de precio | `strategies/` |
| Filtros de liquidez y spread | `strategies/` |
| Confirmación por ticks | `strategies/` |
| Stop-loss / stop-drop / timeout | `strategies/` |
| Tamaño máximo de posición | `risk/` |
| Capital total expuesto | `risk/` |
| Límite de posiciones simultáneas | `risk/` |
| Hedge "opposite" | `risk/` |

Una estrategia que toma decisiones de sizing o límites de capital está
violando esta separación. Moverlo al Risk Engine.

---

## Señales de alerta (Red Flags)

- Una estrategia accede directamente a la base de datos o a Redis.
  → Las estrategias son puras: solo reciben `MarketTick` y `MarketCycle`.
- `should_enter` o `should_exit` tienen efectos secundarios (escriben logs,
  modifican estado externo, envían mensajes).
  → Estas funciones deben ser puras. Los efectos van en el Engine.
- Se añade un sexto método al ABC sin actualizar el Decisions Log.
- El Strategy Engine toma decisiones de riesgo directamente.
  → Toda señal pasa por `RiskEngine.evaluate()` antes de ejecutarse.

---

## Verificación de completitud (checklist de exit)

Antes de marcar B8/B9 del roadmap como completados:

- [ ] `strategies/base.py` contiene `BaseStrategy` con los 5 métodos abstractos.
- [ ] `strategies/buy_above_threshold.py` implementa los 5 métodos.
- [ ] `domain/models/signal.py` contiene `StrategySignal`, `SignalDirection`, `Outcome`.
- [ ] `BuyAboveThresholdConfig` es un dataclass con los 7 parámetros listados.
- [ ] `StrategyEngine` registra estrategias por nombre y llama al protocolo.
- [ ] Tests en `tests/unit/test_buy_above_threshold.py` cubren:
      entrada correcta, umbral no alcanzado, liquidez insuficiente,
      spread excesivo, ticks insuficientes, stop-loss, stop-drop, timeout.
- [ ] `python -m pytest tests/unit/test_buy_above_threshold.py -v` pasa sin errores.