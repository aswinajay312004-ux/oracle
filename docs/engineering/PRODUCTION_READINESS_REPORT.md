# Production readiness report (2026-10-18)

## Status: NOT PRODUCTION READY

Two blocking criteria are not met (spec §18): **Oracle/Axpert integration is not demonstrated** on the real ERP and
**the laptop verification has not been run** with this code. Everything that can be done and proven without the owner's
laptop has been done in this programme; the remaining steps are listed at the end with exact commands.

## 1. Audit findings → what was done

| # | Audit finding | Root cause | Change (files) | Tests | Risk now | Phase | Verification |
|---|---|---|---|---|---|---|---|
| 1 | Position-hashed templates break on an inserted row/column | Signature over exact label positions | Re-anchoring by labels, size range by colour/total columns, changes listed for approval (`layouts.py`, `workbooks.relocated_template`) | `test_acceptance.py` (found again ×3, added size, renamed label refused) | Low | 3 | Tested |
| 2 | A template of one buyer affects another | Template choice ignored the buyer; unit restored regardless | Own-buyer template wins; unit/basis never carried to another buyer; no learning from another buyer (`workbooks.choose_template/restore_from_template`, `main._learn_unit`) | `test_B_another_buyers_lookalike…`, `test_B_a_buyers_own_layout_wins…` | Low | 1 | Tested |
| 3 | 120 → 360 silent conversion | as #2 | as #2 + every conversion listed with source figures (`planner._conversions`) | same + `test_a_pack_conversion…` | Low | 1 | Tested |
| 4 | Fields shown but never saved | Proposed/missing columns not reported | `planner.destination/field_destinations`, badge, approval list, receipt `not_saved` | `test_values_oracle_will_not_receive…` | Low (reported); **the values themselves are still not in Oracle** | 1 | Tested |
| 5 | Extra blocks, sheets, rows silently ignored | No accounting of the workbook | Coverage manifest; second blocks, row limit, unmapped and hidden sheets named; `/map` keeps other sheets' orders; merch BOM second block no longer fails the file (`coverage.py`, `workbooks.py`, `main.mapping`) | `test_fidelity.py` (coverage ×5) | Low | 1 | Tested |
| 6 | Misleading success / verification | "Saved and verified" shown before read-back; partial save shown as full | Saved screen states from the fresh read-back; "with exceptions"; Saved orders statuses from `verification` (`workspace.showSaved`, `main.history_graph`) | `test_verified_only_after…` + browser | Low | 1–2 | Tested |
| 7 | 13 of 197 ORDMAS columns, Axpert compatibility unknown | Direct INSERT with a minimal column set | Gap report script; owner procedure (`scripts/axpert_gap_report.py`, ORACLE_AXPERT_INTEGRATION) | `test_oracle.py::test_axpert_gap_report…` (replica) | **High — blocking** | 2 | **Environment-blocked** |
| 8 | Ask AI answers misread questions | No catalog intent; count defaulted to ORDMAS; answers at any confidence | Catalog answers from the data dictionary; "count what?"; low confidence asks first (`insight/catalog_qa.py`, `understand.interpret`, `engine.gate`) | `test_insight.py` (4 new) ; benchmark 12/12 on the replica | Low | 1 | Tested |
| 9 | Blocking, monolithic backend; no operational protections | One lock across the Oracle save; no timeouts on writes; no health/logs | Per-draft locks, writer `call_timeout`, `/healthz` `/readyz`, JSON logs with request IDs, SQLite WAL + closed connections; CI segfault fixed (`main.py`, `oracle_store.py`, `logs.py`, `local_store.py`, `insight/service.py`) | `test_operations.py`, `test_large_db.py` | Medium (no pool, no router split) | 0, 7 | Tested |
| 10 | Technical complexity in the UI | Codes and hashes shown | Business wording on approval/confirmation; codes in small print; "Read before approving" in words | browser | Medium (audit, explorer still technical) | 5 | Browser-checked |
| 11 | Responsive and keyboard gaps | Fixed widths, no Back, click-only rows | 390 px everywhere, Back/Forward, keyboard reach, dialogs, focus handling | browser | Medium (screen-reader walk-through not done) | 5 | Browser-checked |
| 12 | No visual mapping editor | Click-to-pick only | Drag-and-drop, focus highlight, edit the saved layout as applied, file beside the order | browser | Low | 4 | Browser-checked |
| 13 | Company configuration incomplete | GARMENT_DEMO hard stop, settings in code | `config/company.json`, schema guard on writes | `test_operations.py` | Medium (scripts still GARMENT_DEMO; no unit dictionary) | 6 | Tested |
| 14 | Integration/deployment tests unfinished | No real Oracle in CI runs passing; Windows unproven | Oracle CI job green (read-only account created; crash fixed); Windows unit job green | CI run 70 | Medium (Windows service/install not run) | 0, 7 | CI |

Additional defects found and fixed during the work: the CI segmentation fault (dropped connection + `call_timeout`),
Windows test IDs over 32,767 characters, gitleaks false positives, the explorer 503 in the Oracle CI job, an
empty sample section zeroing the order, the sample type invented by the reader, unknown/after-commit save outcomes
shown as failures, viewers offered upload, `prompt()` for passwords, a Node test broken by a UI change (fixed before
CI went green).

## 2. Release criteria (spec §18)

| Criterion | Met? | Evidence / missing |
|---|---|---|
| Data fidelity | Yes, for tested cases | EXCEL_DATA_FIDELITY_SPEC, `test_fidelity.py` |
| Excel understanding | Partly | Known layouts and re-anchored layouts read; unknown layouts need one mapping; accuracy on real files not measured |
| Mapping | Yes | Only verified columns written; everything else reported |
| Oracle integration | **No** | Replica proven; Axpert not verified; laptop not run |
| Saving | Yes (design + replica) | Idempotent receipt, unknown-outcome handling (stand-in) |
| Verification | Yes | Fresh read-only read-back with control totals |
| User experience | Mostly | Main journey usable without database knowledge; some technical screens remain |
| Security | Yes, for tested controls | Roles, four-eyes, CSRF, offline, read-only account |
| Operations | Partly | Health, logs, backup/restore tested in the cloud; Windows service/upgrade not run |
| Testing | Yes, in CI | Run 70 all green; laptop pending |

## 3. Remaining blockers and exact steps (owner)

```powershell
cd D:\oracle
git pull
.\setup.ps1 -Dev
.\start.ps1                      # sign in; Setup → Settings: four-eyes is ON by default now (create an approver account,
                                 # or allow self-approval while testing alone — it is recorded in the audit)
.\verify_local.ps1               # Oracle check + every test against the real local Oracle + Ask AI benchmark
# Demo A: upload a buyer file, review, approve, then open Saved orders → "Verified in Oracle"
.\.venv\Scripts\python.exe scripts\axpert_gap_report.py     # read-only; writes data\axpert_gap_report.md
# Open the POPPYS-saved order in Axpert's own screens and note what is missing or wrong.
```

Send back: the `verify_local.ps1` summary, `data\axpert_gap_report.md` (counts only, no records), and what Axpert showed.
Then the owner decides the write channel (direct INSERT with the missing columns, or Axpert's own posting) and which
proposed columns (exporter, BOM tables) become verified with evidence.

## 4. Rollback

Every change is a normal commit on `claude/fervent-babbage-jqo8r4`; nothing was force-pushed and no data or schema
was changed. To go back to the audited state: `git checkout 09bdeb4` (code) — the local store migrations added only
tables/columns (`import_ledger`, WAL mode), which older code ignores. Saved Oracle orders are not affected by a rollback.
