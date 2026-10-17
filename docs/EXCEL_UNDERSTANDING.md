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
format), `raw` (the stored number when `value` is the text Excel shows, `8` formatted `0000` → `'0008'`), `text` (the
characters the file shows for a number when the number alone loses them: `5012.10` typed in a CSV/HTML export, or
`5012.1` formatted `0.00`; code fields keep this text) and `flag`:
- `decimal_comma`: text such as `1.234,56`, or in a decimal-comma file `2.000`. It is kept as text, never read as a number.
- `scientific`: a code Excel already turned into `1.23457E+11`.
- `big_number`: 16 or more digits, kept as text so no digit is lost.

Dot-grouped text such as `2.000` or `12.500` is kept as text by every text reader (CSV, HTML, JSON strings, text
cells), whatever the delimiter: it is 2000 in a file from a decimal-comma country and 2 elsewhere (§7b).

Each sheet also carries `capabilities`, saying whether its reader could see merges, hidden rows and columns,
number formats and formulas, so a note can say what a format cannot show, and `date1904: true` for a workbook that
counts dates from 1904 (Mac Excel; openpyxl `epoch`, xlrd `datemode`). Error cells (`#REF!`, `#N/A`, `#DIV/0!`) have
`error: true`; text that spells an Excel error is treated the same way (§4, §7a).

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
   dates. **Value** of a label = first non-empty cell to the right (up to 3 cells) or below (up to 2). In a
   **strip of labels** — a row whose every filled cell reads like a label ("PO No | Buyer Ref | Style | Ex-Fty"),
   two or more with a value right under them — the value is the cell below, never the next label. A label that is
   exactly another field's alias ("PO No") is never proposed for a different field.
2. **Table candidates**: header rows followed by rows containing numbers. A *matrix* header has a run
   of ≥ 3 size-like cells (text sizes — S…XXXXL, 2XS, US plus 1X–3X, toddler 2T–5T, months, years, cm; numeric
   sizes such as 28, 30, 32 only when a colour, style or total heading names the row as a table — the heuristic
   fallback follows the same rule, so a row of quantities is never taken for the sizes); a *flat* header has size +
   quantity headings (a quantity heading may carry its unit, "Qty (pcs)"). A total column is any total or quantity
   heading right of the sizes ("Total", "TTL", "Pcs"). A heading only *like* "colour" ("Fabric Colour") is proposed
   as the colour column with medium confidence. The numbers under the sizes / quantity must pass the
   quantity rule of §4. The table body runs past block totals and sub totals to a grand total or an empty row.
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
6. **One-to-one assignment**: best scores first; a cell or column is used once. When two labels both name a
   field exactly ("Item" and "Style No", "Order No" and "PO No") with different values, the proposal is `low`
   (shown with both cells, never pre-selected) — unless exactly one of the values is in the Oracle master.

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

Rejection rules: empty cell; cell is itself a known label; a cell holding an Excel error value (`#REF!`, `#N/A`);
date field value not a date; quantity column not numeric in ≥ 80 % of filled rows or any negative; size header row
with < 60 % size-like cells (matrix); a total or quantity heading inside the size columns (a saved layout's size
range that now ends on this file's "Total" column); a size heading right next to the size columns, or a second set
of sizes on the same header row (Delivery 1 | Delivery 2), with quantities under it — they would be left out;
buyer/description that is a pure number or a date; the same cell used for two fields; a column outside the sheet.

## 5. Quantity unit (acceptance test C)

Each order has `qty_unit` = `PCS`, `PACK` or empty, plus `pieces_per_unit`.

* Evidence from the file: quantity header or nearby label containing `pcs/pieces/pc` → `PCS`;
  `pack/packs/pk/set/sets/ratio` → `PACK`; a unit label (`UOM | PCS`); a note just above the table;
  quantities written with their unit (`1,500 pcs`, `20 packs`; the number is kept, the unit is evidence) — only in
  the rows the table read, never a trims list further down the same columns;
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
(Indian convention); `12/20/2026` is unmistakably month-first. A date typed as a plain number in a 1904 workbook
(`date1904`) is counted from 1904-01-01, and its source says so.

An ambiguous date such as `03/04/2026` depends on the rest of the file:
* **Day-first by default.** It is converted day-first; the field's source chip says so and a review item on that date
  field (§7a) puts it under "Please read before approving".
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
| A total row that disagrees with the rows it adds up (its block, or the whole table) | `lines` | “Row 9 (“Total”) … says the total is 20, but row 8 above it adds up to 18” |
| Lines after the last total row with no total of their own | `lines` | “Rows 11–12 … come after the total in row 10 and have no total of their own” |
| An unlabelled row equal to the one line above it (read as a total); a “TOTAL …” colour equal to it (read as a colour) | `lines` | “Row 7 … has no colour and repeats the quantities of row 6, so it was read as a total row” |
| A second date label for the same field | that date field | “F1 “Revised Ship Date” says 2027-02-01, but the ship date was read from B3 …” |
| Two labels of one field with different values (the field is left empty and asked) | that field | “A1 “Order No” says “SO-889” and A2 “PO No” says “PO-4471” …” |
| A date read day-first that could be month-first | that date field | “Ship date: "05/06/2026" read as day/month/year (2026-06-05) — check it.” |
| An Excel error value where a value should be (the field or colour is left empty and asked) | that field / `lines` | “B2 on “PO” holds the Excel error “#N/A” …” |
| Hidden rows or columns that hold data (never read) | `lines` | names the hidden rows / columns |
| What the reader could not see (backup reader, .xlsb): codes that may have lost leading zeros, hidden rows, merged cells | code field / `lines` | “The style number “123” was read … without its cell format, so leading zeros (such as “00123”) may be missing” |
| Formula cells without a calculated value | `lines` | names the cells |
| Quantities left below the table end or below its grand total | `lines` | names the cells |
| A heading right next to the size columns with numbers under it (not a known size, total or price) | `lines` | “E5 “Extra”, right next to the size columns B–D …, has numbers under it but was not read as a size” |
| A ratio / assortment row or a pack / carton count row in a size matrix (not read as a line) | `qty_unit` | “Row 6 (“Ratio”) … is a size ratio (how a pack is made up), not quantities …” |
| Dot-grouped or split quantities kept as text (§7b) | `lines` | names the cells |
| A visible sheet with data that gave no order | `lines` (every order) | “No order was read from sheet “PO 1002”, which holds data …” |
| The same order on two sheets | `style` | names both sheets |
| A style column whose rows sometimes have no style (that order's style is asked) | `style` | “Row 7 … has no style in column A, while other rows have one” |

Totals, subtotal and grand-total rows are never read as sizes or colours: any label with a total word — `Total`,
`TOTAL PCS`, `Sub-Total`, `G.Total`, `G. TTL`, `TTL`, `SUM`, `Colour Total`, `Order Total`, `ZK-100 Total`,
`Total for RED` — and, in a size matrix, an unlabelled row whose quantities are the sums of the block above. A total
word followed by other words (“TOTAL ECLIPSE”) is a colour unless its quantities are the sums of two or more rows
above it. A block total (“Total” under each colour) is checked against its block and the table goes on below it;
a grand total (`Grand`, `G.`, `Overall`, `Order Total`) ends the table. Cells in hidden rows or columns are never read.

Size matrices (`workbooks.matrix_orders`) also skip empty rows right under the headings (headings merged over two
rows), read a style or colour merged down over several rows on each of them, and give **one order per style** when
the label column is a style column. A layout that reads no line at all is refused with “No order lines were found
under the headings in row N …” (a 409 on `/map`), never saved as an empty order.

Every way of reading a sheet — automatic, a saved layout, a layout mapped with `/map` — ends with the same checks
(`workbooks.read_checks`, then `workbooks.finish_orders` across the draft), so mapping a layout never drops a
finding the upload would have raised. Each order remembers its `sheet`.

## 7b. Quantities the reader will not guess

* Text quantities with thousands separators or units (`1,500`, `1,500 pcs`) become the number.
* A quantity written with a dot between groups of three digits (`2.000`, `12.500 pcs`) is two thousand in a file
  from a decimal-comma country and 2 elsewhere. In **every** format (semicolon or comma CSV, Unicode text, HTML
  export, JSON strings, text cells of a workbook; the merch BOM and buyer contract readers too, and a line's source
  total) such cells keep the file's text and are marked unclear. The planner marks each one as an **error**
  ("“2.000” could mean 2000 …"), and the size table says why, until a person types the whole number.
* A row of a text file with more values than headings (an unquoted `1,200` splits into `1` and `200`): in a flat
  table its quantity is kept as the joined text (`1,200`, when the quantity is the last heading) and marked unclear,
  and the size table names the extra cells; a size matrix with such a row is refused (which size was split is unknown).
* A number no float can hold (`1E+400`) is not a number; it stays text and the planner names it.
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
  template, hidden data, what the reader could not see, sheets with data that gave no order),
  `templates_refused: [{id, name, version, sheet, reasons}]`, `template: {id, name, version}`
  or `null`, and `suggestion` (§3 shape; set when no order was read, a template was refused, or a visible sheet with
  data gave no order — the suggestion is then for that sheet). Each order carries `sheet` (the sheet it was read
  from). Each
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
  On success → the draft. The orders of **that sheet** are added or replaced; orders read from the file's other
  sheets stay. The mapped orders go through the same checks as an upload (§7a): hidden data, second dates, Excel
  errors, what the reader could not see, sheets still unread. The draft's notes about that sheet's layout question
  are cleared; the other sheets' notes and the checks' findings stay. With `save_template`, the template is saved
  (§6) with the buyer, anchors, unit and basis, and `draft.template` names it. Someone without the `templates`
  permission saving a new version of an existing layout gets it switched off, with the note “Saved as version N of
  “<name>”. An administrator must switch it on in Setup → Layout templates before it is used for new files.” (the
  draft itself is read with the layout applied). 409 when the layout reads no line.
* `PUT /api/drafts/{id}` accepts `qty_unit` / `pieces_per_unit` / `quantity_basis` on each order; the
  preview (`POST /api/drafts/{id}/preview`) reports unit questions on `orders.N.qty_unit` and
  `orders.N.pieces_per_unit`, and fields an administrator made required on `orders.N.<key>`.
* `GET /api/templates` → every version, newest first per layout: `[{id, name, buyer, version, family,
  signature, labels, definition, anchors, unit: {qty_unit, pieces_per_unit} | null, quantity_basis,
  created_by, created_at, uses, active}]`. `POST /api/templates/{id}/active` body `{active}` (role with
  `templates` permission) → the same list; activating a version deactivates the layout's other versions.
* `matcher.check(sheet, definition, fields)` takes a sheet of the cell model (`draft.sheets[i]`) and the
  effective fields as `GET /api/fields` returns them (or none for the defaults).
