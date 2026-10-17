# POPPYS AI — Implementation status

**Read this first in every new session.** It is the hand-off record between sessions: what works, what is
broken, what is next. Update it and push after every work batch.

- Working branch: `claude/fervent-babbage-jqo8r4` (continues the earlier `claude/loving-mccarthy-9p0do9` work, fast-forwarded).
- Local secrets: the Oracle password lives only in the ignored `.env.poppys` on the Windows laptop. Never commit it.
- Cloud sessions cannot reach the laptop's Oracle. "Cloud-tested" means synthetic or replica tests here;
  "Local Oracle-tested" needs the commands in [Local verification](#local-verification) run on the laptop.

Status words: **Implemented** (code exists) · **Cloud-tested** (automated tests pass here) ·
**Local Oracle-tested** (passed against the real local database) · **Fully verified** · **Blocked** · **Broken**.

## Current test run

| Date | Result | Notes |
|---|---|---|
| 2026-10-17 (session 2 start) | 343 passed, 33 failed, 18 skipped | 26 of the 33 were a cloud setup gap (`python-calamine` not installed); after a full `pip install -r requirements-dev.txt`, 7 real failures remained |
| 2026-10-17 (session 2, batch 2) | **381 passed, 0 failed, 18 skipped**; ruff clean | Ask AI, explorer, value index and dictionary harvest moved to the separate read-only account |
| 2026-10-17 (session 2, batch 1) | **377 passed, 0 failed, 18 skipped**; ruff clean | Root cause of 6: formulas saved without a result (script/export files) read as empty, so pack/piece totals and workbook arithmetic were missing. Added `backend/formulas.py` (safe subset: + − × ÷ ^, refs, SUM/MIN/MAX/AVERAGE/COUNT/ROUND*; nothing executed; cells marked `computed`, sheet note added). 1 stale test updated for the intentional `words` rule in `closed_lists()`. The 18 skips are opt-in local-Oracle tests and client workbooks that exist only on the laptop |

## Feature status

| # | Feature | Status | Evidence / gap |
|---|---|---|---|
| A1 | Excel reading (.xlsx/.xlsm; .xls/.xlsb/.ods via isolated readers), provenance, formulas, hidden, merged | Implemented, Cloud-tested | `backend/readers.py`, `backend/workbooks.py`, `backend/formulas.py` |
| A2 | Layout detection: merch BOM, buyer contract, flat table, saved templates, matcher | Implemented, Cloud-tested (partly) | `backend/matcher.py`, `backend/fields.py` |
| A3 | Raw → business draft → ERP plan → approved snapshot (hash-bound) | Implemented, Cloud-tested | `backend/erp/planner.py`, `tests/test_planner.py` |
| A4 | Mapping registry (verified/proposed columns) | Implemented, Cloud-tested | `config/oracle_mapping.json`, `backend/erp/mapping.py` |
| A5 | Deterministic validation (quantities, packs, masters, duplicates, lengths) | Implemented, Cloud-tested | planner |
| A6 | Approval + roles (admin/approver/operator/viewer), four-eyes option | Implemented, Cloud-tested | `backend/main.py` |
| A7 | Oracle writer: lock, re-plan, sequence keys, binds, read-back, receipt, rollback | Implemented, replica-tested | `backend/oracle_store.py`, `tests/oracle_replica.py`; needs Local Oracle test |
| A8 | Field-level error UI | Implemented | `frontend/js/workspace.js` |
| B1 | Knowledge catalog + relationship statuses | Implemented, Cloud-tested | `backend/insight/knowledge.py`, `config/semantic_catalog.json` |
| B2 | Question → intent → compiled read-only SELECT (no model SQL) | Implemented, Cloud-tested | `backend/insight/understand.py`, `query.py`, `engine.py` |
| B3 | Local AI (Ollama) fallback for understanding and mapping | Implemented | `backend/ai.py`; needs local Ollama check |
| B4 | Read-only executor (READ ONLY txn, timeout, row cap) on a **separate read-only account** `POPPYS_RO` | Implemented, Cloud-tested | `oracle_store.connect_readonly()` refuses an account with any write privilege; `scripts/create_readonly_user.py` (run by `setup.ps1`); `/api/health` reports it; `tests/test_readonly.py`. Needs Local Oracle test |
| C1 | Explorer: search, rows, keyset paging, linked records | Implemented, Cloud-tested | `/api/explorer/*`, `frontend/js/explorer.js` |
| C2 | Relationship graph / flowchart (no CDN, SVG) | Implemented | `frontend/js/graph.js`, `flow.js` |
| D1 | Windows service, backup/restore, upgrade/rollback | Implemented, Cloud-tested | `deploy/windows/`, `scripts/backup.py` |
| D2 | Offline packaging (wheelhouse, no CDN) | Needs check | — |

## Next steps (in order)

1. ~~Fix the failing tests~~ (done, batch 1).
2. Remove leftover PDF wording in docs (PDF is out of scope).
3. Re-audit gaps against the master prompt; implement missing pieces.
4. Produce exact local Oracle verification commands and record results here.

## Local verification

The cloud session **cannot** connect to the laptop's Oracle (`127.0.0.1:1521` is the laptop's own address).
Run these on the Windows laptop in PowerShell, in the project folder, and paste the output into the next
session so this file can be updated.

```powershell
git fetch origin; git checkout claude/fervent-babbage-jqo8r4; git pull
# Docker Desktop running, container oracle26ai healthy
.\setup.ps1 -Dev                      # venv, packages, POPPYS_APP (if new), grants, POPPYS_RO, connection check
python scripts\check_oracle.py         # expect: Connected ... / Read-only account for Ask AI: POPPYS_RO, no write privileges
python scripts\discover_schema.py      # asks the GARMENT_DEMO password (typed, never saved); writes data\schema_discovery.*
$env:POPPYS_TEST_ORACLE='1'; python -m pytest -q    # runs the 18 local Oracle tests too (TEST-labelled rows only)
.\start.ps1                            # then open http://127.0.0.1:8008
```

Demo A (Excel → Oracle) and Demo B (Ask AI) are then run in the browser; record what happened below.

| Date | Check | Result |
|---|---|---|
| — | `check_oracle.py` | not run yet |
| — | `POPPYS_TEST_ORACLE=1 pytest` | not run yet |
| — | Demo A / Demo B | not run yet |
