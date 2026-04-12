#!/usr/bin/env bash

set -euo pipefail

# This script runs on NAS.
# 1) Issue/renew cert via acme.sh docker container
# 2) Copy cert files into DSM default certificate directory

if [[ -f ".env" ]]; then
    set -a
    # shellcheck disable=SC1091
    source ".env"
    set +a
else
    echo "ERROR: .env not found in current directory"
    exit 1
fi

GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'

required_vars=(
    DOMAIN_NAME
    CERT_DNS_TYPE
    CERT_DNS_SLEEP
    CERT_SERVER
    CERT_DOCKER_CONTAINER
)

for v in "${required_vars[@]}"; do
    if [[ -z "${!v:-}" ]]; then
        echo -e "${RED}ERROR: missing env var ${v}${NC}"
        exit 1
    fi
done

if ! docker ps -a --format '{{.Names}}' | grep -qx "${CERT_DOCKER_CONTAINER}"; then
    echo -e "${RED}ERROR: docker container not found: ${CERT_DOCKER_CONTAINER}${NC}"
    exit 1
fi

ACME_HOME="${ACME_HOME:-/acme.sh}"
DSM_CERT_DIR="${DSM_CERT_DIR:-/usr/syno/etc/certificate/system/default}"
NAS_CERT_ISSUE_MARKER="${NAS_CERT_ISSUE_MARKER:-./log/last_cert_issue_at}"

echo "DOMAIN_NAME: ${DOMAIN_NAME}"
echo "CERT_DNS_TYPE: ${CERT_DNS_TYPE}"
echo "CERT_DNS_SLEEP: ${CERT_DNS_SLEEP}"
echo "CERT_SERVER: ${CERT_SERVER}"
echo "ACME_HOME: ${ACME_HOME}"
echo "DSM_CERT_DIR: ${DSM_CERT_DIR}"

issue_cmd=(
    acme.sh --force --log --issue
    --server "${CERT_SERVER}"
    --dns "${CERT_DNS_TYPE}"
    --dnssleep "${CERT_DNS_SLEEP}"
    -d "${DOMAIN_NAME}"
    -d "*.${DOMAIN_NAME}"
)

echo "INFO: issuing certificate via acme.sh ..."
docker exec "${CERT_DOCKER_CONTAINER}" "${issue_cmd[@]}"

domain_dir_ecc="${ACME_HOME}/${DOMAIN_NAME}_ecc"
domain_dir_rsa="${ACME_HOME}/${DOMAIN_NAME}"
if docker exec "${CERT_DOCKER_CONTAINER}" test -d "${domain_dir_ecc}"; then
    ACME_DOMAIN_DIR="${domain_dir_ecc}"
elif docker exec "${CERT_DOCKER_CONTAINER}" test -d "${domain_dir_rsa}"; then
    ACME_DOMAIN_DIR="${domain_dir_rsa}"
else
    echo -e "${RED}ERROR: cert directory not found in container:${NC} ${domain_dir_ecc} or ${domain_dir_rsa}"
    echo "INFO: check acme log: ${ACME_HOME}/acme.sh.log"
    exit 1
fi

echo "INFO: detected cert dir: ${ACME_DOMAIN_DIR}"
docker exec "${CERT_DOCKER_CONTAINER}" chmod -R 755 "${ACME_DOMAIN_DIR}"
docker exec "${CERT_DOCKER_CONTAINER}" find "${ACME_DOMAIN_DIR}" -type f -exec chmod 644 {} \;

tmp_dir="$(mktemp -d)"
trap 'rm -rf "$tmp_dir"' EXIT

echo "INFO: exporting cert files from container ..."
docker cp "${CERT_DOCKER_CONTAINER}:${ACME_DOMAIN_DIR}/." "${tmp_dir}/"

FULLCHAIN_FILE="${tmp_dir}/fullchain.cer"
CERT_FILE="${tmp_dir}/${DOMAIN_NAME}.cer"
KEY_FILE="${tmp_dir}/${DOMAIN_NAME}.key"
CHAIN_FILE="${tmp_dir}/ca.cer"

if [[ ! -f "${FULLCHAIN_FILE}" ]]; then
    echo -e "${RED}ERROR: missing fullchain.cer in ${ACME_DOMAIN_DIR}${NC}"
    exit 1
fi

if [[ ! -f "${KEY_FILE}" ]]; then
    KEY_FILE="$(find "${tmp_dir}" -maxdepth 1 -name '*.key' | head -n 1 || true)"
fi
if [[ -z "${KEY_FILE}" || ! -f "${KEY_FILE}" ]]; then
    echo -e "${RED}ERROR: private key not found in ${ACME_DOMAIN_DIR}${NC}"
    exit 1
fi

mkdir -p "${DSM_CERT_DIR}"

backup_dir="${DSM_CERT_DIR}/backup_$(date +%Y%m%d%H%M%S)"
mkdir -p "${backup_dir}"
for f in cert.pem chain.pem fullchain.pem privkey.pem; do
    if [[ -f "${DSM_CERT_DIR}/${f}" ]]; then
        cp -a "${DSM_CERT_DIR}/${f}" "${backup_dir}/${f}"
    fi
done

echo "INFO: installing cert into DSM default path ..."
if [[ -f "${CERT_FILE}" ]]; then
    install -m 644 "${CERT_FILE}" "${DSM_CERT_DIR}/cert.pem"
else
    install -m 644 "${FULLCHAIN_FILE}" "${DSM_CERT_DIR}/cert.pem"
fi

if [[ -f "${CHAIN_FILE}" ]]; then
    install -m 644 "${CHAIN_FILE}" "${DSM_CERT_DIR}/chain.pem"
else
    install -m 644 "${FULLCHAIN_FILE}" "${DSM_CERT_DIR}/chain.pem"
fi

install -m 644 "${FULLCHAIN_FILE}" "${DSM_CERT_DIR}/fullchain.pem"
install -m 600 "${KEY_FILE}" "${DSM_CERT_DIR}/privkey.pem"

if command -v synow3tool >/dev/null 2>&1; then
    synow3tool --gen-all || true
elif [[ -x /usr/syno/bin/synow3tool ]]; then
    /usr/syno/bin/synow3tool --gen-all || true
fi

mkdir -p "$(dirname "${NAS_CERT_ISSUE_MARKER}")"
date +%s > "${NAS_CERT_ISSUE_MARKER}"

echo -e "${GREEN}OK: DSM certificate updated at ${DSM_CERT_DIR}${NC}"
echo -e "${GREEN}OK: marker updated: ${NAS_CERT_ISSUE_MARKER}${NC}"
