#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

ENV_PATH="${PROJECT_ROOT}/.env"
DRY_RUN=false
SKIP_NAS_UPDATE=false

usage() {
    cat <<'EOF'
Usage: openclaw_cert_sync.sh [options]

Options:
  --env <path>          Use specified env file (default: ../.env)
  --dry-run             Print commands only, do not execute
  --skip-nas-update     Skip remote certificate issue/deploy on NAS
  -h, --help            Show this help message

Workflow:
  1) SSH to NAS and run cert update script
  2) Copy cert files from NAS to local OpenClaw machine
  3) Sync local cert files to server
  4) SSH to server and reload nginx
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --env)
            ENV_PATH="$2"
            shift 2
            ;;
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        --skip-nas-update)
            SKIP_NAS_UPDATE=true
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Unknown option: $1" >&2
            usage
            exit 1
            ;;
    esac
done

if [[ ! -f "$ENV_PATH" ]]; then
    echo "ERROR: env file not found: $ENV_PATH" >&2
    exit 1
fi

set -a
# shellcheck disable=SC1090
source "$ENV_PATH"
set +a

require_var() {
    local name="$1"
    if [[ -z "${!name:-}" ]]; then
        echo "ERROR: missing required env var: $name" >&2
        exit 1
    fi
}

run_cmd() {
    if $DRY_RUN; then
        printf '[DRY-RUN] '
        printf '%q ' "$@"
        echo
    else
        "$@"
    fi
}

require_var DOMAIN_NAME
require_var NAS_CERT_DIR
require_var NAS_CERT_SITE_NAME
require_var SERVER_CERT_DIR

NAS_SSH_HOST="${NAS_SSH_HOST:-${NAS_IP:-}}"
SERVER_SSH_HOST="${SERVER_SSH_HOST:-${SERVER_IP:-}}"
SERVER_SSH_USER="${SERVER_SSH_USER:-${SSH_USER:-}}"
NAS_SSH_PORT="${NAS_SSH_PORT:-22}"
SERVER_SSH_PORT="${SERVER_SSH_PORT:-22}"
LOCAL_CERT_STAGING_DIR="${LOCAL_CERT_STAGING_DIR:-$PROJECT_ROOT/tmp/certs}"
CERT_ISSUE_COOLDOWN_DAYS="${CERT_ISSUE_COOLDOWN_DAYS:-15}"

require_var NAS_SSH_HOST
require_var NAS_SSH_USER
require_var SERVER_SSH_HOST
require_var SERVER_SSH_USER

if ! $SKIP_NAS_UPDATE; then
    require_var NAS_PROJECT_PATH
fi

DOMAIN_PREFIX="${DOMAIN_NAME%%.*}"
NAS_CERT_END_DIR="${NAS_CERT_DIR%/}/${NAS_CERT_SITE_NAME}"
LOCAL_CERT_DIR="${LOCAL_CERT_STAGING_DIR%/}/${DOMAIN_PREFIX}"
SERVER_CERT_END_DIR="${SERVER_CERT_DIR%/}/${DOMAIN_PREFIX}"

echo "INFO: domain prefix: $DOMAIN_PREFIX"
echo "INFO: local staging: $LOCAL_CERT_DIR"

if ! $SKIP_NAS_UPDATE; then
    if ! [[ "$CERT_ISSUE_COOLDOWN_DAYS" =~ ^[0-9]+$ ]]; then
        echo "ERROR: CERT_ISSUE_COOLDOWN_DAYS must be an integer" >&2
        exit 1
    fi

    if [[ -z "${NAS_CERT_ISSUE_MARKER:-}" ]]; then
        NAS_CERT_ISSUE_MARKER="${NAS_PROJECT_PATH%/}/log/last_cert_issue_at"
    fi

    printf -v NAS_PROJECT_PATH_Q '%q' "$NAS_PROJECT_PATH"
    printf -v NAS_CERT_ISSUE_MARKER_Q '%q' "$NAS_CERT_ISSUE_MARKER"
    NAS_UPDATE_CMD="cd ${NAS_PROJECT_PATH_Q} && bash src/nas_cert_update.sh"
    NAS_COOLDOWN_CHECK_CMD="bash -lc 'marker=${NAS_CERT_ISSUE_MARKER_Q}; cooldown_days=${CERT_ISSUE_COOLDOWN_DAYS}; now=\$(date +%s); if [[ -f \"\$marker\" ]]; then last=\$(cat \"\$marker\" 2>/dev/null || true); if [[ \"\$last\" =~ ^[0-9]+$ ]]; then min_gap=\$((cooldown_days*86400)); elapsed=\$((now-last)); if (( elapsed < min_gap )); then echo SKIP; exit 0; fi; fi; fi; echo RUN'"

    if $DRY_RUN; then
        echo "1/4 Dry-run: would check NAS issue cooldown (${CERT_ISSUE_COOLDOWN_DAYS} days) and then decide to run update or skip"
        run_cmd ssh -p "$NAS_SSH_PORT" "${NAS_SSH_USER}@${NAS_SSH_HOST}" "$NAS_COOLDOWN_CHECK_CMD"
        run_cmd ssh -p "$NAS_SSH_PORT" "${NAS_SSH_USER}@${NAS_SSH_HOST}" "$NAS_UPDATE_CMD"
    else
        echo "1/4 Check NAS issue cooldown (${CERT_ISSUE_COOLDOWN_DAYS} days)"
        NAS_UPDATE_DECISION="$(ssh -p "$NAS_SSH_PORT" "${NAS_SSH_USER}@${NAS_SSH_HOST}" "$NAS_COOLDOWN_CHECK_CMD")"
        if [[ "$NAS_UPDATE_DECISION" == "SKIP" ]]; then
            echo "1/4 Skip NAS cert update: already issued within ${CERT_ISSUE_COOLDOWN_DAYS} days"
        else
            echo "1/4 Run cert update on NAS"
            run_cmd ssh -p "$NAS_SSH_PORT" "${NAS_SSH_USER}@${NAS_SSH_HOST}" "$NAS_UPDATE_CMD"
        fi
    fi
else
    echo "1/4 Skip NAS cert update (--skip-nas-update)"
fi

echo "2/4 Pull certs from NAS to OpenClaw"
run_cmd mkdir -p "$LOCAL_CERT_DIR"
run_cmd rsync -avz --delete -e "ssh -p ${NAS_SSH_PORT}" \
    "${NAS_SSH_USER}@${NAS_SSH_HOST}:${NAS_CERT_END_DIR}/" \
    "${LOCAL_CERT_DIR}/"

echo "3/4 Push certs from OpenClaw to server"
run_cmd ssh -p "$SERVER_SSH_PORT" "${SERVER_SSH_USER}@${SERVER_SSH_HOST}" "mkdir -p '${SERVER_CERT_END_DIR}'"
run_cmd rsync -avz --delete -e "ssh -p ${SERVER_SSH_PORT}" \
    "${LOCAL_CERT_DIR}/" \
    "${SERVER_SSH_USER}@${SERVER_SSH_HOST}:${SERVER_CERT_END_DIR}/"

echo "4/4 Reload nginx on server"
run_cmd ssh -p "$SERVER_SSH_PORT" "${SERVER_SSH_USER}@${SERVER_SSH_HOST}" "sudo nginx -s reload"

echo "OK: certificate flow completed"
