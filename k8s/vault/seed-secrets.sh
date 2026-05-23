#!/usr/bin/env bash
# k8s/vault/seed-secrets.sh
# PolyBot — Siembra inicial de secrets en Vault KV v2
#
# Requisitos:
#   1. Vault server corriendo y VAULT_ADDR/VAULT_TOKEN seteados
#   2. KV v2 engine montado en secret/ (default)
#   3. setup-auth-role.sh ya ejecutado
#
# Uso:
#   export VAULT_ADDR=https://vault.internal:8200
#   export VAULT_TOKEN=$(vault login -method=oidc -token-only)
#   bash k8s/vault/seed-secrets.sh
#
# NOTA: Este script NUNCA debe contener valores de secrets reales.
#       Lee los valores del entorno actual o pide input interactivo.
# -------------------------------------------------------------------
set -euo pipefail

: "${VAULT_ADDR:?VAULT_ADDR no está definido}"
: "${VAULT_TOKEN:?VAULT_TOKEN no está definido — ejecuta vault login primero}"

echo "══════════════════════════════════════════════════════════════════"
echo "  PolyBot — Vault Secret Seeding"
echo "══════════════════════════════════════════════════════════════════"
echo ""
echo "  🔒 Este script siembra los secrets en Vault KV v2."
echo "     Los valores se toman del entorno actual (.env) del operador."
echo "     NUNCA se loguean ni se persisten en disco."
echo ""

# ── Función segura: lee del entorno o pide input ──────────────────
get_secret() {
  local var_name="$1"
  local description="$2"
  local value="${!var_name:-}"

  if [[ -n "$value" ]]; then
    echo "  ✅ $var_name: (desde entorno)"
  else
    read -rsp "  Ingresa $description ($var_name): " value
    echo ""
  fi
  echo "$value"
}

# ── Recoge todos los secrets ──────────────────────────────────────
echo "[1/2] Recogiendo secrets..."

PRIVATE_KEY=$(get_secret POLYMARKET_PRIVATE_KEY "PolyMarket Private Key")
API_KEY=$(get_secret POLYMARKET_API_KEY "PolyMarket API Key")
API_SECRET=$(get_secret POLYMARKET_API_SECRET "PolyMarket API Secret")
API_PASSPHRASE=$(get_secret POLYMARKET_API_PASSPHRASE "PolyMarket API Passphrase")
WALLET_ADDRESS=$(get_secret POLYMARKET_WALLET_ADDRESS "PolyMarket Wallet Address")
TELEGRAM_TOKEN=$(get_secret TELEGRAM_BOT_TOKEN "Telegram Bot Token")
TELEGRAM_CHAT_ID=$(get_secret TELEGRAM_ADMIN_CHAT_ID "Telegram Admin Chat ID")
DATABASE_URL=$(get_secret DATABASE_URL "Database URL")
REDIS_URL="${REDIS_URL:-redis://redis:6379/0}"
REAL_MODE_PIN=$(get_secret REAL_MODE_PIN "Real Mode PIN")

# ── Valida que todos los campos tengan valor ───────────────────────
if [[ -z "$PRIVATE_KEY" || -z "$API_KEY" || -z "$API_SECRET" ||
      -z "$API_PASSPHRASE" || -z "$WALLET_ADDRESS" ||
      -z "$TELEGRAM_TOKEN" || -z "$TELEGRAM_CHAT_ID" ||
      -z "$DATABASE_URL" || -z "$REAL_MODE_PIN" ]]; then
  echo ""
  echo "  ❌ ERROR: Uno o más secrets están vacíos. Abortando."
  exit 1
fi

# ── Escribe en Vault (los valores nunca se muestran) ──────────────
echo ""
echo "[2/2] Escribiendo secrets en Vault..."

# ── Grupo principal: polybot ──────────────────────────────────────
# Usa stdin para evitar que los secrets aparezcan en /proc/PID/cmdline
vault kv put secret/polybot - <<EOF
{
  "data": {
    "POLYMARKET_PRIVATE_KEY": "$PRIVATE_KEY",
    "POLYMARKET_API_KEY": "$API_KEY",
    "POLYMARKET_API_SECRET": "$API_SECRET",
    "POLYMARKET_API_PASSPHRASE": "$API_PASSPHRASE",
    "POLYMARKET_WALLET_ADDRESS": "$WALLET_ADDRESS",
    "TELEGRAM_BOT_TOKEN": "$TELEGRAM_TOKEN",
    "TELEGRAM_ADMIN_CHAT_ID": "$TELEGRAM_CHAT_ID",
    "REAL_MODE_PIN": "$REAL_MODE_PIN"
  }
}
EOF

echo "  ✅ secret/polybot"

# ── Grupo de infraestructura ──────────────────────────────────────
vault kv put secret/polybot-infra - <<EOF
{
  "data": {
    "DATABASE_URL": "$DATABASE_URL",
    "REDIS_URL": "$REDIS_URL"
  }
}
EOF

echo "  ✅ secret/polybot-infra"

# ── Verifica que se puedan leer ───────────────────────────────────
echo ""
echo "  Verificando lectura..."

vault kv get secret/polybot > /dev/null 2>&1 && echo "  ✅ secret/polybot legible"
vault kv get secret/polybot-infra > /dev/null 2>&1 && echo "  ✅ secret/polybot-infra legible"

echo ""
echo "══════════════════════════════════════════════════════════════════"
echo "  ✅ Secrets sembrados en Vault"
echo ""
echo "  PRÓXIMO PASO: Desplegar Vault Agent Injector en el cluster:"
echo "    helm install vault hashicorp/vault -f k8s/vault/agent-config.yaml"
echo "══════════════════════════════════════════════════════════════════"
