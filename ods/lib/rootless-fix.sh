#!/bin/bash
# =============================================================================
# ODS — lib/rootless-fix.sh
# =============================================================================
# Helpers for detecting Docker rootless mode and correcting data-directory
# ownership so non-root container users can write their bind-mount paths.
#
# In Docker rootless mode the UID namespace is shifted by the host user's
# subuid offset (typically 100000). Containers that run as UID 0 map to the
# host user (fine), but containers that run as a non-root UID N map to host
# UID (100000 + N - 1). The installer creates data directories owned by the
# host user (UID 1000), so those non-root containers hit EACCES on startup.
#
# The only reliable way to chown without sudo is to start a short-lived
# Alpine container as UID 0 (which maps to the host user in rootless mode)
# and run `chown` inside that container — exactly what the issue author's
# workaround does.
#
# Public API
# ----------
#   ods_is_rootless_docker     → exit 0 if rootless, exit 1 otherwise
#   ods_fix_rootless_ownership → chown all affected dirs; idempotent / safe
#   ods_warn_rootless_docker   → print a human-readable warning block
#
# Caller requirements
# -------------------
#   INSTALL_DIR must be set before calling ods_fix_rootless_ownership.
#   docker must be in PATH.
#   The functions are deliberately side-effect-free when rootless is not
#   detected; they are always safe to call unconditionally.
# =============================================================================

# Mapping: data subdirectory → container UID that needs write access.
# Sourced from the compose files and entrypoint scripts for each service.
#
# Services NOT listed here run as UID 0 (root) in the container; those map
# to the host user in rootless mode and need no special handling.
declare -A ODS_ROOTLESS_UID_MAP=(
    # n8n — runs as node (UID 1000)
    [n8n]=1000
    # whisper — runs as ubuntu (UID 1000)
    [whisper]=1000
    # tts (Kokoro) — runs as appuser (UID 1000)
    [tts]=1000
    # token-spy — runs as odser (UID 1000)
    [token-spy]=1000
    # privacy-shield — runs as UID 1000
    [privacy-shield]=1000
    # ape — runs as ape (UID 100)
    [ape]=100
    # hermes — gateway runs as hermes (UID 10000)
    [hermes]=10000
    # langfuse-web — runs as nextjs (UID 1001); postgres/clickhouse/redis/minio
    # all run as root (UID 0) so only langfuse itself needs fixing.
    # The top-level langfuse dir is the NextJS app data dir.
    [langfuse]=1001
    # openclaw — runs as node (UID 1000); data/openclaw/home is used by the container
    [openclaw]=1000
)

# ---------------------------------------------------------------------------
# ods_is_rootless_docker
# Returns 0 if Docker is running in rootless mode, 1 otherwise.
# ---------------------------------------------------------------------------
ods_is_rootless_docker() {
    docker info --format '{{.SecurityOptions}}' 2>/dev/null | grep -q rootless
}

# ---------------------------------------------------------------------------
# ods_warn_rootless_docker [INSTALL_DIR]
# Print a human-readable advisory block.  Safe to call regardless of mode.
# ---------------------------------------------------------------------------
ods_warn_rootless_docker() {
    local install_dir="${1:-${INSTALL_DIR:-~/ods}}"
    cat <<ROOTLESS_WARN
[!!] Docker rootless mode detected.
     In rootless mode, container UIDs are remapped through the host user's
     subuid offset (typically 100000).  Non-root container users such as
     node (UID 1000), hermes (UID 10000), and nextjs (UID 1001) will be
     remapped to host UIDs 100999, 109999, 101000, etc.  Data directories
     created by the installer are owned by the host user and cannot be
     written by those remapped UIDs.

     ODS will automatically fix ownership for all affected services.
     If you see EACCES errors after a manual reinstall, run:
       ods repair rootless-ownership

ROOTLESS_WARN
}

# ---------------------------------------------------------------------------
# _ods_rootless_chown_dir SERVICE_DATA_DIR CONTAINER_UID
# Internal helper — runs a short-lived Alpine container to chown one dir.
# ---------------------------------------------------------------------------
_ods_rootless_chown_dir() {
    local dir="$1"
    local uid="$2"

    [[ -d "$dir" ]] || return 0          # nothing to fix
    [[ -n "$uid" ]] || return 0

    # Use UID 0 in the container (= host user in rootless mode) so we have
    # permission to chown without sudo.
    docker run --rm \
        --user 0:0 \
        --network none \
        -v "${dir}:/data" \
        alpine:3 \
        sh -c "chown -R ${uid}:${uid} /data" \
        2>/dev/null || {
            echo "[warn] rootless-fix: chown ${uid}:${uid} on ${dir} failed (non-fatal)" >&2
            return 0   # non-fatal; continuing is better than hard-failing
        }
}

# ---------------------------------------------------------------------------
# ods_fix_rootless_ownership [INSTALL_DIR]
# Idempotent — sets ownership on all affected data dirs for rootless Docker.
# Only does anything when rootless mode is actually detected.
# ---------------------------------------------------------------------------
ods_fix_rootless_ownership() {
    local install_dir="${1:-${INSTALL_DIR:-}}"

    if [[ -z "$install_dir" ]]; then
        echo "[warn] rootless-fix: INSTALL_DIR not set — skipping ownership fix" >&2
        return 0
    fi

    if ! ods_is_rootless_docker; then
        return 0    # not rootless — nothing to do
    fi

    ods_warn_rootless_docker "$install_dir"
    echo "[ods] Fixing data-directory ownership for Docker rootless mode..."

    local svc uid
    for svc in "${!ODS_ROOTLESS_UID_MAP[@]}"; do
        uid="${ODS_ROOTLESS_UID_MAP[$svc]}"
        local target_dir="${install_dir}/data/${svc}"
        if [[ -d "$target_dir" ]]; then
            echo "[ods]   chown -R ${uid}:${uid} data/${svc}"
            _ods_rootless_chown_dir "$target_dir" "$uid"
        fi
    done

    # Special case: openclaw workspace under config/ also needs UID 1000
    local openclaw_ws="${install_dir}/config/openclaw/workspace"
    if [[ -d "$openclaw_ws" ]]; then
        echo "[ods]   chown -R 1000:1000 config/openclaw/workspace"
        _ods_rootless_chown_dir "$openclaw_ws" "1000"
    fi

    echo "[ods] Rootless ownership fix complete."
}
