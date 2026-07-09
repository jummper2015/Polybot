# 🤖 Polybot — Bot de Trading Algorítmico para Polymarket

**Polybot** es un bot de trading algorítmico que opera en los mercados de predicción de
[Polymarket](https://polymarket.com) para resultados de precio de **BTC** y **ETH** en
ventanas temporales de **5 minutos** y **15 minutos**.

> ⚠️ **Estado:** El código está **técnicamente completo (100%)** — 39/39 prioridades
> implementadas, **1488 tests** (929 unit, 73 integration, 41 property, 38 chaos,
> 4 e2e, 4 performance), pipeline CI/CD, manifiestos Kubernetes, dashboards Grafana
> y hardening de seguridad. **R2.0-redeem-impl (CTF on-chain) en progreso:**
> Paso 1 completado (wire CTFRedeemer → clob_client → real_handler, 1488/1488 ✅).
> Consulta [RUTA_IMPLEMENTACION.md](./RUTA_IMPLEMENTACION.md) § R2.0-redeem-impl
> para el progreso del ciclo completo entry→exit→redeem.

---

## Tabla de Contenidos

- [Características](#caracteristicas)
- [Arquitectura](#arquitectura)
- [Stack Tecnológico](#stack-tecnologico)
- [Estructura del Proyecto](#estructura-del-proyecto)
- [Inicio Rápido](#inicio-rapido)
  - [Requisitos Previos](#requisitos-previos)
  - [Instalación](#instalacion)
  - [Configuración](#configuracion)
- [Uso](#uso)
  - [Ejecutar el Bot](#ejecutar-el-bot)
  - [Docker Compose](#docker-compose)
  - [Bot de Telegram](#bot-de-telegram)
  - [Backtesting](#backtesting)
  - [Scripts de Utilidad](#scripts-de-utilidad)
- [Testing](#testing)
- [Despliegue](#despliegue)
  - [Docker](#docker)
  - [Kubernetes](#kubernetes)
  - [CI/CD](#cicd)
- [Observabilidad](#observabilidad)
- [Seguridad](#seguridad)
- [Documentación](#documentacion)
- [Métricas del Proyecto](#metricas-del-proyecto)
- [Limitaciones Conocidas](#limitaciones-conocidas)

---

## Características

### 🎯 Estrategias de Trading

| Estrategia | Rol | Lógica |
|------------|-----|--------|
| **Mean Reversion** *(primaria)* | Genera ~80% del PnL | Compra cuando z-score < umbral de entrada (sobrevendido), vende al revertir a la media. Ideal para mercados de predicción que son naturalmente de reversión a la media. |
| **Buy Above Threshold** *(secundaria)* | Diversificación (~20% PnL) | Basada en momentum: entra cuando el precio YES supera un umbral configurable con confirmación multi-tick, filtros de liquidez y spread, y confirmación multi-temporalidad (M5→M15). |

Ambas estrategias implementan el ABC `BaseStrategy` con 5 métodos requeridos:
`on_cycle_start` → `on_tick` → `should_enter` / `should_exit` → `on_exit`

### 🛡️ Gestión de Riesgo

- **6 reglas de riesgo independientes** evaluadas en cada señal: balance mínimo, exposición máxima, posiciones máximas, drawdown, cobertura y dimensionamiento de posición con Criterio de Kelly
- **Criterio de Kelly** con dimensionamiento fraccional configurable: factor de seguridad, amortiguador de volatilidad, suelo ($5 USDC) y techo ($50 USDC)
- Todas las decisiones registradas con razonamiento estructurado

### ⚡ Modos de Ejecución

| Modo | Descripción |
|------|-------------|
| **Paper Trading** | Modo por defecto. Simula ejecuciones con slippage realista (inversamente proporcional a la liquidez del libro de órdenes). Registra PnL, posiciones y curva de equity. |
| **Real Trading** | Requiere doble confirmación por Telegram + PIN. Confirmación de 3 capas: RiskEngine → Telegram (timeout de 60s) → verificación de idempotencia. Cada orden se persiste como `PENDING` en PostgreSQL **antes** de cualquier llamada a la API del CLOB. |

### 📱 Control por Telegram

- `/start` — Inicializar el bot
- `/status` — Estado del bot en tiempo real (modo, PnL, posiciones, uptime)
- `/positions` — Posiciones abiertas y cerradas con PnL
- `/settings` — Ajustar umbrales BAT, stop-loss, tamaño de posición en tiempo real (persistido en BD)
- `/mode real <PIN>` — Activar trading real con confirmación por PIN de 6 dígitos

### 🖥️ Dashboard Web (React)

- **Tabla de Mercados en Vivo** con profundidad del libro de órdenes en tiempo real (bids/asks, expandible por mercado)
- **Gráfico de Curva de Equity** con filtro paper/real
- **Registro de Operaciones Recientes**
- **Dashboard de Salud** con indicadores de estado de servicios
- Tema financiero oscuro, diseño responsive, polling cada 10 segundos

### 📊 Motor de Backtesting

- Backtesting por CLI con selección de activo/ventana
- Métricas: ratio de Sharpe, drawdown máximo, tasa de acierto, factor de beneficio, PnL total
- Optimización por barrido de parámetros para ambas estrategias
- Generador de datos sintéticos con dinámicas de mercado realistas (tendencias, reversión a la media, shocks informacionales)
- Exportación CSV de resultados de barrido

### 📈 Observabilidad

- **Logging Estructurado** — [structlog](https://www.structlog.org/) con renderizado JSON
- **Métricas Prometheus** — 30+ métricas (uptime del bot, PnL, latencia de ciclo, estado WS, circuit breaker, señales de estrategia)
- **Dashboard Grafana** — 51 paneles en 6 secciones (Salud del Sistema, Rendimiento de Trading, Métricas de Riesgo, Señales de Estrategia, Calidad de Ejecución, Seguridad)
- **Trazado OpenTelemetry** — Trazas distribuidas en ciclos de trading, peticiones HTTP y manejadores de ejecución

### 🔒 Seguridad

- **6 módulos de seguridad:** Audit Log, Key Manager, Log Sanitizer, Rate Limiter, Secure Config, Security Guard
- **Circuit Breaker** — 5 fallos en 60s → circuito ABIERTO por 60s, recuperación half-open
- **Claves de Idempotencia** — SHA256(estrategia + mercado + minuto) previene órdenes duplicadas
- **Guardrails** — Máximo fijo de $500 USDC/orden, mínimo $1 USDC
- **Cero secretos** en el repositorio, logs sanitizados antes de escritura

---

## Arquitectura

Polybot sigue **Clean Architecture** con estricta separación de capas:

```
┌─────────────────────────────────────────────┐
│                INTERFACES                    │
│  FastAPI REST API  │  Telegram Bot (aiogram) │
├─────────────────────────────────────────────┤
│              APPLICATION                     │
│  Trading Service  │  Market Service          │
│  Portfolio Svc    │  Puertos (interfaces ABC) │
├─────────────────────────────────────────────┤
│                 DOMAIN                       │
│  Entidades  │  Value Objects  │  Enums       │
│  (Market, Order, Position, …)               │
├─────────────────────────────────────────────┤
│           INFRASTRUCTURE                     │
│  Polymarket (HTTP/WS/CLOB)                   │
│  BD (SQLAlchemy + asyncpg)                   │
│  Cache (Redis)                               │
│  Seguridad │ Observabilidad                  │
└─────────────────────────────────────────────┘

ESTRATEGIAS (puras, sin efectos secundarios)
    ↕
RISK ENGINE (decisiones permitir/denegar)
    ↕
EXECUTION HANDLERS (paper o real)
```

**Decisiones clave de diseño (del [Decisions Log](./CLAUDE.md#decisions-log)):**

- Las dataclasses de dominio son `frozen=True` (inmutables)
- `should_enter()` y `should_exit()` de las estrategias son **funciones puras** — sin efectos secundarios, sin acceso a BD, sin logging
- Las decisiones de riesgo (dimensionamiento, límites de exposición) residen en `risk/`, **nunca** en `strategies/`
- PostgreSQL es la **fuente de verdad**; el CLOB es el destino, no la fuente
- Todas las funciones públicas llevan type hints

---

## Stack Tecnológico

| Capa | Tecnología | Versión |
|------|------------|---------|
| **Lenguaje** | Python | 3.11+ |
| **Framework API** | FastAPI | 0.111.0 |
| **Servidor ASGI** | Uvicorn + uvloop | 0.29.0 |
| **UI Telegram** | aiogram | 3.7.0 |
| **ORM** | SQLAlchemy (async) | 2.0.30 |
| **Driver BD** | asyncpg | 0.29.0 |
| **Migraciones** | Alembic | 1.13.1 |
| **Cache / Estado** | Redis + hiredis | 5.0.4 |
| **Cliente HTTP** | httpx | ≥0.28.0 |
| **WebSocket** | websockets | 12.0 |
| **Logging** | structlog | 24.1.0 |
| **Métricas** | prometheus-client | 0.20.0 |
| **Trazado** | OpenTelemetry (API + SDK + OTLP) | 1.24.0 |
| **Serialización** | orjson | 3.11.6 |
| **Reintentos** | tenacity | 8.3.0 |
| **SDK Polymarket** | py-clob-client-v2 | ≥1.0.0 |
| **Frontend** | React 18 + Vite + TypeScript + Recharts | — |
| **Infraestructura** | Docker, Kubernetes, GitHub Actions, Vault | — |

---

## Estructura del Proyecto

```
polybot/
├── main.py                        ← Punto de entrada: asyncio.run(bootstrap())
├── pyproject.toml                 ← Config de build + dependencias
├── requirements.txt               ← Dependencias Python
├── alembic.ini                    ← Config de migraciones BD
├── Dockerfile                     ← Build Docker multi-etapa
├── docker-compose.yml             ← Dev local: PG + Redis + Prometheus + Grafana
├── .bandit                        ← Config SAST (escaneo de seguridad)
│
├── SPEC.md                        ← Especificación del proyecto (fuente de verdad)
├── CLAUDE.md                      ← Instrucciones del agente IA + Decisions Log
├── PLAN_MEJORAS.txt               ← Plan de mejoras v5.0 (39 prioridades)
├── RECORRIDO.txt                  ← Seguimiento de implementación (7 fases)
├── AUDIT_REPORT.md                ← Auditoría de seguridad y calidad D20
│
├── src/
│   ├── domain/                    ← Reglas de negocio puras (sin dependencias externas)
│   │   ├── entities/              ← Market, Order, Position (dataclasses congeladas)
│   │   ├── value_objects/         ← MarketTick, Signal, RiskDecision, TradeResult
│   │   ├── enums/                 ← Asset, Window, TradingMode, OrderStatus, etc.
│   │   └── exceptions.py          ← Jerarquía tipada de 31 clases de excepción
│   │
│   ├── application/               ← Casos de uso y orquestación
│   │   ├── services/              ← Servicios de Trading, Market, Portfolio
│   │   └── ports/                 ← Interfaces ABC (Execution, MarketData, Notification, Repository)
│   │
│   ├── strategies/                ← Estrategias algorítmicas
│   │   ├── base.py                ← BaseStrategy ABC (5 métodos requeridos)
│   │   ├── engine.py              ← StrategyEngine: registro + orquestación
│   │   ├── buy_above_threshold/   ← Estrategia BAT + configuración
│   │   ├── mean_reversion/        ← Estrategia MR + configuración
│   │   └── filters/               ← Liquidez, Spread, Confirmación Tick, Tiempo, Multi-Temporalidad
│   │
│   ├── risk/                      ← Gestión de riesgo
│   │   ├── engine.py              ← RiskEngine: evalúa todas las reglas
│   │   ├── context.py             ← RiskContext (estado del portafolio)
│   │   └── rules/                 ← MinBalance, Drawdown, MaxExposure, MaxPositions, Hedge, Kelly
│   │
│   ├── execution/                 ← Manejadores de ejecución de órdenes
│   │   ├── base.py                ← BaseExecutionHandler ABC
│   │   ├── paper_handler.py       ← Ejecuciones simuladas con slippage
│   │   └── real_handler.py        ← Confirmación de 3 capas + reintentos + auditoría
│   │
│   ├── infrastructure/            ← Adaptadores externos
│   │   ├── polymarket/            ← Cliente HTTP, cliente WebSocket, cliente CLOB, adaptadores
│   │   ├── db/                    ← Modelos SQLAlchemy, repositorio, fábrica de sesiones
│   │   ├── cache/                 ← Cliente Redis (basado en orjson)
│   │   ├── security/              ← Audit log, key manager, sanitizer, rate limiter, circuit breaker, guard
│   │   └── observability/         ← Config de logging, métricas Prometheus, trazado OTel
│   │
│   ├── interfaces/                ← Interfaces externas
│   │   ├── api/                   ← App FastAPI, routers, schemas, middleware, estáticos (React SPA)
│   │   └── telegram/              ← Bot aiogram, handlers, middleware
│   │
│   ├── backtesting/               ← Motor de replay histórico
│   │   └── cli.py, engine.py, data_loader.py, metrics.py, reporter.py
│   │
│   └── core/                      ← Bootstrap, contenedor DI, configuración, ciclo de vida
│
├── dashboard/                     ← SPA React 18 + Vite + TypeScript
│   └── src/                       ← Componentes, hooks, tipos, estilos
│
├── tests/
│   ├── unit/                      ← 298 tests (dominio, estrategia, riesgo, ejecución, trazado, config)
│   ├── property/                  ← 33 tests de invariantes con Hypothesis
│   ├── integration/               ← 45 tests (routers API, ciclo de trading, motor de riesgo, repositorio)
│   ├── e2e/                       ← Tests de ciclo completo en paper trading
│   ├── chaos/                     ← 38 tests de chaos engineering (5 escenarios)
│   └── performance/               ← Pruebas de carga con Locust
│
├── scripts/                       ← Scripts de utilidad
│   ├── check_env.py               ← Verificar variables de entorno
│   ├── download_historical_data.py← Descargar datos de Polymarket vía Gamma API
│   ├── record_live_data.py        ← Grabar ticks WebSocket a CSV
│   ├── optimize_bat.py            ← Barrido de parámetros de estrategia BAT
│   ├── optimize_mr.py             ← Barrido de parámetros de Mean Reversion
│   ├── validate_criteria.py       ← Validación automatizada de criterios de éxito
│   └── security_scan.sh           ← Escaneo unificado: bandit + pip-audit + trivy + secrets
│
├── k8s/                           ← Manifiestos Kubernetes (17 YAMLs)
│   ├── base/                      ← Namespace, ConfigMap, Secrets, ServiceAccount
│   ├── staging/                   ← Despliegues de paper trading
│   ├── canary/                    ← Canary de trading real (tope $50)
│   ├── production/                ← Despliegues de producción + HPA + PDB + CronJob
│   └── vault/                     ← Integración HashiCorp Vault
│
├── monitoring/                    ← Config de observabilidad
│   ├── prometheus.yml
│   ├── alerts.yml                 ← 15 alertas (7 críticas + 8 de advertencia)
│   └── grafana/dashboard/         ← Dashboard de trading de 51 paneles
│
├── alembic/                       ← Migraciones de BD
│   └── versions/                  ← 001 (inicial), 003 (bot_settings), 004 (order retry) — 002 omitida (audit_logs ya en 001)
│
└── .github/workflows/             ← Pipeline CI/CD (10 jobs)
    └── ci-cd.yml
```

---

## Inicio Rápido

### Requisitos Previos

- **Python 3.11+**
- **PostgreSQL 15+** (para persistencia de órdenes/posiciones)
- **Redis 7+** (para caché, estado WebSocket, rate limiting, almacenamiento FSM)
- **Node.js 18+** (solo si se compila el dashboard React desde fuente)
- **Docker y Docker Compose** (opcional, para despliegue contenerizado)

### Instalación

```bash
# 1. Clonar el repositorio
git clone <repo-url> polybot
cd polybot

# 2. Crear y activar un entorno virtual
python -m venv .venv
source .venv/bin/activate     # Linux/macOS
# .venv\Scripts\activate      # Windows

# 3. Instalar dependencias
pip install -r requirements.txt

# 4. (Opcional) Compilar dashboard desde fuente
cd dashboard && npm install && npm run build && cd ..
```

### Configuración

Copiar y editar el archivo de entorno:

```bash
cp .env.example .env    # Crear desde plantilla
```

**Variables de entorno requeridas:**

```bash
# ── API CLOB de Polymarket ────────────────────────
POLYMARKET_API_KEY=           # Tu API key
POLYMARKET_API_SECRET=        # Tu API secret
POLYMARKET_API_PASSPHRASE=    # Tu API passphrase
POLYMARKET_PRIVATE_KEY=       # Clave privada EIP-712 (NUNCA en logs)

# ── Base de Datos ─────────────────────────────────
DATABASE_URL=postgresql+asyncpg://usuario:pass@localhost:5432/polybot

# ── Redis ─────────────────────────────────────────
REDIS_URL=redis://localhost:6379/0

# ── Telegram ──────────────────────────────────────
TELEGRAM_BOT_TOKEN=           # De @BotFather
TELEGRAM_ADMIN_CHAT_ID=       # Tu chat ID de Telegram (único usuario autorizado)

# ── Seguridad ─────────────────────────────────────
REAL_MODE_PIN=                # PIN de 6 dígitos para activar trading real
LOG_LEVEL=INFO

# ── Observabilidad ────────────────────────────────
PROMETHEUS_PORT=9090
GRAFANA_ADMIN_PASSWORD=

# ── Servidor API ──────────────────────────────────
API_HOST=0.0.0.0
API_PORT=8000
```

Ejecuta el verificador de entorno para validar tu configuración:

```bash
python scripts/check_env.py
```

---

## Uso

### Ejecutar el Bot

```bash
# Sistema completo (FastAPI + Telegram + ciclos de trading)
python main.py

# Solo API (desarrollo)
uvicorn src.interfaces.api.app:create_app --factory --reload --port 8000
```

### Docker Compose

```bash
# Iniciar todos los servicios (app + PostgreSQL + Redis + Prometheus + Grafana)
docker-compose up -d

# Ver logs
docker-compose logs -f app

# Detener
docker-compose down
```

Puntos de acceso:
- **API:** `http://localhost:8000`
- **Docs API:** `http://localhost:8000/docs`
- **Dashboard:** `http://localhost:8000`
- **Prometheus:** `http://localhost:9090`
- **Grafana:** `http://localhost:3000`

### Bot de Telegram

Una vez en ejecución, envía estos comandos a tu bot en Telegram:

| Comando | Descripción |
|---------|-------------|
| `/start` | Inicializar y mostrar mensaje de bienvenida |
| `/status` | Estado del bot en tiempo real, PnL, posiciones, uptime |
| `/positions` | Listar posiciones abiertas y cerradas recientemente |
| `/settings` | Ajustar parámetros de estrategia (umbral, stop-loss, tamaño de posición, etc.) |
| `/mode real <PIN>` | **Activar trading real** (requiere PIN de 6 dígitos, doble confirmación) |

⚠️ **El trading real tiene confirmación de 3 capas:**
1. RiskEngine evalúa y aprueba la señal
2. Se envía mensaje por Telegram — tienes 60 segundos para confirmar
3. Se verifica la clave de idempotencia contra órdenes recientes (previene duplicados)

Nunca actives el trading real sin haber completado primero ≥100 ciclos de paper trading con PnL positivo.

### Backtesting

```bash
# Ejecutar backtest en mercados BTC de 5 minutos
python -m src.backtesting.cli --asset BTC --window 5m --start 2024-01-01

# Ejecutar en ETH 15 minutos con informe detallado
python -m src.backtesting.cli --asset ETH --window 15m --report

# Barrido de parámetros para estrategia Mean Reversion
python scripts/optimize_mr.py --quick --n-ticks 3000

# Barrido de parámetros para estrategia BAT
python scripts/optimize_bat.py --quick --n-ticks 2000

# Validar criterios de éxito
python scripts/validate_criteria.py --quick
```

### Scripts de Utilidad

```bash
# Verificar que todas las variables de entorno están configuradas
python scripts/check_env.py

# Descargar datos históricos de la API Gamma de Polymarket
python scripts/download_historical_data.py --all --days 30

# Grabar datos WebSocket en vivo a CSV (construir tu propio dataset)
python scripts/record_live_data.py --all --duration-hours 168

# Ejecutar suite de escaneo de seguridad
bash scripts/security_scan.sh
```

---

## Testing

Polybot tiene una suite de tests exhaustiva en 6 niveles:

```bash
# Tests unitarios (298 tests)
pytest tests/unit/ -v

# Tests basados en propiedades con Hypothesis (33 tests)
pytest tests/property/ -v

# Tests de integración — requiere PostgreSQL + Redis (45 tests)
pytest tests/integration/ -v

# Tests end-to-end — requiere servicios en ejecución
pytest tests/e2e/ -v

# Tests de chaos engineering (38 tests, 5 escenarios)
pytest tests/chaos/ -v

# Tests de rendimiento con Locust
locust -f tests/performance/locustfile.py --headless -u 10 -r 2 -t 30s

# Todos los tests con cobertura
pytest --cov=src --cov-report=term-missing tests/

# Linting y verificación de tipos
ruff check src/
mypy src/ --ignore-missing-imports
```

**Resumen de tests:** 1030 tests pasando (878 unitarios + 73 integración + 33 property + 38 chaos + 4 e2e + 4 performance) — cero fallos.

Consulta `AUDIT_REPORT.md` para un análisis detallado de cobertura por módulo.

---

## Despliegue

### Docker

```bash
# Compilar
docker build -t polybot:latest .

# Ejecutar
docker run -d \
  --env-file .env \
  -p 8000:8000 \
  polybot:latest
```

### Kubernetes

El directorio `k8s/` contiene 17 manifiestos YAML en 4 entornos:

| Entorno | Propósito | Modo de Trading |
|---------|-----------|-----------------|
| `base/` | Recursos compartidos (namespace, configmap, serviceaccount) | — |
| `staging/` | Desarrollo y pruebas | Paper |
| `canary/` | Validación pre-producción | Real ($50 máx) |
| `production/` | Trading en vivo | Real |

```bash
# Aplicar en orden
kubectl apply -f k8s/base/
kubectl apply -f k8s/staging/
kubectl apply -f k8s/production/
```

**Características de producción:**
- Horizontal Pod Autoscaler (API: 3–10 réplicas)
- Pod Disruption Budget (API minAvailable 2, trading maxUnavailable 0)
- CronJob de backtesting diario
- Sistema de archivos raíz de solo lectura, sin escalada de privilegios
- HashiCorp Vault para gestión de secretos

### CI/CD

Pipeline de GitHub Actions (`.github/workflows/ci-cd.yml`) con 10 jobs:

1. **Lint y Typecheck** — ruff + mypy + bandit
2. **Escaneo de Seguridad** — pip-audit + trivy + escaneo de secrets
3. **Tests Unitarios** — pytest en Python 3.11 y 3.12
4. **Tests de Integración** — PostgreSQL + Redis
5. **Tests E2E** — ciclo completo con servicios reales
6. **Regresión de Backtest** — todas las combinaciones activo/ventana
7. **Build y Push** — imagen Docker a GHCR
8. **Desplegar Staging** — automático al hacer push a main
9. **Desplegar Canary** — automático al hacer push a main
10. **Desplegar Producción** — requiere aprobación manual

Disparadores: push a `main`, PRs a `main`, programación diaria (06:00 UTC), dispatch manual.

---

## Observabilidad

### Dashboard de Grafana

51 paneles en 6 secciones colapsables:

| Sección | Paneles |
|---------|---------|
| **Salud del Sistema** | Uptime, conexiones WS, fuente de datos de mercado, circuit breaker, salud API, latencia BD/Redis |
| **Rendimiento de Trading** | Balance, PnL, posiciones abiertas, órdenes, tasa de acierto, serie temporal PnL, slippage p50/p95/p99 |
| **Métricas de Riesgo** | Indicador de drawdown, indicador de exposición, decisiones de riesgo (permitir/denegar), disparos de reglas |
| **Señales de Estrategia** | Señales por tipo/activo, rechazos de filtros, mapas de calor de confianza, z-score (en vivo) |
| **Calidad de Ejecución** | Latencia de ciclo p50/p95/p99, reintentos de órdenes, latencia HTTP/API |
| **Seguridad** | Bloqueos de rate limit, disparos de guardrails, entradas de audit log, disparos de circuit breaker |

### Alertas

15 alertas de Prometheus (7 críticas + 8 de advertencia):
- Drawdown diario >15% → crítica (PagerDuty)
- Errores en órdenes reales >3 en 5 minutos → crítica
- Circuit breaker abierto → crítica
- Balance aproximándose al mínimo → advertencia
- Tasa de errores 5xx API >5% → advertencia
- WS degradado a REST → advertencia

---

## Seguridad

| Control | Implementación |
|---------|----------------|
| **Rate Limiter** | Ventana deslizante Redis, 10 órdenes/hora |
| **Circuit Breaker** | 5 fallos en 60s → bloqueo 60s, recuperación half-open |
| **Audit Log** | Tabla inmutable `audit_events` — cada orden real registrada |
| **Security Guard** | Validación pre-orden con múltiples comprobaciones |
| **Key Manager** | Carga segura de clave privada desde entorno |
| **Claves de Idempotencia** | SHA256(estrategia + mercado + minuto truncado) → 16 caracteres |
| **Log Sanitizer** | Filtra `POLYMARKET_PRIVATE_KEY`, `API_SECRET`, `PIN` de todos los logs |
| **Autenticación Telegram** | Solo `TELEGRAM_ADMIN_CHAT_ID` autorizado |
| **BD-antes-de-CLOB** | `Order` persistida como `PENDING` antes de cualquier llamada API |
| **Guardrails** | Máximo fijo de $500 USDC/orden, mínimo $1 USDC (inmutables) |

---

## Documentación

| Documento | Propósito |
|-----------|-----------|
| [`SPEC.md`](./SPEC.md) | Especificación del proyecto, contratos de arquitectura, Decisions Log |
| [`CLAUDE.md`](./CLAUDE.md) | Instrucciones del agente IA + decisiones inmutables |
| [`PLAN_MEJORAS.txt`](./PLAN_MEJORAS.txt) | Plan de mejoras v5.0 (39 prioridades, 7 fases) |
| [`RECORRIDO.txt`](./RECORRIDO.txt) | Registro completo de seguimiento de implementación |
| [`AUDIT_REPORT.md`](./AUDIT_REPORT.md) | Auditoría de seguridad y calidad D20 (puntuación: 81/100) |
| [`README.md`](./README.md) | Este archivo |

---

## Métricas del Proyecto

| Métrica | Valor |
|---------|-------|
| **Tests totales** | 1030 (878 unit + 73 integration + 33 property + 38 chaos + 4 e2e + 4 perf) |
| **Tests de propiedad** | 33 (Hypothesis, ~200 ejemplos cada uno) |
| **Experimentos de chaos** | 5 escenarios, 38 tests |
| **Archivos fuente** | ~200 |
| **Manifiestos K8s** | 17 archivos YAML en 4 entornos |
| **Paneles Grafana** | 51 en 6 secciones |
| **Jobs CI/CD** | 10 |
| **Módulos de seguridad** | 6 |
| **Reglas de riesgo** | 6 (incl. Criterio de Kelly) |
| **Clases de excepción** | 31 en jerarquía tipada |
| **Prioridades completadas** | 39/39 (100%) |
| **Fases estratégicas** | 8-11 en progreso (Fases 8-10 ✅, Fase 11 🔄) |

---

## Limitaciones Conocidas

1. **Sin validación con mercado real** — El bot está técnicamente completo pero no ha sido validado con datos reales del mercado de Polymarket. Esto está bloqueado por la falta de credenciales de la API de Polymarket. Consulta `PLAN_MEJORAS.txt` § P7.3 para la checklist de validación.

2. **La estrategia BAT rinde por debajo en datos sintéticos** — La estrategia Buy Above Threshold (momentum) está fundamentalmente desalineada con los mercados de predicción, que son de reversión a la media por naturaleza. Ha sido degradada a un rol secundario/de diversificación. La estrategia Mean Reversion es la estrategia principal.

3. **Limitaciones de la API de Polymarket** — El endpoint `/prices-history` devuelve arrays vacíos para mercados de predicción; los datos históricos de precios deben recolectarse mediante grabación WebSocket en vivo (`scripts/record_live_data.py`).

4. **Handlers de Telegram** — Existen dos elementos TODO en `src/interfaces/telegram/handlers/` relacionados con la inyección de dependencias del contenedor para la activación del modo real y la persistencia de configuraciones. Ambos tienen soluciones alternativas implementadas.

5. **Brechas de cobertura de tests** — Los routers de API, handlers de Telegram y adaptadores de infraestructura (clientes Redis, WebSocket, CLOB) tienen <50% de cobertura de tests. Consulta `AUDIT_REPORT.md` § 2 para más detalles.

---

## Licencia

[Especifica la licencia aquí — añade un archivo `LICENSE` al repositorio.]

---

*Polybot — Trading algorítmico, rigurosamente diseñado.*
