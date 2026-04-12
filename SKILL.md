---
name: openclaw-nasbridge
description: Operate NASBridge as a cross-host operations skill for NAS and server environments. Use when tasks involve remote certificate update and sync across NAS, OpenClaw, and server, nginx or caddy proxy generation, reachability checks, SSL expiry checks, or hosts and fallback routing automation through this repository scripts.
---

# OpenClaw NASBridge Skill

Run this repository as a skill-first operations workspace, where OpenClaw acts as the control plane and NAS/server are execution targets.

## Use This Skill

Use this skill to do any of the following:

- Run remote certificate lifecycle orchestration from OpenClaw
- Check NAS/server reachability and trigger notification workflows
- Check SSL expiry for configured domains
- Generate proxy configs from `domains_config.yaml`
- Update dynamic hosts or router rules with existing scripts

## Primary Workflow

Follow this order for certificate rollout:

1. Validate `.env` contains SSH and cert paths required by the orchestrator.
2. Run `scripts/run_cert_flow.sh --dry-run` first.
3. Run `scripts/run_cert_flow.sh` for real execution.
4. Verify server nginx loaded new cert and service is healthy.

`scripts/run_cert_flow.sh` wraps `src/openclaw_cert_sync.sh` and executes:

1. SSH to NAS and run certificate update script.
2. Pull cert files from NAS to local OpenClaw staging directory.
3. Push cert files from OpenClaw staging directory to server target directory.
4. SSH to server and reload nginx.

## Command Entry Points

- `scripts/run_cert_flow.sh [--dry-run] [--skip-nas-update]`
- `python3 src/check_ssl_expiry.py`
- `python3 src/server_load_proxy_from_config.py`
- `python3 src/nas_ping_test.py`
- `python3 src/server_ping_test.py`
- `python3 src/hosts_update.py`

## References

- Read `references/env-mapping.md` before changing `.env`.
- Read `references/target-boundary.md` before running remote operations.
- Read `references/ops-playbook.md` for standard runbook and failure handling.
