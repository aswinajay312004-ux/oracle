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
| 2026-10-17 (session 2, batch 9) | **426 passed, 0 failed, 18 skipped**; ruff clean | Local read-only MCP server; repository assessment written |
| 2026-10-17 (session 2, batch 8) | **420 passed, 0 failed, 18 skipped**; ruff clean | Ask AI accuracy benchmark (golden questions + hand-written SQL). It found and fixed: lines/sizes of cancelled orders were counted (241 instead of 240) |
| 2026-10-17 (session 2, batch 7) | **413 passed, 0 failed, 18 skipped**; ruff clean | Expert data team, part 1: independent SQL safety review (SQLGlot), join check, total cross-check, check trace in the UI. Found and fixed a wrong "All groups together" figure when one record sits in several groups |
| 2026-10-17 (session 2, batch 6) | 388 passed, 0 failed, 18 skipped | Offline release builder |
| 2026-10-17 (session 2, batch 5) | **388 passed, 0 failed, 18 skipped** | Undo/redo in the review screen |
| 2026-10-17 (session 2, batch 4) | **388 passed, 0 failed, 18 skipped**; ruff clean | Evidence ledger; docs no longer claim PDF support |
| 2026-10-17 (session 2, batch 3) | **382 passed, 0 failed, 18 skipped**; ruff clean | Overview + work queue, Ask AI charts, phone layout |
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
| A9 | Undo / redo of corrections before approval (buttons + Ctrl+Z / Ctrl+Y; typing burst = one step; issues restored with the values) | Implemented, browser-tested on demo server | `frontend/js/workspace.js` |
| B1 | Knowledge catalog + relationship statuses | Implemented, Cloud-tested | `backend/insight/knowledge.py`, `config/semantic_catalog.json` |
| B2 | Question → intent → compiled read-only SELECT (no model SQL) | Implemented, Cloud-tested | `backend/insight/understand.py`, `query.py`, `engine.py` |
| B3 | Local AI (Ollama) fallback for understanding and mapping | Implemented | `backend/ai.py`; needs local Ollama check |
| B5 | Ask AI charts (bar for categories, line for dates) from the exact returned rows; table kept as the accessible view | Implemented, Cloud-tested (rendered on demo server) | `frontend/js/chart.js` |
| B4 | Read-only executor (READ ONLY txn, timeout, row cap) on a **separate read-only account** `POPPYS_RO` | Implemented, Cloud-tested | `oracle_store.connect_readonly()` refuses an account with any write privilege; `scripts/create_readonly_user.py` (run by `setup.ps1`); `/api/health` reports it; `tests/test_readonly.py`. Needs Local Oracle test |
| B7 | Independent SQL safety review on **every** executor statement: parsed with SQLGlot, fail-closed; one SELECT only, no DML/DDL/PL/SQL, FOR UPDATE, CONNECT BY, db links, other schemas (DUAL, SYS, POPPYS_APP) or non-allowlisted functions (DBMS_*, UTL_*, user functions); answers also need catalog tables + row limit | Implemented, Cloud-tested (16 hostile statements refused) | `backend/insight/policy.py`, `tests/test_policy.py` |
| B8 | Answer verification: join check (Oracle PK/unique key, else bounded duplicate probe, cached 1 h; a repeating parent stops a total) and total cross-check (breakdown re-asked without groups as a separate query); confidence lowered on problems | Implemented, Cloud-tested | `backend/insight/verify.py`, `tests/test_verify.py`; harvest now reads PK/unique constraints and unique indexes |
| B9 | "Expert data team" trace on every answer: analyst → SQL engineer → security reviewer → data quality → Oracle → data quality, shown as a check strip + ordered list | Implemented, browser-tested on demo server | `backend/insight/engine.py` `run()`, `frontend/js/chat.js` |
| B11 | Accuracy benchmark: golden questions asked as a user would, compared with hand-written SQL on the same DB (rows / ordered / scalar / values); params pick real values so the shipped set fits any ERP copy; runs saved + audited; Overview shows the last score; Setup → Answer accuracy; `scripts/run_benchmark.py` | Implemented, Cloud-tested (demo 15/15, shipped 12/12). **Needs a run on the laptop** | `backend/insight/benchmark.py`, `config/golden_questions.json`, `tests/golden/demo.json`, `tests/test_benchmark.py` |
| B12 | Detail rows (order lines, sizes) of cancelled orders are excluded through their order's CANCEL flag (many-to-one links only); "cancelled" questions on lines filter the order's flag | Fixed (found by B11) | `query.cancel_owner()` |
| B13 | Local read-only MCP server (stdio, protocol 2025-11-25 + `server/discover`): search_catalog, describe_table, relationship_path, evidence, ask, run_intent, accuracy_report; `run_select` only with `--allow-sql` and still safety-reviewed + row-capped; every call audited; no write tool | Implemented, Cloud-tested (incl. stdio subprocess) | `backend/mcp_server.py`, `scripts/mcp_server.py`, `tests/test_mcp.py` |
| B10 | Oracle sessions named MODULE='POPPYS AI', ACTION='read-only questions' / 'writer account' (visible in V$SESSION) | Implemented | `backend/oracle_store.py` `_tag()`; needs Local Oracle check |
| C1 | Explorer: search, rows, keyset paging, linked records | Implemented, Cloud-tested | `/api/explorer/*`, `frontend/js/explorer.js` |
| C2 | Relationship graph / flowchart (no CDN, SVG) | Implemented | `frontend/js/graph.js`, `flow.js` |
| E1 | Overview screen (default landing): work queue, real counts, recent Oracle saves from receipts, connection lights; nothing estimated (offline → "—") | Implemented, Cloud-tested (rendered desktop + phone) | `GET /api/overview`, `frontend/js/dashboard.js` |
| E2 | Phone layout: no horizontal page scroll on any screen | Fixed (was broken on New order, Database, Overview) | `minmax(0,1fr)` grids in `frontend/styles.css` |
| B6 | Evidence ledger (REA design): immutable, idempotent, conflict-refusing, atomic bundles, open questions; "Verified" requires a reviewer statement + auto-recorded facts | Implemented, Cloud-tested (dialog exercised in browser) | `backend/evidence.py`, `/api/evidence*`, Setup dialogs, `tests/test_evidence.py` |
| D1 | Windows service, backup/restore, upgrade/rollback | Implemented, Cloud-tested | `deploy/windows/`, `scripts/backup.py` |
| D2 | Offline packaging: hashed Windows wheelhouse + `requirements.lock` + `SHA256SUMS` + zip; `setup.ps1 -Offline` installs with `--require-hashes`; frontend has no CDN | Implemented, Cloud-tested (35 wheels verified offline; tamper refused). Windows run pending | `scripts/build_release.py`, `setup.ps1`, `docs/WINDOWS_SETUP.md` |

## Next steps (in order)

1. ~~Fix the failing tests~~ (done, batch 1).
2. ~~Remove leftover PDF wording in docs~~ (done, batch 4).
3. Re-audit gaps against the master prompt; implement missing pieces. Done so far: read-only account, charts, overview. Undo/redo done (batch 5). REA reviewed and its evidence-ledger design adopted (batch 4).
4. Produce exact local Oracle verification commands and record results here.
5. Expert data team (master prompt addendum), plan:
   - ~~SQL safety review, join check, total cross-check, check trace~~ (batch 7).
   - ~~Accuracy benchmark~~ (batch 8). On the laptop: `python scripts\run_benchmark.py`, then add the company's own questions to `data\golden_questions.json`.
   - ~~Local read-only MCP server~~ (batch 9).
   - ~~Adopt/adapt/reject table~~ (batch 9, `docs/OPEN_SOURCE_RESEARCH.md` → "Expert data team").
   - Optional next: M-Schema-style catalog cards for the local model; candidate agreement (rules vs. model) as in XiYan-SQL.

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
python scripts\run_benchmark.py       # Ask AI accuracy on the real data: expect "12 of 12 correct"
.\start.ps1                            # then open http://127.0.0.1:8008
```

Demo A (Excel → Oracle) and Demo B (Ask AI) are then run in the browser; record what happened below.

| Date | Check | Result |
|---|---|---|
| — | `check_oracle.py` | not run yet |
| — | `POPPYS_TEST_ORACLE=1 pytest` | not run yet |
| — | `run_benchmark.py` | not run yet |
| — | Demo A / Demo B | not run yet |
