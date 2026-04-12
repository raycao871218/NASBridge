# Env Mapping

## Host Routing

- `NAS_SSH_HOST`: NAS ssh host, fallback to `NAS_IP`
- `NAS_SSH_PORT`: NAS ssh port, default `22`
- `NAS_SSH_USER`: NAS ssh user
- `NAS_PROJECT_PATH`: repository path on NAS for `src/nas_cert_update.sh`
- `SERVER_SSH_HOST`: server ssh host, fallback to `SERVER_IP`
- `SERVER_SSH_PORT`: server ssh port, default `22`
- `SERVER_SSH_USER`: server ssh user, fallback to `SSH_USER`

## Cert Source and Destination

- `DOMAIN_NAME`: full domain used for prefix extraction
- `NAS_CERT_DIR`: cert base dir on NAS
- `NAS_CERT_SITE_NAME`: cert site dir name on NAS
- `SERVER_CERT_DIR`: cert base dir on server
- `LOCAL_CERT_STAGING_DIR`: local temp staging on OpenClaw
- `CERT_ISSUE_COOLDOWN_DAYS`: skip re-issuing cert if last successful issue is within this many days (default 15)
- `NAS_CERT_ISSUE_MARKER`: marker file on NAS storing last successful issue unix timestamp

## NAS Cert Issue

- `CERT_DNS_TYPE`: acme dns provider (`dns_ali`, `dns_cf`, etc.)
- `CERT_DNS_SLEEP`: dns propagation sleep seconds
- `CERT_SERVER`: acme server
- `CERT_DOCKER_CONTAINER`: acme.sh container name on NAS
