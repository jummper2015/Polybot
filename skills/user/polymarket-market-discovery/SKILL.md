---
name: polymarket-market-discovery
description: >
  Gestiona el descubrimiento y ciclo de vida de mercados BTC/ETH en Polymarket
  para ventanas de 5 minutos y 15 minutos. Activa cuando se trabaja en cualquier
  módulo relacionado con: obtención de mercados activos, filtrado de market_id,
  construcción de MarketCycle, suscripción al order book via WebSocket, o
  cualquier lógica que consuma o produzca datos de mercado. También activa ante
  preguntas sobre "qué mercado usar", "cómo filtrar BTC/ETH", o "cómo sé si el
  mercado está activo".
---

# Skill: Polymarket Market Discovery

## Overview

Este skill define el contrato completo del ciclo de descubrimiento de mercados
para el bot algorítmico de Polymarket. Establece cómo identificar, filtrar,
validar y publicar mercados BTC y ETH activos en ventanas de 5 minutos (5m) y
15 minutos (15m), y cómo construir el objeto `MarketCycle` que alimenta al
Strategy Engine.

**Alcance fijado e inamovible** (heredado del Decisions Log del proyecto):
- Activos: BTC y ETH únicamente.
- Ventanas temporales: 5m y 15m únicamente.
- Plataforma: Polymarket únicamente (CLOB API v2).

Cualquier extensión fuera de este alcance requiere aprobación explícita del
humano ANTES de modificar este skill o el código relacionado.

---

## Cuándo usar este skill

Activa este skill cuando:

- Se esté implementando o modificando `infrastructure/polymarket/` (cliente HTTP
  o WebSocket de Polymarket).
- Se trabaje en `application/services/market_discovery_service.py` o cualquier
  archivo que construya un `MarketCycle`.
- Aparezca cualquier pregunta sobre cómo identificar si un mercado de Polymarket
  corresponde a BTC o ETH.
- Se diseñe o revise el contrato de `MarketTick` o `MarketCycle`.
- Se implemente el timer de ciclo de mercado (`MarketTimer`).
- Se revise la lógica de suscripción al order book via WebSocket.

NO actives este skill para:
- Lógica de estrategias (usar `algorithmic-strategy-protocol`).
- Lógica de ejecución de órdenes (usar `paper-vs-real-execution-mode`).
- Seguridad de claves API (usar `security-and-hardening`).

---

## Contratos de dominio (inamovibles)

Estos son los contratos de datos definidos en `domain/`. No los redefinas
sin actualizar primero el Decisions Log del SPEC.md del proyecto.

### MarketInfo
Representa un mercado descubierto desde la API de Polymarket.

```python
# domain/models/market.py
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

class Asset(str, Enum):
    BTC = "BTC"
    ETH = "ETH"

class Window(str, Enum):
    M5  = "5m"
    M15 = "15m"

@dataclass(frozen=True)
class MarketInfo:
    market_id: str          # condition_id de Polymarket
    asset: Asset            # BTC o ETH (inamovible)
    window: Window          # 5m o 15m (inamovible)
    question: str           # texto original del mercado
    end_date: datetime      # cierre del mercado
    yes_token_id: str       # token_id del outcome YES
    no_token_id: str        # token_id del outcome NO
    is_active: bool = True  # False si ya cerró o está resuelto
```

### MarketTick
Representa un snapshot de precio/liquidez en el order book.

```python
# domain/models/market.py (continuación)
@dataclass(frozen=True)
class MarketTick:
    market_id: str
    asset: Asset
    window: Window
    timestamp: datetime
    yes_price: float        # precio actual del outcome YES (0.0–1.0)
    no_price: float         # precio actual del outcome NO (0.0–1.0)
    yes_liquidity: float    # liquidez disponible en YES (USDC)
    no_liquidity: float     # liquidez disponible en NO (USDC)
    spread: float           # diferencia bid-ask del order book

    @property
    def is_liquid_enough(self) -> bool:
        """Filtro mínimo de liquidez: ambos lados > 50 USDC."""
        return self.yes_liquidity >= 50.0 and self.no_liquidity >= 50.0
```

### MarketCycle
Agrupa los ticks de un ciclo de evaluación de la estrategia.

```python
# domain/models/market.py (continuación)
@dataclass
class MarketCycle:
    market: MarketInfo
    cycle_start: datetime
    ticks: list[MarketTick] = field(default_factory=list)
    cycle_number: int = 0

    def latest_tick(self) -> MarketTick | None:
        return self.ticks[-1] if self.ticks else None

    def tick_count(self) -> int:
        return len(self.ticks)
```

---

## Proceso de descubrimiento (paso a paso)

Claude Code debe seguir estos pasos en orden. No saltar ni combinar pasos.

### Paso 1 — Fetch de mercados activos desde Polymarket API

```
GET https://clob.polymarket.com/markets
  ?active=true
  &closed=false
  &limit=500
```

Filtra la respuesta por los criterios de este skill (Paso 2) antes de
construir ningún objeto de dominio.

### Paso 2 — Filtrado de activo y ventana

Aplica los tres filtros en orden. Un mercado pasa solo si cumple los tres:

1. **Filtro de activo**: el campo `question` debe contener exactamente
   "BTC" o "ETH" (case-insensitive). No admitir "Bitcoin" o "Ethereum"
   sin confirmar que se refieren al mismo activo.
2. **Filtro de ventana**: el campo `question` debe contener "5 minute",
   "5-minute", "15 minute" o "15-minute" (case-insensitive).
3. **Filtro de actividad**: `end_date` > now() y `resolved == false`.

Si ningún mercado pasa los tres filtros, lanzar `NoActiveMarketsError`
y loggear en structlog con nivel WARNING. No continuar con ciclo vacío.

### Paso 3 — Construcción de MarketInfo

Para cada mercado que pasa el filtro:

```python
# application/services/market_discovery_service.py
def _build_market_info(self, raw: dict) -> MarketInfo:
    asset  = Asset.BTC if "BTC" in raw["question"].upper() else Asset.ETH
    window = Window.M5 if "5" in raw["question"] else Window.M15

    return MarketInfo(
        market_id    = raw["condition_id"],
        asset        = asset,
        window       = window,
        question     = raw["question"],
        end_date     = datetime.fromisoformat(raw["end_date_iso"]),
        yes_token_id = raw["tokens"][0]["token_id"],   # YES siempre [0]
        no_token_id  = raw["tokens"][1]["token_id"],   # NO siempre [1]
    )
```

Verificar que `yes_token_id != no_token_id`. Si son iguales, el mercado
está malformado — descartar con log ERROR.

### Paso 4 — Suscripción WebSocket al order book

Una vez construido `MarketInfo`, el `WebSocketClient` se suscribe al
order book del mercado usando `market_id` como `asset_id`:

```python
# infrastructure/polymarket/ws_client.py
subscribe_payload = {
    "auth": {},
    "type": "market",
    "markets": [market_info.market_id],
    "assets_ids": [market_info.yes_token_id, market_info.no_token_id],
}
```

### Paso 5 — Construcción de MarketTick desde evento WS

Por cada evento `book` o `price_change` recibido del WebSocket:

```python
def _build_tick(self, event: dict, market: MarketInfo) -> MarketTick:
    best_yes_bid = event["bids"][0]["price"] if event.get("bids") else 0.0
    best_no_bid  = 1.0 - best_yes_bid          # el precio NO siempre es complemento

    return MarketTick(
        market_id     = market.market_id,
        asset         = market.asset,
        window        = market.window,
        timestamp     = datetime.utcnow(),
        yes_price     = best_yes_bid,
        no_price      = best_no_bid,
        yes_liquidity = event.get("liquidity_yes", 0.0),
        no_liquidity  = event.get("liquidity_no",  0.0),
        spread        = event.get("spread", 0.05),
    )
```

### Paso 6 — Disparo del MarketCycle (timer)

El `MarketTimer` dispara un nuevo ciclo cada N segundos según la ventana:
- Window.M5  → cada 300 segundos
- Window.M15 → cada 900 segundos

```python
# application/services/market_timer.py
CYCLE_SECONDS = {Window.M5: 300, Window.M15: 900}

async def run(self, market: MarketInfo) -> None:
    cycle_number = 0
    while True:
        cycle = MarketCycle(
            market=market,
            cycle_start=datetime.utcnow(),
            cycle_number=cycle_number,
        )
        await self._event_bus.publish("cycle.started", cycle)
        await asyncio.sleep(CYCLE_SECONDS[market.window])
        await self._event_bus.publish("cycle.ended", cycle)
        cycle_number += 1
```

---

## Reglas de calidad obligatorias

Estas reglas son verificaciones de exit criteria. No marcar ninguna tarea
del Decisions Log como completada sin haber cumplido las que apliquen.

- [ ] `MarketInfo` es un dataclass `frozen=True` (inmutable tras creación).
- [ ] `MarketTick` es un dataclass `frozen=True`.
- [ ] El filtrado de activo y ventana usa comparación explícita (no regex
      complejo ni heurística difusa).
- [ ] `NoActiveMarketsError` está definido en `domain/exceptions.py`.
- [ ] El WebSocket client reconecta automáticamente con backoff exponencial
      (máximo 5 reintentos, espera 2^n segundos entre intentos).
- [ ] Cada `MarketTick` recibido se publica en Redis con TTL de 60 segundos
      bajo la clave `tick:{market_id}:{timestamp_unix}`.
- [ ] El discovery service tiene tests unitarios que cubren: mercado BTC 5m
      correcto, mercado ETH 15m correcto, mercado con activo inválido
      descartado, mercado expirado descartado.

---

## Señales de alerta (Red Flags)

Si ves cualquiera de estas señales, detente y pregunta al humano antes
de continuar:

- Se está añadiendo un tercer activo (BTC, ETH + otro). Violación del
  Decisions Log.
- Se está añadiendo una tercera ventana (5m, 15m + otra). Violación del
  Decisions Log.
- El filtro de activo usa coincidencia difusa ("bitcoin" → BTC) sin
  haberlo acordado explícitamente.
- `MarketCycle` tiene campos mutables que se modifican desde fuera del
  módulo de discovery (violación de inmutabilidad de dominio).
- El WebSocket client no tiene lógica de reconexión.

---

## Racionalizaciones comunes que Claude Code debe rechazar

**"Puedo inferir el activo desde el market_id directamente."**
→ No. El `market_id` es un hash opaco. El filtro siempre va sobre `question`.

**"Agrego ETH2 o WBTC porque también son criptomonedas."**
→ No. El alcance es BTC y ETH. Cualquier extensión requiere aprobación humana.

**"El timer puede correr con cualquier intervalo configurable en runtime."**
→ No. Los intervalos son 300s y 900s, fijados por el Decisions Log. La
ventana es una propiedad de `MarketInfo`, no un parámetro libre.

---

## Verificación de completitud (checklist de exit)

Antes de marcar B6/B7 del roadmap como completados, verificar:

- [ ] `domain/models/market.py` contiene `MarketInfo`, `MarketTick`,
      `MarketCycle`, `Asset`, `Window`.
- [ ] `domain/exceptions.py` contiene `NoActiveMarketsError`.
- [ ] `application/services/market_discovery_service.py` implementa los
      6 pasos de este skill.
- [ ] `application/services/market_timer.py` dispara ciclos según la ventana.
- [ ] `infrastructure/polymarket/http_client.py` encapsula el fetch de
      mercados (no se llama la API directamente desde el service).
- [ ] `infrastructure/polymarket/ws_client.py` encapsula la suscripción
      al order book con reconexión automática.
- [ ] Tests en `tests/unit/test_market_discovery.py` con al menos 4 casos.
- [ ] El módulo compila sin errores: `python -m pytest tests/unit/test_market_discovery.py -v`