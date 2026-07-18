#!/usr/bin/env bash
# Regression: Windows `ods restart` must recreate containers so model/env
# changes made after bootstrap hot-swap are visible to env-backed consumers
# such as Perplexica.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ODS_PS1="$ROOT_DIR/installers/windows/ods.ps1"

PASS=0
FAIL=0
pass() { echo "[PASS] $1"; PASS=$((PASS + 1)); }
fail() { echo "[FAIL] $1"; FAIL=$((FAIL + 1)); }

restart_block="$(awk '
    /function Invoke-Restart/ { in_block=1 }
    in_block { print }
    in_block && /function Invoke-Logs/ { exit }
' "$ODS_PS1")"

[[ -n "$restart_block" ]] \
    && pass "Invoke-Restart block extracted" \
    || fail "Invoke-Restart block missing"

if grep -qF -- '-ComposeArgs @("up", "-d", "--force-recreate", $Service)' <<<"$restart_block"; then
    pass "single-service restart recreates the selected container with current .env"
else
    fail "single-service restart must use docker compose up -d --force-recreate <service>"
fi

if grep -qF -- '-ComposeArgs @("up", "-d", "--force-recreate")' <<<"$restart_block"; then
    pass "all-service restart recreates containers with current .env"
else
    fail "all-service restart must use docker compose up -d --force-recreate"
fi

if grep -qF -- '-ComposeArgs @("restart"' <<<"$restart_block"; then
    fail "Invoke-Restart must not use docker compose restart because it preserves stale container env"
else
    pass "Invoke-Restart avoids docker compose restart stale-env behavior"
fi

if grep -qF 'docker compose up --force-recreate failed' <<<"$restart_block"; then
    pass "restart failure message names the recreate command"
else
    fail "restart failure message should name docker compose up --force-recreate"
fi

echo
echo "Windows restart recreate contract: $PASS passed, $FAIL failed"
[[ "$FAIL" -eq 0 ]]
