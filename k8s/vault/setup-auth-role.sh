#!/usr/bin/env bash
# k8s/vault/setup-auth-role.sh
# PolyBot — Configura la autenticación Kubernetes → Vault
#
# Requisitos previos:
#   1. Vault server corriendo (interno o externo)
#   2. kubectl configurado al cluster correcto
#   3. VAULT_ADDR y VAULT_TOKEN seteados
#
# Uso:
#   export VAULT_ADDR=https://vault.internal:8200
#   export VAULT_TOKEN=$(vault login -method=oidc -token-only)
#   bash k8s/vault/setup-auth-role.sh
# -------------------------------------------------------------------
set -euo pipefail

: "${VAULT_ADDR:?VAULT_ADDR no está definido}"
: "${VAULT_TOKEN:?VAULT_TOKEN no está definido — ejecuta vault login primero}"

NAMESPACES=("staging" "canary" "production")
SA_NAME="polybot"
POLICY_NAME="polybot-policy"

echo "══════════════════════════════════════════════════════════════════"
echo "  PolyBot — Vault Kubernetes Auth Setup"
echo "══════════════════════════════════════════════════════════════════"
echo ""

# ── 1. Habilitar Kubernetes auth method (si no existe) ────────────
echo "[1/4] Habilitando Kubernetes auth method..."
vault auth enable kubernetes 2>/dev/null || echo "  (ya estaba habilitado)"

# ── 2. Configurar conexión al cluster ──────────────────────────────
echo ""
echo "[2/4] Configurando conexión al cluster Kubernetes..."

KUBE_HOST=$(kubectl config view --raw -o jsonpath='{.clusters[0].cluster.server}')
KUBE_CA_CERT=$(kubectl config view --raw -o jsonpath='{.clusters[0].cluster.certificate-authority-data}')
KUBE_TOKEN_REVIEWER_JWT=$(
  kubectl create token vault-auth -n default --duration=8760h 2>/dev/null ||
  kubectl get secret vault-auth-token -n default -o jsonpath='{.data.token}' 2>/dev/null | base64 -d ||
  echo ""
)

if [[ -z "$KUBE_TOKEN_REVIEWER_JWT" ]]; then
  echo "  ⚠️  No se pudo obtener el token reviewer JWT."
  echo "     Crea un ServiceAccount 'vault-auth' en default namespace:"
  echo "       kubectl create sa vault-auth -n default"  echo "     kubectl create token vault-auth -n default --duration=8760h"
  exit 1
fi

vault write auth/kubernetes/config \
  kubernetes_host="$KUBE_HOST" \
  kubernetes_ca_cert="@<(echo $KUBE_CA_CERT | base64 -d)" \
  token_reviewer_jwt="$KUBE_TOKEN_REVIEWER_JWT" \
  issuer="https://kubernetes.default.svc.cluster.local"

echo "  ✅ Kubernetes auth configurado"

# ── 3. Cargar policy ───────────────────────────────────────────────
echo ""
echo "[3/4] Cargando policy '$POLICY_NAME'..."
vault policy write "$POLICY_NAME" k8s/vault/policy.hcl
echo "  ✅ Policy cargada"

# ── 4. Crear roles para cada namespace ─────────────────────────────
echo ""
echo "[4/4] Creando roles Vault para cada namespace..."

for ns in "${NAMESPACES[@]}"; do
  ROLE_NAME="polybot-${ns}"

  vault write "auth/kubernetes/role/${ROLE_NAME}" \
    bound_service_account_names="$SA_NAME" \
    bound_service_account_namespaces="$ns" \
    policies="$POLICY_NAME" \
    ttl="24h" \
    max_ttl="72h"

  echo "  ✅ Role '$ROLE_NAME' → SA=$SA_NAME, NS=$ns"
done

echo ""
echo "══════════════════════════════════════════════════════════════════"
echo "  ✅ Vault Kubernetes Auth configurado"
echo ""
echo "  Roles creados:"
for ns in "${NAMESPACES[@]}"; do
  echo "    - polybot-${ns}  (namespace: $ns, SA: $SA_NAME)"
done
echo ""
echo "  PRÓXIMO PASO: Sembrar los secrets en Vault:"
echo "    bash k8s/vault/seed-secrets.sh"
echo "══════════════════════════════════════════════════════════════════"
