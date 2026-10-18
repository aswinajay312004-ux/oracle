# Excel extraction engine

How an uploaded workbook becomes orders, and what happens when it cannot be understood. The engine is deterministic;
the local AI may only propose, and every proposal (AI, matcher, template or person) passes the same validator
(`matcher.check`).

## 1. Ingestion

| Step | Code | Notes | Status |
|---|---|---|---|
| Size and type check from the bytes | `readers.sniff` | Empty, oversized (12 MB) and non-spreadsheet files refused with a reason | Implemented, Tested |
| Original archived | `main.archive_original` | Before parsing (EXCEL_DATA_FIDELITY_SPEC F1) | Implemented, Tested |
| Parse in a worker | `isolation.py` | Spawned process, time and memory budget; falls back to in-process with a log line if the worker cannot start | Implemented, Tested (Windows Job Object: Environment-blocked) |
| Formats | `readers.py` | .xlsx/.xlsm/.xltx/.xltm, .xls (BIFF), .xlsb, .ods, SpreadsheetML 2003 XML, HTML/MHTML exports, CSV/TSV/TXT, JSON records | Implemented, Tested (`test_formats.py`, 167 tests) |
| Hostile content | `readers.py` | Zip bombs, XML entities, deep nesting, macros not executed, external links not fetched | Implemented, Tested |
| Cell model | `readers.Book.put` | `{cell, row, col, value, formula, error, hidden}` + merged ranges, per sheet | Implemented, Tested |

## 2. Understanding, in order (per visible sheet)

1. **Saved layout, exact**: layout signature and anchor labels unchanged, validator accepts → read with it
   (`workbooks.choose_template`). The buyer's own layout wins over a better-scoring one of another buyer.
2. **Saved layout, re-anchored**: labels found again after inserted rows/columns, a moved table or an added size
   (`backend/layouts.py`); the changes are listed for the approver. Refused when a label is missing or ambiguous.
3. **Merch BOM** (fixed labels Garment/CONTRACT/Total): order/sample/summary blocks, fabric and trim tables
   (`workbooks.merch_bom`). An all-zero sample section shows the order quantities and asks; a second block of the same
   kind is named, not combined.
4. **Contract confirmation report** (fixed columns) (`workbooks.buyer_contract`).
5. **Flat table** whose headings are exact aliases (size + quantity + style column or header field).
6. Otherwise: **"Needs column mapping"** with a matcher proposal (`matcher.suggest`, evidence and confidence per field);
   the local AI can be asked for a second proposal from the mapping screen.

Every sheet's outcome is recorded in the coverage manifest (read, not read, hidden).

## 3. Field recognition and confidence

| Evidence | Where |
|---|---|
| Label equals an alias of the field (per-installation aliases in Setup → Order fields) | `matcher.name_score` |
| Value shape (date, number, code, size, colour) | `matcher.value_score` |
| Known master values (buyers, colours, sizes from Oracle) | `matcher.masters_index` |
| Label position: right of the label (≤3 cells) or below (≤2) | `matcher.label_for` |
| One-to-one: a cell used by one field is not used by another | `matcher.check` |
| Local AI proposal, accepted only where the matcher's own score for that cell is higher | `ai.suggest` |

Confidence is High / Medium / Low from these scores. Low-confidence proposals are shown, never pre-selected. Nothing
is approved automatically at any confidence.

## 4. Region handling

| Region | Handling | Status |
|---|---|---|
| Header key-value block | label → value cell | Implemented |
| Size matrix (sizes across) | header row, colour/garment/total columns, size range; merged colour cells fill their rows; a Style first column gives one order per style | Implemented, Tested |
| Flat table (one row per size) | columns by heading; merged style/colour cells fill down | Implemented, Tested |
| Totals / subtotals | left out of lines, the file's grand total checked against the lines | Implemented, Tested |
| Second table on the same sheet | named in notes and coverage, not added | Implemented, Tested |
| More than 200 matrix rows | first 200 read + a note + coverage exclusion | Implemented, Tested |
| Fabric / yarn / trims | detected by headings on the BOM sheet | Implemented (BOM layout only) |
| Delivery schedule, price/costing, multi-delivery quantity columns, transposed matrices | not understood: visible as "not read" in coverage | Not implemented |
| General region finder (occupancy grid, region types) | — | Not implemented — Known risk |

## 5. Known gaps (from the pipeline audit, current state)

| Audit # | Case | Now |
|---|---|---|
| B1 | Second matrix block | Named, not read (coverage exclusion) |
| B2 | > 200 matrix rows | Named with a note |
| B3 / B4 | Unmapped or hidden sheet beside a read one | Named in coverage; `/map` of one sheet keeps the others' orders |
| B5 | Several styles in one matrix | One order per style (Style first column) |
| B6 | Merged colour cells | Filled for every covered row |
| B7 | Two order blocks in a merch BOM | Second named; the file is read |
| B9 | Template after an inserted row/column/size | Re-anchored by labels, changes listed |
| B10 | Size words outside the patterns (2T, OS, EU 36) | Still need the Oracle size master or a person — Known risk |
| B11 | Text dates with month names first, with times, compact | Asked (field emptied with a warning) — Known risk |
| B14 | "Buyer: ZEN KIDS" in one cell | Not split — Known risk |
| B18 | Blank = not ordered | Implemented |
| B20 | Nested JSON sizes | Needs mapping — Known risk |
| B21 | Transposed matrices, delivery columns | Not implemented |
| B22 | Excel error cells | Never used as values |
| B26 | Real Ollama, client workbooks | Environment-blocked |

Accuracy on real buyer files is **not measured**: no anonymised corpus of client layouts exists in the repository. The
redesign target (≥30 real layouts with per-field accuracy) is open.
