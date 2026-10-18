# Recommended product redesign

**Scope.** This report proposes a direction based on the seven audit reports in this folder. It changes no code. Every
problem it addresses is cited to the report section, or the source line, where the evidence is. Fixed constraints:
- runtime fully offline, with no cloud AI or API;
- no change to the client's production database;
- every write approved by a person;
- exact numerical fidelity;
- auditability;
- Oracle relationships written only when verified.

Status labels used: IMPLEMENTED, PARTIALLY IMPLEMENTED, DOCUMENTED ONLY, NOT IMPLEMENTED, NOT VERIFIED.

## 1. What to keep

These parts are sound and should be kept as the foundation.

| Strength | Evidence |
|---|---|
| **One cell model for 9 spreadsheet formats**, parsed in time- and memory-limited worker processes. | COMPLETE_EXCEL_PIPELINE_AUDIT §3, §4; `backend/readers.py`, `backend/isolation.py` |
| **Every proposal goes through one deterministic validator** (`matcher.check`), whichever source made it: the matcher, the local AI, a template or a person. The local AI only advises. | COMPLETE_EXCEL_PIPELINE_AUDIT summary item 3; CURRENT_PRODUCT_ARCHITECTURE §4 |
| **A registry separates business fields from Oracle columns** (`config/oracle_mapping.json`). Only "verified" columns are written. | MAPPING_AND_ORACLE_WRITE_AUDIT §3.4–3.7 |
| **Write design:** preview hash, write lock, re-plan inside the transaction, one transaction, read-back inside the transaction with rollback on mismatch, idempotency keys. *Designed, but NOT VERIFIED against real Oracle.* | MAPPING_AND_ORACLE_WRITE_AUDIT §4.6–4.7 |
| **Ask AI never writes SQL.** It uses a separate read-only account, an independent SQL safety review and a six-check trace. | TEST_COVERAGE_AND_PRODUCTION_RISKS Part A; `backend/insight/policy.py` |
| **No framework, no CDN and no telemetry.** The only network traffic goes to local Oracle and local Ollama. | CURRENT_PRODUCT_ARCHITECTURE §2, §8 |
| **Broad automated tests:** 465 passed, 18 skipped, 82% line coverage. | TEST_COVERAGE_AND_PRODUCTION_RISKS Part B |

## 2. Fundamental problems

These are design-level problems. Patching individual screens will not fix them.

1. **Unknown layouts are understood by a few fixed detectors plus position-based templates, not by a general model.**
   - Without a person, only a saved template, a merch BOM with fixed labels, one contract report with fixed columns (F–K), or a flat table with exact headings is read (COMPLETE_EXCEL_PIPELINE_AUDIT summary item 2, §5: about 75 hardcoded rules).
   - Template fingerprints hash label text together with its exact row and column. One inserted row or column makes the template unusable: the score drops to 0.0 (LAYOUT_TEMPLATE_CAPABILITY_MATRIX §3, experiments).
2. **Templates are not tied to buyers, and they carry business choices such as the unit.**
   - A second buyer whose file has the same structure is read with the first buyer's template and its "packs of 3". In the experiment, 120 source units became 360 pieces, and nothing asked about it (LAYOUT_TEMPLATE_CAPABILITY_MATRIX §5, §7). The selection code is `workbooks.choose_template`, `backend/workbooks.py:925-945`; it ranks by score and uses, with no buyer.
   - A second structure saved under the default name replaces the first one (§7).
3. **Several things are silent.**
   - Data the user typed is not saved: Exporter, Department, Gender, Season and more are "proposed" columns (`config/oracle_mapping.json:39-44`), and no screen says so (MAPPING_AND_ORACLE_WRITE_AUDIT §5.1).
   - A partial save is shown as complete. Fabric, yarn and trims were not written, yet the screen shows "The order is saved in Oracle" with a green tick (SCREEN_BY_SCREEN_UX_AUDIT §12, item 1).
   - Data from the file is dropped without a warning: a second matrix block, more than 200 lines, an unmapped neighbouring sheet, hidden sheets (COMPLETE_EXCEL_PIPELINE_AUDIT items 4–6).
4. **The write path is unproven against the real ERP.**
   - The cloud tests replace `oracle_store.commit` completely (`tests/fake_oracle.py`), and the real-Oracle tests have not run (TEST_COVERAGE Part B; MAPPING §4.9).
   - A direct INSERT fills 13 of 197 ORDMAS columns. New masters get only an ID and a name. IDs come from POPPYS's own sequence (MAPPING §7, rank 2).
   - Whether Axpert opens these records correctly is unknown. The project's own decision record treats this as a blocker (`docs/RESEARCH_DECISIONS.md:55`).
5. **Masters are created inside order posting.** Duplicates are still possible:
   - the same new value spelled two ways in one file;
   - short values such as `X-L` or `PCS.`.

   The 2026-10-18 change reuses an existing record when only the writing differs, but there is no separate, reviewed step for creating masters (MAPPING §4.3, §5.4).
6. **Ask AI answers questions it did not understand.**
   - A count question that names no table, column or value counts the default table ORDMAS (`backend/insight/understand.py:363-372`, `:469-472`).
   - The engine answers whatever the score (`backend/insight/engine.py:290-293`).
   - Reproduced: "how many table present in database" gave "Number of orders", low confidence, and a number.
   - There is no catalog or metadata question type (TEST_COVERAGE Part A).
7. **Single-company code.** The application refuses to start unless the owner schema is GARMENT_DEMO (`backend/config.py:15-17`). Setup scripts and table names are tied to Axpert. There is no notion of a company, tenant or profile (TEST_COVERAGE Part C; MAPPING §3.10).
8. **Monolithic, blocking server.** `backend/main.py` is 1,595 lines and has 57 routes. One process-wide lock is held during the Oracle save. There is no connection pool, no writer timeout, no logging and no `/readyz`, although deploy scripts call it (CURRENT_PRODUCT_ARCHITECTURE §11).
9. **The UX is built for engineers.**
   - Table codes, IDs, SQL, hashes and raw JSON are shown to ordinary users.
   - Phone screens scroll sideways.
   - The browser's Back button leaves the app.
   - Viewers are offered upload.
   - There is no drag-and-drop or side-by-side visual mapping.

   (SCREEN_BY_SCREEN_UX_AUDIT §10, §12)
10. **CI is red on this branch** (runtime evidence, run 39124073690), for these reasons:
    - **Gitleaks:** it flags the synthetic test passwords in `tests/test_auth.py`. This is a false positive, but it still blocks the run.
    - **Windows:** two `tests/test_formats.py` cases fail because their generated test ID exceeds the 32,767-character environment-variable limit.
    - **Oracle Free job:** in run 49 the process crashed with a segmentation fault (exit 139) while setting `call_timeout`
      (`backend/insight/service.py:251`) after the timeout test `tests/test_large_db.py:290`
      (IMPLEMENTED_VS_MISSING_FEATURES rows OR-09, PK-08).
      - **This is a product risk, not only a CI problem:** the same code path runs when an Ask AI or explorer query times out on
        real Oracle, and a crash would stop the whole single-process server.
      - The root cause is NOT VERIFIED, and it is Phase 0 item 1.

## 3. Target design

### 3.1 Pipeline layers (each with one job and a typed contract)

```
File ─► 1 Ingest (keep readers.py + isolation) ─► cell model
     ─► 2 Region finder: occupancy-grid regions, header-band scoring, key-value blocks, multi-block,
          merged-cell fill, wide↔long reshape                                   ─► regions[]
     ─► 3 Field mapper: buyer layout profile (anchor-relative, not absolute cells) + name/value/master
          evidence + one-to-one optimal assignment + confidence bands + capped local-AI vote
                                                                                ─► mapping + confidence
     ─► 4 Canonical order (versioned schema, one model, provenance per value, unit as entered + factor)
     ─► 5 Validation: control totals (Σ per colour and size), unit dictionary, date ranges, required
          fields from the company profile, explicit "not ordered" vs "missing"
     ─► 6 Review & approval (person) ─► 7 Posting service (company write adapter) ─► 8 Fresh read-back,
          VERIFIED / POST_FAILED state, ledger entry
```

Layers 2–4 replace these parts of the current code:
- the fixed detectors and position-hash templates (`workbooks.py`);
- the greedy matcher, which uses one-to-one assignment by best score first (`matcher.py:335`).

These designs are already decided in `docs/RESEARCH_DECISIONS.md` rows 44–49, but they are DOCUMENTED ONLY (IMPLEMENTED_VS_MISSING_FEATURES).

### 3.2 Layout profiles instead of templates

- **Scoped to the buyer.** A profile is keyed by `(company, buyer, layout family)`. A file from another buyer may reuse a structure, but always asks about the unit and pack choices again.
- **Anchored to labels, not positions.** Each field is stored relative to its label ("the cell to the right of 'PO No'"), and tables by their header band. Inserted rows and columns, swapped columns and extra info rows then keep working.
- **Partial reuse with a diff.** "8 of 9 fields matched; 'Ship date' moved; 'Season' is new". The person confirms only the difference.
- **Corrections become aliases**, for that buyer first, then for everyone after review.
- **Names never collide silently.** Saving under an existing name asks: new version, or new structure?

### 3.3 Mapping to Oracle through a company profile

- **One profile per company:**
  - owner schema;
  - write adapter;
  - mapping registry;
  - field registry;
  - master policies;
  - golden questions;
  - data folder.

  This removes the GARMENT_DEMO hard stop in `backend/config.py:15-17` and keeps the registry approach.
- **The write adapter is chosen per ERP.** Before more direct-INSERT work, decide with the ERP owner whether posting should go through Axpert's own procedure or interface. If it stays direct INSERT, the profile must list every column Axpert needs.
- **Masters become a reviewed step of their own.** It sits inside the same approval screen as one-click "create N new masters", but it is listed and confirmed separately. Two spellings of the same new value inside one file are grouped.
- **Field visibility is always shown.**
  - Every field on the review screen shows a badge for where it goes: *saved to Oracle*, *kept in POPPYS only*, or *not saved (mapping not verified)*.
  - The Saved screen states exactly what was not written.

### 3.4 Posting and proof

- **Connection handling:** a pool, a writer `call_timeout`, and per-draft locks instead of one global lock.
- **Recorded, provable outcome:**
  - an import ledger keyed by content hash plus business key (buyer, PO, style, colour, size);
  - control totals checked inside the transaction and again after commit on a fresh connection;
  - states DRAFT → CHECKED → APPROVED → POSTED → VERIFIED, or POST_FAILED.
- **Recovery after commit.** If the local update fails after Oracle commits, the ledger still records POSTED and the screen says so. It no longer says "nothing was saved" (MAPPING §4.8).

### 3.5 Ask AI

- **Scope gate.** When a question names no business entity, measure or value, ask a clarifying question instead of using the default table.
- **Catalog questions** such as "how many tables", "what columns does ORDMAS have" or "how is size linked to order" are answered from the catalog (`knowledge.py`), never from data.
- **Low confidence shows choices instead of a number.**
- **"Correct – remember it" is limited to approvers and admins**, and it can be undone.
- **Unanswerable and out-of-scope questions are added to the golden set** (TEST_COVERAGE Part A).

### 3.6 UX

- **Plain language by default.** Business words on every screen; table codes, IDs, SQL and hashes go behind one "Technical details" toggle.
- **Visual mapping.**
  - The sheet sits on the left and the field list on the right. Click or drag a cell to a field, then see the confidence and the reason.
  - The same screen edits a saved profile ("Read this file differently" today always starts from fresh proposals).
- **Approval summary in business terms.** "1 new buyer, 3 colours, 24 size quantities = 4,560 pieces; fabric and trims NOT saved", plus a reason box for Reject.
- **Basics fixed:**
  - Back and Forward work between screens;
  - controls follow the user's role;
  - the layout fits a 390 px screen;
  - every action is keyboard-reachable;
  - risky admin actions ask for confirmation.

### 3.7 Server structure and operations

- **Split by concern.** `main.py` becomes routers for auth, drafts, posting, insight, explorer and admin.
- **Logging.** Structured JSON logs with a request ID, plus `/readyz` and `/healthz`.
- **Local store hygiene.**
  - Retention for revisions, chat log and uploads.
  - SQLite with WAL, `synchronous=FULL`, closed connections and versioned migrations.
- **Restricted endpoints.** `OLLAMA_URL` and the Oracle DSN must point to the local machine, so "offline" is enforced rather than left to configuration. `/docs` and `/openapi.json` are disabled in production.

## 4. Safest implementation order

Each phase is shippable on its own. Each keeps approval-controlled writes and ends with tests plus a laptop check.

| Phase | Content | Why first | Done when |
|---|---|---|---|
| **0. Prove what exists**, no behaviour change | (a) Root-cause the Oracle `call_timeout` segfault (CI run 49), then make CI green: an allowlist entry for the synthetic test password, a shorter Windows test ID. (b) On the laptop: `verify_local.ps1`, Demo A and Demo B, the real Ollama run. (c) **Axpert check:** open an order saved by POPPYS in Axpert's own screens and list every missing or wrong column. | Everything later depends on the real write path working and Axpert accepting the rows. | CI green; laptop results recorded in `IMPLEMENTATION_STATUS.md`; Axpert gap list written. |
| **1. Honesty and safety** (small changes) | Saved screen states what was and was not written. Review shows which fields are not saved. Templates do not cross buyers without asking the unit again. Template name collisions prompt. Ask AI scope gate and catalog answers. Writer `call_timeout`. Correct state after a post-commit local failure. Four-eyes on by default and checked against the last editor. Every silent drop becomes a note (second block, more than 200 lines, hidden or unmapped sheets). "Blank means not ordered". | Removes every path where the product shows the user something untrue. | New tests for each case; UX re-check in a browser. |
| **2. Data correctness** | Unit dictionary (DZN, PR, SET, CTN with the entered unit and factor stored). Control totals plus a fresh-connection read-back and the VERIFIED state. Import ledger (content hash plus business key) and an amendment flow. Masters as a reviewed step, with in-file duplicate grouping. Date ranges. | Quantity fidelity and duplicates matter more than convenience. | Golden-file tests per rule; laptop run on the real Oracle. |
| **3. Dynamic understanding** | Region finder, canonical order model, label-anchored buyer profiles with partial reuse and diff, corrections becoming aliases, optimal assignment, confidence bands, capped local-AI vote. | This is the core of "any buyer layout", and Phases 1–2 make it safe to change. | A fixture corpus of at least 30 real anonymised layouts with measured field accuracy; no silent drops. |
| **4. Visual mapping UX** | Side-by-side mapping, profile editor, business-language approval summary, "Technical details" toggle, phone layout, keyboard and Back button. | It builds on the Phase 3 contracts. | Usability walk-through with an operator; accessibility checks. |
| **5. Multi-company** | Company profiles, removal of the GARMENT_DEMO hard stop, parameterised setup scripts, a data folder per company, a write adapter per ERP. | Only after one company works end to end. | A second schema (a replica) runs the same tests. |
| **6. Operations** | Router split, connection pool and per-draft locks, logging and `/readyz`, SQLite retention and migrations, local-only endpoints, Windows service, backup and upgrade verified on Windows. | Hardening for daily use. | The `RUNBOOK.md` steps executed on the laptop and recorded. |

## 5. Decisions the owner must make

1. **Write channel:** direct INSERT into Axpert tables, or Axpert's own posting procedure or import. This depends on the Phase 0 Axpert check.
2. **Master creation:** whether new buyers, colours or sizes may be created from a file at all, or must exist in Axpert first.
3. **Four-eyes:** whether it should be on by default.
4. **Multi-company model:** one installation per company (simplest isolation), or one installation with company profiles.

## 6. What not to do

- No cloud models and no model-written SQL.
- No automatic approval at any confidence level.
- No UPDATE or DELETE of existing ERP orders before the write channel is verified.
- No claim of "100% accurate" reading. Accuracy should be measured on a real, anonymised layout corpus and reported per field.
