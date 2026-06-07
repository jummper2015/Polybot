# 🚀 GUÍA DE DESPLIEGUE — PolyBot en VPS Hostinger KVM2

> **Tu VPS:** KVM2 con Claude Code pre-instalado  
> **OS esperado:** Ubuntu 22.04 o 24.04 LTS  
> **Acceso:** SSH como root o usuario con sudo

---

## 📋 PASO 0 — Conectar al VPS

```bash
# Desde tu terminal local
ssh root@<IP-DEL-VPS>

# Verificar specs
lscpu | grep "Model name"      # 2 vCPUs
free -h                        # RAM disponible
df -h /                        # Disco disponible
cat /etc/os-release            # Ubuntu 22.04/24.04
```

---

## 📦 PASO 1 — Instalar dependencias del sistema

```bash
# Actualizar sistema
sudo apt update && sudo apt upgrade -y

# Instalar dependencias esenciales
sudo apt install -y \
    git curl wget build-essential \
    python3 python3-pip python3-venv python3-dev \
    libpq-dev gcc \
    postgresql postgresql-client \
    redis-server \
    nodejs npm \
    nginx

# Verificar versiones
python3 --version    # Debe ser 3.11+
node --version       # 18+
redis-cli --version  # 7+
psql --version       # 15+
```

---

## 🔐 PASO 2 — Configurar PostgreSQL

```bash
# Iniciar PostgreSQL
sudo systemctl enable postgresql
sudo systemctl start postgresql

# Crear base de datos y usuario
sudo -u postgres psql <<EOF
CREATE USER botuser WITH PASSWORD 'TU_PASSWORD_SEGURO';
CREATE DATABASE polybot OWNER botuser;
GRANT ALL PRIVILEGES ON DATABASE polybot TO botuser;
\q
EOF

# Verificar conexión
psql -h localhost -U botuser -d polybot -c "SELECT 1;"  # Debe mostrar 1
```

---

## 📂 PASO 3 — Clonar el repositorio

```bash
cd /opt
git clone <URL-DEL-REPO-POLYBOT> polybot
cd polybot

# Verificar que todo está
ls -la
# Debes ver: src/ tests/ dashboard/ monitoring/ k8s/ docker-compose.yml etc.
```

---

## ⚙️ PASO 4 — Configurar variables de entorno (.env)

```bash
# Crear .env desde el template
cp .env.example .env   # Si existe .env.example
# O crearlo manualmente:

cat > .env << 'ENVEOF'
# ── Polymarket API ────────────────────────────
POLYMARKET_API_KEY=TU_API_KEY
POLYMARKET_API_SECRET=TU_API_SECRET
POLYMARKET_API_PASSPHRASE=TU_API_PASSPHRASE
POLYMARKET_PRIVATE_KEY=TU_PRIVATE_KEY_EIP712
POLYMARKET_BUILDER_CODE=0xTU_BUILDER_CODE

# ── Base de Datos ─────────────────────────────
DATABASE_URL=postgresql+asyncpg://botuser:TU_PASSWORD_SEGURO@localhost:5432/polybot

# ── Redis ─────────────────────────────────────
REDIS_URL=redis://localhost:6379/0

# ── Telegram ──────────────────────────────────
TELEGRAM_BOT_TOKEN=TU_BOT_TOKEN
TELEGRAM_ADMIN_CHAT_ID=TU_CHAT_ID

# ── Seguridad ─────────────────────────────────
REAL_MODE_PIN=123456

# ── Modo de Trading ───────────────────────────
TRADING_MODE=paper
REST_ONLY=true

# ── Ensemble Mode (P11.2) ─────────────────────
ENSEMBLE_MODE=true

# ── Logging ───────────────────────────────────
LOG_LEVEL=INFO

# ── API Server ────────────────────────────────
API_HOST=0.0.0.0
API_PORT=8000

# ── Prometheus ────────────────────────────────
PROMETHEUS_PORT=9090

# ── Grafana ───────────────────────────────────
GRAFANA_ADMIN_PASSWORD=TU_PASSWORD_GRAFANA
ENVEOF

# Ajustar permisos
chmod 600 .env
```

---

## 🐍 PASO 5 — Instalar dependencias Python

```bash
# Crear entorno virtual
python3 -m venv .venv
source .venv/bin/activate

# Instalar dependencias
pip install --upgrade pip
pip install -r requirements.txt

# Verificar import exitoso
python -c "from src.core.bootstrap import bootstrap; print('✅ Import OK')"
```

---

## 🗄️ PASO 6 — Migrar base de datos

```bash
# Ejecutar migraciones Alembic
alembic upgrade head

# Verificar que las tablas se crearon
psql -h localhost -U botuser -d polybot -c "\dt"
# Debes ver: markets, orders, positions, bot_settings, audit_events, alembic_version
```

---

## ✅ PASO 7 — Verificar instalación

```bash
# Ejecutar tests rápidos (sin DB/Redis - solo tests unitarios)
python -m pytest tests/unit/ -q --tb=short 2>&1 | tail -5

# Deben pasar ~900+ tests. Si pasan, la instalación es correcta.

# Verificar variables de entorno
python scripts/check_env.py

# Debe mostrar todas las variables [SET] en verde.
```

---

## 🚀 PASO 8 — Ejecutar el bot en modo paper

```bash
# Asegurar que el entorno virtual está activo
source /opt/polybot/.venv/bin/activate
cd /opt/polybot

# Ejecutar el bot
python main.py

# Salida esperada:
#   [INFO] Bootstrapping PolyBot...
#   [INFO] Markets discovered: X BTC, Y ETH
#   [INFO] Trading mode: paper
#   [INFO] Bot started. Listening on 0.0.0.0:8000
```

> ⚠️ **Primera ejecución:** El bot arrancará en paper trading.  
> Déjalo correr al menos 30 min para verificar estabilidad.  
> Ctrl+C para detener limpiamente.

---

## 📊 PASO 9 — Acceder a los servicios

Una vez el bot está corriendo, puedes acceder a:

| Servicio | URL | Descripción |
|----------|-----|-------------|
| **API** | `http://<IP>:8000` | API REST principal |
| **API Docs** | `http://<IP>:8000/docs` | Swagger UI |
| **Dashboard** | `http://<IP>:8000` | Dashboard React |
| **Prometheus** | `http://<IP>:9090` | Métricas |
| **Grafana** | `http://<IP>:3000` | Dashboards (user: admin) |
| **Telegram** | Tu bot en Telegram | Control remoto |

---

## 🔄 PASO 10 — Ejecutar recording 24/7 (datos reales)

```bash
# En una sesión tmux separada (para que no se pare al cerrar SSH):
tmux new -s recording

cd /opt/polybot && source .venv/bin/activate

# Grabar 168h (1 semana) de datos
python scripts/record_live_headless.py \
    --all \
    --duration-hours 168 \
    --batch-size 500 \
    --output-dir data/parquet

# Ctrl+B, D para salir de tmux (el proceso sigue corriendo)
# Para volver: tmux attach -t recording
```

---

## 🐳 ALTERNATIVA: Usar Docker Compose (recomendado para producción)

Si prefieres Docker en vez de instalación manual:

```bash
# Instalar Docker
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
# Cerrar y reabrir sesión SSH

# Instalar Docker Compose
sudo apt install -y docker-compose-plugin

# Asegurar que .env está configurado (Paso 4)

# Construir e iniciar todos los servicios
docker compose up -d

# Ver logs
docker compose logs -f app

# Detener
docker compose down
```

---

## 📝 PASO 11 — Próximos pasos en el VPS

Una vez el bot está corriendo estable:

1. **Paper trading 100+ ciclos**  
   `python main.py --mode paper` y déjalo correr horas/días.  
   Verifica en Grafana → "PolyBot — Regime Awareness" que ves regímenes y señales.

2. **Optimizar MeanReversion con datos reales**  
   Después de acumular ≥168h de recording:  
   `python scripts/optimize_mr.py --csv data/parquet/`

3. **Validar criterios de éxito**  
   `python scripts/validate_criteria.py --strategy mean_reversion`

4. **Activar real trading (requiere credenciales)**  
   Solo cuando los pasos anteriores den positivo:  
   Desde Telegram: `/mode real <PIN>`

---

## 🛠️ Comandos útiles en el VPS

```bash
# Ver si el bot está corriendo
ps aux | grep main.py

# Ver logs en tiempo real
tail -f /opt/polybot/logs/*.log

# Reiniciar el bot limpiamente
kill -SIGINT $(pgrep -f main.py)  # shutdown graceful
# Luego: python main.py

# Ver espacio en disco (importante para recording)
df -h /opt/polybot/data/parquet/

# Ejecutar tests rápidos
source .venv/bin/activate && python -m pytest tests/unit/ -q

# Ver métricas Prometheus
curl http://localhost:8000/metrics | grep polybot

# Backup de la BD
pg_dump -U botuser polybot > backup_$(date +%Y%m%d).sql
```

---

## 🚨 Checklist pre-real-trading en el VPS

Antes de activar real trading, verifica en el VPS:

- [ ] Paper trading 100+ ciclos sin errores
- [ ] 168h+ de recording con 0% data loss
- [ ] Sharpe > 0.8 en validación con datos reales
- [ ] `.env` con credenciales reales configuradas
- [ ] `POLYMARKET_BUILDER_CODE` configurado
- [ ] Telegram bot responde a `/status`
- [ ] Grafana muestra dashboards con datos
- [ ] Alertas Prometheus funcionales
- [ ] Backup diario de PostgreSQL configurado
- [ ] `REAL_MODE_PIN` configurado y seguro

---

## 🔐 Seguridad adicional en el VPS

```bash
# Firewall: solo exponer puertos necesarios
sudo ufw allow 22/tcp      # SSH
sudo ufw allow 8000/tcp    # API + Dashboard
sudo ufw allow 3000/tcp    # Grafana (o usa tunnel SSH)
sudo ufw allow 9090/tcp    # Prometheus (o usa tunnel SSH)
sudo ufw enable

# Tunnel SSH para Grafana (más seguro que exponer puerto)
# Desde tu máquina local:
ssh -L 3000:localhost:3000 root@<IP-DEL-VPS>
# Luego abre http://localhost:3000 en tu navegador

# Backup automático diario de BD
echo "0 3 * * * pg_dump -U botuser polybot > /opt/polybot/backups/polybot_\$(date +\%Y\%m\%d).sql" | crontab -
```

---

*Guía completada. El VPS está listo para ejecutar PolyBot. Empieza con paper trading y escala gradualmente.*
