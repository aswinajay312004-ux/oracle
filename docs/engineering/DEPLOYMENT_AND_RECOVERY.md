# Deployment and recovery

Target: one Windows laptop (or server) per company, Oracle in Docker (`oracle26ai`), Python 3.13, optional Ollama.
Step-by-step guide for people: `docs/WINDOWS_SETUP.md` and `docs/USER_MANUAL.txt`. Operator runbook: `docs/RUNBOOK.md`.

## 1. Install and start

| Step | Command | Status |
|---|---|---|
| Install (online) | `.\setup.ps1 -Dev` | Locally verified (owner, 2026-10-18) |
| Install (offline) | `python scripts/build_release.py` → copy → `.\setup.ps1 -Offline` | Implemented, Tested (cloud); Environment-blocked on Windows |
| Oracle accounts | `scripts/bootstrap_local.py`, `scripts/create_readonly_user.py`, `scripts/grant_bom_tables.py` | Locally verified (setup) |
| Connection check | `scripts/check_oracle.py` | Locally verified |
| Schema discovery | `scripts/discover_schema.py` | Locally verified |
| Start | `.\start.ps1` → http://127.0.0.1:8008 | Locally verified (earlier) |
| Start in Docker | `.\docker-start.ps1` (starts `oracle26ai`, builds/starts `poppys-ai`, data in volume `poppys-data`; docs/DOCKER.md) | Tested (cloud, real Oracle replica, PowerShell 7); not yet on the laptop |
| Full local check | `.\verify_local.ps1` (Oracle check, all tests with real Oracle, Ask AI benchmark) | Environment-blocked (owner run pending) |
| Windows service | `deploy/windows/install-service.ps1` (WinSW) | Implemented; Environment-blocked |

## 2. Health

| Probe | Answer |
|---|---|
| `GET /healthz` | `{"status":"ok"}` while the process runs |
| `GET /readyz` | 200 `pass`/`warn` (Oracle or local AI down = warn), 503 `fail` when the local store is unusable; used by install-service, upgrade, rollback and the restore drill |
| Sidebar lights | Oracle connected / offline, local AI ready / off |

## 3. Logs

`data/logs/poppys.log`: JSON lines (time, level, request ID, method, path, status, ms), rotated 5 × 5 MB. Errors carry
the reference shown to the person. `POPPYS_LOG_LEVEL`, `POPPYS_LOG_QUIET`. No secrets or business data.

## 4. Backup, restore, upgrade, rollback

| Operation | How | Status |
|---|---|---|
| Backup | `python scripts/backup.py create` (SQLite online backup API, config, optional uploads, manifest with hashes) | Implemented, Tested (cloud) |
| Scheduled backup | `deploy/windows/backup-task.ps1` | Environment-blocked |
| Restore + drill | `python scripts/backup.py restore …` / restore drill starts a scratch app and waits for `/readyz` | Implemented, Tested (cloud) |
| Upgrade / rollback | `deploy/windows/upgrade.ps1`, `rollback.ps1` | Implemented; Environment-blocked |
| Oracle data | belongs to the ERP's own backup; POPPYS writes only approved orders and its PP_ tables | — |

## 5. Recovery situations

| Situation | What to do |
|---|---|
| "Save outcome unknown" | Start Oracle; open the draft; **Check again**. Never re-enter the order by hand. |
| App does not start | `data/logs/poppys.log`; `GET /readyz`; disk space; `.env.poppys` present |
| Local store damaged | stop the app; restore the last backup; drafts saved to Oracle are still in Oracle (receipts) |
| Wrong order saved | POPPYS never updates or deletes Oracle orders; correct it in Axpert; the audit log and receipt show what was saved |
| Lost administrator password | `python scripts/reset_login.py` on the computer (removes accounts after typed DELETE), then create a new administrator |

## 6. Not yet verified

Windows service lifecycle, offline install on Windows, scheduled backup, upgrade and rollback on Windows, disk-usage
cleanup (no retention policy yet).
