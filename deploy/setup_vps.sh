#!/usr/bin/env bash
# =============================================================================
#  deploy/setup_vps.sh — PolyBot VPS Deployment Script
# =============================================================================
#  Automatiza los PASOS 1-10 de GUIA_DESPLIEGUE_VPS.md.
#
#  USO (desde tu máquina local):
#    1. scp deploy/setup_vps.sh root@<IP-DEL-VPS>:/root/
#    2. ssh root@<IP-DEL-VPS>
#    3. chmod +x setup_vps.sh && ./setup_vps.sh
#
#  OPCIÓN RÁPIDA (copia y ejecuta en una línea desde local):
#    scp deploy/setup_vps.sh root@<IP>:/root/ && \
#    ssh root@<IP> "chmod +x /root/setup_vps.sh && /root/setup_vps.sh"
# =============================================================================

set -euo pipefail

# ═══════════════════════════════════════════════════════════════════════════
# CONFIGURACIÓN — CAMBIA ESTOS VALORES
# ═══════════════════════════════════════════════════════════════════════════

# IP pública del VPS (se puede pasar como argumento: ./setup_vps.sh 1.2.3.4)
VPS_IP="${1:-CAMBIA_ESTE_VALOR}"

# Tu dominio de Hostinger
DOMAIN="${2:-CAMBIA_ESTE_VALOR}"

# URL del repositorio (HTTPS o SSH)
REPO_URL="${3:-CAMBIA_ESTE_VALOR}"

# Contraseña de PostgreSQL
DB_PASSWORD="${4:-$(openssl rand -hex 16)}"

# Contraseña de Grafana
GRAFANA_PASSWORD="${5:-$(openssl rand -hex 12)}"

# ═══════════════════════════════════════════════════════════════════════════
# NO MODIFICAR DEBAJO DE ESTA LÍNEA (a menos que sepas lo que haces)
# ═══════════════════════════════════════════════════════════════════════════

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

PROJECT_DIR="/opt/polybot"

echo ""
echo -e "${CYAN}════════════════════════════════════════════════════════════${NC}"
echo -e "${CYAN}  POLYBOT VPS SETUP — Hostinger KVM2${NC}"
echo -e "${CYAN}════════════════════════════════════════════════════════════${NC}"
echo ""

# ── Validaciones iniciales ──────────────────────────────────────────────
if [[ "$VPS_IP" == "CAMBIA_ESTE_VALOR" ]]; then
    echo -e "${RED}❌ ERROR: Debes pasar la IP del VPS como primer argumento${NC}"
    echo "   Uso: ./setup_vps.sh <IP> <DOMINIO> <REPO_URL>"
    exit 1
fi
if [[ "$DOMAIN" == "CAMBIA_ESTE_VALOR" ]]; then
    echo -e "${YELLOW}⚠️  Sin dominio especificado — se saltará la configuración de Nginx/SSL${NC}"
    SKIP_NGINX=true
else
    SKIP_NGINX=false
fi
if [[ "$REPO_URL" == "CAMBIA_ESTE_VALOR" ]]; then
    echo -e "${RED}❌ ERROR: Debes pasar la URL del repo como tercer argumento${NC}"
    exit 1
fi

# ── PASO 1: Actualizar sistema e instalar dependencias ──────────────────
echo -e "\n${CYAN}[1/9] Instalando dependencias del sistema...${NC}"
apt-get update -qq && apt-get upgrade -y -qq
apt-get install -y -qq \
    git curl wget build-essential \
    python3 python3-pip python3-venv python3-dev \
    libpq-dev gcc \
    postgresql postgresql-client \
    redis-server \
    nginx \
    certbot python3-certbot-nginx \
    tmux fail2ban

# Node.js via NVM (para Codebuff)
if ! command -v node &>/dev/null; then
    curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.1/install.sh | bash
    export NVM_DIR="$HOME/.nvm"
    [ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"
    nvm install --lts
    nvm use --lts
fi

echo -e "${GREEN}✅ Dependencias instaladas${NC}"

# ── PASO 2: PostgreSQL ──────────────────────────────────────────────────
echo -e "\n${CYAN}[2/9] Configurando PostgreSQL...${NC}"
systemctl enable postgresql --quiet
systemctl start postgresql

sudo -u postgres psql <<EOF 2>/dev/null
DO \$\$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'botuser') THEN
        CREATE USER botuser WITH PASSWORD '${DB_PASSWORD}';
    END IF;
END
\$\$;
CREATE DATABASE polybot OWNER botuser;
GRANT ALL PRIVILEGES ON DATABASE polybot TO botuser;
EOF

echo -e "${GREEN}✅ PostgreSQL listo (user: botuser, db: polybot)${NC}"

# ── PASO 3: Redis ───────────────────────────────────────────────────────
echo -e "\n${CYAN}[3/9] Iniciando Redis...${NC}"
systemctl enable redis-server --quiet
systemctl start redis-server
echo -e "${GREEN}✅ Redis corriendo${NC}"

# ── PASO 4: Clonar repositorio ──────────────────────────────────────────
echo -e "\n${CYAN}[4/9] Clonando repositorio...${NC}"
if [[ -d "$PROJECT_DIR" ]]; then
    echo -e "${YELLOW}⚠️  $PROJECT_DIR ya existe — actualizando...${NC}"
    cd "$PROJECT_DIR" && git pull
else
    git clone "$REPO_URL" "$PROJECT_DIR"
fi
cd "$PROJECT_DIR"
echo -e "${GREEN}✅ Repositorio listo en $PROJECT_DIR${NC}"

# ── PASO 5: Entorno virtual Python ──────────────────────────────────────
echo -e "\n${CYAN}[5/9] Instalando dependencias Python...${NC}"
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip -q
pip install -r requirements.txt -q
echo -e "${GREEN}✅ Dependencias Python instaladas${NC}"

# ── PASO 6: .env ────────────────────────────────────────────────────────
echo -e "\n${CYAN}[6/9] Configurando .env...${NC}"
if [[ -f ".env" ]]; then
    echo -e "${YELLOW}⚠️  .env ya existe — conservando${NC}"
else
    if [[ -f ".env.example" ]]; then
        cp .env.example .env
    fi
    # Actualiza las variables que conocemos
    cat >> .env <<ENVEOF

# ── Autogenerado por setup_vps.sh ────────────────
DATABASE_URL=postgresql+asyncpg://botuser:${DB_PASSWORD}@localhost:5432/polybot
REDIS_URL=redis://localhost:6379/0
API_HOST=0.0.0.0
API_PORT=8000
TRADING_MODE=paper
REST_ONLY=true
LOG_LEVEL=INFO
GRAFANA_ADMIN_PASSWORD=${GRAFANA_PASSWORD}
ENVEOF
    chmod 600 .env
    echo -e "${YELLOW}⚠️  .env creado con valores por defecto${NC}"
    echo -e "${YELLOW}   DEBES configurar manualmente:${NC}"
    echo -e "${YELLOW}   - POLYMARKET_API_KEY, POLYMARKET_API_SECRET${NC}"
    echo -e "${YELLOW}   - POLYMARKET_PRIVATE_KEY, POLYMARKET_BUILDER_CODE${NC}"
    echo -e "${YELLOW}   - TELEGRAM_BOT_TOKEN, TELEGRAM_ADMIN_CHAT_ID${NC}"
fi

# ── PASO 7: Migraciones DB ──────────────────────────────────────────────
echo -e "\n${CYAN}[7/9] Ejecutando migraciones...${NC}"
source .venv/bin/activate
python -m alembic upgrade head
echo -e "${GREEN}✅ Migraciones aplicadas${NC}"

# ── PASO 8: Nginx + SSL ─────────────────────────────────────────────────
if [[ "$SKIP_NGINX" == false ]]; then
    echo -e "\n${CYAN}[8/9] Configurando Nginx + SSL para $DOMAIN...${NC}"

    # Firewall
    ufw allow 22/tcp
    ufw allow 80/tcp
    ufw allow 443/tcp
    ufw --force enable

    # Copiar config de Nginx desde el template del proyecto
    if [[ -f "deploy/nginx-polybot.conf" ]]; then
        sed "s/tudominio.com/$DOMAIN/g" deploy/nginx-polybot.conf > /etc/nginx/sites-available/polybot
    fi

    rm -f /etc/nginx/sites-enabled/default
    ln -sf /etc/nginx/sites-available/polybot /etc/nginx/sites-enabled/
    nginx -t && systemctl reload nginx

    # SSL (solo si el dominio resuelve al VPS)
    if host "$DOMAIN" 2>/dev/null | grep -q "$VPS_IP"; then
        certbot --nginx -d "$DOMAIN" -d "www.$DOMAIN" --non-interactive --agree-tos \
            -m "admin@${DOMAIN}" --redirect 2>/dev/null || true
        echo -e "${GREEN}✅ SSL configurado para $DOMAIN${NC}"
    else
        echo -e "${YELLOW}⚠️  Dominio $DOMAIN no resuelve a $VPS_IP aún${NC}"
        echo -e "${YELLOW}   Configura los DNS A records en Hostinger y ejecuta:${NC}"
        echo -e "${YELLOW}   sudo certbot --nginx -d $DOMAIN -d www.$DOMAIN${NC}"
    fi
else
    echo -e "\n${CYAN}[8/9] Saltando Nginx (sin dominio configurado)${NC}"
fi

# ── PASO 9: Systemd service ─────────────────────────────────────────────
echo -e "\n${CYAN}[9/9] Creando servicio systemd para PolyBot...${NC}"

useradd --system --no-create-home --shell /usr/sbin/nologin polybot 2>/dev/null || true
chown -R polybot:polybot "$PROJECT_DIR"
mkdir -p "$PROJECT_DIR/logs"
chown polybot:polybot "$PROJECT_DIR/logs"

cat > /etc/systemd/system/polybot.service << SERVEOF
[Unit]
Description=PolyBot — Polymarket Algorithmic Trading
After=network.target postgresql.service redis-server.service
Wants=network.target postgresql.service redis-server.service

[Service]
Type=simple
User=polybot
WorkingDirectory=${PROJECT_DIR}
Environment=PATH=${PROJECT_DIR}/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
EnvironmentFile=${PROJECT_DIR}/.env
ExecStart=${PROJECT_DIR}/.venv/bin/python ${PROJECT_DIR}/main.py
Restart=on-failure
RestartSec=10
StandardOutput=append:${PROJECT_DIR}/logs/bot.log
StandardError=append:${PROJECT_DIR}/logs/bot.log
LimitNOFILE=65536
MemoryMax=2G
CPUQuota=150%

[Install]
WantedBy=multi-user.target
SERVEOF

systemctl daemon-reload
systemctl enable polybot.service

echo -e "${GREEN}✅ Servicio systemd creado${NC}"

# ── Instalar Codebuff (opcional) ────────────────────────────────────────
if command -v npm &>/dev/null; then
    echo -e "\n${CYAN}[EXTRA] Instalando Codebuff...${NC}"
    npm install -g codebuff 2>/dev/null && echo -e "${GREEN}✅ Codebuff instalado${NC}" || echo -e "${YELLOW}⚠️  Codebuff no se pudo instalar${NC}"
fi

# ── RESUMEN ─────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}════════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}  ✅ SETUP COMPLETADO${NC}"
echo -e "${GREEN}════════════════════════════════════════════════════════════${NC}"
echo ""
echo -e "  📁 Proyecto:   ${PROJECT_DIR}"
echo -e "  🗄️  PostgreSQL: botuser / ${DB_PASSWORD}"
echo -e "  📊 Grafana:    admin / ${GRAFANA_PASSWORD}"
echo ""
echo -e "  ${YELLOW}PRÓXIMOS PASOS MANUALES:${NC}"
echo -e "  1. Configura las credenciales en ${PROJECT_DIR}/.env"
echo -e "  2. Verifica: cd ${PROJECT_DIR} && source .venv/bin/activate"
echo -e "  3. Tests:    python -m pytest tests/unit/ -q"
echo -e "  4. Arranca:  sudo systemctl start polybot.service"
echo -e "  5. Logs:     sudo journalctl -u polybot.service -f"
echo ""
if [[ "$SKIP_NGINX" == false ]]; then
    echo -e "  🌐 Dashboard: https://${DOMAIN}"
    echo -e "  📖 API Docs:  https://${DOMAIN}/docs"
else
    echo -e "  🌐 Dashboard: http://${VPS_IP}:8000"
    echo -e "  📖 API Docs:  http://${VPS_IP}:8000/docs"
fi
echo ""
echo -e "  🔒 Recuerda:  passwd -l root && configurar fail2ban"
echo ""
