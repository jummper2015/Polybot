# Polybot

Agente de trading y backtesting para mercados de criptomonedas (Polymarket).

**Resumen**
- Proyecto en Python que incluye: conectores a Polymarket, servicios de mercado, ejecución (paper/real), motor de estrategias, reglas de riesgo y backtesting.

**Estado**
- Código: prototipo listo para desarrollo y pruebas.
- Tests: hay suites unitarias, de integración y E2E en `tests/`.

**Requisitos**
- Python 3.11+
- Redis (opcional, para cache)
- PostgreSQL (si se usa persistencia)
- Docker y docker-compose (opcional)

**Instalación rápida**
1. Clona el repositorio:

```bash
git clone <repo-url> polybot
cd polybot
```

2. Crea y activa un entorno virtual:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt || pip install -e .
```

3. Copia variables de entorno:

```bash
cp .env.example .env
# editar .env según sea necesario
```

**Ejecutar en desarrollo**
- Lanzar la aplicación:

```bash
python main.py
```

- Ejecutar el backtester:

```bash
python -m src.backtesting.cli --help
```

**Docker**
- Levantar servicios con Docker Compose:

```bash
docker-compose up --build
```

**Pruebas**
- Ejecutar tests unitarios y de integración:

```bash
pytest tests/unit tests/integration -q
```

- Ejecutar E2E (requiere servicios levantados):

```bash
pytest tests/e2e -q
```

**Estructura principal**
- `src/` – código fuente principal
  - `application/` – DTOs, puertos y servicios de aplicación
  - `domain/` – entidades, enums y value objects
  - `infrastructure/` – adaptadores, DB, clientes HTTP/WS
  - `execution/` – handlers (paper/real)
  - `strategies/` – implementaciones de estrategia y motor
  - `risk/` – reglas y motor de riesgo
- `tests/` – pruebas unitarias, integración y E2E
- `alembic/` – migraciones de base de datos

**Variables de entorno**
- Revisar `.env.example` para todas las variables necesarias.

**Contribuir**
- Abrir issues para bugs o mejoras.
- Hacer PRs desde ramas con descripciones claras.

**Contacto**
- Mantener pr y referencias en el repositorio remoto.

**Licencia**
- (Indicar licencia aquí) — añadir `LICENSE` si procede.
