#!/usr/bin/env bash
# =============================================================================
#  scripts/security_scan.sh — PolyBot Security Scanning (P3.5)
#
#  Ejecuta 4 herramientas de seguridad:
#    1. bandit        — SAST: vulnerabilidades en código Python
#    2. pip-audit     — SCA:  CVEs en dependencias Python
#    3. trivy         — Filesystem scan (via Docker)
#    4. secrets scan  — Detección de secrets commiteados
#
#  Uso:
#    chmod +x scripts/security_scan.sh
#    ./scripts/security_scan.sh
#
#  CI usage:
#    ./scripts/security_scan.sh --ci  # exit code 1 on any finding
# =============================================================================

set -euo pipefail

CI_MODE=false
if [[ "${1:-}" == "--ci" ]]; then
    CI_MODE=true
fi

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

PASS=0
FAIL=0
WARN=0

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_ROOT"

echo ""
echo "================================================================================"
echo "  POLYBOT SECURITY SCAN — P3.5"
echo "================================================================================"
echo ""

# ──────────────────────────────────────────────────────────────────────────────
# 1. BANDIT — Static Analysis Security Testing
# ──────────────────────────────────────────────────────────────────────────────
echo -e "${CYAN}[1/4] bandit — SAST (Python code vulnerabilities)${NC}"
echo "--------------------------------------------------------------------------------"

if command -v bandit &>/dev/null; then
    # -r src/: recursive scan of source
    # -ll: medium + high severity only
    # -f json: machine-readable output (stderr to /dev/null to keep JSON clean)
    BANDIT_OUTPUT="$(bandit -r src/ -ll -f json --quiet 2>/dev/null)" || true

    # Count findings by severity (|| true: don't abort scan if JSON parse fails)
    HIGH=$(echo "$BANDIT_OUTPUT" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    results = data.get('results', [])
    high = sum(1 for r in results if r.get('issue_severity') == 'HIGH')
    med  = sum(1 for r in results if r.get('issue_severity') == 'MEDIUM')
    print(f'{high} {med}')
except: print('0 0')
" 2>/dev/null || echo "0 0")
    HIGH_COUNT=$(echo "$HIGH" | awk '{print $1}')
    MED_COUNT=$(echo "$HIGH" | awk '{print $2}')

    if [[ "$HIGH_COUNT" -gt 0 ]]; then
        echo -e "  ${RED}❌ HIGH: $HIGH_COUNT findings${NC}"
        FAIL=$((FAIL + 1))
    else
        echo -e "  ${GREEN}✅ HIGH: 0${NC}"
        PASS=$((PASS + 1))
    fi

    if [[ "$MED_COUNT" -gt 0 ]]; then
        echo -e "  ${YELLOW}⚠️  MEDIUM: $MED_COUNT findings (review recommended)${NC}"
        WARN=$((WARN + 1))
    else
        echo -e "  ${GREEN}✅ MEDIUM: 0${NC}"
    fi

    # Print details for any findings (|| true: don't abort scan if parse fails)
    if [[ "$HIGH_COUNT" -gt 0 ]] || [[ "$MED_COUNT" -gt 0 ]]; then
        echo ""
        echo "  Details:"
        echo "$BANDIT_OUTPUT" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    for r in data.get('results', []):
        sev = r.get('issue_severity', '')
        if sev in ('HIGH', 'MEDIUM'):
            print(f\"    [{sev}] {r['filename']}:{r['line_number']} — {r['test_name']}: {r['issue_text']}\")
except: pass
" 2>/dev/null || true
    fi
else
    echo -e "  ${YELLOW}⚠️  bandit not installed. Run: pip install bandit${NC}"
    WARN=$((WARN + 1))
fi

echo ""

# ──────────────────────────────────────────────────────────────────────────────
# 2. PIP-AUDIT — Software Composition Analysis
# ──────────────────────────────────────────────────────────────────────────────
echo -e "${CYAN}[2/4] pip-audit — SCA (CVE scanning for Python dependencies)${NC}"
echo "--------------------------------------------------------------------------------"

if command -v pip-audit &>/dev/null; then
    # stderr to /dev/null: keep JSON output clean for parsing
    PIP_AUDIT_OUTPUT="$(pip-audit --format json 2>/dev/null)" || true
    VULN_COUNT=$(echo "$PIP_AUDIT_OUTPUT" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    deps = data.get('dependencies', [])
    count = sum(1 for d in deps if d.get('vulns'))
    print(count)
except: print('0')
" 2>/dev/null || echo "0")

    if [[ "$VULN_COUNT" -gt 0 ]]; then
        echo -e "  ${RED}❌ Vulnerabilities found: $VULN_COUNT packages with CVEs${NC}"
        echo ""
        echo "  Details:"
        echo "$PIP_AUDIT_OUTPUT" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    for d in data.get('dependencies', []):
        for v in d.get('vulns', []):
            print(f\"    [{v.get('severity','?')}] {d['name']} {d['version']}: {v['id']} — {v.get('description','')[:120]}\")
except: pass
" 2>/dev/null || true
        FAIL=$((FAIL + 1))
    else
        echo -e "  ${GREEN}✅ No known CVEs in dependencies${NC}"
        PASS=$((PASS + 1))
    fi
else
    echo -e "  ${YELLOW}⚠️  pip-audit not installed. Run: pip install pip-audit${NC}"
    WARN=$((WARN + 1))
fi

echo ""

# ──────────────────────────────────────────────────────────────────────────────
# 3. TRIVY — Filesystem vulnerability scanner
# ──────────────────────────────────────────────────────────────────────────────
echo -e "${CYAN}[3/4] trivy — Filesystem scan (HIGH, CRITICAL)${NC}"
echo "--------------------------------------------------------------------------------"

TRIVY_RAN=false
TRIVY_EXIT=0
if command -v trivy &>/dev/null; then
    # Native trivy binary — capture exit code before || true
    set +e
    trivy fs --severity HIGH,CRITICAL --quiet "$PROJECT_ROOT" 2>&1
    TRIVY_EXIT=$?
    set -e
    TRIVY_RAN=true
elif docker --version &>/dev/null 2>&1; then
    # Docker-based trivy — capture exit code before || true
    echo "  Using trivy via Docker..."
    set +e
    docker run --rm --pull=missing \
        -v "$PROJECT_ROOT":/project:ro \
        aquasec/trivy:latest \
        fs --severity HIGH,CRITICAL --quiet /project 2>&1
    TRIVY_EXIT=$?
    set -e
    TRIVY_RAN=true
fi

if [[ "$TRIVY_RAN" == true ]]; then
    if [[ "$TRIVY_EXIT" -eq 0 ]]; then
        echo -e "  ${GREEN}✅ No HIGH/CRITICAL findings${NC}"
        PASS=$((PASS + 1))
    else
        echo -e "  ${YELLOW}⚠️  trivy found issues (see output above)${NC}"
        WARN=$((WARN + 1))
    fi
else
    echo -e "  ${YELLOW}⚠️  trivy not available (install binary or Docker).${NC}"
    echo "     Install: curl -sfL https://raw.githubusercontent.com/aquasecurity/trivy/main/contrib/install.sh | sh"
    WARN=$((WARN + 1))
fi

echo ""

# ──────────────────────────────────────────────────────────────────────────────
# 4. SECRETS SCAN — git history grep for leaked secrets
# ──────────────────────────────────────────────────────────────────────────────
echo -e "${CYAN}[4/4] secrets scan — Committed secrets detection${NC}"
echo "--------------------------------------------------------------------------------"

SECRET_PATTERNS=(
    'POLYMARKET_PRIVATE_KEY\s*=\s*[A-Za-z0-9]{20,}'
    'POLYMARKET_API_SECRET\s*=\s*[A-Za-z0-9+/]{20,}'
    'TELEGRAM_BOT_TOKEN\s*=\s*[0-9]{8,}:[A-Za-z0-9_-]{20,}'
    'DATABASE_URL\s*=\s*postgres(ql)?://[^:]+:[^@]+@'
    'REAL_MODE_PIN\s*=\s*[0-9]{4,}'
    'sk-[A-Za-z0-9]{32,}'
    'xox[bprs]-[A-Za-z0-9-]+'
    '-----BEGIN (RSA|EC|DSA|OPENSSH) PRIVATE KEY-----'
    'ghp_[A-Za-z0-9]{36}'
    'AIza[0-9A-Za-z\-_]{35}'
)

FOUND_SECRETS=0
for pattern in "${SECRET_PATTERNS[@]}"; do
    # Search committed files only (not working tree)
    MATCHES=$(git grep -n -E "$pattern" HEAD 2>/dev/null || true)
    if [[ -n "$MATCHES" ]]; then
        FOUND_SECRETS=$((FOUND_SECRETS + 1))
        echo -e "  ${RED}❌ Potential secret found:${NC}"
        echo "$MATCHES" | while IFS= read -r line; do
            echo "     $line"
        done
    fi
done

# Also check .env files in git history (|| true: don't abort if no commits)
ENV_SECRETS=$(git log --all --full-history -- '*.env' '**/.env' 2>/dev/null | head -20) || true
if [[ -n "$ENV_SECRETS" ]]; then
    FOUND_SECRETS=$((FOUND_SECRETS + 1))
    echo -e "  ${YELLOW}⚠️  .env files found in git history (may contain secrets)${NC}"
fi

if [[ "$FOUND_SECRETS" -eq 0 ]]; then
    echo -e "  ${GREEN}✅ No secrets found in git history${NC}"
    PASS=$((PASS + 1))
else
    echo -e "  ${RED}❌ $FOUND_SECRETS potential secret(s) found${NC}"
    FAIL=$((FAIL + 1))
fi

# ──────────────────────────────────────────────────────────────────────────────
# SUMMARY
# ──────────────────────────────────────────────────────────────────────────────
echo ""
echo "================================================================================"
echo "  SCAN SUMMARY"
echo "================================================================================"
TOTAL=$((PASS + FAIL + WARN))
echo -e "  ${GREEN}Pass:  $PASS/${TOTAL}${NC}"
echo -e "  ${RED}Fail:  $FAIL/${TOTAL}${NC}"
echo -e "  ${YELLOW}Warn:  $WARN/${TOTAL}${NC}"
echo "--------------------------------------------------------------------------------"

if [[ "$CI_MODE" == true ]]; then
    if [[ "$FAIL" -gt 0 ]] || [[ "$HIGH_COUNT" -gt 0 ]]; then
        echo "  ❌ CI CHECK FAILED — review findings above"
        exit 1
    else
        echo "  ✅ CI CHECK PASSED"
        exit 0
    fi
fi

echo ""
echo "  Scan complete at $(date -u +"%Y-%m-%dT%H:%M:%SZ")"
echo "================================================================================"
