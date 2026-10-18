# Mapping architecture and Oracle write audit (areas 4 and 6)

- Repository: `/home/user/oracle`, branch `claude/fervent-babbage-jqo8r4`, commit `e811402`.
- Audit date: 2026-10-18. Documentation only: no code, configuration or database was changed.
- Scope: how spreadsheet cells become canonical order fields, how those fields become GARMENT_DEMO rows,
  and whether the approval-to-commit path is safe and correct.
- Status labels used: **IMPLEMENTED** (code plus a passing test or runtime check, cited), **PARTIALLY
  IMPLEMENTED**, **DOCUMENTED ONLY**, **NOT IMPLEMENTED**, **NOT VERIFIED**.

## 1. Summary

The write path follows a careful design. A deterministic planner (`backend/erp/planner.py`) turns a
reviewed order into a hashed list of row operations, driven by a registry of columns
(`config/oracle_mapping.json`). An approver can only accept the exact preview hash. The writer
(`backend/oracle_store.py` `commit()`) takes a global Oracle row lock, re-plans inside the transaction,
refuses a changed hash, inserts with bind variables, reads every inserted row back in the same
transaction, writes a receipt, and rolls back on any exception. Only INSERT is ever issued against
GARMENT_DEMO. The planner checks column lengths, numeric precision and scale, dates, nullability,
grants, duplicates and master ambiguity, and it fails closed rather than truncating or rounding
stored values.

The most important limits found:

1. **The real Oracle writer is not exercised by any test that ran here.** Every cloud test replaces
   `oracle_store.commit` with `tests/fake_oracle.py`. The tests that use real Oracle are skipped (8 skips:
   `tests/test_oracle.py`, two in `tests/test_acceptance.py`, one in `tests/test_api.py`).
   `docs/IMPLEMENTATION_STATUS.md:135-137` records `POPPYS_TEST_ORACLE=1 pytest` and Demo A as "not run yet".
   Only setup, connection and discovery passed on the laptop (`docs/IMPLEMENTATION_STATUS.md:133-134`).
   Real-Oracle commit behaviour is therefore **NOT VERIFIED**.
2. **Masters are created automatically inside order posting** (`"create": true` for 7 masters). Probes
   showed duplicate master rows in three cases, all with no warning: two spellings of the same new
   value in one file, short values (3 characters or fewer once punctuation is removed), and spacing
   variants of sizes.
3. **Fields the user enters can be silently left out of Oracle.** Exporter, Against, Department, Gender,
   Packing type and Components are shown as entry fields but their columns are `proposed`. Season has no
   column at all. The plan lists skipped columns, but no screen shows that list.
4. **Table-level status is ignored for ORDMAS, ORDSTYLE and ORDSIZE.** If an admin marks a core column
   such as BUYER as `proposed`, it disappears from the insert with no error or warning.
5. **Direct INSERT bypasses Axpert's own logic.** New rows fill 13 of 197 ORDMAS columns. New master rows
   fill only the key and the label. IDs come from POPPYS's own sequence. The repository itself
   records this as a blocker (`docs/RESEARCH_DECISIONS.md:55`).
6. **Local state can disagree with Oracle.** If saving the local SQLite draft fails after a successful
   Oracle commit, the user is told the save failed ("Nothing was partially saved"). The draft stays
   "Ready to approve" and no local audit row records the approval. This was reproduced with the fake.

None of these produces a silently *changed* existing ERP record: POPPYS_APP has no UPDATE or DELETE grant
on GARMENT_DEMO. Each one can still produce wrong or incomplete *new* records.

## 2. Method and evidence base

### 2.1 Commands run

| Command | Result |
|---|---|
| `python3 -m pytest -q -p no:cacheprovider tests/test_planner.py tests/test_masters.py tests/test_workflow.py tests/test_acceptance.py tests/test_readonly.py tests/test_api.py tests/test_oracle.py -rs` | **66 passed, 8 skipped** in 26.5 s (Python 3.13.16). Skips: `test_acceptance.py:520`, `:580` (REAL marker), `test_api.py:29`, `test_oracle.py:21, 32, 49, 55, 72` (all `POPPYS_TEST_ORACLE` unset) |
| Same files with `-rA` (planner, masters, workflow, readonly) | All 35 listed tests PASSED (names cited in the sections below) |
| `python3 -m pytest -q -p no:cacheprovider tests/test_evidence.py` | **6 passed** |
| `git status --short` after all runs | clean (tests write to `tmp_path`; no repository file changed) |

### 2.2 Scratch probes

These were throw-away scripts in the session scratchpad, not committed. They run the real planner against
`SnapshotCatalog` (the committed dictionary snapshot) with in-memory masters, or run the real API with
`FakeOracle`. They show what the code does. They say nothing about real Oracle.

| ID | Input | Observed |
|---|---|---|
| P1 | Order with exporter, department, gender, season filled | ORDMAS insert has none of them; `errors=[]`, `warnings=[]`; `skipped_columns.ORDMAS = [ENAME, OAGAINST, DEPARTMENT, GENDER, COMPTYPE, NOCOMP]` |
| P2 | Override `ORDMAS.BUYER` to `proposed` | ORDMAS insert without BUYER; no error, no warning |
| P3 | Override table `ORDFAB` to `verified`, columns left `proposed` | ORDFAB insert `{ORDFABID, ORDMASID}` only; no error, no warning |
| P4 | Two lines with new colours `NAVY BLUE` and `Navy-Blue` | Two COLORMAS inserts (`$m5`, `$m7`); no warning |
| P5 | Size `2-3 Y`, master holds `2-3Y` | New SIZE_MAS row `2-3 Y`; no warning |
| P6 | Style `ST-12`, master holds `ST-0012` | Reuses record 11, with a warning (not blocking) |
| P7 | `qty_unit=PACK`, 3 per pack, sizes 10/20 | RQTY 30/60, OQTY 90. `PACK` with no pieces per pack gives error "how many pieces are in one pack?" |
| P8 | Dates `2026-1-5`, `2026-02-30`, `1901-01-01`/`2099-01-01` | Normalised to `2026-01-05`; error; accepted without warning |
| P9 | UNITMAS holds only `PCS.` | New UNITMAS row `PCS` is planned |
| P10 | Override tables `ORDMAS` and `ORDSIZE` to `proposed` | ORDMAS, ORDSTYLE and ORDSIZE still planned for insert; no error |
| P11 | Override `ORDMAS.STYLENO` to `proposed` | `ORDSTYLE.STNO = None` (the copy of a skipped column) |
| P12 | PACK × 3, workbook pieces 100, accessory total 300, ORDACC verified | `APOQTY = 900`; warning says "scaled from the workbook's 100 pieces to the 300 pieces" |
| P13 | `readback._same('ABCDEFGHIJKLMNOP','ABCDEFGHIJ')`; `oracle_store.same('007','7')`; `same('T  ','T')` | `True`; `True`; `False` |
| P14 | `CADWT='123.4567'`, `NOCOMP='two'`, `NOCOMP=2.5`, 30-char Department (both verified) | Errors: more than 3 decimal places; requires a number; more than 0 decimal places; "allows 25. Shorten it." |
| P15 | 81-character PO starting with `$`; colour `$PECIAL` | No planner error (the length check is skipped for `$` values) |
| P16 | API + FakeOracle: local `store.save` fails once after commit | First accept **409** "Draft changed in another window"; Oracle receipt exists; second accept **200** `already_committed`; draft summary `Ready to approve`, `committed: False`; draft audit = upload, edit, preview only |
| P17 | API + FakeOracle, four-eyes on: approver edits and previews an operator's upload, then accepts | **200** (allowed) |
| P18 | FakeOracle after a save that created 5 masters | The fake's master catalogue still holds only BUYMAS and UNITMAS |
| P19 | Draft without `qty_unit`, `pieces_per_unit=2` | Quantities doubled, no warning |
| P20 | `order_ref='oc  1/a'` | DOCID written as typed (lower case, double space); SUBNO `oc  1/a:1` |

## 3. Mapping architecture (area 4)

### 3.1 Pipeline

```
workbook cells ─► workbooks.parse / apply_template ─► order dict (canonical fields + provenance)
   (readers.py)        (templates, matcher, AI-checked)        │  PUT /api/drafts/{id} (whitelisted edits)
                                                               ▼
                           planner.plan(catalog, orders, filehash, mapping)  ─► operations[] + hash
                    (registry: oracle_mapping.json + SQLite overrides; catalog: live or snapshot)
                                                               ▼
              accept → oracle_store.commit: lock → re-plan → same hash → INSERT … → read-back → receipt
```

### 3.2 Stage 1: cells to canonical order fields

- **Order shape.** `workbooks.base_order()` (`backend/workbooks.py:121-127`) defines the keys:
  `buyer, style, description, po_number, order_ref, ship_date, order_date, season, merch, sample_type,
  exporter, against, department, gender, packing_type, components, qty_unit, pieces_per_unit,
  quantity_basis` (default `'unconfirmed'`), `lines, provenance, warnings, order_lines, sample_lines, fabrics,
  accessories, packs, workbook_pieces`. Lines hold `label, color, sizes[{size, quantity, source}],
  declared_total`. The canonical field list with aliases is `backend/fields.py:21-49`.
- **Numbers.** `number()` (`workbooks.py:63-73`) accepts plain numbers and Western or Indian digit grouping.
  It refuses `"1,5"`, so a decimal comma is never read as 15. `qty_value()` (`:79-92`) reads `"20 packs"` as
  (20, PACK). Total and subtotal rows are excluded (`is_total`, `:109-114`).
- **Dates.** `iso_date()` (`workbooks.py:756-796`) reads Excel serial numbers (20000-80000), ISO,
  `d-Mon-y` and numeric d/m/y. Numeric dates are read **day-first**. An ambiguous one is converted
  with a warning ("read as day/month/year — check it"). It is left empty and asked only when another date
  in the file proves month-first (`finish()`, `:827-866`).
- **Code fields that read like sentences** (5+ words or more than 40 characters) are emptied and asked for
  style, PO and order reference (`finish()`). This stops a description being created as a style number.
- **Layout mapping.** A person can confirm or override where each field is through
  `POST /api/drafts/{id}/map` (`backend/main.py:481-513`, permission `edit`, so operators can too). Every
  definition goes through `matcher.check()` (`backend/matcher.py:369`), which rejects wrong fields with
  a reason (HTTP 409 listing each field, `main.py:474-494`). The definition can be saved as a versioned
  buyer template (`main.py:501-510`, `local_store.save_template` `:139-155`). Both actions are audited
  (`template_saved`, `mapping_applied`). Tests: `test_workflow.py::test_mapping_new_layout_saves_template_for_next_upload`,
  `test_acceptance.py::test_D_*` (passed). Status: **IMPLEMENTED**.

### 3.3 Stage 2: draft edits

`PUT /api/drafts/{id}` (`main.py:412-427`, permission `edit`) passes the client's orders through
`local_store.edited_orders()` (`backend/local_store.py:296-339`). This merge:

- copies only whitelisted order fields (`ORDER_FIELDS`, `local_store.py:11-13`), line `label`/`color`, size
  `size`/`quantity`, and the fabric, yarn and accessory fields (`:14-17`);
- keeps provenance, `order_lines`, `packs` and `workbook_pieces` server-owned. The client cannot change
  the workbook quantity used for accessory scaling;
- sets `declared_total = None` when a line's sizes or quantities change (`:329-331`). This switches off the
  "size total differs from source total" check for that line (`planner.py:311-314`), on purpose.
- records a field-level diff in the audit (`store.changes`, `:342-361`; `main.py:424-425`).

Each edit bumps the draft version and clears the plan (`main.py:422`). Status: **IMPLEMENTED**
(`test_workflow.py::test_full_order_flow_with_readback_graph` checks the `edit` audit entry).

### 3.4 Stage 3: the registry (`config/oracle_mapping.json`)

Value sources a column can have (checked by `mapping.validate`, `backend/erp/mapping.py:20-34`):

| Spec | Meaning | Planner code |
|---|---|---|
| `key: true` | Generated primary key, written as `'$<opkey>'` | `planner.py:241-242` |
| `parent: T` | Parent's generated key | `planner.py:243-244` |
| `copy: T.C` | Copy of a value already planned for the parent row | `planner.py:245-247` |
| `field: scope.name` | Order, line, size, fabric, yarn, acc or calc value | `_field`, `planner.py:99-102` |
| `value` | Constant (e.g. `ORDCATEGORY='SAMPLE ORDER'`, `UOM='PCS'`) | `planner.py:249` |
| `template` | Text with `{scope.name}` placeholders (REMARKS) | `_template`, `planner.py:105-106` |
| `master: role` | Value resolved to a master record ID | `master()`, `planner.py:181-234` |
| `optional` | Blank value means the column is left out | `planner.py:251-252, 256-257` |
| `required` | Only used for master columns (blank gives an error) | `planner.py:186-189, 255` |
| `status` | `verified` is written; `proposed` is skipped | `planner.py:239-240` |

`effective(overrides)` (`mapping.py:37-49`) applies admin overrides from SQLite `mapping_overrides`. An
override can change the status of any table, and of any column that is not a key or a parent link
(`test_planner.py::test_mapping_overrides_cannot_unlock_keys_or_parents`, passed). `fingerprint()`
(`:52-53`) hashes the effective mapping into every plan, so changing the mapping after a preview invalidates
the approval (`test_planner.py::test_plan_hash_changes_when_mapping_changes`, passed).
`referenced_tables()` (`:71-72`) is the list of tables whose dictionary rows and grants are read.
`tables_in_order()` (`:56-68`) is never called: parent-before-child order comes from the planner's
hard-coded traversal (section 4.4). The registry is cached with `lru_cache` (`mapping.py:13-17`), so
editing the JSON needs a server restart.

### 3.5 Canonical field to Oracle column

| Canonical field | Oracle target | Status | Transformation |
|---|---|---|---|
| `order_ref` | ORDMAS.DOCID; prefix of every SUBNO | verified | stripped only, not upper-cased (P20) |
| `buyer` | ORDMAS.BUYER → BUYMAS.BUYMASID (label BUYERNAME) | verified, `create` | master lookup |
| `style` | ORDMAS.STYLENO → STYLENUM; copied to ORDSTYLE.STNO | verified, `create` | master lookup |
| `description` | ORDMAS.STYLEDESC → STYLEMAS (SDESC); copied to ORDSTYLE.STYLENAME | verified, `create` | master lookup |
| `po_number` | ORDMAS.BONO; copied to ORDSTYLE.PONUM | verified | text |
| `order_date` | ORDMAS.ODATE | verified | `YYYY-MM-DD` → DATE |
| `ship_date` | ORDSTYLE.SHIPDT | verified | `YYYY-MM-DD` → DATE |
| `sample_type` | ORDMAS.SAMPLETYPE | verified | text |
| `merch` | ORDMAS.MERCH → MERCHMAS (NAME) | verified, optional, `create` | omitted when blank |
| (constant) `PCS` | ORDMAS.UOM → UNITMAS; copied to ORDSTYLE.PUOM | verified, `create` | always PCS |
| `quantity_basis` | inside ORDMAS.REMARKS template | verified | `"POPPYS demo sample import; quantity basis: …"` |
| size quantities × factor | ORDMAS.OQTY, ORDSTYLE.ORDQTY/PRODQTY, ORDSIZE.RQTY/PQTY | verified | `calc.pieces` |
| `line.color` | ORDSTYLE.COMBO → COLORMAS (COLOR) | verified, `create` | master lookup |
| `size.size` | ORDSIZE.GSIZE → SIZE_MAS (SIZES) | verified, `create` | master lookup |
| `exporter` | ORDMAS.ENAME → PARTYMAS (LEDGER) | **proposed** (not written) | — |
| `against`, `department`, `gender`, `packing_type`, `components` | OAGAINST, DEPARTMENT, GENDER, COMPTYPE, NOCOMP | **proposed** (not written) | — |
| `season` | **no column** | — | never written |
| `qty_unit`, `pieces_per_unit` | none (factor only) | — | multiplies quantities |
| fabric, yarn and accessory rows | ORDFAB, ORDYARN, ORDACC | table and data columns **proposed** | reference only (section 3.7) |

Source: `config/oracle_mapping.json:19-149`. ORDMAS has 197 columns in the dictionary snapshot; 13 are
`verified` and written (a count run over `docs/live_schema_columns.json`, which has 727 columns. That
matches the 727 that discovery reported in `docs/IMPLEMENTATION_STATUS.md:134`).

### 3.6 Catalog (`backend/erp/catalog.py`)

- `OracleCatalog` (`:40-87`, `live=True`) reads `all_tab_columns` for owner GARMENT_DEMO, INSERT grants from
  `user_tab_privs` (direct grants only, `:61-65`), master lists (`:67-77`), `PP_IMPORT_LOG` by file hash
  (`:79-82`) and `ORDMAS.DOCID` by `upper(trim(DOCID))` (`:84-87`).
- `masters()` reads at most 5,000 rows per master. A larger master raises `ValueError` and the preview fails
  (`:73-76`): it fails closed, but it would block a company with more than 5,000 styles. A row is
  "cancelled" only when `CANCEL = 'T'` (`:70, 77`). Other flags such as `ACTIVE` (or UNITMAS's misspelt
  `ACIVE`, per the snapshot) are ignored.
- `SnapshotCatalog` (`:90-118`, `live=False`) reads `docs/live_schema_columns.json`. A plan made with it is
  refused at accept (`main.py:590-591`;
  `test_workflow.py::test_offline_preview_is_marked_and_cannot_be_approved`, passed).

### 3.7 Verified versus proposed: what is written

- **Columns.** `evaluate()` skips every column whose status is not `verified` (`planner.py:238-240`).
  Skipped columns are returned in `plan['skipped_columns']` (`:166-167, 355`). Grep shows the key appears only
  in `backend/erp/planner.py:355`: **no frontend file reads it**. The approval view
  (`frontend/js/flowview.js`) shows only the columns that will be written.
- **BOM tables.** ORDFAB, ORDYARN and ORDACC are `proposed` at table level. The planner still checks their
  arithmetic (warnings instead of errors when not writable, `planner.py:415-416, 508`). It counts them as
  `reference_only` (`:470, 483, 546`), and the UI says "not saved to Oracle yet"
  (`frontend/js/workspace.js:306-311, 692`). Status: **IMPLEMENTED**
  (`test_planner.py::test_unverified_bom_is_previewed_but_never_planned_for_insert`,
  `test_workflow.py::test_bom_rows_saved_only_after_admin_verifies_mapping`, passed).
- **Table status is only honoured for the BOM tables.** `writable` (`planner.py:154`) is used for the grant
  check (`:161-165`) and in `_plan_fabrics` and `_plan_accessories`. ORDMAS, ORDSTYLE and ORDSIZE are planned
  unconditionally (`:324-339`). P10 confirms it: marking ORDMAS `proposed` changes nothing. The planner's
  docstring (`planner.py:5-6`, "only verified columns of verified tables are planned") is inaccurate for these
  three tables.
- **Who can change status.** `POST /api/mapping/status` (`main.py:811-830`) needs permission `mapping`, which
  only `admin` has (`main.py:29-34`). An operator gets 403 (`test_workflow.py::test_roles_and_four_eyes`).
  Keys and parent links are refused (`main.py:816-817`).
- **Evidence.** Setting `verified` records an evidence bundle: the reviewer's statement (at least 15
  characters, `backend/evidence.py:34, 66-70`), plus the registry's `evidence` note and the discovery facts
  when `data/schema_discovery.json` exists (`main.py:818-827`). With no statement the request is refused with
  409 (`test_workflow.py::test_bom_rows_saved_only_after_admin_verifies_mapping`;
  `test_evidence.py::test_verifying_a_mapping_needs_a_statement_and_records_the_known_facts`, passed). The
  ledger is append-only, idempotent and refuses conflicts (`evidence.py:82-104`). Setting `proposed`
  needs **no** evidence. The statement is free text: nothing requires a discovery match, a foreign key or a
  read-back.
- **Audit.** Every change writes `mapping_status` to the local audit (`main.py:829`). The override lives in the
  laptop's SQLite (`local_store.py:42-43, 115-119`). It is not stored in Oracle and is not shared between
  installations.

### 3.8 Column and relationship discovery

- `scripts/discover_schema.py` connects **as the owner GARMENT_DEMO** (`:57-63`) and runs only SELECTs. It
  collects columns, constraints (P/U/R/C), triggers, sequences, indexes (`:176-199`), per-column fill and
  sample values (`:134-164`), whether reference columns resolve in a master (`:222-230`), and child-to-parent
  SUBNO matches within the same ORDMASID (`:231-242`). Output goes to git-ignored `data/schema_discovery.*`.
  It is read-only by convention, not by account: the owner account could write. The 2026-10-18 laptop run
  found 0 triggers, primary keys on every table except STYLENUM, and one foreign key, ORDMAS.BUYER → BUYMAS
  (`docs/IMPLEMENTATION_STATUS.md:134`). ORDFAB, ORDYARN and ORDACC were empty, so their links have no row
  evidence. Status: **IMPLEMENTED**, laptop run recorded.
- `backend/insight/service.py` `harvest()` (`:90-131`) reads the dictionary, comments, foreign keys, indexes and
  unique keys **for the read-only side** (Ask AI, explorer). Join statuses (`verified`/`declared`/`proposed`/
  `inferred`/`rejected`, `backend/insight/knowledge.py:23, 99-139`) are edited through
  `PUT /api/knowledge/objects` (`main.py:1092-1117`, permission `mapping`, with evidence when verifying). These
  statuses and the registry's `links` list (`oracle_mapping.json:151-158`) **do not affect writes**. Writes
  link rows only through `parent` and `calc.subno` specs. `links` are used for read-back graph edges
  (`readback.py:80-81`) and the semantic model (`semantic.py:31-38`).

### 3.9 Customising mappings through the UI

| Capability | Status | Evidence |
|---|---|---|
| Pick-and-click mapping of sheet cells and columns to order fields | **IMPLEMENTED** | `frontend/js/mapping.js:73` ("Click Pick next to a field, then click its value in the sheet"), `:167-191`; `main.py:481-513` |
| Saved buyer templates, versioned, admin can activate or deactivate | **IMPLEMENTED** | `main.py:886-890` (permission `templates`); `local_store.py:139-187` |
| Buyer label aliases per order field, extra required fields | **IMPLEMENTED** | `PUT /api/fields/{key}`, `main.py:862-879` (permission `mapping`, audited `field_changed`); `fields.py:142-158` refuses an alias owned by another field |
| Drag-and-drop field mapping | **NOT IMPLEMENTED** | Grep for `drag`/`drop`/`dataTransfer`/`draggable` finds only the file dropzone (`frontend/js/app.js:107-112`) and diagram panning (`frontend/js/graph.js:281-324`) |
| Change which Oracle column a business field goes to, or add a column | **NOT IMPLEMENTED** in the UI | Only the status can change (`main.py:803-830`). New columns need an edit to `config/oracle_mapping.json` and a restart |

### 3.10 Single company or several

**NOT IMPLEMENTED (hard-coded to GARMENT_DEMO and the Axpert order tables):**
`backend/config.py:15-17` raises unless `ORACLE_OWNER == 'GARMENT_DEMO'`. The planner hard-codes
the ORDMAS → ORDSTYLE → ORDSIZE traversal and the BOM table names (`planner.py:324-339, 413-546`).
`catalog.order_ref_exists` hard-codes `ORDMAS.DOCID` (`catalog.py:86`), and `commit` reads `vals['DOCID']`
(`oracle_store.py:184-185`). The scripts hard-code owner and table lists (`scripts/bootstrap_local.py:9`,
`scripts/grant_bom_tables.py:16`, `scripts/discover_schema.py:30-54`). There is one mapping file and no
per-company registry. The `owner` key in the JSON is informational (`semantic.py:46`). The registry carries
demo-specific constants: `ORDCATEGORY='SAMPLE ORDER'` and REMARKS `"POPPYS demo sample import; …"`
(`oracle_mapping.json:35-36`).

## 4. Oracle safety and correctness (area 6)

### 4.1 Accounts and privileges

| Account | Privileges (from the scripts) | Evidence |
|---|---|---|
| POPPYS_APP (writer) | CREATE SESSION; SELECT, INSERT on BUYMAS, STYLENUM, STYLEMAS, SIZE_MAS, COLORMAS, UNITMAS, MERCHMAS, ORDMAS, ORDSTYLE, ORDSIZE; then SELECT on every GARMENT_DEMO table and INSERT on ORDFAB, ORDYARN, ORDACC. Owns PP_IMPORT_LOG, PP_ORDER_KEYS, PP_WRITE_LOCK and PP_RECORD_SEQ, so it can also UPDATE or DELETE them | `scripts/bootstrap_local.py:34-46`; `scripts/grant_bom_tables.py:26-31`. Laptop check passed 2026-10-18 (`IMPLEMENTATION_STATUS.md:133`) |
| POPPYS_RO (Ask AI, explorer) | CREATE SESSION + SELECT on GARMENT_DEMO tables and views; `connect_readonly()` refuses any account with other privileges | `scripts/create_readonly_user.py:42-55`; `backend/oracle_store.py:39-68`; `tests/test_readonly.py` (4 passed) |
| GARMENT_DEMO (owner) | Used only by `discover_schema.py`, password typed each time | `scripts/discover_schema.py:57-63` |

- No UPDATE, DELETE or MERGE against GARMENT_DEMO exists in the writer (grep of `backend/oracle_store.py`,
  `backend/erp/`, `backend/main.py`; the only `FOR UPDATE` is the lock row). Without UPDATE or DELETE grants,
  the database also refuses them. **IMPLEMENTED** by scripts; grant state confirmed on the laptop only by
  `check_oracle.py` output.
- The writer has no `call_timeout`; only the lock waits (`WAIT 10`, `oracle_store.py:146`). This is a known gap
  (`IMPLEMENTATION_STATUS.md:102`).
- Sessions are tagged with MODULE/ACTION (`_tag`, `oracle_store.py:17-24`). **NOT VERIFIED** on Oracle.
- The decided design uses READ grants and a separate POPPYS_WR account (`RESEARCH_DECISIONS.md:54`):
  **DOCUMENTED ONLY**.

### 4.2 Master lookup and ambiguous matching

`planner.master()` (`planner.py:181-234`) with `backend/erp/masters.py`:

| Case | Outcome | Evidence |
|---|---|---|
| Blank value, required master | Error "`{role}` is required" | `planner.py:186-189` |
| Blank value, optional master (MERCH, and ENAME/ACCCOLOR/ACCSIZES if verified) | Column left out, no message | `planner.py:251-252` |
| Master table or label column not visible | Error "not available in GARMENT_DEMO" (fails closed) | `planner.py:194-197`; `test_planner.py::test_verified_column_with_missing_master_table_fails_closed` |
| Exact match (upper-case, collapsed spaces), 1 record | Reuse | `planner.py:201, 218-220` |
| Exact match, 2 or more records (even if one is cancelled) | Error "ambiguous … Resolve duplicate master records" | `planner.py:211-213` |
| Exact match on a cancelled record | Error "is cancelled" | `planner.py:215-217` (probe output: "color master "OLD RED" is cancelled") |
| No exact match; same fingerprint (letters and digits, `&`=AND, leading zeros dropped), longer than 3 characters, 1 record | Reuse **with a warning only** | `masters.py:23-26, 56-58`; `planner.py:207-210`; `test_masters.py::test_a_value_written_differently_reuses_the_existing_master_and_says_so`; P6 |
| Same fingerprint, 2 or more different IDs | Error "could be any of … Type it exactly" | `planner.py:203-206`; `test_masters.py::test_a_value_matching_two_existing_records_must_be_typed_exactly` |
| Fingerprint of 3 characters or fewer | Exact match only; otherwise a **new record, no warning** | `masters.py:20, 58`; `test_masters.py::test_short_codes_and_style_numbers_are_never_guessed` (asserts `X-L` is created with no warning); P5, P9 |
| No match, `create: false` (exporter, fabric, count, accessory) | Error "Add it in Axpert first" | `planner.py:221-223` |
| No match, `create: true` | New master planned; for names (buyer, colour, description, merch), similar names with token-set score ≥ 0.8 are listed as a warning | `masters.py:18-19, 60-69`; `planner.py:224-232`; `test_masters.py::test_a_new_name_close_to_an_existing_one_is_created_with_a_note_not_blocked` |

The cache `known[(role, normalized label)]` (`planner.py:191-193, 233`) makes one plan reuse a new master
across orders and lines (probe: two orders with buyer NEWCO produced one BUYMAS insert). The index is built
only from **existing** records (`planner.py:198-199`). A second spelling of a value that is new in the same
file therefore creates a second master (P4). Status: **IMPLEMENTED** as designed, with the gaps listed in
section 5.

### 4.3 Master creation

- Seven masters have `"create": true` (`oracle_mapping.json:6-12`). Creation happens inside order posting,
  authorised by the order's approver. There is no separate step or permission. The decided design ("Master
  creation is a separate, permission-gated command", `RESEARCH_DECISIONS.md:52`) is **DOCUMENTED ONLY**. This
  is listed as production gap 1 in `IMPLEMENTATION_STATUS.md:91-93`.
- A new master row gets only `{key: '$mN', label: <text>}` (`planner.py:232`). The snapshot shows every other
  master column is nullable (for example BUYMAS has 44 columns: CANCEL, ACTIVE, CREATEDBY, CREATEDON,
  SOURCEID, APP_LEVEL, BUYERCODE, …). All of them stay NULL. Whether Axpert shows or uses such rows is
  **NOT VERIFIED**.
- INSERT permission on the master table is required (`planner.py:225-226`). The label length is checked against
  the live column (`_check_columns`, `planner.py:552-595`; MERCHMAS.NAME is VARCHAR2(30), STYLEMAS.SDESC
  VARCHAR2(50)).

### 4.4 Generated IDs, foreign keys, parent-child order, SUBNO

- **Placeholders.** Every inserted row's key is `'$' + opkey` (`planner.py:178, 241-244`), and references to
  new masters are `'$mN'`. Operations are appended in dependency order. `evaluate()` calls `master()` (which
  appends the master operation) before the row itself is added, and parents are added before children
  (`planner.py:324-339, 466-481`). `commit` replaces `'$x'` with the ID already allocated (`oracle_store.py:169-170`).
  A missing reference raises `KeyError` and the transaction rolls back. `tests/test_oracle.py:32-47` checks
  this with a poisoned reference (skipped here).
- **IDs.** Every inserted row (orders, lines, sizes, masters, BOM) gets `PP_RECORD_SEQ.nextval`
  (`oracle_store.py:165`). The sequence starts at 1,000,000,000,000, cache 100 (`bootstrap_local.py:38`). It is
  not Axpert's numbering. A collision with an existing row is caught at insert time: by the primary key
  (every table except STYLENUM, per discovery), or by the read-back that requires exactly one row
  (`oracle_store.py:180`). A collision with an ID Axpert allocates **later** is outside POPPYS's control.
  Whether the ranges overlap is **NOT VERIFIED**.
- **Foreign keys.** Only ORDMAS.BUYER → BUYMAS is declared in the database (`IMPLEMENTATION_STATUS.md:134`).
  All other links (ORDSTYLE.ORDMASID, COMBO, GSIZE, …) rely entirely on the planner.
- **SUBNO.** POPPYS writes `SUBNO = '<order_ref>:<line number>'` (`planner.py:329`) into ORDSTYLE.SUBNO,
  ORDSIZE.SUB3_SUBNO and, when verified, ORDFAB.FSUBNO, ORDYARN.SUB9_FSUBNO and ORDACC.ASUBNO. An accessory or
  fabric row with no line gets the bare order reference (`_resolve_line`, `planner.py:401-407`). Discovery
  confirms SUB3_SUBNO → SUBNO matches within an order (38/38), but not Axpert's SUBNO **format**. The test
  stand-in `tests/demo_db.py:58` uses `'<ORDMASID>:<n>'`, a different convention. **NOT VERIFIED**.
- **Axpert bypass.** Direct INSERT skips Axpert's own numbering and save scripts. 184 of 197 ORDMAS columns
  stay NULL, including FINYEAR, OTYPE, CANCEL, CREATEDBY and CREATEDON (snapshot count). The repository
  records this as a blocker (`RESEARCH_DECISIONS.md:55`: "Axpert Gen/MD maps, auto-numbering and tstruct
  scripts are bypassed by direct INSERT"). **NOT VERIFIED** whether Axpert screens open such orders
  correctly (Demo A not run, `IMPLEMENTATION_STATUS.md:137`).

### 4.5 Transformation rules

**Quantities and units.**
- `count()` (`planner.py:29-42`) parses with `Decimal(str(v))`. It accepts only whole numbers from 0 to
  999,999,999: `12.0` is accepted as 12 and `12.5` is an error (probe). Booleans and blanks are errors.
- `_unit_factor()` (`planner.py:114-138`): `PCS` gives a factor of 1. If pieces per unit is not 1 it is an
  error. `PACK` needs a whole number of at least 2 pieces per pack. A blank or unknown unit is an error that
  quotes the file's question. Quantities × factor are stored as pieces (`:310, 317`; P7;
  `test_acceptance.py::test_C_missing_unit_is_asked_and_blocks_saving_until_answered`, passed).
- Drafts saved before `qty_unit` existed fall back to `pieces_per_unit` without any unit question
  (`planner.py:117-118`; P19).
- `_check_packs()` (`planner.py:380-398`) only **warns** when header packs × pieces disagree with the size
  breakdown (gap 2 in `IMPLEMENTATION_STATUS.md:94`). Units other than PCS and PACK (DZN, SET, CTN) are
  **NOT IMPLEMENTED**.

**Decimals and rounding.**
- `decimal()` (`planner.py:45-57`) removes thousands commas and accepts values from 0 to 999,999,999.
  `q()` rounds to 3 places with ROUND_HALF_UP (`:60-61`).
- Fabric kg = pieces × grams ÷ 1000 × rejection, rounded to 3 places (`:441`). The yarn kg split puts the
  rounding residual on the largest share, so the yarn total equals the fabric total (`:453-458`; probe:
  0.297 + 0.593 = 0.890).
- `plain()` turns Decimals into int or float for the JSON plan and hash (`:64-68`). `commit` turns floats back
  with `Decimal(str(v))` (`oracle_store.py:173-174`). Below about 15 significant digits this round-trip is
  exact.
- `_check_columns()` refuses values with more decimals than the column scale or more digits than its
  precision (`planner.py:577-586`; P14). POPPYS never lets Oracle round a stored value silently. Raw user
  strings such as `fabric.yy` are bound as text into NUMBER columns (P14 shows `CADWT='123.4567'`), so they
  depend on Oracle's implicit conversion and NLS settings at insert time (**NOT VERIFIED**).

**Dates.** The planner requires `YYYY-MM-DD` that `strptime` can parse and normalises it (`planner.py:281-288`;
P8). Ship date before order date is an error. There is no range check, so 1901 and 2099 are accepted (P8; gap 5
in `IMPLEMENTATION_STATUS.md:99`). `commit` binds a `datetime` at midnight for DATE columns
(`oracle_store.py:171-172`).

**Missing values.**
- Hard-coded required fields: order reference, both dates, PO, sample type, quantity basis
  (`planner.py:264-292`), 1-100 lines and 1-100 sizes (`:296-303`), and a line total that is not zero
  (`:315-316`).
- Admin-added required fields are checked only at preview (`planner.py:277-280`; `main.py:554`). The re-plan
  inside `commit` passes no `required` (`oracle_store.py:150`).
- A missing fabric rejection allowance defaults to 1.0 with a warning (`planner.py:429-431`). An accessory with
  quantity 0 is skipped with a warning (`:526-528`).
- `fields.py` marks Exporter "locked/required" (`:35`), but the planner does not check it and does not write it
  (ENAME is proposed). The Settings default fills it at upload (`main.py:306-314`).

**Lengths, types, nullability** (`_check_columns`, `planner.py:552-595`; **IMPLEMENTED**,
`test_planner.py::test_accessory_quantities_scale_to_saved_pieces_and_too_long_text_is_grouped`):
- VARCHAR2 and CHAR length is checked in UTF-8 bytes. NUMBER precision and scale, DATE format and NOT NULL
  are checked against the live dictionary.
- Values that start with `$` skip the checks (`:570-571`). P15 shows that a user value starting with `$`
  passes unchecked. In `commit` it is then treated as a reference: `KeyError`, rollback, and the API answers
  404 "Not found. Reload and try again." (`main.py:77-78`). In the unlikely case that the text equals a real
  operation key (for example a PO typed as `$o1`), the generated ID would be written in its place.
- The NOT NULL check catches little in practice: in the snapshot only primary keys are NOT NULL.

### 4.6 Approval-to-commit workflow, end to end

1. **Preview.** `POST /api/drafts/{id}/preview` (`main.py:568-578`), permission `preview` (operator and up).
   - Runs under the in-process `LOCK` (`main.py:27, 570`). `editable()` refuses a stale version, a local
     result, or an Oracle receipt for this draft (`:401-409`).
   - `_plan()` (`:551-565`) uses the live `OracleCatalog` if Oracle connects, otherwise `SnapshotCatalog` with
     `live=False` and a warning.
   - The plan, its graph and the plan hash are stored, the version goes up (`:574-575`), and `preview` is
     audited with the hash (`:576-577`).
2. **Accept.** `POST /api/drafts/{id}/accept` (`main.py:581-606`), permission `approve` (approver, admin).
   Under `LOCK`:
   - If Oracle already holds a receipt, it is returned (`:585-587`).
   - Otherwise the request needs the same draft version and the same 64-character plan hash (`:588-589`), a
     live plan (`:590-591`), and no errors (`:592-593`).
   - Four-eyes applies only when `allow_self_approval` is false. The default is **true** (`main.py:35`), and
     the rule compares only `uploaded_by` (`:594-595`). P17: an approver who edited and previewed someone
     else's upload may approve it.
   - Tests: `test_workflow.py::test_stale_or_changed_preview_cannot_be_approved`, `::test_roles_and_four_eyes` (passed).
3. **Commit.** `oracle_store.commit()` (`oracle_store.py:143-195`) runs on one POPPYS_APP connection in one
   transaction (python-oracledb does not autocommit):
   - `SELECT … FROM PP_WRITE_LOCK WHERE ID=1 FOR UPDATE WAIT 10` (`:146`) serialises every POPPYS writer,
     across processes too.
   - `read_result()` by draft id: an existing receipt is returned (`:147-149`).
   - It **re-plans** with the live catalog while holding the lock (`:150`). Any error refuses (`:151-152`). A
     different hash refuses with "Database or preview changed" (`:153-154`). The hash covers operations,
     issues, totals, the dictionary fingerprint and the mapping fingerprint (`planner.py:350-358`).
   - `PP_ORDER_KEYS` gets one row per order, normalised reference, primary key on ORDER_REF (`:158-159`;
     `bootstrap_local.py:43`).
   - For each insert operation: `nextval`, resolve references, convert types, then
     `INSERT INTO GARMENT_DEMO.<table> (…) VALUES (:v0, …)` with **bind variables** for every value
     (`:165-177`). Identifiers come from the registry. Unknown columns were already refused by the planner.
   - **In-transaction read-back**: `SELECT <same columns> … WHERE <pk>=:id`. It must return exactly one row and
     every value must be equal under `same()` (Decimal comparison for numbers, `==` for dates, string
     equality otherwise, `:120-128, 178-181`). Any difference raises an error, so success is never reported
     for a row that read back differently.
   - The receipt JSON goes into `PP_IMPORT_LOG` (CLOB bind, `:186-192`), then `conn.commit()` (`:193`).
   - **Any exception** leads to `conn.rollback()` and a re-raise (`:194-195`).
4. **After commit.** The local draft gets `result`, the version goes up, the local save is checked against
   the expected version, and `approved_saved` is audited (`main.py:601-605`). If the commit raised,
   `approve_failed` is audited (`:598-600`) and the exception is mapped: ValueError → 409, KeyError → 404,
   `oracledb.Error` → 503 "Nothing was partially saved", RuntimeError → 503 (`main.py:73-88`).
5. **Post-commit read-back.** The saved screen calls `GET /api/history/{id}/graph` (`main.py:659-666`;
   `frontend/js/workspace.js:694-698`):
   - It uses a **new connection**, reads `SELECT *` by primary key (`oracle_store.live_rows`, `:204-219`),
     and compares with the receipt using `readback._same` (`readback.py:6-12, 48-57`).
   - It shows "Verified in Oracle" or "Differences found". The result is not stored or audited.
   - The planned VERIFIED / POST_FAILED states and control totals are **NOT IMPLEMENTED**
     (`IMPLEMENTATION_STATUS.md:95-96`).

Status of the whole chain: **IMPLEMENTED in code; real-Oracle behaviour NOT VERIFIED.** Cloud coverage is
through `FakeOracle`, which replaces step 3 entirely (section 4.9).

### 4.7 Idempotency and concurrency

| Situation | Handling | Verdict |
|---|---|---|
| Same approval retried or double-clicked | In process: `LOCK` plus `oracle_committed` returns the receipt (`main.py:583-587`). Across processes: lock row plus `read_result` inside the lock (`oracle_store.py:146-149`) | Prevented in code. Fake-tested (`test_workflow.py::test_full_order_flow_with_readback_graph`; `test_acceptance.py::test_E_approved_order_is_one_transaction_and_every_row_reads_back_equal`). Real Oracle: `test_oracle.py:55-61` skipped |
| Two drafts with the same order reference approved together | Serialised by the lock; the second re-plan sees DOCID (`catalog.py:84-87`), so its plan has an error and is refused; PP_ORDER_KEYS primary key as a backstop | Prevented in code; real Oracle NOT VERIFIED |
| Same workbook bytes uploaded twice | `file_committed` on PP_IMPORT_LOG.FILEHASH (`catalog.py:79-82`; `planner.py:171-172`) | Prevented (`test_planner.py::test_duplicate_file_and_existing_order_reference_are_blocked`). The same order in another file format or under a new reference is **not** caught (`IMPLEMENTATION_STATUS.md:97-98`) |
| Two drafts each creating the same new master | Second re-plan reuses the now-existing row, so its hash differs and approval is refused until re-preview | Prevented between POPPYS writers. An Axpert user creating the same master at the same moment is not covered by the lock (`RESEARCH_DECISIONS.md:55`) |
| Mapping or dictionary changed between preview and approval | `mapping_fingerprint` and `schema_fingerprint` are inside the hash | Prevented (`test_planner.py::test_plan_hash_changes_when_mapping_changes`) |
| Draft edited after preview | Version check at accept; preview itself bumps the version | Prevented (`test_workflow.py::test_stale_or_changed_preview_cannot_be_approved`) |
| Commit acknowledgement lost (network drops during `commit()`) | Exception, rollback attempt, 503 "Nothing was partially saved". A retry finds the receipt and returns it | No double insert. The first message may be **false**. Transaction Guard is **DOCUMENTED ONLY** (`RESEARCH_DECISIONS.md:55`) |
| Server deployment | One uvicorn worker (`deploy/windows/PoppysAI.xml:30`) | `LOCK` covers that process; the Oracle lock covers the rest |

### 4.8 Local SQLite draft state versus Oracle

The Oracle receipt is the authority: `oracle_committed()` is checked before edit, preview and accept
(`main.py:392-409, 585`). If the local save fails after a successful commit (`store.save` raises when the
version does not match, `local_store.py:240-243`), P16 shows:

- The user gets 409 "Draft changed in another window", or "… Nothing was partially saved" from the
  frontend wrapper (`workspace.js:677`), although Oracle committed.
- The local audit has neither `approved_saved` nor `approve_failed`, because the failure happens after the
  `try` block at `main.py:596-600`.
- A retry returns the receipt (`already_committed`) but never writes `result` into the local draft. The
  queue and overview keep showing "Ready to approve" (`local_store._status`, `:258-266`).

While Oracle is unreachable, `oracle_committed()` returns `None` (`main.py:397-398`), so such a draft can be
edited again. A later accept returns the old receipt, and those edits are not saved. Verdict: **no duplicate
write is possible, but the local state can stay wrong indefinitely** (PARTIALLY IMPLEMENTED consistency).

### 4.9 Test evidence and what `tests/fake_oracle.py` emulates

| Area | Real-Oracle test | Ran here? |
|---|---|---|
| Preview never inserts | `test_oracle.py::test_preview_validation_never_inserts` | Skipped |
| Mid-transaction failure rolls everything back | `test_oracle.py::test_mid_transaction_failure_rolls_back_all_rows` | Skipped |
| Changed preview refused | `test_oracle.py::test_changed_preview_is_rejected` | Skipped (fake equivalent passed) |
| Commit, retry, duplicate, quantity reconciliation | `test_oracle.py::test_commit_readback_retry_duplicate_and_quantity_reconciliation` | Skipped |
| Read-back graph equals committed rows | `test_oracle.py::test_live_readback_graph_matches_committed_rows`; `test_acceptance.py::test_E_F_real_oracle_*` | Skipped |

`FakeOracle` (`tests/fake_oracle.py:27-83`) **does**:

- run the real planner over a `SnapshotCatalog` with `live=True`, so the dictionary's lengths, types and
  nullability are checked at plan time;
- check errors and the hash;
- allocate IDs from a counter starting after 1,000,000,000,000;
- resolve `$` references;
- treat the draft id as the idempotency key;
- remember file hashes and DOCIDs (upper case) and keep receipts.

It **does not**:

- take a lock or run anything concurrently;
- roll back. Rows are stored as each operation runs, so a failure part-way leaves partial rows in memory;
- enforce primary key, unique or foreign key constraints, or write PP_ORDER_KEYS;
- emulate Oracle type binding, DATE conversion or numeric rounding;
- run the in-transaction read-back comparison. Read-back always equals what it stored;
- check grants: snapshot `insertable` returns every table;
- emulate triggers, NLS settings, the 5,000-row master limit, or commit-outcome failures;
- add newly created masters to its own master catalogue (P18). Repeated saves in tests therefore never
  exercise "master now exists".

`install()` patches `oracle_store.plan/read_result/commit/live_rows/history/masters` and `main.OracleCatalog`
(`:77-83`), so **no line of the real `oracle_store.commit` runs in the cloud suite**. The opt-in replica builder
`tests/oracle_replica.py` needs a Docker Oracle; there is no record of it running in this session.

## 5. Possibilities of silent field omission, wrong mapping, quantity corruption, duplicate master creation, or false success

Verdicts: **Prevented** (refused before writing), **Detected** (shown to a person or raised, nothing silently
wrong), **Possible** (a concrete path exists).

### 5.1 Silent field omission

| # | Scenario | Code path | Verdict |
|---|---|---|---|
| O1 | User fills Exporter, Against, Department, Gender, Packing type or Components on the review screen (`frontend/js/workspace.js:9-16`) | Columns `proposed` (`oracle_mapping.json:39-44`); `evaluate` skips them (`planner.py:239-240`); `skipped_columns` never shown | **Possible.** P1: no error, no warning, values not saved |
| O2 | User fills Season | No registry column at all; still an editable field (`local_store.py:11`, `fields.py:36`) | **Possible.** Never saved; an admin can even make it "required" |
| O3 | Admin marks a core column (BUYER, STYLENO, DOCID, …) `proposed`, which needs no evidence | `effective()` allows it (`mapping.py:46-48`); columns are nullable, so the NOT NULL check does not fire | **Possible.** P2 for BUYER. P11: dependent copies become NULL (STNO). For DOCID, `commit` fails with `KeyError` at `oracle_store.py:185`, so it is not silent but gives a misleading 404 |
| O4 | Admin marks the ORDMAS, ORDSTYLE or ORDSIZE table `proposed`, expecting writes to stop | Table status ignored for these tables (`planner.py:324-339`) | **Possible** (the opposite problem: rows are still written). P10 |
| O5 | Admin verifies a BOM table but not its columns | Only keys and parents are verified by default (`oracle_mapping.json:94-95, 119-120, 138-139`) | **Possible.** P3: ORDFAB rows with only ORDFABID and ORDMASID; no message |
| O6 | Optional MERCH blank | `planner.py:251-252` | By design, silent (NULL) |
| O7 | Fabric, yarn and accessory rows | Reference only while `proposed` | **Detected.** Banner "not saved to Oracle yet" (`workspace.js:311, 692`) |
| O8 | Accessory row with quantity 0, or unparseable while reference-only | `planner.py:523-528` | **Detected** (warning) |

### 5.2 Wrong mapping (wrong record or wrong column)

| # | Scenario | Verdict |
|---|---|---|
| W1 | Fingerprint reuse joins two different codes that differ only in punctuation or leading zeros (`ST-12` → `ST-0012`, P6; `ZK 02002` → `ZK-2002` is the intended behaviour, `test_masters.py:35-37`) | **Possible.** Reuse goes ahead after a warning only; nothing forces acknowledgement (`IMPLEMENTATION_STATUS.md:99`) |
| W2 | Exact or fingerprint match against two or more records | **Prevented** (error; `test_masters.py::test_a_value_matching_two_existing_records_must_be_typed_exactly`) |
| W3 | Match on a cancelled record (`CANCEL='T'`) | **Prevented** (error, `planner.py:215-217`). Rows marked inactive only through `ACTIVE` are **not** recognised (`catalog.py:70, 77`) |
| W4 | Layout mapping puts a wrong cell in a field | **Detected** for type and shape mismatches (`matcher.check`, `test_acceptance.py::test_D_*`). A plausible but wrong cell (for example the wrong date column) relies on human review |
| W5 | Ambiguous day/month date | **Detected** (warning, or left empty when month-first evidence exists, `workbooks.py:785-866`); a warning does not block |
| W6 | Wrong Oracle column for a field (registry error) | Depends on `evidence` statements; no automated proof required (section 3.7). Verified columns match discovery evidence for ORDMAS, ORDSTYLE and ORDSIZE references (100% resolve, `IMPLEMENTATION_STATUS.md:134`) |
| W7 | User value beginning with `$` equal to an operation key (`$o1`) | **Possible but unlikely**: replaced by a generated ID (`oracle_store.py:169-170`). Other `$` values skip length checks (P15) and then fail at commit with a misleading 404 |
| W8 | DOCID duplicate check with different internal spacing or case in an existing DOCID | **Possible.** The check compares `upper(trim(DOCID))` with a whitespace-collapsed input (`catalog.py:86`; `planner.py:25-26, 270`); DOCID is stored as typed (P20) |

### 5.3 Quantity corruption

| # | Scenario | Verdict |
|---|---|---|
| Q1 | Fractional, negative, text or huge quantities | **Prevented** (`count()`; P7, `12.5` gives an error) |
| Q2 | Pack factor chosen wrongly by a person (PACK when the file is in pieces) | **Possible**, by human decision. The planner multiplies; `_check_packs` only warns on a header mismatch |
| Q3 | Unit not decided | **Prevented** (error; `test_acceptance.py::test_C_*`) |
| Q4 | Legacy draft without `qty_unit` | **Possible.** `pieces_per_unit` applied without a question (P19) |
| Q5 | Accessory scaling when the workbook quantities are packs: the ratio divides saved pieces by raw workbook quantities (`planner.py:511-518`, `workbooks.py:831`) | **Possible** once ORDACC is verified. P12 turns 300 into 900, with a warning that calls packs "pieces". Today ORDACC is reference only |
| Q6 | Rounding of stored values by Oracle | **Prevented** by scale and precision checks (P14). Computed kg values are rounded half-up to 3 places by design and shown with their formula (`planner.py:462-463`) |
| Q7 | Line, size and order totals disagree | **Prevented**: OQTY is computed as the sum of line pieces (`planner.py:318`); a source total that differs is an error (`:311-314`) until the person edits the sizes |
| Q8 | Read-back changes a number | **Detected**. Decimal comparison in the transaction (`oracle_store.py:120-128, 180-181`). Real Oracle NOT VERIFIED |

### 5.4 Duplicate master creation

| # | Scenario | Verdict |
|---|---|---|
| D1 | Two spellings of the same new value in one file (`NAVY BLUE` / `Navy-Blue`) | **Possible, silent** (P4). The index only holds existing records |
| D2 | Short values (3 characters or fewer after fingerprinting): sizes `X-L` vs `XL`, `2-3 Y` vs `2-3Y`, unit `PCS.` vs `PCS` | **Possible, silent** (P5, P9; `test_masters.py::test_short_codes_and_style_numbers_are_never_guessed` asserts it) |
| D3 | New description or style wording close to an existing one | **Possible.** A similarity warning is shown for descriptions; style numbers get none (`masters.py:18`) |
| D4 | Same new master in two orders of one file | **Prevented** (`known` cache) |
| D5 | Two POPPYS drafts creating the same master concurrently | **Prevented** (lock, re-plan, hash) |
| D6 | Axpert user creating the same master at the same time; no unique constraint on labels (only primary keys) | **Possible.** Not covered by `PP_WRITE_LOCK` |
| D7 | Master table larger than 5,000 rows | Fails closed: preview refused (`catalog.py:75-76`) |

### 5.5 False success

| # | Scenario | Verdict |
|---|---|---|
| F1 | Read-back in the transaction differs | **Prevented**: raise and roll back; success is not reported (`oracle_store.py:180-181`) |
| F2 | Read-back compares only the columns POPPYS wrote | By design. Columns changed by defaults or triggers are not compared (0 triggers at discovery) |
| F3 | Post-commit graph comparison is looser: `_same` treats a 10-character written string as equal to any live value with the same first 10 characters (`readback.py:12`, P13) | **Possible false "Verified in Oracle"** on that screen. The commit itself uses the strict `same()` |
| F4 | `same('007','7')` is True for text columns that look numeric (`oracle_store.py:126`) | **Possible** (low impact) |
| F5 | Saved screen shows "Saved and verified" from the receipt before the graph loads (`workspace.js:686`); a later "Differences found" is not stored | Detected on screen only |
| F6 | The reverse: false failure. A commit succeeded, then the local save failed or the network dropped, and the user sees "Nothing was partially saved" (P16; `main.py:84`; `workspace.js:677`) | **Possible.** A retry returns "Already saved" |
| F7 | Offline (snapshot) preview approved | **Prevented** (`main.py:590-591`) |

## 6. Status summary

| Item | Status | Evidence |
|---|---|---|
| Registry-driven mapping with verified/proposed columns | IMPLEMENTED (columns); PARTIALLY IMPLEMENTED (table status ignored for 3 core tables) | `planner.py:154, 239, 324-339`; P10 |
| Evidence-gated verification and audit of status changes | IMPLEMENTED (statement only; un-verify needs nothing) | `main.py:811-830`; `test_evidence.py` |
| Showing skipped columns to users | NOT IMPLEMENTED | `skipped_columns` appears only at `planner.py:355` |
| Master matching tiers (exact, fingerprint, similar) | IMPLEMENTED | `masters.py`; `test_masters.py` (8 passed) |
| Separate, permission-gated master creation; buyer-scoped aliases | DOCUMENTED ONLY | `RESEARCH_DECISIONS.md:52`; `IMPLEMENTATION_STATUS.md:91-93` |
| Planner checks (lengths, precision, dates, grants, duplicates) | IMPLEMENTED | `planner.py:552-595`; `test_planner.py` (15 passed) |
| Writer: lock, re-plan, hash, binds, read-back, receipt, rollback | IMPLEMENTED in code; NOT VERIFIED on Oracle | `oracle_store.py:143-195`; real tests skipped |
| Post-commit fresh-connection graph | IMPLEMENTED (on demand, not persisted) | `main.py:659-666` |
| VERIFIED / POST_FAILED states, control totals, Transaction Guard | NOT IMPLEMENTED / DOCUMENTED ONLY | `IMPLEMENTATION_STATUS.md:95-96`; `RESEARCH_DECISIONS.md:55` |
| Four-eyes | PARTIALLY IMPLEMENTED (off by default; checks the uploader only) | `main.py:35, 594-595`; P17 |
| Drag-and-drop mapping | NOT IMPLEMENTED | grep (section 3.9) |
| Multi-company or multi-schema mappings | NOT IMPLEMENTED | `config.py:15-17` |
| Units other than PCS and PACK | NOT IMPLEMENTED | `planner.py:121-137` |
| Axpert compatibility of directly inserted rows | NOT VERIFIED | `RESEARCH_DECISIONS.md:55` |

## 7. Ranked risk list

| Rank | Severity | Scenario | Evidence | Current mitigation |
|---|---|---|---|---|
| 1 | High | The real commit path (lock wait, binds, DATE/NUMBER binding, read-back, rollback, PP_ORDER_KEYS) has not run in any test in this environment; a defect would surface on the first real save | `fake_oracle.py:77-83` replaces `commit`; 8 skipped tests; `IMPLEMENTATION_STATUS.md:135-137` | The code fails closed (rollback on any exception). Run `verify_local.ps1` and `POPPYS_TEST_ORACLE=1 pytest` on the laptop before relying on it |
| 2 | High | Orders and masters written by direct INSERT may be incomplete for Axpert: 184 of 197 ORDMAS columns NULL, masters hold only key and label, IDs from `PP_RECORD_SEQ`, SUBNO format assumed | Section 4.3-4.4; `RESEARCH_DECISIONS.md:55` | None in code. Demo A in Axpert has not been run |
| 3 | High | Duplicate master rows created silently: two spellings of a new value in one file, short sizes or units (`X-L`, `2-3 Y`, `PCS.`), free-text descriptions | P4, P5, P9; `planner.py:198-232`; `test_masters.py:66-71` | Similarity warning for longer names only; the approver must notice. Creation is not a separate step |
| 4 | Medium-High | Data the user enters (Exporter, Against, Department, Gender, Packing type, Components, Season) is not saved, and no screen says so | P1; `oracle_mapping.json:39-44`; `skipped_columns` unused | Registry notes; the approval graph lists only written columns |
| 5 | Medium | An admin un-verifies a core column (no evidence needed) and BUYER, STYLENO and others silently disappear from new orders; table status for ORDMAS, ORDSTYLE and ORDSIZE does nothing | P2, P10, P11; `main.py:818`; `planner.py:324-339` | Admin-only, audited `mapping_status`; the plan hash changes, so old previews are refused |
| 6 | Medium | Verifying a BOM table without its columns writes key-only rows | P3 | The planner checks fabric data but has no "at least one data column" rule |
| 7 | Medium | Fingerprint reuse maps a file value to a different existing code (leading zeros, punctuation) | P6; `masters.py:23-26` | Warning on the field; no forced acknowledgement |
| 8 | Medium | Oracle committed but the local draft and audit not updated; the user is told nothing was saved | P16; `main.py:596-605`; `workspace.js:677` | The Oracle receipt blocks re-insert; a retry shows "Already saved"; PP_IMPORT_LOG keeps the approver |
| 9 | Medium | Four-eyes off by default and blind to edits: an approver can change quantities on someone else's upload and approve them | `main.py:35, 594-595`; P17 | Edit diffs are audited (`main.py:424-425`) |
| 10 | Medium (latent) | Accessory quantities multiplied by the pack factor because workbook pieces are raw quantities | P12; `planner.py:511-529`; `workbooks.py:831` | ORDACC is reference only today; a warning shows the ratio |
| 11 | Low-Medium | Concurrent master or order creation by Axpert users is not serialised with POPPYS; only primary keys and one foreign key exist in the schema | `IMPLEMENTATION_STATUS.md:134`; `RESEARCH_DECISIONS.md:55` | Re-plan inside the lock narrows the window to the transaction |
| 12 | Low | Post-commit "Verified in Oracle" uses a looser comparison (10-character prefix; numeric-looking strings equal) | P13; `readback.py:6-12`; `oracle_store.py:120-128` | The strict in-transaction read-back already passed |
| 13 | Low | No date range check (1901 and 2099 accepted); ambiguous d/m dates read day-first with a warning only | P8; `workbooks.py:785` | Ship date before order date is refused; warnings shown |
| 14 | Low | Values beginning with `$` skip length checks and break commit with a misleading 404; `$o1`-style text could be replaced by an ID | P15; `planner.py:570-571`; `oracle_store.py:169-170`; `main.py:77-78` | Rollback; no partial write |
| 15 | Low | Duplicate-order detection misses DOCIDs with different internal spacing; DOCID stored un-normalised; the same order under a new reference or file format is not caught | P20; `catalog.py:86`; `IMPLEMENTATION_STATUS.md:97-98` | PP_ORDER_KEYS catches POPPYS re-imports of the same normalised reference |
| 16 | Low | No writer `call_timeout`; master catalogues above 5,000 rows block previews; receipts in PP_IMPORT_LOG can be changed by the POPPYS_APP owner account | `oracle_store.py:27-31`; `catalog.py:75-76`; `bootstrap_local.py:39-45` | Lock `WAIT 10`; fail closed; local SQLite audit as a second record |
