# k8s/vault/policy.hcl
# PolyBot — Vault Policy: acceso de lectura a secrets del bot
#
# KV v2 engine: las capabilities se definen sobre el path lógico.
# - secret/data/*     → datos (lectura del secreto)
# - secret/metadata/* → metadata (listado, existencia)
#
# Aplicar con:
#   vault policy write polybot-policy k8s/vault/policy.hcl

# ── Secrets de Polymarket y del bot ──────────────────────────────────
path "secret/data/polybot" {
  capabilities = ["read"]
}

path "secret/metadata/polybot" {
  capabilities = ["read", "list"]
}

# ── Secrets de infraestructura (DB, Redis) ───────────────────────────
path "secret/data/polybot-infra" {
  capabilities = ["read"]
}

path "secret/metadata/polybot-infra" {
  capabilities = ["read", "list"]
}

# ── Denegar acceso a cualquier otro path ─────────────────────────────
# Vault por defecto deniega — esta es una regla explícita de seguridad
path "*" {
  capabilities = ["deny"]
}
