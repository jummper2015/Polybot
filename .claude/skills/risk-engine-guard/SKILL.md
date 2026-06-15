---
name: risk-engine-guard
description: >
  Auditoría de las 6 reglas del Risk Engine y de cualquier cambio que
  pueda alterar su comportamiento. Activa para cualquier edición en
  src/risk/ (engine, context, base, rules/{kelly_sizing, drawdown,
  max_exposure, max_positions, min_balance, hedge}), para cambios que
  invoquen RiskEngine.evaluate(), para diseño/ajuste de límites de capital,
  y cuando aparece una racionalización del tipo "bypass risk just this once".
  NO activa para estrategias (usa strategy-validation-protocol) ni ejecución
  (usa paper-vs-real-execution).
---

# Skill: Risk Engine Guard

## Regla cero (no negociable)

**Nunca bypassear `RiskEngine.evaluate()` en el flujo de entrada de órdenes.**

Toda señal `ENTER` pasa por las 6 reglas. Cualquier código que escriba al `ExecutionHandler` sin un `RiskDecision` previo es un bug crítico.

---

## Cuándo activa este skill

- Edición de cualquier archivo en `src/risk/` (`engine.py`, `context.py`, `base.py`, `rules/*.py`).
- Cambios que invoquen `RiskEngine.evaluate()` desde `application/` o `interfaces/`.
- Modificación de variables `.env` `RISK_*` (límites de capital).
- Discusión sobre kill switch, drawdown limit, exposure cap.
- Cualquier intento de saltar reglas "por excepción".

NO activa para:
- Estrategias (usa `strategy-validation-protocol`).
- Ejecución (usa `paper-vs-real-execution`).
- Infra Polymarket (usa `polymarket-clob-audit`).

---

## Las 6 reglas (inamovibles en orden de evaluación)

```
1. min_balance      → balance > RISK_MIN_BALANCE_USDC  (default 50)
2. max_positions    → posiciones abiertas < RISK_MAX_OPEN_POSITIONS  (default 5)
3. max_exposure     → exposición acumulada < RISK_MAX_EXPOSURE_PCT * balance (default 30%)
4. drawdown         → drawdown diario < RISK_MAX_DAILY_DRAWDOWN (default 10%) — kill switch
5. hedge            → no abrir hedge "opposite" sobre posición existente
6. kelly_sizing     → tamaño final dictado por Kelly fraccionado (cap 25% del balance)
```

Cualquier regla que devuelva `allowed=False` corta la cadena. La decisión final lleva la regla que la bloqueó y un mensaje legible para Telegram + audit log.

---

## Contratos de dominio (inamovibles)

```python
@dataclass(frozen=True)
class RiskDecision:
    allowed: bool
    position_size_usdc: float
    reason: str
    triggered_rule: str | None  # None si allowed=True
```

`RiskEngine.evaluate(signal, context)` es **pura** dado el `RiskContext` (snapshot: balance, posiciones, drawdown, etc.). No hace I/O. El `context` se construye en `risk/context.py` desde DB/Redis **una vez por ciclo**, no por tick.

---

## Tests obligatorios (property-based con Hypothesis)

**Cambios en `src/risk/` requieren property tests** (regla dura de CLAUDE.md). Cobertura mínima:

| Propiedad | Test |
|---|---|
| Idempotencia | `evaluate(s, ctx) == evaluate(s, ctx)` siempre. |
| Monotonía balance | Si baja `balance`, `position_size_usdc` no sube. |
| Cota dura de exposure | `sum(pos.exposure) + decision.position_size_usdc ≤ max_exposure_pct * balance` siempre que `allowed=True`. |
| Kill switch DD | Si `drawdown >= max_daily_drawdown` → `allowed=False, triggered_rule="drawdown"`. |
| Kelly cap | `position_size_usdc ≤ 0.25 * balance`. |
| Min balance corta primero | Si `balance < min_balance`, ninguna otra regla se evalúa. |
| Hedge opposite | Posición YES abierta + señal NO en mismo market → `allowed=False, triggered_rule="hedge"`. |

Strategy generators: balances [0, 100_000], confidence [0, 1], drawdown [-1, 1], posiciones [0, 20].

---

## Racionalizaciones a rechazar

- *"Esta señal es de muy alta confianza, bypass de Kelly."* → No. Kelly **es** la traducción de la confianza a tamaño; bypass = doble cuenta.
- *"Subo `max_exposure_pct` a 50% para esta sesión."* → No durante una sesión activa. Cambios de límites pasan por RFC + reinicio controlado.
- *"En paper se puede saltar el risk porque no hay dinero real."* → No. Paper alimenta backtesting; saltar risk corrompe los datos.
- *"Hago kill switch manual desde el código en vez de via drawdown rule."* → No. La regla `drawdown` es la única vía oficial; cualquier otro corte rompe el audit log.
- *"Permito hedge `opposite` si la confianza > 0.9."* → No. La regla `hedge` no admite excepciones; protege contra estrategias contradictorias.
- *"Refactor del orden de evaluación para optimizar latencia."* → Solo con RFC. El orden actual hace fail-fast con la regla más barata primero.

---

## Red flags

- Llamada a `ExecutionHandler.execute(signal, ...)` sin un `RiskDecision.allowed=True` previo.
- Regla de risk que hace I/O (DB, Redis, HTTP) dentro de `evaluate`.
- Cambio en `RiskContext` que no se construye desde la fuente de verdad (DB).
- Cambio en `.env` `RISK_*` durante producción sin RFC.
- Test nuevo de risk que **no** usa Hypothesis (property-based).
- Mensaje de `RiskDecision.reason` que filtra info sensible (saldos exactos en logs públicos, IDs internos).

---

## Defaults seguros (de `.env.example`)

```
RISK_MIN_BALANCE_USDC=50.0
RISK_MAX_DAILY_DRAWDOWN=0.10        # kill switch a -10% intradía
RISK_MAX_EXPOSURE_PCT=0.30          # 30% del balance
RISK_MAX_OPEN_POSITIONS=5
```

Cambios solo con justificación numérica (Monte Carlo p1, Sharpe diff, justificación de capacidad de mercado).

---

## Auditoría operativa

- Cada `RiskDecision` (allowed o no) se registra en `audit_events` con timestamp, señal hash, regla disparada, contexto resumido.
- Métrica Prometheus `risk_decisions_total{rule,allowed}` actualizada en cada llamada.
- Si una regla devuelve `allowed=False` repetidamente (>10 en 5 min), alerta WARNING.

---

## Checklist de exit (antes de merge)

- [ ] Cambio acompañado de property tests Hypothesis nuevos.
- [ ] `pytest tests/unit/test_risk_*.py -v` verde, incluido el property test.
- [ ] Sin nuevas llamadas a `ExecutionHandler.execute` que bypaseen `RiskEngine.evaluate`.
- [ ] Audit log actualizado para capturar la nueva ruta.
- [ ] Métricas Prometheus extendidas si aparece regla nueva.
- [ ] `RECORRIDO_ACTUAL.md` actualizado si el set de 6 reglas cambia (RFC previo obligatorio).
