---
name: db-integrity-guard
description: >
  Auditoría obligatoria de integridad de base de datos. Activa cuando se
  modifica alembic/versions/ o src/infrastructure/db/ (models, repository,
  session). Constraints anti-duplicado, índices parciales, idempotencia
  (idempotency_key UNIQUE), migraciones reversibles (upgrade + downgrade
  simétricos), y manejo de IntegrityError en el repositorio.
  NO activa para cambios de lógica de negocio — solo schema y persistencia.
---

# Skill: DB Integrity Guard

## Regla cero (no negociable)

**Toda migración debe ser reversible.** `alembic downgrade -1` debe dejar la DB en el estado anterior
sin pérdida de datos estructurales. Los datos insertados durante `upgrade` pueden perderse (eso es esperado),
pero el schema debe restaurarse exactamente.

---

## Cuándo activa este skill

- Edición de cualquier archivo en `alembic/versions/`.
- Edición de `src/infrastructure/db/models.py`, `repository.py`, `session.py`.
- Añadir/quitar constraints (UNIQUE, FOREIGN KEY, CHECK, partial indexes).
- Cambios que toquen `idempotency_key` o la tabla `orders`.
- Discusión sobre upsert, ON CONFLICT, o manejo de `IntegrityError`.
- Scripts de migración manual (`scripts/seed_db.py`).

NO activa para:
- Lógica de negocio (usa `strategy-validation-protocol` o `risk-engine-guard`).
- Endpoints API que solo leen DB.
- Redis/cache (no es DB estructural).

---

## Checklist de integridad (obligatorio en cada cambio de schema)

### A. Migraciones

- [ ] `upgrade()` y `downgrade()` son simétricos: cada `add_column` tiene su `drop_column`, cada `create_index` su `drop_index`.
- [ ] `downgrade()` usa `IF EXISTS` para índices y constraints creados en `upgrade()` (defensa contra migraciones parciales).
- [ ] `revision` y `down_revision` correctos (cadena lineal, sin saltos).
- [ ] La migración se ha probado: `alembic upgrade head && alembic downgrade -1 && alembic upgrade head`.
- [ ] No se usa `op.execute()` con raw SQL sin justificación documentada en el comentario.

### B. Constraints anti-duplicado

- [ ] `orders.idempotency_key` tiene UNIQUE constraint (previene órdenes duplicadas).
- [ ] `positions` tiene partial unique index `(market_id, mode) WHERE closed_at IS NULL` (previene 2 posiciones abiertas para el mismo mercado).
- [ ] `markets` tiene unique constraint `(asset, window, expiry)` (previene mercados lógicos duplicados vía discovery).
- [ ] Cualquier constraint nuevo tiene su `IntegrityError` handler correspondiente en `repository.py`.

### C. Repository

- [ ] `save_position()` captura `IntegrityError` con mensaje `uq_positions_open` → warning + retorno sin crash.
- [ ] `save_market()` captura `IntegrityError` con mensaje `uq_markets_asset_window_expiry` → warning + retorno sin crash.
- [ ] `save_order()` respeta el UNIQUE de `idempotency_key` (si hay colisión, es idempotencia real — OK).
- [ ] Todas las operaciones de escritura usan `async with session.begin()` (transaction boundary explícito).
- [ ] No hay `session.commit()` manual fuera de `session.begin()`.

### D. Idempotencia

- [ ] `idempotency_key` se genera con `SHA256(strategy + market + side + operation + minute_bucket)[:16]`.
- [ ] La key se asigna ANTES de llamar a la API externa (CLOB, redeem).
- [ ] Si la API falla y se reintenta, se reusa la misma key.
- [ ] Si la API tiene éxito, la key queda persistida y bloquea reenvíos futuros.

---

## Constraints actuales (snapshot R2.5)

| Tabla | Constraint | Tipo | Propósito |
|---|---|---|---|
| `orders` | `idempotency_key UNIQUE` | Unique index | Anti-duplicado de órdenes |
| `positions` | `(market_id, mode) WHERE closed_at IS NULL` | Partial unique index | Una sola posición abierta por mercado |
| `markets` | `(asset, window, expiry)` | Unique index | Un solo market lógico por trio |

---

## Racionalizaciones a rechazar

- *"No necesito downgrade, nunca volvemos atrás."* → Sí necesitas. Staging y CI usan downgrade para tests de migración.
- *"El IntegrityError nunca va a pasar en producción."* → El discovery corre cada 60s; el race condition es real.
- *"Añado el constraint en el modelo pero la migración la hago después."* → No. Modelo y migración van en el mismo PR; el modelo refleja el schema real.
- *"Uso raw SQL porque SQLAlchemy no soporta partial indexes."* → Válido solo si se documenta en el comentario. El partial index de positions es el caso canónico.
- *"El unique de markets puede romper el discovery, mejor no ponerlo."* → Sin el constraint, el discovery inserta duplicados que aparecen como mercados distintos en el dashboard. El handler de IntegrityError en el repo ya lo gestiona.

---

## Red flags

- Migración sin `downgrade()`.
- `alembic/versions/` con gaps en `down_revision`.
- `save_position()` / `save_market()` sin try/except `IntegrityError`.
- Raw SQL en `upgrade()` sin justificación documentada.
- Nueva tabla sin su modelo en `models.py`.
- `idempotency_key` calculada DESPUÉS de la API call.

---

## Salidas esperadas

1. Migración con `upgrade()` y `downgrade()` simétricos.
2. `IntegrityError` handler en el repositorio para cada constraint nuevo.
3. `pytest tests/integration/test_repository.py` verde.
4. `alembic upgrade head && alembic downgrade -1 && alembic upgrade head` sin errores.
5. Entrada en `AUDIT_REPORT.md` si el cambio es estructural (nueva tabla, nuevo constraint).
