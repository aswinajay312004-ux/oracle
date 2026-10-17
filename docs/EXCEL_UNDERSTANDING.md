# Excel understanding engine

How POPPYS AI turns any buyer's Excel file into a checked Axpert order with the least manual work,
and how it refuses to guess. This document is both the product description and the implementation
contract (API shapes, rules and acceptance tests).

## 1. Pipeline

```
upload ─► readers (any Excel format / JSON → one cell model)            backend/readers.py
       ─► known layout?  saved buyer template (versioned)               backend/workbooks.py + local_store templates
       ─► built-in detectors: merch BOM, buyer contract, flat table      backend/workbooks.py
       ─► unknown layout: field matcher proposes, validator checks,     backend/matcher.py
          local AI (optional) proposes, cross-checked by the matcher    backend/ai.py
       ─► person confirms once → saved as that buyer's template
       ─► order draft (every value keeps its source cell)
       ─► review screen: only missing/doubtful fields are outlined red
       ─► planner checks Oracle rules → approval → one transaction → read-back
```

Principles (from the research in `docs/RESEARCH_DECISIONS.md`; the earlier survey is `docs/OPEN_SOURCE_RESEARCH.md`):

* **Deterministic first, AI second.** A matcher that explains every score (names + values) runs on
  every unknown layout. The local AI may add proposals; every AI proposal is validated and compared
  with the matcher. Neither can save anything: a person confirms the layout once.
* **Validate meaning, not only position.** A date field must hold a date, a quantity column must hold
  numbers, a size header must look like sizes. Wrong proposals are rejected with a reason
  (acceptance test D).
* **Remember per buyer (metadata, not code).** A confirmed layout becomes a versioned template with
  the buyer name, field anchors (cell + the label text next to it), table definition and the unit
  decision. The next file with that layout needs no questions (acceptance test A).
* **Never carry data over.** A template is applied only when the layout matches *and* each anchor's
  label still says what it said when the template was saved. Otherwise the file is treated as new
  (acceptance test B).
* **Ask, don't assume, when the file is silent.** If the file does not say whether quantities are
  pieces or packs, the unit field is outlined red with that question (acceptance test C).

### 1a. Readers and the cell model

`backend/readers.py` turns every accepted file into the same list of sheets. The format is decided from the
file's bytes (OLE2, ZIP parts, XML namespaces, HTML/MHTML markers, JSON, text), never from the extension alone:

| Format | Reader | Reads |
|--------|--------|-------|
| `.xlsx .xlsm .xltx .xltm` | openpyxl; python-calamine for Strict OOXML or damaged files | values, cached formula results, number formats, hidden rows/columns/sheets, merged cells |
| `.xls` (BIFF2–8) | xlrd (lenient mode for damaged files), then python-calamine | values, dates, number formats, hidden rows/columns, merged cells |
| `.xlsb` | python-calamine | values, dates, sheet visibility |
| `.ods` | python-calamine plus a bounded `content.xml` pass | values, merges, hidden rows |
| Excel 2003 XML | defusedxml | values, styles' number formats, merges, hidden rows/columns |
| HTML / MHTML "Excel" | html.parser (+ email for MHTML) | the table as shown, row and column spans |
| `.csv .tsv .txt` | csv with encoding detection (BOM, UTF-8, UTF-16, charset-normalizer) and delimiter sniffing (`sep=` line first) | text; numbers and dates as the file writes them |
| `.json` | json with duplicate-key, NaN/Infinity, depth and digit-count checks | records, row arrays, sheet maps, NDJSON, envelopes such as `{"data": [...]}`, nested documents (an object of plain values becomes columns; one inner list of records becomes one row per inner record, with the outer values repeated) |

Cell model: `{cell, row, col, value, formula, hidden, error}` plus, only when they say something, `fmt` (number
format), `raw` (the stored number when `value` is the text Excel shows, `8` formatted `0000` → `'0008'`) and
`flag`:
- `decimal_comma`: text such as `1.234,56`, or in a decimal-comma file `2.000`. It is kept as text, never read as a number.
- `scientific`: a code Excel already turned into `1.23457E+11`.
- `big_number`: 16 or more digits, kept as text so no digit is lost.

Each sheet also carries `capabilities`, saying whether its reader could see merges, hidden rows and columns,
number formats and formulas, so a note can say what a format cannot show.

Refused with what to do, never guessed:
- password-protected or IRM workbooks;
- other Office documents;
- files over 12 MB, 30 sheets, 250,000 cells per sheet or 400,000 per file;
- zip bombs;
- JSON nested deeper than 20 levels.

Every upload is parsed in a separate worker process (60 s, about 1.5 GB). A reader package missing on the
computer is reported as such ("run setup.ps1 again"), not as a damaged file.

## 2. Business fields (customisable)

`backend/fields.py` holds the canonical fields. Each field: `key`, `label`, `kind`
(`text`, `code`, `date`, `number`, `size`, `quantity`, `colour`, `unit`), `where` (`header` or
`table`), default `aliases` (header words seen in buyer files), `locked` (needed by Axpert, always
required) and `required`.

| key | label | kind | where | Axpert-locked |
|-----|-------|------|-------|---------------|
| buyer | Buyer | text | header (or table column) | yes |
| style | Style # | code | header or column | yes |
| description | Description | text | header | yes |
| po_number | PO / Contract No | code | header or column | yes |
| order_ref | OC No / Order reference | code | header | yes |
| order_date | Ord Confm Dt | date | header | yes |
| ship_date | Ship Dt | date | header or column | yes |
| sample_type | Sample type | text | header | yes (has default) |
| exporter | Exporter | text | header | yes (has default) |
| season, merch, department, gender, against, packing_type, components | … | text/number | header | no — admin may make them required |
| size | Size | size | table | — |
| quantity | Quantity | quantity | table | — |
| color | Colour / combo | colour | table | — |
| qty_unit | Quantity unit (pieces / packs) | unit | header or quantity header text | yes (must be decided) |

Admin (Setup → **Order fields**) can, per field: add or remove buyer aliases ("Article No", "Ref"),
and switch *required* on for unlocked fields. Changes are audited. An alias may belong to one field
only. Effective fields = defaults + saved changes; the parser, matcher, validator, review screen and
planner all use the effective fields.

API:

* `GET /api/fields` → `[{key, label, kind, where, aliases, default_aliases, locked, required, column,
  changed_by, changed_at}]` (`column`: the field can also be a flat-table column)
* `PUT /api/fields/{key}` body `{required?: bool, aliases?: [str]}` (role with `mapping` permission)
  → updated field; 409 when an alias is used by another field, 409 when un-requiring a locked field.
  `aliases` is the full new list: at most 50, each 1–60 characters, de-duplicated after normalising.
  404 for an unknown key, 403 for roles without `mapping`. Audit: `field_changed` with
  `{required, added, removed}`.

## 3. Field matcher (unknown layouts)

`backend/matcher.py`, deterministic and explainable (ideas from COMA/Valentine, FlexMatcher and
spreadsheet-schema-matching; no code copied):

1. **Label candidates**: short text cells near the top or left of the sheet that are not numbers or
   dates. **Value** of a label = first non-empty cell to the right (up to 3 cells) or below (up to 2).
2. **Table candidates**: header rows followed by rows containing numbers. A *matrix* header has a run
   of ≥ 3 size-like cells (text sizes; numeric sizes such as 28, 30, 32 only when a colour, style or
   total heading names the row as a table); a *flat* header has size + quantity headings (a quantity
   heading may carry its unit, "Qty (pcs)"). The numbers under the sizes / quantity must pass the
   quantity rule of §4.
3. **Name score** for (label, field): 1.0 for an exact alias after normalisation; otherwise the best of
   token Jaccard and character-trigram Dice against every alias, after expanding abbreviations
   (`qty→quantity`, `no/num/#→number`, `dt→date`, `col/clr→colour`, `ref→reference`, `desc→description`,
   `cust→customer`, `del→delivery`, `exf/ex-fty→ex factory`).
4. **Value score** by kind: date → parses with `iso_date`; quantity → numbers ≥ 0 in ≥ 80 % of the column;
   size → matches the size pattern or a known Oracle size; colour → text, not numeric; code → short
   alphanumeric; text → not a number or date; master overlap → the value exists in the matching Oracle
   master (buyer, colour, size), which adds confidence.
5. **Score** = 0.6 × name + 0.4 × value (value 0 → rejected for that field; when the label is a strong
   match, name ≥ 0.85, the rejection is listed in `rejected` with its reason). Name matches below 0.5
   are not shown. Confidence: high ≥ 0.85, medium ≥ 0.6, else low (shown, never pre-selected).
6. **One-to-one assignment**: best scores first; a cell or column is used once.

Output (also stored as `draft.suggestion`):

```json
{"sheet": "PO", "definition": {"fields": {"buyer": "B1"}, "table": {...}},
 "fields": {"buyer": {"cell": "B1", "label_cell": "A1", "label": "Customer", "value": "ZEN KIDS",
            "score": 0.93, "confidence": "high", "source": "matcher|ai|both",
            "evidence": ["label 'Customer' = alias of Buyer", "value found in buyer master"]}},
 "table": {"type": "matrix", "header_row": 5, "confidence": "high", "evidence": ["3 size headers: S, M, L"]},
 "rejected": [{"field": "ship_date", "cell": "H3", "value": "NEXT", "source": "ai",
               "reason": "H3 holds \"NEXT\", which is not a date"}],
 "unit": {"value": "", "evidence": "the quantity header says only \"Qty\""},
 "explanation": "one sentence"}
```

As built, the shape also carries: top-level `source` (`matcher` or `matcher + local AI`); per field
`name_score` and `value_score`; `table.source`; `unit.pieces_per_unit` and `unit.cell`. A `rejected`
entry's `source` is `matcher`, `ai` or (in a `/map` 409) `person`. On upload the suggestion is the
matcher's (with the heuristic as fallback, no AI, so uploads stay fast and offline); the local AI is
asked only through `POST /api/drafts/{id}/suggest`. Master names (buyer, colour, size) come from Oracle
when it is reachable; each of those three masters is read on its own, so one master too large to list
does not hide the others.

## 4. Validator (acceptance test D)

`matcher.check(sheet, definition, fields)` returns `(ok_definition, rejected[])`. It is applied to the
matcher's own output, every AI proposal, the heuristic, saved templates when applied, and mappings a
person submits (`POST /api/drafts/{id}/map` answers 409 listing each wrong field).

Rejection rules: empty cell; cell is itself a known label; date field value not a date; quantity
column not numeric in ≥ 80 % of filled rows or any negative; size header row with < 60 % size-like
cells (matrix); buyer/description that is a pure number or a date; the same cell used for two fields;
a column outside the sheet.

## 5. Quantity unit (acceptance test C)

Each order has `qty_unit` = `PCS`, `PACK` or empty, plus `pieces_per_unit`.

* Evidence from the file: quantity header or nearby label containing `pcs/pieces/pc` → `PCS`;
  `pack/packs/pk/set/sets/ratio` → `PACK`; a unit label (`UOM | PCS`); a note just above the table;
  quantities written with their unit (`1,500 pcs`, `20 packs`; the number is kept, the unit is evidence);
  a merch BOM whose size table total equals its `QTY(IN PIECES)` → `PCS`. Provenance records the
  evidence text and cell.
* No evidence → empty. The review screen shows **Quantity unit** (Pieces | Packs) outlined red:
  "The file says only “Qty” — are these pieces or packs?"
* `PCS` → `pieces_per_unit` = 1. `PACK` → `pieces_per_unit` ≥ 2 is required ("How many pieces are in one pack?").
* The planner enforces the same rules (server side), with paths `orders.N.qty_unit` and
  `orders.N.pieces_per_unit`. Drafts saved before this feature derive the unit from `pieces_per_unit`.
* A confirmed unit is stored in the buyer template and restored next time.
* Also asked, never guessed: a file that names both units ("Qty (pcs/packs)"); a merch BOM whose size
  table total differs from its `QTY(IN PIECES)` (the question quotes both numbers); a file whose unit
  contradicts the template's (the question names both). `PCS` with a stale pieces-per-pack is an error
  on `orders.N.pieces_per_unit`, never multiplied in.
* `provenance.qty_unit = {sheet?, cell?, value?, evidence, said?, reason, question?}` with `reason` one
  of `file`, `silent`, `both`, `mismatch`, `conflict`, `template`, `person`, `review`; `question` is
  present only while `qty_unit` is empty and is the text shown on the field.
* `review`: on the layout screen the person chose to decide the unit on the review screen. The
  evidence the file gave is kept, so the question quotes it ("The quantity heading C3 says “Qty (pcs)” —
  are these quantities pieces or packs?") instead of claiming the file is silent.

## 6. Buyer templates (acceptance tests A and B)

Template = `{id, name, buyer, version, signature, labels, definition, anchors, unit, quantity_basis,
created_by, created_at, uses, active}`; `anchors[field] = {cell, label_cell, label}`, plus
`anchors["table.<column>"]` for each table heading the definition relies on (flat columns; a matrix's
colour, style and total headings).

* Saving a layout with the same name (or the same signature for the same buyer) creates version n+1
  and deactivates the previous version; history is kept and visible in Setup → Layout templates.
* Applying: signature equal or label similarity ≥ 0.8, **and** every anchor's label cell still holds
  the same normalised label, **and** the validator accepts the definition on this sheet. If any check
  fails the template is not applied and the reason is recorded in the draft notes.
* Applying restores `qty_unit`, `pieces_per_unit` and `quantity_basis`.
* The signature and labels cover the layout only: the cells the definition reads as values, the size
  headings and everything below the table header are left out, so the buyer's next file (new style,
  PO, colours) still matches. Templates saved before anchors existed keep their whole-sheet signature.
* A refusal reads, in `draft.notes`: *Saved layout “ZEN KIDS PO” (version 1) was not used on sheet
  “PO”: A8 now says “ETD”, not “Ship Date”. The file was read as a new layout.* The matcher's
  suggestion is then computed for that sheet.
* A unit or quantity basis answered on the review screen of a draft read with a template that did not
  know it yet is saved into that template (audited `template_unit_learned`); a saved decision is never
  overwritten that way.

## 7. Dates

`workbooks.iso_date` turns `2027-01-15`, `2027-01-15 0:00:00`, `20/12/2026`, `20-Dec-26`,
`5 Mar 2027`, `2026/12/20` and Excel serial numbers into `YYYY-MM-DD`. Numbers are read day-first
(Indian convention); `12/20/2026` is unmistakably month-first.

An ambiguous date such as `03/04/2026` depends on the rest of the file:
* **Day-first by default.** It is converted day-first, and the notes and the field's source chip say so.
* **Not when the file writes month first.** If another date in the same file can only be month-first
  (`12/20/2026`) and none can only be day-first, the date is *not* converted. The field is left empty
  with the question "“03/04/2026” could be 3 April 2026 or 4 March 2026 — enter the ship date."

A text date that cannot be read is left empty with a warning, never guessed.

## 7a. Review items

Some findings are not errors, but the approver must see them before approving. `workbooks.review(o, field,
message)` adds them to `order.review = [{field, message}]`. The planner raises each one as a **warning** on that
field (`orders.N.<field>`, or `orders.N.lines` for the size table), so it appears on the review screen and under
"Please read before approving". The person decides; a warning never blocks saving.

| Finding | Field | Example message |
|---------|-------|-----------------|
| A total row that disagrees with the sizes read | `lines` | “the size table says the total is 310, but the rows read add up to 300” |
| A second date label for the same field | that date field | “F1 “Revised Ship Date” says 2027-02-01, but the ship date was read from B3 …” |
| Hidden rows or columns that hold data (never read) | `lines` | names the hidden rows / columns |
| Quantities left below the table end | `lines` | names the cells |
| The same order on two sheets | `style` | names both sheets |

Totals, subtotal and grand-total rows (`Total`, `TOTAL PCS`, `Sub-Total`, `G.Total`; not a colour such as
“TOTAL ECLIPSE”) are never read as sizes or colours. Cells in hidden rows or columns are never read.

## 7b. Quantities the reader will not guess

* Text quantities with thousands separators or units (`1,500`, `1,500 pcs`) become the number.
* In a file that writes decimals with a comma (semicolon CSV, `sep=;`), `2.000` is two thousand, not 2. Such
  cells keep the file's text. The planner marks each one as an **error** ("“2.000” could mean 2000 …"), and
  the size table says why, until a person types the whole number.
* Anything else that is not a whole, non-negative number stays as the file wrote it, and the planner names it.

## 8. Acceptance tests (`tests/test_acceptance.py`)

| Test | Given | Then |
|------|-------|------|
| A — known buyer | A buyer layout mapped and confirmed once (template saved with unit = packs of 3) | The next file from that buyer is read automatically with the same fields, unit and basis; no field outlined except values genuinely missing in the file |
| B — new buyer | Buyer A's template exists; a different buyer's file with a similar-looking but different layout is uploaded | No value from buyer A or from any earlier draft appears; matcher proposals point only at the new file's cells, each with evidence |
| C — missing unit | A flat file whose quantity header is only "Qty" (e.g. 500) | `qty_unit` is empty, the review/planner error asks pieces vs packs; choosing Packs requires pieces per pack; saving is blocked until answered |
| D — wrong mapping | An AI/matcher/person proposal maps Ship date to a cell holding "NEXT" and Quantity to a text column | Those fields are rejected with reasons; the rest of the proposal is kept; `/map` returns 409 for the wrong person mapping |
| E — approved order | A reviewed order is approved | One transaction; every row read back equal (stand-in Oracle in CI; real Oracle with `POPPYS_TEST_ORACLE=1`) |
| F — visual verification | An order reference is searched in Database | The order record opens with its linked ORDSTYLE lines and ORDSIZE size rows and decoded masters |

## 9. API as built

Shapes the endpoints above settle (the frontend and `tests/test_acceptance.py` rely on them).

* `POST /api/uploads` → the draft, which also carries `notes` (draft-level reasons such as a refused
  template), `templates_refused: [{id, name, version, sheet, reasons}]`, `template: {id, name, version}`
  or `null`, and `suggestion` (§3 shape; set when no order was read or a template was refused). Each
  order carries `qty_unit` (`PCS`, `PACK` or empty), `pieces_per_unit` (1 for `PCS`; ≥ 2 or `null` for
  `PACK`; `null` while undecided), `quantity_basis` and `provenance.qty_unit` (§5). A draft read with a
  template has `format` = `Template: <name>`.
* `POST /api/drafts/{id}/suggest` body `{sheet, use_ai = true}` → a §3 suggestion (matcher, plus the local AI
  when `use_ai` is true and it is running, cross-checked; heuristic when no table was found); 409 when nothing
  reliable is found. The layout screen asks with `use_ai: false` when it opens (fast, offline) and asks the
  local AI only from its own button.
* `POST /api/drafts/{id}/map/check` — the same body as `/map` → `{ok, rejected: [{field, cell, value, reason,
  source: "person"}], detail}`. A dry run: the draft, its version and the templates are unchanged. The layout
  screen checks first, so a wrong choice is shown on its field without a failed request.
* `POST /api/drafts/{id}/map` body `{version, sheet, definition: {fields, table}, save_template,
  template_name, qty_unit?, pieces_per_unit?, quantity_basis?}` — the unit decision travels as order
  fields (`qty_unit`: `PCS`, `PACK`, or `''` to ask on the review screen; `quantity_basis`: `sample` or
  `order_demo`). `header_row` + `columns` instead of `definition` still map a flat table. The validator
  runs first: 409 `{detail, rejected: [{field, cell, value, reason, source: "person"}]}` names each wrong
  field and changes nothing (same version, no template). 409 for `PACK` with fewer than 2 pieces per pack.
  On success → the draft; with `save_template`, the template is saved (§6) with the buyer, anchors, unit
  and basis, and `draft.template` names it. The draft's notes about the layout question are cleared.
* `PUT /api/drafts/{id}` accepts `qty_unit` / `pieces_per_unit` / `quantity_basis` on each order; the
  preview (`POST /api/drafts/{id}/preview`) reports unit questions on `orders.N.qty_unit` and
  `orders.N.pieces_per_unit`, and fields an administrator made required on `orders.N.<key>`.
* `GET /api/templates` → every version, newest first per layout: `[{id, name, buyer, version, family,
  signature, labels, definition, anchors, unit: {qty_unit, pieces_per_unit} | null, quantity_basis,
  created_by, created_at, uses, active}]`. `POST /api/templates/{id}/active` body `{active}` (role with
  `templates` permission) → the same list; activating a version deactivates the layout's other versions.
* `matcher.check(sheet, definition, fields)` takes a sheet of the cell model (`draft.sheets[i]`) and the
  effective fields as `GET /api/fields` returns them (or none for the defaults).
