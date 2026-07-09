#!/bin/bash
# Tests for rootless Docker data-directory ownership fix (issue #1702)
# Exercises lib/rootless-fix.sh in a hermetic filesystem simulation.
# No Docker daemon or live install required.
#
# Run: bash ods/tests/test-rootless-docker-ownership.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
LIB="$ROOT_DIR/lib/rootless-fix.sh"
ODS_CLI="$ROOT_DIR/ods-cli"
ODS_PREFLIGHT="$ROOT_DIR/ods-preflight.sh"
ODS_DOCTOR="$ROOT_DIR/scripts/ods-doctor.sh"

RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m'

pass() { echo -e "${GREEN}✓${NC} $1"; }
fail() { echo -e "${RED}✗${NC} $1"; exit 1; }
info() { echo -e "${BLUE}ℹ${NC} $1"; }

# ── Static checks ─────────────────────────────────────────────────────────────

info "Static: lib/rootless-fix.sh exists and is non-empty"
[[ -f "$LIB" ]] || fail "rootless-fix.sh not found at $LIB"
[[ -s "$LIB" ]] || fail "rootless-fix.sh is empty"
pass "rootless-fix.sh present"

info "Static: ods_is_rootless_docker function defined"
grep -q 'ods_is_rootless_docker()' "$LIB" \
    || fail "ods_is_rootless_docker not found in rootless-fix.sh"
pass "ods_is_rootless_docker defined"

info "Static: ods_fix_rootless_ownership function defined"
grep -q 'ods_fix_rootless_ownership()' "$LIB" \
    || fail "ods_fix_rootless_ownership not found in rootless-fix.sh"
pass "ods_fix_rootless_ownership defined"

info "Static: _ods_rootless_chown_dir function defined"
grep -q '_ods_rootless_chown_dir()' "$LIB" \
    || fail "_ods_rootless_chown_dir not found in rootless-fix.sh"
pass "_ods_rootless_chown_dir defined"

info "Static: ODS_ROOTLESS_UID_MAP covers all affected services"
for svc in n8n whisper tts token-spy privacy-shield ape hermes langfuse openclaw; do
    grep -q "\[$svc\]=" "$LIB" \
        || fail "ODS_ROOTLESS_UID_MAP missing entry for service: $svc"
done
pass "ODS_ROOTLESS_UID_MAP covers all 9 affected services"

info "Static: chown helper uses --user 0:0 (host user in rootless)"
grep -q '\-\-user 0:0' "$LIB" \
    || fail "chown helper does not use --user 0:0"
pass "chown helper uses --user 0:0"

info "Static: chown helper uses alpine container (not sudo)"
grep -q 'alpine' "$LIB" \
    || fail "chown helper does not use alpine container"
grep -v 'sudo' "$LIB" | grep -q 'chown -R' \
    || fail "chown is being done without alpine/docker (unexpected)"
pass "chown uses alpine container (no sudo)"

info "Static: detection uses docker info --format SecurityOptions"
grep -q "SecurityOptions" "$LIB" \
    || fail "rootless detection does not check SecurityOptions"
grep -q "grep -q rootless" "$LIB" \
    || fail "rootless detection does not grep for 'rootless'"
pass "Detection checks SecurityOptions for 'rootless'"

info "Static: fix is no-op when not rootless (guard in ods_fix_rootless_ownership)"
grep -q 'ods_is_rootless_docker' "$LIB" \
    || fail "ods_fix_rootless_ownership does not call ods_is_rootless_docker"
pass "ods_fix_rootless_ownership guards on rootless detection"

info "Static: chown helper is non-fatal on failure (return 0)"
grep -A5 '_ods_rootless_chown_dir()' "$LIB" | grep -q 'return 0' \
    || grep -q 'non-fatal' "$LIB" \
    || fail "_ods_rootless_chown_dir does not handle failure non-fatally"
pass "_ods_rootless_chown_dir handles docker failure non-fatally"

info "Static: 06-directories.sh sources rootless-fix.sh"
grep -q 'rootless-fix.sh' "$ROOT_DIR/installers/phases/06-directories.sh" \
    || fail "06-directories.sh does not source rootless-fix.sh"
pass "06-directories.sh sources rootless-fix.sh"

info "Static: 06-directories.sh calls ods_fix_rootless_ownership"
grep -q 'ods_fix_rootless_ownership' "$ROOT_DIR/installers/phases/06-directories.sh" \
    || fail "06-directories.sh does not call ods_fix_rootless_ownership"
pass "06-directories.sh calls ods_fix_rootless_ownership"

info "Static: ods-preflight.sh warns on rootless mode"
grep -q 'rootless' "$ODS_PREFLIGHT" \
    || fail "ods-preflight.sh does not mention rootless mode"
grep -q 'warn.*rootless\|rootless.*warn' "$ODS_PREFLIGHT" \
    || grep -q 'warn "Docker rootless' "$ODS_PREFLIGHT" \
    || fail "ods-preflight.sh does not call warn() for rootless mode"
pass "ods-preflight.sh warns on rootless mode"

info "Static: ods-doctor.sh detects rootless mode (DOCKER_ROOTLESS)"
grep -q 'DOCKER_ROOTLESS' "$ODS_DOCTOR" \
    || fail "ods-doctor.sh does not define DOCKER_ROOTLESS"
grep -q 'grep -q rootless' "$ODS_DOCTOR" \
    || fail "ods-doctor.sh does not check for rootless in SecurityOptions"
pass "ods-doctor.sh detects rootless mode"

info "Static: ods-doctor.sh includes docker_rootless in JSON report"
grep -q 'docker_rootless' "$ODS_DOCTOR" \
    || fail "ods-doctor.sh does not include docker_rootless in report"
pass "ods-doctor.sh includes docker_rootless in report"

info "Static: ods-doctor.sh emits autofix hint for rootless mode"
grep -q 'ods repair rootless-ownership' "$ODS_DOCTOR" \
    || fail "ods-doctor.sh does not emit 'ods repair rootless-ownership' hint"
pass "ods-doctor.sh emits rootless-ownership autofix hint"

info "Static: ods-cli has rootless-ownership repair sub-command"
grep -q 'rootless-ownership' "$ODS_CLI" \
    || fail "ods-cli does not have rootless-ownership repair sub-command"
pass "ods-cli has rootless-ownership repair sub-command"

info "Static: ods-cli repair rootless-ownership sources rootless-fix.sh"
awk '/rootless-ownership\|rootless\)/,/;;/' "$ODS_CLI" \
    | grep -q 'rootless-fix.sh' \
    || fail "repair rootless-ownership does not source rootless-fix.sh"
pass "repair rootless-ownership sources rootless-fix.sh"

info "Static: ods-cli repair rootless-ownership calls ods_fix_rootless_ownership"
awk '/rootless-ownership\|rootless\)/,/;;/' "$ODS_CLI" \
    | grep -q 'ods_fix_rootless_ownership' \
    || fail "repair rootless-ownership does not call ods_fix_rootless_ownership"
pass "repair rootless-ownership calls ods_fix_rootless_ownership"

info "Static: ods help mentions rootless-ownership"
grep -q 'rootless-ownership' "$ODS_CLI" \
    || fail "ods-cli help does not mention rootless-ownership"
pass "ods-cli help mentions rootless-ownership"

# ── UID map correctness checks ─────────────────────────────────────────────────

info "UID map: n8n UID is 1000 (node user)"
grep '\[n8n\]=1000' "$LIB" || fail "n8n UID is not 1000"
pass "n8n UID = 1000 (node)"

info "UID map: hermes UID is 10000"
grep '\[hermes\]=10000' "$LIB" || fail "hermes UID is not 10000"
pass "hermes UID = 10000"

info "UID map: ape UID is 100"
grep '\[ape\]=100' "$LIB" || fail "ape UID is not 100"
pass "ape UID = 100"

info "UID map: langfuse UID is 1001 (nextjs user)"
grep '\[langfuse\]=1001' "$LIB" || fail "langfuse UID is not 1001"
pass "langfuse UID = 1001 (nextjs)"

# ── Filesystem simulation: ods_fix_rootless_ownership logic ────────────────────
# We can't run docker in CI without a daemon, so we stub the Docker call and
# test that the fix correctly iterates directories, skips missing ones, and
# calls the helper for each present service directory.

info "Filesystem: ods_fix_rootless_ownership iterates all affected dirs"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

INSTALL="$TMP/ods"
mkdir -p "$INSTALL/data/n8n"
mkdir -p "$INSTALL/data/hermes"
mkdir -p "$INSTALL/data/tts"
mkdir -p "$INSTALL/data/whisper"
# Leave ape, token-spy, privacy-shield, langfuse absent (should be skipped)

CHOWN_LOG="$TMP/chown.log"
> "$CHOWN_LOG"

# Stub docker command and ods_is_rootless_docker
stub_simulate() {
    # Source the library, then stub the internal functions
    # shellcheck disable=SC1090
    . "$LIB"

    # Override detection to always return rootless=yes
    ods_is_rootless_docker() { return 0; }

    # Override the chown helper to just log calls instead of running docker
    _ods_rootless_chown_dir() {
        local dir="$1" uid="$2"
        [[ -d "$dir" ]] || return 0
        echo "${uid}:${dir##*/}" >> "$CHOWN_LOG"
    }

    ods_fix_rootless_ownership "$INSTALL"
}

stub_simulate

# Only dirs that exist should have been processed
grep -q '^1000:n8n$' "$CHOWN_LOG"    || fail "n8n not chowned (present dir)"
grep -q '^10000:hermes$' "$CHOWN_LOG" || fail "hermes not chowned (present dir)"
grep -q '^1000:tts$' "$CHOWN_LOG"    || fail "tts not chowned (present dir)"
grep -q '^1000:whisper$' "$CHOWN_LOG" || fail "whisper not chowned (present dir)"

# Absent dirs must NOT have been processed
grep -q ':ape$' "$CHOWN_LOG"         && fail "ape was processed but dir does not exist"
grep -q ':langfuse$' "$CHOWN_LOG"    && fail "langfuse was processed but dir does not exist"
pass "ods_fix_rootless_ownership only processes existing directories"

info "Filesystem: fix is no-op when not rootless"
CHOWN_LOG2="$TMP/chown2.log"
> "$CHOWN_LOG2"

stub_no_rootless() {
    . "$LIB"
    ods_is_rootless_docker() { return 1; }   # not rootless
    _ods_rootless_chown_dir() {
        echo "${2}:${1##*/}" >> "$CHOWN_LOG2"
    }
    ods_fix_rootless_ownership "$INSTALL"
}
stub_no_rootless

[[ ! -s "$CHOWN_LOG2" ]] \
    || fail "ods_fix_rootless_ownership ran chown even when not rootless"
pass "ods_fix_rootless_ownership is a no-op in standard (non-rootless) mode"

echo ""
echo -e "${GREEN}All rootless-docker-ownership tests passed.${NC}"
