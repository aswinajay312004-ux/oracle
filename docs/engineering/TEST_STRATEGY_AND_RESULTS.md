# Test strategy and results

## 1. Where tests run

| Level | Where | What it proves | Command |
|---|---|---|---|
| Unit + API (cloud) | Python 3.13, stand-in Oracle (`tests/fake_oracle.py`) and SQLite copy of the GARMENT_DEMO structure (`tests/demo_db.py`) | Reading, understanding, planning, approval rules, outcomes, Ask AI, explorer, security | `bash scripts/check.sh tests` |
| Real Oracle (replica) | Oracle Free 23.26 in a throwaway container, synthetic GARMENT_DEMO replica (`tests/oracle_replica.py`), real POPPYS_APP and POPPYS_RO accounts | The same suite with real inserts, sequences, locks, read-back, time limits, read-only enforcement, explorer on Oracle | `POPPYS_TEST_ORACLE=1 … scripts/check.sh oracle` (CI job "Oracle integration") |
| Windows | GitHub `windows-latest`, PowerShell 5.1 | The unit suite on the laptop's OS | `scripts/check.ps1 all` |
| Browser | Chromium (Playwright) against `tests/demo_server.py` | Screens, navigation, phone width, drag-and-drop, approval tick box | scratch scripts in the session (not yet in CI) |
| Laptop | owner's Windows + local Oracle + Ollama | Real data, real Axpert schema, real model | `.\verify_local.ps1` |
| Accuracy (Ask AI) | golden questions vs hand-written SQL | Answers equal independent results | `scripts/run_benchmark.py` |

Fake Oracle tests are never reported as Oracle results; replica results are labelled "replica".

## 2. Results (2026-10-18, commit after Phase 4 + docs)

| Check | Result |
|---|---|
| Cloud suite | **516 passed, 0 failed, 19 skipped** (skips: client workbooks only on the laptop, PowerShell/git-dependent tests, opt-in Oracle tests) |
| Real Oracle replica (Oracle Free 23.26) | **529 passed, 0 failed, 6 skipped** |
| Ask AI benchmark on the replica | **12 of 12 correct** |
| GitHub CI run 70 (`db23169`) | **all 7 jobs green**: secret scan (gitleaks, whole history), lint, JavaScript, unit tests, Oracle integration, dependency audit, Windows |
| Laptop (`verify_local.ps1`, owner's Windows + local Oracle 23.26 GARMENT_DEMO, `f9b0aa0`) | **518 passed, 0 failed, 18 skipped**; Oracle connection PASSED; Ask AI **12 of 12**. The first run (513 passed, 4 failed) found the DOCID receipt defect and three test problems, fixed in `0d6a0b0`–`f9b0aa0` |
| Real Ollama model | **not run** — Environment-blocked |

## 3. Coverage of the spec's test list (§14)

| Area | Tested by | Notes |
|---|---|---|
| Parser: formats, merged cells, hidden rows/sheets, formulas, error cells, dates, units, totals, large files, malformed files | `test_formats.py` (167), `test_engine.py`, `test_documents.py`, `test_fidelity.py`, `test_large_db.py` | Real client workbooks: laptop-only |
| Data fidelity: original bytes, hash, coverage, no invented values, conversions, unmapped fields | `test_fidelity.py` | |
| Metamorphic: inserted rows/columns, added size, renamed label (refused) | `test_acceptance.py` (layouts found again) | Reordered columns / moved to another sheet: not yet |
| Mapping: buyer scope, versions, collisions, cross-buyer contamination, validator | `test_acceptance.py` (A, B), `test_engine.py`, `test_masters.py` | |
| Oracle: rows, masters, links, rollback, idempotency, duplicate detection, read-back, control totals | `test_oracle.py`, `test_acceptance.py` (E/F real), `test_workflow.py`, `test_fidelity.py` | Concurrency and real network loss: not on real Oracle |
| Unknown commit, error after commit, local failure after commit | `test_fidelity.py` (stand-in) | |
| Roles, four-eyes, approval tick, CSRF, sessions, passwords | `test_workflow.py`, `test_fidelity.py`, `test_auth.py`, `test_api.py` | |
| Ask AI: catalog questions, scope gate, low-confidence confirm, SQL safety, joins, totals | `test_insight.py`, `test_policy.py`, `test_verify.py`, `test_agreement.py`, `test_benchmark.py` | |
| Operations: health, readiness, logs, request IDs, WAL, local-only AI, backup/restore | `test_operations.py`, `test_backup.py`, `test_ci.py` | Windows service: not tested |
| UI end-to-end | browser scripts (session) | Not in CI — Known risk |
| Performance | `test_large_db.py` (2.4M/24M-row replica design, budgets), `docs/PERFORMANCE.md` | Laptop timings: not measured with this code |

## 4. Acceptance criteria (spec §14.7) — current state

| # | Criterion | State |
|---|---|---|
| 1 | No known test case silently corrupts quantity or source values | Met for the tested cases |
| 2 | Original files survive successful and failed processing unchanged | Met (tested) |
| 3 | Every source field is accounted for in the coverage manifest | Met for numbers; text counted, not listed |
| 4 | Every ERP-written field has traceable approved lineage | Met (provenance + receipt) |
| 5 | Unverified mappings cannot authorize writes | Met (only verified columns of verified tables) |
| 6 | Wrong template reuse cannot silently alter units | Met (tested) |
| 7 | A partial write cannot appear as fully verified | Met ("with exceptions", `complete`) |
| 8 | Retry does not duplicate a committed order | Met (receipt under lock, unique keys; tested) |
| 9 | Role and approval restrictions pass server-side tests | Met |
| 10 | Real local Oracle integration tests pass | **Met** (replica and owner's laptop, 2026-10-18) |
| 11 | Axpert-visible correctness on representative orders | **Not met** (not verified) |
| 12 | Offline installation and runtime tests pass | Cloud: met; Windows: not run |
| 13 | Responsive and accessibility tests pass | Responsive measured at 390 px; accessibility partly |
| 14 | CI passes without masking failures | Met (run 70) |
| 15 | Remaining limitations documented | Met (KNOWN_LIMITATIONS.md) |
