# Ops Playbook

## Certificate Rollout

1. Run dry-run:

```bash
scripts/run_cert_flow.sh --dry-run
```

2. Execute rollout:

```bash
scripts/run_cert_flow.sh
```

If the last successful NAS certificate issue is within `CERT_ISSUE_COOLDOWN_DAYS` (default 15), the flow skips NAS issue/deploy and keeps sync plus nginx reload.

3. Validate service:

```bash
python3 src/check_ssl_expiry.py
```

## Monitoring Tasks

- NAS network check: `python3 src/nas_ping_test.py`
- Server failover check: `python3 src/server_ping_test.py`
- Proxy config load: `python3 src/server_load_proxy_from_config.py`

## Failure Handling

- SSH failure: verify host/user/key/port and sudo policy.
- Rsync failure: verify source/target cert paths and permissions.
- Nginx reload failure: run `nginx -t` on server and fix broken config first.
- Cert issue failure on NAS: verify acme container and dns provider credentials.
