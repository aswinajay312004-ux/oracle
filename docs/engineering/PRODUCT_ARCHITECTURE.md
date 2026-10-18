# Product architecture

POPPYS AI turns a buyer's Excel order into an Axpert-style order in a local Oracle database, after a person approves it.
Everything runs on one computer: a Python server (FastAPI), a browser front end with no external resources, a SQLite
store, the local Oracle database (Docker on the laptop) and, optionally, a local language model (Ollama).

## 1. Components

| Component | Code | Job |
|---|---|---|
| Web server | `backend/main.py` (≈67 routes), `start.ps1` | Sign-in, roles, uploads, drafts, previews, approval, explorer, Ask AI, setup |
| Readers | `backend/readers.py`, `backend/isolation.py`, `backend/formulas.py`, `backend/numfmt.py` | Every accepted spreadsheet format → one cell model, parsed in a time- and memory-limited worker |
| Understanding | `backend/workbooks.py`, `backend/matcher.py`, `backend/layouts.py`, `backend/fields.py`, `backend/ai.py` | Cell model → orders: saved layouts (exact or re-anchored by labels), built-in detectors, matcher proposals, optional local AI proposals (always checked by the matcher) |
| Coverage | `backend/coverage.py` | What of the workbook is in an order and what is not |
| Planner | `backend/erp/planner.py`, `masters.py`, `mapping.py`, `catalog.py` | Orders → an exact, hashable list of row operations against the verified write mapping; validation; what will not be saved |
| Writer | `backend/oracle_store.py` | One Oracle transaction under a database lock, re-plan + hash check, read-back inside the transaction, receipt in `PP_IMPORT_LOG` |
| Verification | `backend/erp/readback.py` | Fresh read on the read-only account after the commit, control totals |
| Local store | `backend/local_store.py` (SQLite, WAL) | Users, sessions, drafts and revisions, templates, settings, audit, import ledger, Ask AI history and examples |
| Ask AI | `backend/insight/*`, `backend/insight_api.py` | Questions → catalog answers or compiled read-only SELECTs, independent SQL review, checks, confidence gate |
| Operations | `backend/logs.py`, `/healthz`, `/readyz`, `scripts/backup.py`, `deploy/windows/*` | Logs, health, backup/restore, Windows service, upgrade/rollback |
| Front end | `frontend/index.html`, `frontend/js/*.js`, `frontend/styles.css` | Overview, New order (upload → review → check & approve → saved), Saved orders, Ask AI, Database, Setup |

## 2. Data layers (spec §2.1)

| Layer | Where | Rule | Status |
|---|---|---|---|
| A — original file | `data/<draft id>.upload`, written once, read-only, before parsing; SHA-256 in the draft and the audit | Never changed; served only while its hash matches | Implemented, Tested |
| B — extracted source | `draft.sheets` (cell model: sheet, row, column, address, value, formula, hidden, error, merged ranges) | Never edited by review; every order value keeps `{sheet, cell, value, formula}` provenance | Implemented, Tested |
| C — business records | `draft.orders` (see CANONICAL_BUSINESS_MODEL) | Edited by people; the provenance of a value stays server-owned | Implemented, Tested |
| ERP write and read-back | `PP_IMPORT_LOG` receipt in Oracle (rows written, masters reused, what was not saved), `draft.result`, `draft.verification`, `import_ledger` | Kept apart from A–C | Implemented, Tested (stand-in and replica) |

## 3. Request flow of one order

```
Upload ─► sniff + size check ─► archive original (A) ─► parse in worker ─► cell model (B)
       ─► saved layout (exact, else re-anchored by labels) │ merch BOM │ contract report │ flat table │ matcher proposal
       ─► orders (C) + coverage manifest ─► review (person) ─► preview: planner against live Oracle masters
       ─► "read before approving" list (conversions, new masters, not saved, not read, layout changes)
       ─► approve (approver, four-eyes, list read) ─► intent recorded ─► Oracle transaction (lock, re-plan, hash,
          inserts, in-transaction read-back, receipt) ─► outcome decided by Oracle's own log
       ─► fresh read-back on the read-only account + control totals ─► Verified / differences found
```

## 4. Process model and limits

- One server process (uvicorn). Parsing runs in a spawned worker with a time and memory limit (`backend/isolation.py`).
- Drafts are locked one at a time (`main.draft_lock`); Oracle writes are serialised by the database row lock
  `PP_WRITE_LOCK` inside the transaction. Users, sessions and settings share one lock.
- Each Oracle statement of a save has a call timeout (`WRITE_TIMEOUT_S`, default 60 s); each read-only query has its own
  (`QUERY_TIMEOUT_S`, default 20 s). A connection the driver drops after a time limit is never touched again (see
  ORACLE_AXPERT_INTEGRATION §6).
- No connection pool: each request opens its own Oracle connection. **Known risk** for many simultaneous users.
- `backend/main.py` is still one large module (router split not done). **Known risk** for maintainability.

## 5. What runs where

| Piece | Location | Network |
|---|---|---|
| App | the laptop, `127.0.0.1:8008` (TrustedHost: 127.0.0.1, localhost) | loopback only |
| Oracle | Docker container `oracle26ai`, `127.0.0.1:1521/FREEPDB1` | loopback |
| Local AI | Ollama `127.0.0.1:11434` (refused unless loopback / private network) | loopback / LAN |
| Front end | served by the app; no CDN, no fonts or scripts from elsewhere (CSP `default-src 'self'`) | none |

## 6. Status

| Item | Status |
|---|---|
| Layers A, B, C and the ERP record kept apart | Implemented, Tested |
| End-to-end flow on the real Oracle replica (save, read-back, explorer) | Tested (replica, 2026-10-18) |
| End-to-end flow on the laptop's Oracle | Environment-blocked (owner's `verify_local.ps1` run pending) |
| Router split, connection pool, job queue | Not implemented — Known risk |
