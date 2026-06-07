# 🚀 GUÍA DE DESPLIEGUE — PolyBot en VPS Hostinger KVM2

> **Tu VPS:** KVM2 con Ubuntu pre-instalado
> **OS esperado:** Ubuntu 22.04 o 24.04 LTS
> **Acceso:** SSH como root o usuario con sudo
> **Dominio:** Gratuito con Hostinger (1 año)

---

## 📋 ÍNDICE RÁPIDO

| Fase | Descripción | Tiempo estimado |
|------|-------------|-----------------|
| PASO 0 | Conectar al VPS | 2 min |
| PASO 1 | Instalar dependencias del sistema | 5 min |
| PASO 2 | Configurar PostgreSQL | 3 min |
| PASO 3 | Clonar el repositorio | 1 min |
| PASO 4 | Configurar variables de entorno (.env) | 5 min |
| PASO 5 | Instalar dependencias Python | 3 min |
| PASO 6 | Migrar base de datos | 1 min |
| PASO 7 | Verificar instalación | 2 min |
| **PASO 8** | **Asignar dominio gratuito Hostinger** | **10 min** |
| **PASO 9** | **Configurar Nginx + SSL (HTTPS)** | **10 min** |
| PASO 10 | Ejecutar el bot en modo paper | 2 min |
| **PASO 11** | **Instalar Codebuff en el VPS** | **5 min** |
| **PASO 12** | **Completar desarrollo pendiente con Codebuff** | **Variable** |

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
    nginx \
    certbot python3-certbot-nginx \
    tmux

# ── Node.js via NVM (necesario para Codebuff en PASO 11) ──
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.1/install.sh | bash
source ~/.bashrc
nvm install --lts
nvm use --lts

# Verificar versiones
python3 --version    # Debe ser 3.11+
node --version       # 20 LTS+
npm --version        # 10+
redis-cli --version  # 7+
psql --version       # 15+
nginx -v             # 1.24+
```

---

## 🔐 PASO 2 — Configurar PostgreSQL

```bash
# Iniciar PostgreSQL
sudo systemctl enable postgresql
sudo systemctl start postgresql

# Crear base de datos y usuario
sudo -u postgres psql <<EOF
CREATE USER botuser WITH PASSWORD 'Ceg0@123';
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

## 🌐 PASO 8 — Asignar el dominio gratuito de Hostinger al VPS

> **Hostinger te da 1 dominio gratis con tu plan de hosting/VPS.** Sigue estos pasos para apuntarlo a tu VPS.

### 8.1 — Reclamar el dominio gratuito (si aún no lo has hecho)

1. Inicia sesión en **[hPanel de Hostinger](https://hpanel.hostinger.com/)**
2. Ve a **Dominios** → **Dominio Gratuito** (o **Claim Free Domain**)
3. Busca y selecciona tu dominio deseado (ej: `mibotpredicciones.com`)
4. Completa el checkout ($0.00 el primer año)
5. El dominio aparecerá en **Dominios** → **Portafolio de Dominios**

### 8.2 — Apuntar el dominio a la IP del VPS (DNS A Records)

1. En hPanel, ve a **Dominios** → **Portafolio de Dominios**
2. Haz clic en **Gestionar** junto a tu dominio
3. En la barra lateral, selecciona **DNS / Nameservers**
4. Localiza (o crea) los registros tipo **A**:

| Tipo | Nombre | Apunta a (Value) | TTL |
|------|--------|------------------|-----|
| A | @ | `<IP-DEL-VPS>` | 3600 |
| A | www | `<IP-DEL-VPS>` | 3600 |

5. Haz clic en **Guardar cambios**

> ⚠️ **Propagación DNS:** Los cambios pueden tardar de 5 minutos a 24 horas en propagarse. Normalmente son ~5-15 minutos.

### 8.3 — Verificar que el dominio resuelve

```bash
# Desde tu terminal local (NO desde el VPS)
ping -c 3 tudominio.com

# Debe responder con la IP de tu VPS
# Si no resuelve todavía, espera unos minutos y vuelve a intentar

# También puedes verificar con:
nslookup tudominio.com
dig tudominio.com +short
```

---

## 🔒 PASO 9 — Configurar Nginx Reverse Proxy + SSL (HTTPS)

> Esto hace que tu dominio sirva la API/Dashboard de PolyBot con HTTPS automático.

### 9.1 — Activar el firewall (solo puertos necesarios)

```bash
# Permitir SSH, HTTP y HTTPS
sudo ufw allow 22/tcp
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp

# (Opcional) Si quieres acceder a Grafana/Prometheus directamente:
# sudo ufw allow 3000/tcp
# sudo ufw allow 9090/tcp

sudo ufw enable
sudo ufw status numbered
```

### 9.2 — Crear configuración de Nginx

```bash
# Crear archivo de configuración para tu dominio
sudo nano /etc/nginx/sites-available/polybot
```

Pega esta configuración (reemplaza `tudominio.com` por tu dominio real):

```nginx
# /etc/nginx/sites-available/polybot
# PolyBot — Nginx Reverse Proxy

# ── Redirección HTTP → HTTPS ──────────────────
server {
    listen 80;
    server_name tudominio.com www.tudominio.com;

    # Para Let's Encrypt ACME challenge
    location /.well-known/acme-challenge/ {
        root /var/www/html;
    }

    location / {
        return 301 https://$host$request_uri;
    }
}

# ── Servidor HTTPS principal ──────────────────
server {
    listen 443 ssl http2;
    server_name tudominio.com www.tudominio.com;

    # ── SSL (Certbot lo autocompleta luego) ────
    ssl_certificate     /etc/letsencrypt/live/tudominio.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/tudominio.com/privkey.pem;
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_ciphers         HIGH:!aNULL:!MD5;

    # ── Seguridad ──────────────────────────────
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-XSS-Protection "1; mode=block" always;
    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;

    # ── Límites ────────────────────────────────
    client_max_body_size 10M;
    client_body_timeout 30s;
    client_header_timeout 30s;

    # ── Rate Limiting ──────────────────────────
    limit_req_zone $binary_remote_addr zone=polybot_api:10m rate=20r/s;
    limit_req zone=polybot_api burst=30 nodelay;

    # ── Proxy Principal → PolyBot API (puerto 8000) ──
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_cache_bypass $http_upgrade;
        proxy_read_timeout 60s;
        proxy_send_timeout 60s;
    }

    # ── WebSocket (si se usa en el futuro) ─────
    location /ws {
        proxy_pass http://127.0.0.1:8000/ws;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_read_timeout 86400s;
    }

    # ── Métricas Prometheus (solo acceso local) ──
    location /metrics {
        proxy_pass http://127.0.0.1:8000/metrics;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        allow 127.0.0.1;
        allow ::1;
        deny all;
    }

    # ── Health Check ───────────────────────────
    location /api/v1/health {
        proxy_pass http://127.0.0.1:8000/api/v1/health;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        access_log off;
    }
}
```

### 9.3 — Activar el sitio y recargar Nginx

```bash
# Activar el sitio
sudo ln -s /etc/nginx/sites-available/polybot /etc/nginx/sites-enabled/

# Eliminar el sitio por defecto (opcional pero recomendado)
sudo rm -f /etc/nginx/sites-enabled/default

# Verificar sintaxis
sudo nginx -t
# Debe decir: "syntax is ok" y "test is successful"

# Recargar Nginx
sudo systemctl reload nginx

# Verificar que Nginx está corriendo
sudo systemctl status nginx
```

### 9.4 — Obtener certificado SSL con Let's Encrypt (GRATIS)

```bash
# Asegúrate de que Nginx está corriendo y el dominio resuelve al VPS
# (El dominio DEBE estar propagado antes de ejecutar certbot)

# Obtener certificado (modo automático)
sudo certbot --nginx -d tudominio.com -d www.tudominio.com

# Seguir las instrucciones en pantalla:
# 1. Ingresa tu email (para avisos de expiración)
# 2. Acepta los términos de servicio
# 3. Decide si compartir email con EFF (opcional)
# 4. Elige si redirigir HTTP a HTTPS (recomendado: opción 2)

# ── Verificar certificado ─────────────────────
sudo certbot certificates
# Debe mostrar tu dominio con fecha de expiración

# ── Verificar auto-renovación ─────────────────
sudo certbot renew --dry-run
# Debe decir "The dry run was successful"

# El certificado se renueva automáticamente (systemd timer de certbot)
# Verificar con: systemctl list-timers | grep certbot
```

### 9.5 — Probar que todo funciona

```bash
# Desde el VPS (prueba local)
curl -k https://localhost/api/v1/health

# Desde tu máquina local
curl https://tudominio.com/api/v1/health
# Debe devolver: {"status":"healthy","timestamp":"..."}

# También puedes abrir en el navegador:
# https://tudominio.com/docs  → Swagger UI
# https://tudominio.com        → Dashboard React (cuando el bot está corriendo)
```

---

## 🚀 PASO 10 — Ejecutar el bot en modo paper (vía systemd)

> **IMPORTANTE:** No ejecutes `python main.py` directamente. Usa systemd para que el bot se auto-reinicie si se cae y arranque al boot del VPS.

### 10.1 — Crear servicio systemd

```bash
sudo cat > /etc/systemd/system/polybot.service << 'SERVEOF'
[Unit]
Description=PolyBot — Polymarket Algorithmic Trading
After=network.target postgresql.service redis-server.service
Wants=network.target postgresql.service redis-server.service

[Service]
Type=simple
User=polybot
WorkingDirectory=/opt/polybot
Environment=PATH=/opt/polybot/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
EnvironmentFile=/opt/polybot/.env
ExecStart=/opt/polybot/.venv/bin/python /opt/polybot/main.py
Restart=on-failure
RestartSec=10
StandardOutput=append:/opt/polybot/logs/bot.log
StandardError=append:/opt/polybot/logs/bot.log

# ── Límites ───────────────────────────────────
LimitNOFILE=65536
MemoryMax=2G
CPUQuota=150%

[Install]
WantedBy=multi-user.target
SERVEOF

# Crear usuario dedicado para el bot (seguridad)
useradd --system --no-create-home --shell /usr/sbin/nologin polybot
chown -R polybot:polybot /opt/polybot

# Crear directorio de logs
mkdir -p /opt/polybot/logs
chown polybot:polybot /opt/polybot/logs

# Recargar systemd y activar el servicio
sudo systemctl daemon-reload
sudo systemctl enable polybot.service
sudo systemctl start polybot.service

# Verificar estado
sudo systemctl status polybot.service

# Ver logs en tiempo real
sudo journalctl -u polybot.service -f
# O: tail -f /opt/polybot/logs/bot.log
```

### 10.2 — Acceder a los servicios

Una vez el bot está corriendo, puedes acceder a:

| Servicio | URL | Descripción |
|----------|-----|-------------|
| **Dashboard** | `https://tudominio.com` | Dashboard React (principal) |
| **API Docs** | `https://tudominio.com/docs` | Swagger UI |
| **API Health** | `https://tudominio.com/api/v1/health` | Health check |
| **Prometheus** | `http://<IP>:9090` (solo local) | Métricas |
| **Grafana** | `http://<IP>:3000` (solo local) | Dashboards (user: admin) |
| **Telegram** | Tu bot en Telegram | Control remoto |

> 💡 **Acceder a Grafana/Prometheus remotamente (túnel SSH):**
> ```bash
> # Desde tu máquina local
> ssh -L 3000:localhost:3000 -L 9090:localhost:9090 root@tudominio.com
> # Luego abre http://localhost:3000 en tu navegador
> ```

---

## 🤖 PASO 11 — Instalar Codebuff en el VPS

> **Codebuff** es el CLI de IA que usas para desarrollar con asistencia de código. Instalado en el VPS, podrás darle instrucciones directamente allí para completar las tareas pendientes del proyecto.

### 11.1 — Instalar Codebuff

```bash
# Node.js ya está instalado (PASO 1). Verificar:
node -v   # Debe mostrar v20+
npm -v    # Debe mostrar v10+

# Instalar Codebuff globalmente
npm install -g codebuff

# Verificar instalación
codebuff --version
```

### 11.2 — Configurar Codebuff

```bash
# Ir al directorio del proyecto
cd /opt/polybot

# Iniciar Codebuff (primera vez pedirá autenticación)
codebuff

# Sigue las instrucciones en pantalla para:
# 1. Autenticarte (API key o login vía navegador)
# 2. Seleccionar el modelo (deepseek, claude, etc.)
```

> 📚 **Documentación de Codebuff:** [codebuff.com/docs](https://codebuff.com/docs)
>
> 🔑 **API Keys:** [codebuff.com/api-keys](https://codebuff.com/api-keys)

### 11.3 — Tips para usar Codebuff en el VPS

```bash
# ── Usar dentro de tmux (para sesiones persistentes) ──
tmux new -s dev
cd /opt/polybot
codebuff
# Ctrl+B, D → salir de tmux (Codebuff sigue corriendo)
# tmux attach -t dev → volver

# ── Para actualizar Codebuff ───────────────────
npm install -g codebuff@latest

# ── Ejecutar Codebuff desde cualquier directorio ──
cd /opt/polybot && codebuff
```

---

## 📝 PASO 12 — Completar el desarrollo pendiente con Codebuff

Con Codebuff instalado en el VPS, ahora puedes completar las tareas pendientes del proyecto directamente. Según `RUTA_IMPLEMENTACION.md`, esto es lo que falta:

### Tareas CRÍTICAS (R1)

Copia y pega estos prompts en Codebuff dentro del VPS. Cada uno completará una tarea pendiente:

**Prompt para R1.1:**
```
Completa R1.1 — Paper Trading Extendido.
Crea el script scripts/run_paper_marathon.py que ejecute 100+ ciclos
de paper trading sin errores, con auto-reinicio con backoff,
métricas por ciclo (latencia, señales, posiciones, PnL), y
guarde resultados en reports/paper_marathon.json.
Sigue el plan en RUTA_IMPLEMENTACION.md.
```

**Prompt para R1.2:**
```
Completa R1.2 — Validación MR con Datos Reales.
Ejecuta scripts/optimize_mr.py con los datos Parquet reales
en data/parquet/, ejecuta walk-forward validation (P10.1),
y guarda los resultados en data/optimization/optimal_params_mr_real.json.
Valida Sharpe > 0.5 (out-of-sample), Profit Factor > 1.1,
Max Drawdown < 20%.
```

**Prompt para R1.3:**
```
Completa R1.3 — Dashboard Event-Driven P11.4.
El dashboard del EventDetector ya debería existir en
monitoring/grafana-event-dashboard.json. Verifica que:
- Tenga paneles para los 4 tipos de eventos
- HALTs activos (gauge)
- Eventos por severidad
- Timeline de eventos
Si falta algo, complétalo y actualiza Grafana provisioning.
```

**Prompt para R1.4:**
```
Completa R1.4 — Auditoría de Seguridad.
Ejecuta scripts/security_scan.sh, verifica que bandit muestra
0 HIGH/MEDIUM issues, verifica que pip-audit no tiene CVEs
críticos nuevos, verifica que .env no expone secrets.
Actualiza AUDIT_REPORT.md con los resultados.
```

**Prompt para R1.5:**
```
Completa R1.5 — Cobertura de Tests.
Añade tests para routers API (markets, orders, positions, dashboard),
execution handlers, y Telegram handlers.
Sube cobertura al 80%+ en esos módulos sin romper tests existentes.
Verifica con pytest --cov.
```

### Tareas de VERIFICACIÓN (R2)

**Prompt para R2.1:**
```
Completa el checklist pre-real-trading R2.1.
Ayúdame a verificar los pasos 2-6:
- Paso 2: Recording 168h activo
- Paso 3: optimize_mr.py con datos reales
- Paso 4: validate_criteria.py con Sharpe > 0.8
- Paso 5: Paper trading 100 ciclos (R1.1)
- Paso 6: Preparar /mode real <PIN>
```

### 12.1 — Guardar y subir cambios a GitHub desde el VPS

> **IMPORTANTE:** Cada vez que Codebuff complete una tarea, haz commit y push para no perder el trabajo.

```bash
cd /opt/polybot

# Ver qué archivos cambiaron
git status

# Añadir todos los cambios
git add -A

# Commit con mensaje descriptivo
git commit -m "feat: completar R1.X — [descripción de la tarea]"

# Subir al repositorio remoto
git push origin main

# ── Si pide autenticación ──
# Opción 1: Usar token de GitHub
#   git remote set-url origin https://TU_TOKEN@github.com/TU_USUARIO/Polybot.git
#
# Opción 2: Usar SSH key
#   ssh-keygen -t ed25519 -C "vps@polybot"
#   cat ~/.ssh/id_ed25519.pub  # Añadir a GitHub → Settings → SSH Keys
#   git remote set-url origin git@github.com:TU_USUARIO/Polybot.git
```

> 💡 **Tip:** Crea un alias en el VPS para facilitar:
> ```bash
> echo 'alias guardar="git add -A && git commit -m \"save: cambios desde VPS\" && git push"' >> ~/.bashrc
> source ~/.bashrc
> # Luego solo escribes: guardar
> ```

---

## 🔄 PASO 13 — Ejecutar recording 24/7 (datos reales)

```bash
# En una sesión tmux separada:
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

## 🐳 ALTERNATIVA: Usar Docker Compose

Si prefieres Docker en vez de instalación manual:

```bash
# Instalar Docker
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
# Cerrar y reabrir sesión SSH

# Instalar Docker Compose
sudo apt install -y docker-compose-plugin

# Asegurar que .env está configurado (PASO 4)

# Construir e iniciar todos los servicios
docker compose up -d

# Ver logs
docker compose logs -f app

# Detener
docker compose down
```

> ⚠️ **Nota con Docker:** Si usas Docker Compose, el PASO 9 (Nginx) debe configurarse para hacer proxy a los puertos expuestos por Docker (8000, 3000, 9090) en vez de localhost. Ajusta `proxy_pass` en consecuencia.

---

## 🛠️ Comandos útiles en el VPS

```bash
# ── PolyBot ────────────────────────────────────
sudo systemctl status polybot        # Estado del bot
sudo systemctl restart polybot       # Reiniciar bot
sudo journalctl -u polybot -f        # Logs en tiempo real
tail -f /opt/polybot/logs/bot.log    # Logs en archivo

# ── Nginx ──────────────────────────────────────
sudo nginx -t                        # Verificar configuración
sudo systemctl reload nginx          # Recargar sin downtime
sudo systemctl restart nginx         # Reiniciar
sudo tail -f /var/log/nginx/access.log  # Logs de acceso
sudo tail -f /var/log/nginx/error.log   # Logs de error

# ── SSL ────────────────────────────────────────
sudo certbot certificates            # Ver certificados
sudo certbot renew                   # Renovar manualmente
sudo certbot renew --dry-run         # Probar renovación

# ── Sistema ────────────────────────────────────
df -h /opt/polybot/data/parquet/     # Espacio en disco
free -h                              # RAM disponible
htop                                 # Monitor de procesos

# ── Codebuff ───────────────────────────────────
codebuff --version                   # Versión instalada
npm update -g codebuff               # Actualizar

# ── Tests ──────────────────────────────────────
cd /opt/polybot && source .venv/bin/activate
python -m pytest tests/unit/ -q      # Tests unitarios
python -m pytest tests/ -q --cov     # Tests + cobertura

# ── Base de Datos ──────────────────────────────
pg_dump -U botuser polybot > backup_$(date +%Y%m%d).sql  # Backup
psql -U botuser -d polybot           # Consola SQL

# ── Métricas ───────────────────────────────────
curl -s http://localhost:8000/metrics | grep polybot
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
- [ ] Dominio HTTPS funcionando con SSL válido
- [ ] Nginx rate limiting activo

---

## 🔐 Seguridad adicional en el VPS

```bash
# ── Firewall ───────────────────────────────────
sudo ufw allow 22/tcp      # SSH
sudo ufw allow 80/tcp      # HTTP (redirección a HTTPS)
sudo ufw allow 443/tcp     # HTTPS
# SOLO si necesitas acceso directo (mejor usar túnel SSH):
# sudo ufw allow 3000/tcp  # Grafana
# sudo ufw allow 9090/tcp  # Prometheus
sudo ufw enable
sudo ufw status numbered

# ── Túnel SSH para Grafana/Prometheus ──────────
# Desde tu máquina local:
ssh -L 3000:localhost:3000 -L 9090:localhost:9090 root@tudominio.com
# Luego abre http://localhost:3000 en tu navegador

# ── Fail2Ban (protección contra brute-force) ───
sudo apt install -y fail2ban
sudo systemctl enable fail2ban
sudo systemctl start fail2ban

# ── Backup automático diario de BD ─────────────
mkdir -p /opt/polybot/backups
echo "0 3 * * * pg_dump -U botuser polybot > /opt/polybot/backups/polybot_\$(date +\%Y\%m\%d).sql" | crontab -

# ── Auto-actualizaciones de seguridad ──────────
sudo apt install -y unattended-upgrades
sudo dpkg-reconfigure --priority=low unattended-upgrades
```

---

## 📊 Diagrama de despliegue

```
                          Internet
                             │
                             ▼
                   ┌─────────────────┐
                   │   Cloudflare /   │
                   │   Hostinger DNS  │
                   │  (A → VPS IP)    │
                   └────────┬────────┘
                            │
                            ▼
                   ┌─────────────────┐
                   │   NGINX :443     │
                   │   (SSL/TLS)      │
                   │   Reverse Proxy  │
                   │   Rate Limiting  │
                   └────────┬────────┘
                            │
              ┌─────────────┼─────────────┐
              │             │             │
              ▼             ▼             ▼
     ┌────────────┐ ┌────────────┐ ┌────────────┐
     │ PolyBot    │ │ Prometheus │ │ Grafana    │
     │ API :8000  │ │ :9090      │ │ :3000      │
     └─────┬──────┘ └────────────┘ └────────────┘
           │
     ┌─────┼──────┐
     │     │      │
     ▼     ▼      ▼
  ┌────┐ ┌────┐ ┌──────────┐
  │ PG │ │Redis│ │ Parquet  │
  │:5432│:6379│ │ /data/   │
  └────┘ └────┘ └──────────┘
```

---

*Guía completada. El VPS está listo para ejecutar PolyBot con dominio, HTTPS y Codebuff. Empieza con paper trading y escala gradualmente.*
