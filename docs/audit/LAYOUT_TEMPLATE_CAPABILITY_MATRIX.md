# Layout templates and intelligent learning: capability matrix (audit area 3)

Repository `/home/user/oracle`, branch `claude/fervent-babbage-jqo8r4`, commit `e811402` (2026-10-18).
Documentation-only audit. No product file was changed. Experiments ran against temporary data directories.

## Summary

- **Storage.** A saved layout is one row in the SQLite `templates` table. The definition, label set, anchors
  and unit are stored as JSON text (`backend/local_store.py:40-41`, migration `:62-68`, API in `:122-187`).
  Versions share a `family` id. Exactly one version per family is active.
- **Matching.** Matching is deterministic and runs per sheet. The fingerprint is a SHA-256 over the set of
  `row:col:normalised-text` labels in rows 1-60, excluding the cells the definition reads as values
  (`backend/workbooks.py:142-185`). An exact hash scores 1.0; otherwise the score is the position-aware
  Jaccard similarity. A candidate needs a score of at least 0.8 (`workbooks.py:934`). Every anchor label must
  be unchanged, and `matcher.check()` must accept the definition (`workbooks.py:927-949`). The function
  `match_template()` at `workbooks.py:206` is never called.
- **What works, tested.** The same buyer's next file (new style, PO, colours) is read automatically in 5
  file formats, and the unit and quantity basis are restored. Changed anchor labels and cells that no longer
  validate are refused with a reason. Saving under the same name creates version n+1 and keeps the history.
  An older version can be re-activated. A unit answered on the review screen is learned once
  (`tests/test_acceptance.py` A/B/D, `tests/test_engine.py` §6 tests; 55 passed and 2 skipped in my runs).
- **Robustness is position-bound** (my experiments). Each of these drops the score below 0.8, or causes a
  refusal:
  - one row inserted above the table (score 0.0);
  - one column inserted at A (0.0);
  - two columns swapped (0.692);
  - two extra info rows (0.733);
  - a renamed heading;
  - an emptied optional value (`B9 is empty`).

  In each case the whole template is dropped and the person re-maps. Below 0.8 nothing tells the user that
  a saved layout almost matched.
- **Buyer is recorded, not used for matching.** `template_score()` ignores `buyer`. In my experiment, buyer
  BLUE BAY's file with ZEN KIDS's structure was read with the ZEN KIDS template, which applied ZEN's
  "packs of 3". The preview showed 360 pieces from 120 source units, with no question asked. After BLUE BAY
  saved its own template for the identical layout, its next file was *still* read with ZEN's template,
  because ties go to the most-used template.
- **Two structures from one buyer** coexist only under different template names. The mapping screen's
  default name is "`<buyer>` layout". Saving the second structure under that default makes it version 2 of
  the first structure's family and turns the first structure off.
- **Learning is limited to layouts and units.** Mapping corrections are not turned into aliases. Aliases are
  global and admin-edited. Buyer-scoped aliases, near-match partial reuse with a diff, and the richer
  fingerprint and spec from `docs/RESEARCH_DECISIONS.md:45-49` are **DOCUMENTED ONLY**. There is no
  drag-and-drop mapping; the screen uses a click-to-pick grid. No UI test covers the template screens.

## 1. Method

| Step | Command / source | Result |
|---|---|---|
| Acceptance + workflow + documents tests | `python3 -m pytest -p no:cacheprovider -q tests/test_acceptance.py tests/test_workflow.py tests/test_documents.py` (with `PYTHONDONTWRITEBYTECODE=1`, `POPPYS_DATA_DIR` set to a scratch dir) | 43 passed, 2 skipped (E/F real-Oracle variants need `POPPYS_TEST_ORACLE=1`) |
| Template tests in the engine suite | `python3 -m pytest -p no:cacheprovider -q tests/test_engine.py -k "template or anchor or unicode_labels or extra_size"` | 12 passed |
| Experiments | Scratch scripts (not committed) that copy the `env` fixture of `tests/test_acceptance.py:52-59`: `local_store.DATA`/`main.DATA` patched to a temp dir, AI offline, `FakeOracle`, `TestClient(main.app)`; workbooks built with `tests/fixtures.py` `zen_kids_po()` plus openpyxl edits | Outputs are quoted below as E0, S1a-S5f, X1-X3 |

No real client files were used, so behaviour on real buyer workbooks is **NOT VERIFIED**. `git status`
was clean after every run.

## 2. Template storage

### 2.1 SQLite table and migration

`CREATE TABLE IF NOT EXISTS templates(id TEXT PRIMARY KEY, name TEXT NOT NULL, signature TEXT NOT NULL,
labels TEXT NOT NULL, definition TEXT NOT NULL, created_by TEXT, created_at TEXT, uses INTEGER DEFAULT 0,
active INTEGER DEFAULT 1)` is defined at `backend/local_store.py:40-41`.

The additive migration at `local_store.py:62-68` adds these columns when they are missing:
- `buyer TEXT NOT NULL DEFAULT ''`
- `version INTEGER NOT NULL DEFAULT 1`
- `family TEXT`
- `anchors TEXT`
- `unit TEXT`
- `quantity_basis TEXT`

It then sets `family=id` where `family` is null. There is no `PRAGMA user_version` and no index or
uniqueness constraint on `name`, `family` or `signature`.

Runtime check (scratch `e_migrate.py`): I created a 9-column table holding one old row and ran `init()`. The
result had 15 columns, and the old row read back as `buyer='', version=1, family='old1', anchors=None,
active=True`.

### 2.2 Every function that reads or writes templates

| Function | Location | Role |
|---|---|---|
| `_template(r)` | `local_store.py:123-128` | Row → dict; JSON-decodes `labels`, `definition`, `anchors`, `unit` |
| `templates(active_only=True)` | `local_store.py:131-136` | Active rows ordered `uses DESC, created_at DESC` (this order breaks ties); `active_only=False` returns full history ordered `lower(name), family, version DESC` |
| `save_template(...)` | `local_store.py:139-155` | Picks the family (§5), sets `active=0` on the whole family, inserts the new active version |
| `template_used(tid)` | `local_store.py:158-160` | `uses += 1`; called once per upload that applied it (`main.py:362-363`) |
| `fill_template_unit(tid, unit, basis)` | `local_store.py:163-176` | Fills `unit` / `quantity_basis` only when empty ("a saved decision is never overwritten") |
| `set_template_active(tid, active)` | `local_store.py:179-187` | Activating one version deactivates the others in its family; `KeyError` when the id is unknown |
| `upload()` | `main.py:351-376` | Reads `store.templates()`, increments uses, sets `draft.template` |
| `mapping()` | `main.py:481-513` | Validates, applies, and with `save_template` calls `save_template` and audits |
| `_learn_unit()` | `main.py:449-458` | On `PUT /api/drafts/{id}` (`main.py:426`), learns the unit for `draft.template` |
| `get_templates()` / `template_active()` | `main.py:833-836`, `:886-890` | List all versions / toggle active |
| `template_score`, `choose_template`, `anchor_problems`, `restore_from_template`, `apply_template` | `workbooks.py:194-203, 927-949, 911-924, 952-980, 1274-1290` | Matching and application (§3) |
| `matcher.anchors()` / `matcher.check()` | `matcher.py:529-543` / `:369-405` | Anchor capture at save time / validator at apply time |

No function deletes, renames or edits a stored template. `grep -rn "DELETE FROM templates" backend/` finds
nothing.

### 2.3 JSON shape (from `GET /api/templates`, experiment E0, signature shortened)

```json
{"id": "…32 hex…", "name": "ZEN KIDS PO", "buyer": "ZEN KIDS", "version": 1, "family": "<id of v1>",
 "signature": "063fba584e8e5be9…",
 "labels": ["11:1:colour","11:2:remark","11:7:total","1:1:zen kids ltd","2:1:purchase order","4:1:customer",
            "5:1:article no","6:1:description","7:1:po no","8:1:ship date","9:1:season"],
 "definition": {"fields": {"buyer":"B4","style":"B5","description":"B6","po_number":"B7","ship_date":"B8","season":"B9"},
                "table": {"type":"matrix","header_row":11,"color_col":1,"first_size_col":3,"last_size_col":6,"total_col":7}},
 "anchors": {"buyer": {"cell":"B4","label_cell":"A4","label":"Customer"}, "...": "...",
             "table.color_col": {"cell":"A11","label_cell":"A11","label":"Colour"},
             "table.total_col": {"cell":"G11","label_cell":"G11","label":"Total"}},
 "unit": {"qty_unit":"PACK","pieces_per_unit":3}, "quantity_basis": "sample",
 "created_by": "admin", "created_at": "…", "uses": 0, "active": true}
```

The definition takes one of two forms (validated by `workbooks.validate_definition`, `workbooks.py:1212-1234`):
- **flat:** `{type:'flat', header_row, columns:{style?, size, quantity, color?, …}}`, where style is either a
  column or `fields.style`;
- **matrix:** `{type:'matrix', header_row, first_size_col, last_size_col, label_col?, color_col?, total_col?}`.

`fields` maps a header-field key to an A1 cell. Anchors cover every header field, plus `table.<column>` for
flat columns or a matrix's label, colour and total headings (`matcher.py:529-543`). Size headings are not
anchored.

## 3. Matching algorithm

1. **Order per sheet.** `parse_sheets()` (`workbooks.py:983-1059`) skips hidden sheets. It then tries saved
   templates, the built-in merch BOM detector, the buyer-contract detector and the alias-matched flat table
   (`flat_headers`), in that order. If none of them applies, the format is `Needs column mapping`. Only
   **active** templates are candidates (`main.py:360` calls `store.templates()`, which defaults to
   `active_only=True`).
2. **Score.** `template_score(sheet, t)` (`workbooks.py:194-203`) works as follows:
   - For a template with anchors, it recomputes `layout_labels(sheet, t['definition'])` on the *new* sheet,
     using the *template's* definition to decide which cells to skip.
   - It returns 1.0 if `sha256('|'.join(sorted(labels)))` equals the stored signature.
   - Otherwise it returns `similarity()`, the Jaccard similarity of the two label sets (`:188-191`).
   - Legacy templates (`anchors is None`) use `labels(sheet)` over the whole sheet.
   - A sheet with no labels scores 0.0 (tested: `tests/test_engine.py::test_unicode_labels_keep_buyer_templates_apart`).
3. **Threshold, ranking, ties.** Candidates are sorted by score with a stable `sorted` (`workbooks.py:932`).
   The loop stops at the first score below 0.8 (`:934`). Equal scores keep the order from
   `store.templates()`: most `uses` first, then newest `created_at`. `created_at` has one-second resolution
   (`local_store.now()`). Experiment TIES saved two families with the same signature in the same second. The
   first one won, gained a use, and from then on always won.
4. **Gates per candidate.** These run in this order:
   - `anchor_problems()` (`workbooks.py:911-924`): each anchor's label cell must hold the same `norm()` text.
     Case and punctuation differences are tolerated (X1: "ARTICLE NO." matched "Article No"). An anchor whose
     `label_cell` is `None` is not checked.
   - `matcher.check()` (`matcher.py:369-405`): cells must not be empty, must have the right type, must not be
     a label, must not be used twice; quantity columns must be numeric; size headings must look like sizes;
     an adjacent extra size column is refused.
   - `apply_template()` must not raise.

   The first candidate that passes all three is applied. A failing candidate is recorded in
   `templates_refused` (`:948`) with its reasons, and the next candidate is tried.
5. **After a match.**
   - `restore_from_template()` (`workbooks.py:952-980`) restores `qty_unit` / `pieces_per_unit` /
     `quantity_basis` with provenance `reason:'template'`. When the file itself states a different unit, the
     unit is blanked and asked instead (tested: `test_file_evidence_that_contradicts_the_template_is_asked`).
   - A warning "Read with the saved layout “X” (version n)." is added.
   - `format` gets `Template: <name>`. `uses` is incremented (`main.py:362-363`). `draft.template` is set to
     the *first* used template in store order (`main.py:366`). In experiment S1c, where two templates were
     used on two sheets, `draft.template` named only one of them.
6. **After a refusal.** Each refusal becomes a note: "Saved layout … was not used on sheet …: <reasons>. The
   file was read as a new layout." (`workbooks.py:993-995`). The matcher suggestion is computed
   (`main.py:365`). **Templates scoring below 0.8 leave no trace.** The draft looks like a brand-new layout
   (S3b, S3c, S3e, S3h, S5e).

### 3.1 Fingerprint inputs and robustness

`labels()` (`workbooks.py:142-152`) keeps string cells with these properties:
- in rows ≤ 60;
- non-numeric and not ISO dates;
- normalised length 2-40;
- digits making up no more than ⅓ of the characters.

Each kept cell is stored as `row:col:text`. `layout_labels()` (`:163-181`) also removes:
- cells referenced in `definition.fields`;
- every row below the table header row;
- for a matrix, the size headings in `first_size_col..last_size_col`.

| Input | In the fingerprint? | Evidence |
|---|---|---|
| Label text (normalised) | Yes | `workbooks.py:149-151` |
| Absolute row and column of each label | Yes, so any shift changes every key | `workbooks.py:151`; S3b/S3c score 0.0 |
| Title / company text near the top | Yes (e.g. `1:1:zen kids ltd`) | E0 labels |
| Short free-text values not mapped by the definition | Yes (they look like labels) | S3g adds `10:2:girls` |
| Sheet name | No | S3i: sheet renamed to "Order 2027" still matched (score 1.0) |
| Other sheets / sheet count | No (each sheet is matched alone) | `workbooks.py:986-992`; S3i, S1c |
| Column count, header row index, buyer | No (only implicitly through positions) | `workbooks.py:159-160` |
| Hidden rows/columns | Yes. `labels()` has no hidden filter, although `Grid` ignores hidden cells | X2: a hidden row with text lowered the score to 0.917 |

Because Jaccard is computed over small sets, each label weighs a lot. The ZEN layout has 11 labels, so one
added label pair scores 0.846 and two pairs score 0.733.

## 4. Version handling

- **New version or new family.** In `save_template()` (`local_store.py:144-148`), the save becomes the next
  version of an existing family when either condition holds:
  - the **name matches case-insensitively**, regardless of buyer; or
  - the **signature is equal and the buyer is non-empty and equal** case-insensitively.

  When both conditions point at different families, the family with the higher max version wins. Otherwise
  the save starts a new family, with `family = id` and `version = 1`. The new version is always inserted as
  active, and every other version in the family is set to `active=0` (`:149`). Nothing is updated in place,
  so old versions stay as inactive history.

  Tests:
  - `tests/test_engine.py::test_template_versions_by_name_or_signature_and_buyer`: the same name with a
    different buyer joins the family, so a family can mix buyers.
  - `tests/test_acceptance.py::test_A_saving_the_layout_again_creates_a_new_version`: v1 inactive, v2 active,
    the next file uses v2's packs of 6.
- **Rollback and deactivation.** `POST /api/templates/{id}/active` (`main.py:886-890`, requires the
  `templates` permission, which only admin has, `main.py:29-34`) calls `set_template_active`. In S3k, v2 was
  saved, v1 was re-activated, and the next file was read with v1 (packs of 3, season back). Turning v1 off
  left the family with no active version, and the next file showed `Needs column mapping`.
- **Admin UI.** `frontend/js/admin.js:85-139` groups rows by `family` and shows the active version (or the
  newest one). The other versions sit behind a "History (n earlier)" toggle. The screen shows:
  - "Recognises" (anchor labels, or legacy label texts);
  - buyer, uses per version and across versions, unit, author and date;
  - an "older format" pill for anchor-less templates;
  - an In use / Off switch, disabled without `can('templates')`.

  The section is visible to every role (`frontend/index.html:148` has no `data-permission`). There is no
  rename, delete, definition view or diff. **NOT VERIFIED** in a browser (no UI test exists; `tests/test_ci.py`
  only lints JS).
- **Mapping panel.**
  - `frontend/js/mapping.js:363-368` sets the default template name to the refused template's name, or else
    "`<buyer cell value>` layout".
  - `drawVersion()` (`:380-391`) warns "Updates “X”: saved as version n+1 … Version n stays in the history".
    It compares names only; it does not use the signature-and-buyer rule.
- **"Read this file differently".** `frontend/js/workspace.js:156` shows the button when orders exist, and
  `:549` opens the mapping panel. `renderMapping` (`mapping.js:22-35`) starts from `draft.suggestion`, or
  else from an empty definition, and then asks the matcher (`autoPropose`, `:39-44`). `upload` does not
  compute a suggestion when a template applied cleanly (`main.py:365`), so **the panel never shows the saved
  template's definition for editing.** The person rebuilds the mapping from the matcher's proposal (code
  reading; not checked in a UI).

## 5. Buyer association

- On save, `buyer` is the single distinct non-empty `buyer` value of the mapped orders, or `''` when there
  are none or several (`main.py:503-506`). It is raw file text (whitespace collapsed,
  `local_store.py:142`), not checked against BUYMAS.
- `buyer` is used **only** to choose the family when saving (§4). It plays no part in
  `template_score`/`choose_template` (`workbooks.py:194-203, 927-949`), and it is not compared with the
  buyer read from the new file. S2a shows the result: the BLUE BAY order got the warning "Read with the saved
  layout “ZEN KIDS PO”", and its unit provenance says "Confirmed for the saved layout “ZEN KIDS PO”".
- Aliases (`field_settings`, `local_store.py:51`) have no buyer column, so they are global.

## 6. Reuse workflow end to end

| Step | Endpoint / code | Audit entry |
|---|---|---|
| Upload | `POST /api/uploads` (`main.py:351-376`, permission `upload`): parse with active templates; `uses+1`; `draft.template`, `templates_refused`, `notes`, `suggestion` | `upload` {filename, format (e.g. `Template: ZEN KIDS PO`), orders, sha256} |
| Propose (unknown or refused layout) | Matcher at upload (`main.py:334-348`, `use_ai=False`); `POST /api/drafts/{id}/suggest` (`main.py:535-540`) adds the local AI when available | none |
| Confirm a mapping | `POST /api/drafts/{id}/map` (`main.py:481-513`, permission `edit` = operator, approver, admin), body `{version, sheet, definition, save_template, template_name≤80, qty_unit?, pieces_per_unit?, quantity_basis?}`. A 409 lists each wrong field and changes nothing. On success, the draft's orders are **replaced** by those of that one sheet | `mapping_applied` {sheet, definition}; with save: `template_saved` {name, version, family, definition, unit, quantity_basis} |
| Review edits | `PUT /api/drafts/{id}` (`main.py:412-427`) → `_learn_unit` | `edit`; `template_unit_learned` {draft, unit?, quantity_basis?} |
| List | `GET /api/templates` (`main.py:833-836`, any signed-in user) | none |
| Activate / deactivate / rollback | `POST /api/templates/{id}/active` `{active}` (`main.py:886-890`, admin) | `template_active` {active} |

No endpoint deletes, renames, exports or imports templates, or patches a template's definition. Refusals
and template choices are not audited as separate events; they appear only in draft notes and in the upload
`format`. Templates live in `poppys.sqlite3`, which the backup archive includes
(`tests/test_backup.py:98`).

## 7. Scenarios (actual behaviour)

### Scenario 1: one buyer sends several structures

- **Observed.** S1a: with only structure 1 ("ZEN KIDS PO", matrix, labels in A:B) saved, structure 2 (labels
  in D:E, matrix at row 8) came back as `Needs column mapping`. That is correct; no refusal note. Saving
  structure 2 as "ZEN KIDS ORDER SHEET" created a second family; both were active. Re-uploads of each
  structure were read with their own template, and each kept its own unit (structure 1 PACK×3 sample,
  structure 2 PCS). S1c: one workbook with both structures on two sheets produced 2 orders (ZK-21, ZK-22),
  with format `Template: ZEN KIDS PO, Template: ZEN KIDS ORDER SHEET`.
- **S1b: name collision.** Structure 1 was saved as "ZEN KIDS layout" (the UI default) and structure 2 under
  the same default name. Templates became `[("ZEN KIDS layout", v2, active), ("ZEN KIDS layout", v1, inactive)]`,
  and the next structure-1 file showed `Needs column mapping` with no note. The UI does show "Updates “ZEN
  KIDS layout”: saved as version 2" (`mapping.js:389`), but that text does not say the other structure will
  stop being recognised.
- **S1d: known sheet plus unknown sheet.** In one workbook, sheet PO matched and sheet ORDER was unknown. The
  draft held 1 order (ZK-31), `notes: []`, and no suggestion. The ORDER sheet was skipped with no message,
  and the UI hides the mapping panel because orders exist (`workspace.js:149`). Mapping ORDER through `/map`
  then left only ZK-32: the PO sheet's order was dropped (`main.py:495-499`).
- **Status.** Multiple templates per buyer: IMPLEMENTED (experiments S1a/S1c; no suite test).
- **Risk: high.**
  - The default name silently retires a structure.
  - An unknown sheet beside a known one is skipped without a note.
  - `/map` keeps one sheet only.

### Scenario 2: another buyer uses a structure seen before

- **Observed.**
  - S2a: a BLUE BAY file built like ZEN KIDS (own title "BLUE BAY LTD") scored 0.833 against the ZEN
    template and was read with it. Buyer and style were read from the new file (`BLUE BAY`, `BB-1`), with no
    data leak. ZEN's unit was applied: PACK×3, sample.
  - Preview (`w2a.py`) gave `pieces: 360` for 120 source units, with no unit question and no error. The only
    hint was the warning "Read with the saved layout “ZEN KIDS PO” (version 1)."
  - S2b: with a generic title (identical signature), BLUE BAY saved its own template ("BLUE BAY PO", PCS,
    order_demo) and got a separate family. Its next file was still read with "ZEN KIDS PO" (uses 1 vs 0) and
    got PACK×3.
  - The B tests show that when one anchor label or the label positions differ, the template is not applied
    and nothing leaks (`test_B_new_buyer_gets_no_template_and_no_data_from_another_buyer[moved-and-renamed |
    one-anchor-renamed]`, passed).
- **Status.**
  - Layout reuse across buyers: happens automatically (IMPLEMENTED, unintended).
  - Buyer-specific selection: NOT IMPLEMENTED.
  - Isolation when labels differ: IMPLEMENTED (test B).
- **Risk: high.** A unit or basis confirmed for one buyer can multiply another buyer's quantities without a
  question. A buyer's own template can be permanently shadowed.

### Scenario 3: columns rearranged or renamed

**In the Excel file** (template "ZEN KIDS PO"):

| Change | Score | Result |
|---|---|---|
| S3a rename A5 "Article No" → "Style No" | 0.833 | Refused: "A5 now says “Style No”, not “Article No”"; suggestion offered |
| X1 "Article No" → "ARTICLE NO." | 1.0 | Applied (normalisation) |
| S3b insert a row at top | 0.0 | Not considered, no note; matcher proposes header row 12 |
| S3c insert a column at A | 0.0 | Not considered, no note; matcher proposes shifted columns |
| S3d size headings reversed (XL..S) with values | 1.0 | Applied; lines read by heading: XL 40, L 30, M 20, S 10 (correct) |
| S3e swap Colour/Remark columns | 0.692 | Not considered, no note |
| S3f extra "Notes" column right of table | 0.917 | Applied; extra column ignored |
| S3j flat template, non-alias headings reordered | 0.2 | Not considered; needs mapping |
| S3j flat heading "Units" → "Qty Units" | <0.8 | Not considered; needs mapping |
| X3 flat template, alias headings (Style/Colour/Size/Qty) reordered | <0.8 | Built-in `Tabular orders` reads the data correctly, but the template's PACK×2 is not restored, so the unit is asked again |

**In the UI.** In S3k, POST `/map` on a template-read draft:
- With `save_template=false`, the draft becomes `Reviewed layout mapping` with `template=null`, and the
  stored template is unchanged.
- With `save_template=true` and the same name, v2 is created and active, v1 is inactive (uses 2), and the
  next file uses v2.

Rollback works as described in §4. There is no "edit the saved mapping" mode (§4, "Read this file
differently").

**Status.**
- Reordering of size headings within a matrix: IMPLEMENTED (S3d).
- Reordering of anchored columns: NOT IMPLEMENTED.
- Header rename: refused by design.
- UI re-mapping with versioning: IMPLEMENTED (test A versioning, S3k).

**Risk: medium.** Behaviour fails safe: no wrong data was read in any variant. The cost is repeated manual
mapping and silent loss of the remembered unit. Template families multiply, because a moved layout gets a
new default name instead of a version.

### Scenario 4: a new required field appears

- **In the file.** S4a: the file gained "Department / GIRLS" at A10:B10. The score was 0.846 and the template
  applied. `department` stayed `''` and no note mentioned the new labelled value. A template only reads the
  fields in its definition; `header_fields()` gap-filling runs only on the `Tabular orders` path
  (`workbooks.py:1036`).
- **Admin marks the field required** (`PUT /api/fields/department {required:true}` → 200). The same file was
  still read with the template and was not refused. Preview then raised
  `orders.0.department: "Order 1: Department is required for every order — enter it."`
  (`main.py:551-554` passes `extra_required` to the planner, `erp/planner.py:277-280`). The value then has to
  be typed by hand, or the layout re-mapped and saved as a new version. Templates are not re-validated or
  flagged when the field list changes.
- **Tests.** `tests/test_engine.py::test_admin_required_field_is_checked_by_the_planner` (planner gate),
  plus my experiments.
- **Status.**
  - Enforcement at preview: IMPLEMENTED.
  - Detection of new fields in a template-read file: NOT IMPLEMENTED.
  - Flagging templates that lack a newly required field: NOT IMPLEMENTED.
- **Risk: medium.** Saving is blocked, so nothing wrong reaches Oracle. The value is in the file but is
  ignored, and the user gets no pointer to it.

### Scenario 5: a source column or value disappears

| Change | Score | Result |
|---|---|---|
| S5a delete Remark column B (shift left) | 0.818 | Refused: "G11 is empty; it said “Total”" |
| S5b clear Total column G (no shift) | 0.909 | Refused: "G11 is empty; it said “Total”" |
| S5c Season value B9 empty (optional field) | 1.0 | Refused: "B9 is empty" |
| S5d Season label and value cleared | 0.909 | Refused: "A9 is empty; it said “Season”" |
| S5e delete Season row (shift up) | 0.438 | Not considered, no note; matcher proposes header row 10 |
| S5f delete XL size column (Total shifts) | 0.909 | Refused: "G11 is empty; it said “Total”" |

Suite evidence: `test_template_whose_cells_no_longer_validate_is_refused` (B8 "TBC" is not a date) and
`test_file_evidence_that_contradicts_the_template_is_asked` (G11 renamed).

**Status.**
- Detecting a missing mapped column or label: IMPLEMENTED.
- Tolerating a missing optional value or column: NOT IMPLEMENTED. `check()` rejects any empty mapped cell
  (`matcher.py:388-389`), and one rejection drops the whole template.

**Risk: medium.** This fails safe. But a buyer who leaves Season blank on a PO forces a full re-mapping. If
that re-mapping is saved under the same name, it becomes the active version and drops Season for later
files.

## 8. Capability matrix

| Capability | Status | Evidence | Notes |
|---|---|---|---|
| Store a confirmed layout as a template | IMPLEMENTED | `local_store.py:139-155`; `test_A_known_buyer…`; E0 | JSON in SQLite; raw buyer text |
| Auto-detect a known layout (same buyer, new values) | IMPLEMENTED | `test_A_known_buyer…[xlsx,xls,csv,xml2003,html-export]`; `test_workflow.py::test_mapping_new_layout_saves_template_for_next_upload` | Values are skipped from the signature |
| Template carries layout only, never data | IMPLEMENTED | `test_A` leak assertion; `layout_labels()` `workbooks.py:163-181` | Title text is part of the layout |
| Validate the definition before applying | IMPLEMENTED | `workbooks.py:936-939`; `test_template_whose_cells_no_longer_validate_is_refused` | Same `check()` as for person and AI mappings |
| Refuse on a changed anchor label, with reason | IMPLEMENTED | `workbooks.py:911-924`; `test_changed_anchor_label_refuses_the_template_and_records_why`; S3a | Case and punctuation tolerated (X1) |
| Restore unit and quantity basis | IMPLEMENTED | `workbooks.py:952-980`; `test_template_signature_ignores_values_and_restores_unit_and_basis` | File evidence wins, else asked |
| Tolerate column reorder | PARTIALLY IMPLEMENTED | S3d works (matrix sizes); S3e, S3j fail | Only size headings are position-free |
| Tolerate header rename | NOT IMPLEMENTED | S3a; S3j | Deliberate refusal; no rename mapping |
| Tolerate inserted rows/columns above or left of table | NOT IMPLEMENTED | S3b, S3c, S5e (scores 0.0-0.44) | Positions are part of every label key |
| Tolerate extra columns | PARTIALLY IMPLEMENTED | S3f (right of table) works; S3c (at A) fails | Adjacent extra size column refused (`test_matrix_template_never_drops_an_extra_size_column`) |
| Tolerate extra info rows | PARTIALLY IMPLEMENTED | S3g (one pair, 0.846) works; S3h (two pairs, 0.733) fails | Fixed 0.8 threshold over small label sets |
| Tolerate a missing optional column or value | NOT IMPLEMENTED | S5b, S5c, S5d | An empty mapped cell refuses the template |
| Detect a missing mapped column or label | IMPLEMENTED | S5a, S5b, S5f; suite tests above | Only when the score is ≥ 0.8 |
| Tell the user that a saved layout almost matched (score < 0.8) | NOT IMPLEMENTED | S3b/S3c/S3e/S3h/S5e: `refused: []`, `notes: []` | Docs §6 says "the reason is recorded"; true only for ≥ 0.8 |
| Detect a new field in a template-read file | NOT IMPLEMENTED | S4a | Value ignored silently |
| Enforce admin-required fields on template drafts | IMPLEMENTED | S4b; `test_admin_required_field_is_checked_by_the_planner` | At preview, not at match time |
| Multiple templates per buyer | IMPLEMENTED | S1a, S1c (experiments only) | Needs distinct names (S1b) |
| Buyer-specific choice between identical layouts | NOT IMPLEMENTED | S2b; `workbooks.py:194-203` ignores buyer | Most-used template wins ties |
| Template reused by another buyer | IMPLEMENTED (unintended, risky) | S2a, `w2a.py` (360 pcs from 120) | Unit of buyer A applied to buyer B |
| Versioning (n+1, history kept) | IMPLEMENTED | `test_A_saving_the_layout_again_creates_a_new_version`; `test_template_versions_by_name_or_signature_and_buyer`; S3k | Same name joins a family across buyers |
| Rollback to an older version | IMPLEMENTED | `set_template_active` test (`test_engine.py:277`); S3k via HTTP | Admin only; non-admin denial NOT VERIFIED |
| Deactivate a template | IMPLEMENTED | S3k (all off → needs mapping) | UI switch NOT VERIFIED |
| Delete, rename, or edit a saved definition | NOT IMPLEMENTED | No endpoint (`main.py` routes) | Re-map, then save as a new version |
| Edit starting from the saved template in the UI | NOT IMPLEMENTED | `mapping.js:22-44`, `main.py:365` | Panel starts from matcher proposals |
| Version diff or explanation of changes | NOT IMPLEMENTED | `admin.js:105-139` | — |
| Learn unit / basis from review answers | IMPLEMENTED | `test_unit_answered_on_review_is_learned_by_the_template` | Fills only when empty |
| Learn aliases from user corrections | DOCUMENTED ONLY | `RESEARCH_DECISIONS.md:49`; `edit()` `main.py:412-427` | Aliases are admin-edited and global (`test_alias_customisation_changes_parsing_without_global_state`) |
| Buyer-scoped aliases / buyer history layer | DOCUMENTED ONLY | `RESEARCH_DECISIONS.md:46,49`; `field_settings` has no buyer column | — |
| Near-match partial reuse with diff | DOCUMENTED ONLY | `RESEARCH_DECISIONS.md:49,114` | Code refuses the whole template |
| Deterministic first-mapping proposal | IMPLEMENTED | `matcher.suggest` `matcher.py:566-587`; `test_B…` | Runs at upload, offline |
| AI-assisted first mapping (pipeline and cross-check) | IMPLEMENTED | `ai.py:99-123`; `test_D_ai_proposal_is_cross_checked_and_wrong_fields_rejected` (stubbed model) | On request only, not at upload |
| AI proposal quality with real Ollama | NOT VERIFIED | Needs the laptop's model | — |
| Visual drag-and-drop mapping | NOT IMPLEMENTED | `grep drag frontend/js` finds only the upload dropzone and graph pan | Click-to-pick grid exists (`mapping.js:167-190`), NOT VERIFIED |
| Per-field confidence | IMPLEMENTED (proposals) | `matcher.py:130-165`; `test_B` asserts confidence and evidence; UI chip `mapping.js:232-238` | Template-applied values carry provenance cells, no confidence |
| Explain why a template matched | PARTIALLY IMPLEMENTED | Applied: name and version only (`workbooks.py:979`); refused: reasons (`:993-995`) | No score or matched labels shown |
| Templates per sheet in multi-sheet workbooks | IMPLEMENTED | S1c | Unknown sibling sheet skipped silently (S1d) |
| Sheet-name independence | IMPLEMENTED | S3i | — |
| Legacy (pre-anchor) templates migrate | IMPLEMENTED | `e_migrate.py` | They match the next file poorly (0.684) |
| Audit of template actions | PARTIALLY IMPLEMENTED | `template_saved`, `template_active`, `template_unit_learned` (E0, `test_unit_answered…`) | No event for "applied" or "refused" beyond the upload `format` |
| Templates included in backups | IMPLEMENTED | `tests/test_backup.py:98` (`data/poppys.sqlite3` in archive) | Whole-DB granularity |

## 9. Docs versus code

| Doc claim | Code reality | Label |
|---|---|---|
| EXCEL_UNDERSTANDING §6 (`docs/EXCEL_UNDERSTANDING.md:156-177`): template shape, versioning rule, ≥ 0.8 plus anchors plus validator, unit restore, layout-only signature, refusal note text, unit learning | Matches `local_store.py:139-187` and `workbooks.py:194-203, 911-980`; tests pass | IMPLEMENTED |
| §6: "If any check fails … the reason is recorded in the draft notes" | True only for candidates scoring ≥ 0.8; below that nothing is recorded (S3b/S3e/S5e) | PARTIALLY IMPLEMENTED |
| §1 "Remember per buyer" | Buyer stored but not used in matching (S2a/S2b) | PARTIALLY IMPLEMENTED |
| RESEARCH_DECISIONS:49 fingerprint = SHA-256(header band in order + column count + sheet-name pattern + header row index + buyer) | SHA-256 of sorted `row:col:text` labels; no sheet name, column count or buyer | DOCUMENTED ONLY |
| :49 immutable spec with `parent_version`, `approved_by/at`, `matcher_version`, `dictionary_version`, `source_file_sha256` | Table has none of these columns (PRAGMA in E0); rows are immutable apart from `uses`, `active`, and the one-time `unit`/`basis` fill | DOCUMENTED ONLY |
| :49 exact hit "still validates and previews", with a banner | `check()` runs; banner "Read with your saved layout … (version n)" (`workspace.js:85,154`) | IMPLEMENTED (UI text NOT VERIFIED) |
| :49 near match ≥ 0.8 reuses unchanged columns, rematches changed ones, highlights the diff | All-or-nothing refusal; no diff | DOCUMENTED ONLY |
| :49 correction becomes a buyer alias; global alias after admin approval or 3 buyers | Global aliases edited by an admin only (`main.py:862-881`) | DOCUMENTED ONLY |
| :45 name score blend with RapidFuzz; score 0.45·name + 0.25·master + 0.20·type + 0.10·context | `matcher.py:153` uses `0.6·name + 0.4·value`; no RapidFuzz in `requirements.txt` | DOCUMENTED ONLY |
| :46 Hungarian 1:1 assignment, AUTO/REVIEW/UNMAPPED bands, layer precedence template → buyer history → global history → … | Greedy per-field proposals with high/medium/low at 0.85/0.6 (`matcher.py:23,130-131`); no history layers | DOCUMENTED ONLY |
| :67 mapping table UI with "From template" chip and value-mapping step | Cell-picking layout check; no per-column "From template" chip; no value-mapping step found in `mapping.js` | DOCUMENTED ONLY |
| `docs/IMPLEMENTATION_STATUS.md:39` "saved templates … Implemented, Cloud-tested (partly)" | Consistent with the tests; robustness limits are not stated there | — |

## 10. Main risks, ranked

1. **Cross-buyer unit carry-over** (S2a, S2b; high). The template choice ignores the buyer read from the
   file, so a confirmed pack size can multiply another buyer's quantities. The preview showed 360 pieces for
   120 source units with no question. A buyer's own template for an identical layout can be shadowed for
   good, because ties go to `uses DESC`.
2. **Default-name collision retires a structure** (S1b; high for buyers with several formats). The same name
   always means "new version", even when the signature is completely different.
3. **Silent partial reads in multi-sheet workbooks** (S1d; high). An unknown sheet next to a matched one
   gets no note, and `/map` replaces every order with one sheet's orders.
4. **Brittle fingerprint** (S3b/S3c/S3e/S3h/S5c/S5e; medium). Any row or column shift, a blank optional
   value, or two extra info rows drops the template. Below 0.8 there is no "almost matched" message. Each
   re-mapping risks a duplicate family or loses the remembered unit (X3).
5. **New file fields are ignored** (S4a; medium). A template reads only its definition's cells. A newly
   required field is caught at preview, not at reading.
6. **No template maintenance surface** (low-medium). There is no rename, delete, definition view, diff, or
   "edit the saved mapping". Hidden cells count in the signature (X2), and `match_template()`
   (`workbooks.py:206`) is dead code next to the live hard-coded 0.8 (`workbooks.py:934`).

Behaviour on the client's real buyer files, and with the real local AI model, is **NOT VERIFIED**. These
need the client laptop.
