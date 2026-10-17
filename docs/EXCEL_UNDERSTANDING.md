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

Principles (from the research in `docs/OPEN_SOURCE_RESEARCH.md`):

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

* `GET /api/fields` → `[{key, label, kind, where, aliases, default_aliases, locked, required}]`
* `PUT /api/fields/{key}` body `{required?: bool, aliases?: [str]}` (role with `mapping` permission)
  → updated field; 409 when an alias is used by another field, 409 when un-requiring a locked field.

## 3. Field matcher (unknown layouts)

`backend/matcher.py`, deterministic and explainable (ideas from COMA/Valentine, FlexMatcher and
spreadsheet-schema-matching; no code copied):

1. **Label candidates**: short text cells near the top or left of the sheet that are not numbers or
   dates. **Value** of a label = first non-empty cell to the right (up to 3 cells) or below (up to 2).
2. **Table candidates**: rows with ≥ 3 text cells followed by rows containing numbers. A *matrix*
   header has ≥ 3 size-like cells; a *flat* header has size + quantity columns.
3. **Name score** for (label, field): 1.0 for an exact alias after normalisation; otherwise the best of
   token Jaccard and character-trigram Dice against every alias, after expanding abbreviations
   (`qty→quantity`, `no/num/#→number`, `dt→date`, `col/clr→colour`, `ref→reference`, `desc→description`,
   `cust→customer`, `del→delivery`, `exf/ex-fty→ex factory`).
4. **Value score** by kind: date → parses with `iso_date`; quantity → numbers ≥ 0 in ≥ 80 % of the column;
   size → matches the size pattern or a known Oracle size; colour → text, not numeric; code → short
   alphanumeric; text → not a number or date; master overlap → the value exists in the matching Oracle
   master (buyer, colour, size), which adds confidence.
5. **Score** = 0.6 × name + 0.4 × value (value 0 → rejected for that field). Confidence: high ≥ 0.85,
   medium ≥ 0.6, else low (shown, never pre-selected).
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
  `pack/packs/pk/set/sets/ratio` → `PACK`; a merch BOM whose size table total equals its
  `QTY(IN PIECES)` → `PCS`. Provenance records the evidence text and cell.
* No evidence → empty. The review screen shows **Quantity unit** (Pieces | Packs) outlined red:
  "The file says only “Qty” — are these pieces or packs?"
* `PCS` → `pieces_per_unit` = 1. `PACK` → `pieces_per_unit` ≥ 2 is required ("How many pieces are in one pack?").
* The planner enforces the same rules (server side), with paths `orders.N.qty_unit` and
  `orders.N.pieces_per_unit`. Drafts saved before this feature derive the unit from `pieces_per_unit`.
* A confirmed unit is stored in the buyer template and restored next time.

## 6. Buyer templates (acceptance tests A and B)

Template = `{id, name, buyer, version, signature, labels, definition, anchors, unit, quantity_basis,
created_by, created_at, uses, active}`; `anchors[field] = {cell, label_cell, label}`.

* Saving a layout with the same name (or the same signature for the same buyer) creates version n+1
  and deactivates the previous version; history is kept and visible in Setup → Layout templates.
* Applying: signature equal or label similarity ≥ 0.8, **and** every anchor's label cell still holds
  the same normalised label, **and** the validator accepts the definition on this sheet. If any check
  fails the template is not applied and the reason is recorded in the draft notes.
* Applying restores `qty_unit`, `pieces_per_unit` and `quantity_basis`.

## 7. Dates

`workbooks.iso_date` turns `2027-01-15`, `2027-01-15 0:00:00`, `20/12/2026`, `20-Dec-26`,
`5 Mar 2027`, `2026/12/20` and Excel serial numbers into `YYYY-MM-DD`. Numbers are read day-first
(Indian convention); `12/20/2026` is unmistakably month-first; an ambiguous `03/04/2026` is converted
day-first and reported in the notes and the field's source chip.

## 8. Acceptance tests (`tests/test_acceptance.py`)

| Test | Given | Then |
|------|-------|------|
| A — known buyer | A buyer layout mapped and confirmed once (template saved with unit = packs of 3) | The next file from that buyer is read automatically with the same fields, unit and basis; no field outlined except values genuinely missing in the file |
| B — new buyer | Buyer A's template exists; a different buyer's file with a similar-looking but different layout is uploaded | No value from buyer A or from any earlier draft appears; matcher proposals point only at the new file's cells, each with evidence |
| C — missing unit | A flat file whose quantity header is only "Qty" (e.g. 500) | `qty_unit` is empty, the review/planner error asks pieces vs packs; choosing Packs requires pieces per pack; saving is blocked until answered |
| D — wrong mapping | An AI/matcher/person proposal maps Ship date to a cell holding "NEXT" and Quantity to a text column | Those fields are rejected with reasons; the rest of the proposal is kept; `/map` returns 409 for the wrong person mapping |
| E — approved order | A reviewed order is approved | One transaction; every row read back equal (stand-in Oracle in CI; real Oracle with `POPPYS_TEST_ORACLE=1`) |
| F — visual verification | An order reference is searched in Database | The order record opens with its linked ORDSTYLE lines and ORDSIZE size rows and decoded masters |
