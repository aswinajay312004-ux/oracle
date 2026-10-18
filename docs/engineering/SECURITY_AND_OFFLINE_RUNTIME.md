# Security and offline runtime

## 1. Offline guarantees

| Guarantee | How | Status |
|---|---|---|
| No cloud AI, no external API at runtime | The only outbound calls are Oracle (DSN) and Ollama; Ollama refused unless loopback / private network / `AI_ALLOWED_HOST` (`ai.local_only`); nothing is sent otherwise | Implemented, Tested |
| No CDN, fonts or scripts from elsewhere | CSP `default-src 'self'`; `scripts/check.sh js` refuses external URLs, inline scripts, `on*=` handlers, `eval` | Implemented, Tested (CI) |
| No telemetry | none in the code | Implemented |
| Offline installation | `scripts/build_release.py` (hashed wheelhouse), `setup.ps1 -Offline --require-hashes` | Implemented, Tested (cloud); Windows run: Environment-blocked |
| Works with the internet off | by construction (above) | Not verified on the laptop with the network disabled |

## 2. Sign-in and sessions (`backend/auth.py`)

scrypt (N=2^17, r=8, p=1) password hashes; 12+ character passwords not in the common list, not the username;
throttling per name and per address; idle timeout 30 min, absolute 8 h; sign-in ends the presented session; role,
password or status changes sign the person out; logout clears site data; HttpOnly, SameSite=Strict cookies.
**Implemented, Tested.** `Secure` cookie flag waits for local TLS (plain HTTP on loopback) — Known risk: low.

## 3. Requests

| Control | Status |
|---|---|
| Host header limited to 127.0.0.1 / localhost | Implemented |
| CSRF: changes refused unless Sec-Fetch-Site is same-origin/none and Origin matches | Implemented, Tested |
| 14 MB request limit; 12 MB upload limit; type sniffed from the bytes | Implemented, Tested |
| Security headers (CSP, nosniff, frame DENY, no-store, same-origin referrer) | Implemented |
| API pages (/docs, /openapi.json) off unless `POPPYS_DEV=1` | Implemented, Tested |
| Request ID on every response; server errors give a reference, never a stack trace | Implemented, Tested |

## 4. Authorization

| Role | Can |
|---|---|
| viewer | view drafts, orders, Ask AI, database |
| operator | + upload, edit, map, preview |
| approver | + approve (save to Oracle), audit, remember Ask AI examples |
| admin | + users, write mapping, data dictionary, templates, settings |

Checked server-side on every route (`require(permission)`). Four-eyes on by default (TRANSACTION_AND_RECONCILIATION
§1). **Implemented, Tested.**

## 5. Oracle accounts

| Account | Rights | Used by |
|---|---|---|
| GARMENT_DEMO (owner) | full | setup scripts only; password typed or in `.env.poppys`, never committed |
| POPPYS_APP (writer) | SELECT on the 19 tables, INSERT on the mapped tables, its own PP_ tables | saving, previews |
| POPPYS_RO (read-only) | CREATE SESSION + SELECT; refused at connect time if it has any other privilege | Ask AI, Database, read-back verification |

Ask AI never runs model-written SQL: questions compile to SELECTs from the catalog, reviewed independently by SQLGlot
(one SELECT, allowlisted tables and functions, no DML/DDL/PL/SQL/db links), in read-only transactions with time limits
and row caps. **Implemented, Tested.**

## 6. Files and data

| Item | Status |
|---|---|
| Uploaded originals read-only, hash-checked when served | Implemented, Tested |
| Formula/CSV injection: Ask AI CSV export prefixes text starting with = + - @ tab or CR with an apostrophe | Implemented |
| Path traversal: upload names reduced to the base name; storage by generated ID | Implemented |
| Logs never contain passwords, cookies, query strings or bodies | Implemented, Tested |
| Retention of drafts, revisions, chat log, uploads | Not implemented — Known risk: disk growth |
| Secrets scan in CI (own rules + gitleaks over the whole history; six synthetic test passwords accepted by fingerprint) | Implemented, Tested |
| Backups of the local store and (optionally) uploads | Implemented (`scripts/backup.py`); Windows schedule: Environment-blocked |
