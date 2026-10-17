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
| 2026-10-17 (session 2 start) | 343 passed, 33 failed, 18 skipped | Failures inherited from WIP checkpoints: `test_formats.py` (ods/xlsb/xls fallbacks, OLE2 messages, isolation), `test_documents.py` (pack detection), `test_engine.py` (unit evidence), `test_large_db.py`, `test_planner.py` (composition warnings) |

## Feature status

| # | Feature | Status | Evidence / gap |
|---|---|---|---|
| A1 | Excel reading (.xlsx/.xlsm; .xls/.xlsb/.ods via isolated readers), provenance, formulas, hidden, merged | Implemented, partly Broken | `backend/readers.py`, `backend/workbooks.py`; format tests failing |
| A2 | Layout detection: merch BOM, buyer contract, flat table, saved templates, matcher | Implemented, Cloud-tested (partly) | `backend/matcher.py`, `backend/fields.py` |
| A3 | Raw → business draft → ERP plan → approved snapshot (hash-bound) | Implemented, Cloud-tested | `backend/erp/planner.py`, `tests/test_planner.py` |
| A4 | Mapping registry (verified/proposed columns) | Implemented, Cloud-tested | `config/oracle_mapping.json`, `backend/erp/mapping.py` |
| A5 | Deterministic validation (quantities, packs, masters, duplicates, lengths) | Implemented, Cloud-tested (one failing test) | planner |
| A6 | Approval + roles (admin/approver/operator/viewer), four-eyes option | Implemented, Cloud-tested | `backend/main.py` |
| A7 | Oracle writer: lock, re-plan, sequence keys, binds, read-back, receipt, rollback | Implemented, replica-tested | `backend/oracle_store.py`, `tests/oracle_replica.py`; needs Local Oracle test |
| A8 | Field-level error UI | Implemented | `frontend/js/workspace.js` |
| B1 | Knowledge catalog + relationship statuses | Implemented, Cloud-tested | `backend/insight/knowledge.py`, `config/semantic_catalog.json` |
| B2 | Question → intent → compiled read-only SELECT (no model SQL) | Implemented, Cloud-tested | `backend/insight/understand.py`, `query.py`, `engine.py` |
| B3 | Local AI (Ollama) fallback for understanding and mapping | Implemented | `backend/ai.py`; needs local Ollama check |
| B4 | Read-only executor (READ ONLY txn, timeout, row cap) | Implemented | `backend/insight/service.py`; separate read-only DB account to verify locally |
| C1 | Explorer: search, rows, keyset paging, linked records | Implemented, Cloud-tested | `/api/explorer/*`, `frontend/js/explorer.js` |
| C2 | Relationship graph / flowchart (no CDN, SVG) | Implemented | `frontend/js/graph.js`, `flow.js` |
| D1 | Windows service, backup/restore, upgrade/rollback | Implemented, Cloud-tested | `deploy/windows/`, `scripts/backup.py` |
| D2 | Offline packaging (wheelhouse, no CDN) | Needs check | — |

## Next steps (in order)

1. Fix the 33 failing tests (root cause, not test edits).
2. Remove leftover PDF wording in docs (PDF is out of scope).
3. Re-audit gaps against the master prompt; implement missing pieces.
4. Produce exact local Oracle verification commands and record results here.

## Local verification

To be filled in with exact PowerShell commands once the failing tests are fixed.
