# Target Boundary

Use strict target ownership when executing scripts:

- `nas_*` scripts: run on NAS only
- `server_*` scripts: run on server only
- `openclaw_*` scripts: run on OpenClaw control host

For cert flow:

1. OpenClaw ssh into NAS and run `src/nas_cert_update.sh`
2. OpenClaw pulls cert files from NAS via rsync
3. OpenClaw pushes cert files to server via rsync
4. OpenClaw ssh into server and runs `sudo nginx -s reload`

Never run NAS-only scripts directly on server, and do not write server cert paths from NAS directly when OpenClaw is the orchestration plane.
