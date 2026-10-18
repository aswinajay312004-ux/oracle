# POPPYS AI — current product audit (2026-10-18)

This audit is documentation only. No application code, configuration, mapping or database was changed. Each report
cites source lines, tests, or runtime probes. The probes ran on the cloud stand-ins (SQLite copy, fake Oracle) with
synthetic files. No passwords, secrets or real records appear in these reports.

Status labels used: IMPLEMENTED, PARTIALLY IMPLEMENTED, DOCUMENTED ONLY, NOT IMPLEMENTED, NOT VERIFIED.

| # | Report | What it answers |
|---|---|---|
| 1 | [CURRENT_PRODUCT_ARCHITECTURE.md](CURRENT_PRODUCT_ARCHITECTURE.md) | Frontend and backend structure, all 57 API routes, local AI, Oracle connection model, SQLite store, deployment, architecture problems |
| 2 | [COMPLETE_EXCEL_PIPELINE_AUDIT.md](COMPLETE_EXCEL_PIPELINE_AUDIT.md) | Upload to structured order, step by step; about 75 hardcoded rules; 27 edge cases with experiments |
| 3 | [SCREEN_BY_SCREEN_UX_AUDIT.md](SCREEN_BY_SCREEN_UX_AUDIT.md) | Every screen and Setup section, walked through in Chromium at desktop and phone widths; prioritised UX problems |
| 4 | [MAPPING_AND_ORACLE_WRITE_AUDIT.md](MAPPING_AND_ORACLE_WRITE_AUDIT.md) | Field → registry → Oracle column, masters, IDs, the approval-to-commit path, silent-failure analysis, ranked risks |
| 5 | [LAYOUT_TEMPLATE_CAPABILITY_MATRIX.md](LAYOUT_TEMPLATE_CAPABILITY_MATRIX.md) | Template storage, fingerprint, matching, versions, the five buyer/structure scenarios, capability matrix |
| 6 | [IMPLEMENTED_VS_MISSING_FEATURES.md](IMPLEMENTED_VS_MISSING_FEATURES.md) | 218 documented features and decisions checked against the code |
| 7 | [TEST_COVERAGE_AND_PRODUCTION_RISKS.md](TEST_COVERAGE_AND_PRODUCTION_RISKS.md) | Ask AI misreading (root cause, reproduced), test and coverage inventory, production and multi-company readiness |
| 8 | [RECOMMENDED_PRODUCT_REDESIGN.md](RECOMMENDED_PRODUCT_REDESIGN.md) | Target design, safest implementation order, decisions for the owner |

## Executive summary

**Where it stands.** The cloud test suite gives 465 passed, 18 skipped, 0 failed, with 82% line coverage (report 7).

The feature ledger (report 6) counts:

| Status | Features |
|---|---|
| IMPLEMENTED | 93 |
| PARTIALLY IMPLEMENTED | 42 |
| DOCUMENTED ONLY | 58 |
| NOT VERIFIED | 24 |
| NOT IMPLEMENTED | 1 |

On the real laptop Oracle, only setup, the connection check and schema discovery have passed. Saving to the real
Oracle, the real local AI and every Windows service script are **NOT VERIFIED**. GitHub CI on this branch is red. The
product is not production-ready.

### Current strengths
- One safe cell model for 9 spreadsheet formats, parsed in time- and memory-limited worker processes (report 2).
- Every proposal passes the same deterministic validator, whether it came from the matcher, the local AI, a template or
  a person. The local AI only advises and can never write (reports 1, 2).
- A mapping registry with verified and proposed columns, so only verified columns are written (report 4).
- The write design: preview hash, lock, re-plan inside the transaction, one transaction, in-transaction read-back with
  rollback, idempotency keys. This is designed, but NOT VERIFIED on real Oracle (report 4).
- Ask AI never runs model-written SQL. It uses a read-only account, an independent safety review and a visible
  six-check trace (report 7).
- No CDN, no telemetry and no external calls; the only traffic is local Oracle and local Ollama (report 1).

### Fundamental architecture problems
1. **Saving to the real ERP is unproven.**
   - The cloud tests replace the real commit.
   - The CI Oracle job crashed with a segfault while setting `call_timeout` (`backend/insight/service.py:251`).
   - A direct INSERT fills 13 of 197 order-header columns, and whether Axpert accepts these rows is unknown (reports 4, 6).
2. **Understanding unknown layouts is narrow.**
   - It relies on fixed detectors and position-hashed templates. One inserted row or column breaks a template.
   - Templates ignore the buyer: another buyer's file was read with the first buyer's "packs of 3", so 120 units became
     360 pieces with no question asked (reports 2, 5).
3. **Several outcomes are silent:**
   - typed fields that are never saved (Exporter, Season, …);
   - a partial save shown as a full success;
   - a second matrix block, more than 200 lines, or extra sheets dropped without a note (reports 2, 3, 4).
4. **Ask AI answers questions it did not understand.** "how many table present in database" became "Number of orders"
   at low confidence, and a number was still shown. A count question that names nothing counts the default ORDMAS table.
   There is no catalog question type (report 7; reproduced again for this summary).
5. **Single company:**
   - startup refuses any schema other than GARMENT_DEMO;
   - there is no company profile or tenant;
   - masters are still created inside order posting (reports 4, 7).
6. **A monolithic, blocking server.** `main.py` is 1,595 lines, one global lock is held through the Oracle save, and
   there is no connection pool, no writer timeout, no logging and no `/readyz` (report 1).

### UI problems
- Technical internals shown to ordinary users: table codes, IDs, SQL, hashes, raw JSON.
- The Saved screen claims full success, and says "verified" before the read-back answers.
- Phone layouts scroll sideways (615 px on a 390 px screen).
- The browser's Back button leaves the app.
- Viewers are offered upload.
- A plain browser `prompt()` is used for password reset.
- Mapping is click-to-pick only: there is no drag-and-drop, no side-by-side editor and no way to edit a saved layout.
- Several actions cannot be reached with the keyboard (report 3).

### Safest implementation order (report 8)
0. **Prove what exists**, with no behaviour change:
   - root-cause the Oracle segfault and make CI green;
   - run `verify_local.ps1`, Demo A, Demo B and the real Ollama on the laptop;
   - open a POPPYS-saved order in Axpert and list the gaps.
1. **Honesty and safety:**
   - every silent drop and partial save is stated on screen;
   - templates do not cross buyers without asking about the unit;
   - the Ask AI scope gate and catalog answers;
   - a writer timeout, four-eyes on by default, and correct state after a post-commit failure.
2. **Data correctness:** unit dictionary, control totals with a fresh read-back and VERIFIED state, import ledger and
   amendments, masters as a reviewed step.
3. **Dynamic understanding:** region finder, canonical order model, label-anchored buyer profiles with partial reuse,
   corrections becoming aliases, optimal assignment, a capped local-AI vote.
4. **Visual mapping UX:** side-by-side mapping, profile editor, approval in business language, phone and keyboard support.
5. **Multi-company:** company profiles, removal of the GARMENT_DEMO hard stop, a write adapter per ERP.
6. **Operations:** router split, connection pool, logging and health checks, SQLite hygiene, Windows scripts verified.

**Decisions for the owner:**
- the write channel: direct INSERT, or Axpert's own posting route;
- whether masters may be created from files at all;
- four-eyes by default;
- one installation per company, or company profiles.
