# k8s/vault/README.md
# PolyBot — HashiCorp Vault Integration (P4.3)
# 
# Setup ordenado para secret management con Vault Agent Injector.
#
# ─── Arquitectura ───────────────────────────────────────────────────
#
#   Vault Server (externo o Helm)
#        │
#        │ 1. Kubernetes Auth (SA → Role → Policy)
#        ▼
#   Vault Agent Injector (Helm chart)
#        │
#        │ 2. Webhook muta pods con anotaciones vault.hashicorp.com/*
#        ▼
#   Pod → init container fetch secrets → /vault/secrets/*.env
#        │
#        │ 3. Entrypoint sources /vault/secrets/*.env antes de python
#        ▼
#   App (python main.py) → os.environ → KeyManager / SecureConfig
#
# ─── Setup paso a paso ──────────────────────────────────────────────
#
# 1. Instalar Vault Agent Injector (solo injector, sin server):
#      helm repo add hashicorp https://helm.releases.hashicorp.com
#      helm upgrade --install vault hashicorp/vault \
#        --namespace vault --create-namespace \
#        -f k8s/vault/agent-config.yaml
#
# 2. Configurar autenticación Kubernetes → Vault:
#      export VAULT_ADDR=https://vault.internal:8200
#      vault login -method=oidc
#      bash k8s/vault/setup-auth-role.sh
#
# 3. Sembrar secrets en Vault:
#      bash k8s/vault/seed-secrets.sh
#
# 4. Desplegar la app (ya tiene las anotaciones en los pod templates):
#      kubectl apply -f k8s/base/
#      kubectl apply -f k8s/staging/
#
# ─── Secretos gestionados ───────────────────────────────────────────
#
#   secret/data/polybot (grupo principal):
#     POLYMARKET_PRIVATE_KEY    — Clave privada wallet (EIP-712)
#     POLYMARKET_API_KEY        — API key L2 authentication
#     POLYMARKET_API_SECRET     — API secret para firmas
#     POLYMARKET_API_PASSPHRASE — Passphrase adicional
#     POLYMARKET_WALLET_ADDRESS — Dirección pública wallet
#     TELEGRAM_BOT_TOKEN        — Token del bot Telegram
#     TELEGRAM_ADMIN_CHAT_ID    — Chat ID del admin
#     REAL_MODE_PIN             — PIN para activar real trading
#
#   secret/data/polybot-infra (infraestructura):
#     DATABASE_URL              — URL conexión PostgreSQL
#     REDIS_URL                 — URL conexión Redis
#
# ─── Rotación ───────────────────────────────────────────────────────
#
#   - API keys de Polymarket: rotación manual cada 30 días recomendada
#   - El Vault Agent detecta cambios y actualiza /vault/secrets/*.env
#   - La app debe reiniciarse para recargar env vars (o usar file watch)
#   - Anotación vault...agent-rotate-period: "720h" = 30 días
#
# ─── Modo desarrollo local (sin Vault) ──────────────────────────────
#
#   Para desarrollo local, usar el Secret resource directamente:
#     kubectl create secret generic polybot-secrets --from-env-file=.env -n staging
#
#   El entrypoint script del pod detecta si /vault/secrets/ existe:
#     - Si existe → source de Vault (producción)
#     - Si no existe → usa env vars del Secret (desarrollo)
#
# ─── Archivos ───────────────────────────────────────────────────────
#
#   policy.hcl           — Vault policy (read secret/data/polybot*)
#   agent-config.yaml    — Helm values para Vault Agent Injector
#   setup-auth-role.sh   — Crea Kubernetes auth role en Vault
#   seed-secrets.sh      — Siembra secrets iniciales en Vault
#   README.md            — Este archivo
