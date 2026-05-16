# Dockerfile

FROM python:3.11-slim

# Metadatos
LABEL maintainer="polymarket-bot"
LABEL version="1.0.0"

# Variables de entorno del sistema
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app \
    ENV=production

WORKDIR /app

# Instala dependencias del sistema (mínimas)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Copia y instala dependencias Python primero (caching de layers)
COPY pyproject.toml .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -e .

# Copia el código fuente
COPY src/      ./src/
COPY alembic/  ./alembic/
COPY alembic.ini .
COPY main.py   .

# Usuario no-root para seguridad
RUN useradd --create-home --shell /bin/bash botuser && \
    chown -R botuser:botuser /app
USER botuser

# Puerto de la API
EXPOSE 8000

# Health check del contenedor
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "import httpx; httpx.get('http://localhost:8000/api/v1/health').raise_for_status()"

# Comando de arranque
CMD ["python", "main.py"]