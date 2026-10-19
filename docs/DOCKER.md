# POPPYS AI in Docker

One command starts everything: the Oracle container (`oracle26ai`, with the GARMENT_DEMO data) and POPPYS AI in its
own container. The browser opens at http://127.0.0.1:8008.

```powershell
cd D:\oracle
.\docker-start.ps1            # start (first time: builds the image, needs internet once)
.\docker-stop.ps1             # stop POPPYS AI (Oracle keeps running)
.\docker-stop.ps1 -Oracle     # stop both
```

Or **double-click `docker-start.cmd`** in `D:\oracle` (it runs the same script; also works from Command Prompt:
`cd /d D:\oracle` then `docker-start.cmd`). `docker-stop.cmd` stops it.

After `git pull`: `.\docker-start.ps1 -Rebuild` (or `docker-start.cmd -Rebuild`).

## What runs where

| Part | Where | Notes |
|---|---|---|
| Oracle | its own container `oracle26ai` (unchanged) | Holds the ERP data. The scripts only **start** it; nothing here creates, replaces or removes it |
| POPPYS AI | container `poppys-ai` (image `poppys-ai:local`, `Dockerfile`) | Python 3.13, non-root user, health check on `/healthz`, restarts with Docker Desktop (`restart: unless-stopped`) |
| Local data (users, drafts, uploaded originals, logs) | Docker volume `poppys-data` | Kept across restarts, rebuilds and upgrades. A volume (not the `D:\oracle\data` folder) because SQLite's write-ahead log needs a real Linux file system |
| Accounts and passwords | `.env.poppys` on the laptop, mounted read-only | Never copied into the image (`.dockerignore`) |
| Company profile, write mapping | `config\` on the laptop, mounted read-only | Edit, then `docker compose restart app` |
| Ollama (optional) | on the laptop as before | Reached at `host.docker.internal:11434` |

The container reaches Oracle at `host.docker.internal:1521/FREEPDB1` (the port `oracle26ai` publishes on the laptop).
Another address: set `POPPYS_ORACLE_DSN` before `docker compose up`. The app listens on 127.0.0.1:8008 of the laptop
only, as with `start.ps1`; times and the OC number's financial year use `TZ=Asia/Kolkata`.

## Moving from start.ps1

The first `docker-start.ps1` copies `D:\oracle\data` (users, drafts, uploads) into the `poppys-data` volume once; the
folder itself is not changed. Stop `start.ps1` first (Ctrl+C) — both use port 8008. Do not run both.

## Day to day

| Task | Command |
|---|---|
| Status | `docker ps` (`poppys-ai` should say `healthy`) |
| Logs | `docker compose logs -f app` |
| Readiness | http://127.0.0.1:8008/readyz |
| Backup | `docker compose exec app python scripts/backup.py` then `docker cp poppys-ai:/app/data/backups .\backups` |
| Start automatically | `docker update --restart unless-stopped oracle26ai` once; POPPYS AI already restarts with Docker Desktop |

Scripts that use the laptop's `.venv` (`verify_local.ps1`, `scripts\axpert_gap_report.py`) keep working beside Docker.

## Verified

Built and run in the development environment against a real Oracle 23.26 replica: `/readyz` local store and Oracle
pass; two orders saved through the container got consecutive OC numbers and read back as verified; data survived a
restart; the one-time data copy and `docker-start.ps1` / `docker-stop.ps1` (PowerShell 7) ran end to end, including
starting a stopped Oracle container. **Not yet run on the owner's Windows laptop with Docker Desktop.**
