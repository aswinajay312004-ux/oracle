# Excel data fidelity specification

The uploaded file is the source of truth. Nothing in it is changed, invented, dropped or reinterpreted without the person
seeing it before approval. Each rule below names where it is enforced and how it is tested.

## 1. Rules and enforcement

| # | Rule | Enforcement | Test | Status |
|---|---|---|---|---|
| F1 | The original bytes are kept, unchanged, even when the file cannot be read | `main.archive_original`: written with `open(..., 'xb')` **before** parsing, `fsync`, read-only; SHA-256 in the draft and audit; `upload_unreadable` audited | `test_fidelity.py::test_the_original_file_is_kept…`, `…cannot_be_read_is_still_kept` | Implemented, Tested |
| F2 | The original is served only while it is unchanged | `GET /api/drafts/{id}/original` recomputes SHA-256; mismatch → 409, audited | same | Implemented, Tested |
| F3 | Every value keeps its source | `{sheet, cell, value, formula}` on header fields, colours, labels, size quantities and size names, fabrics, yarns, accessories | `test_acceptance.py` (A), `test_documents.py` | Implemented, Tested |
| F4 | Every worksheet and every number is accounted for | `backend/coverage.py`: sheets read / not read / hidden; numbers read = cited by a value; unread numbers grouped with their headings; totals listed apart | `test_fidelity.py` (coverage tests) | Implemented, Tested |
| F5 | Nothing is dropped silently | Unread regions are "exclusions": reading summary, review banner, approval list (must be read), Oracle receipt | `test_fidelity.py`, browser walk-through | Implemented, Tested |
| F6 | No business value is invented | Defaults (exporter, sample type) come only from Settings and carry "Company default (Settings)"; the reader fills nothing the file lacks; required missing values are asked, never guessed | `test_fidelity.py`, planner tests | Implemented, Tested |
| F7 | Quantities are never converted silently | Units come from the file, the person, or the same buyer's saved layout; another buyer's unit is never applied (asked again quoting it); every pack → piece conversion is listed with source quantity, factor, result and who decided | `test_acceptance.py::test_B_another_buyers_lookalike…`, `test_fidelity.py::test_a_pack_conversion…` | Implemented, Tested |
| F8 | "Blank" is not "zero" and not "missing" | A size cell left blank in a line with other quantities = not ordered (no row written, said on the approval); text or an Excel error in a quantity cell blocks on that cell; an empty required field is asked | `test_fidelity.py::test_a_blank_size…` | Implemented, Tested |
| F9 | Excel errors are not values | `#N/A`, `#REF!`, `#VALUE!`… in a header field → empty + question; in a quantity → blocking error | `test_fidelity.py::test_an_excel_error…` | Implemented, Tested |
| F10 | Formula results are not invented | Formulas without a cached result are evaluated only for a safe subset; others stay empty with a note (`backend/formulas.py`) | `test_formats.py`, `test_engine.py` | Implemented, Tested |
| F11 | What Oracle will not receive is said | `planner.destination`: each filled value without a verified column ("kept in POPPYS only" or "place not confirmed"), BOM rows of unverified tables; badge on the field, list on the approval, `not_saved` + `complete` in the receipt | `test_fidelity.py::test_values_oracle_will_not_receive…` | Implemented, Tested |
| F12 | Exact numbers | Decimal arithmetic in the planner; Oracle precision/scale/length checked before writing (refuse, never round or truncate) | `test_planner.py`, `test_engine.py` | Implemented, Tested |
| F13 | Macros and external content never run | Readers parse XML/binary structures only; no macro execution, no external links fetched | `test_formats.py` | Implemented, Tested |
| F14 | Hidden data is named | Hidden rows/columns are not read and the sheet note says so; hidden sheets appear in coverage as "hidden, not read" | `test_fidelity.py`, `test_engine.py` | Implemented, Tested |

## 2. Value states (spec §2.5)

| State | How it appears |
|---|---|
| Present and verified | value + source chip (cell) |
| Present but ambiguous | value kept, `provenance.question` asks (e.g. day/month dates, "Qty" without unit) |
| Not supplied | empty field; required ones outlined red "Not in the file — please enter it" |
| Explicitly blank (size) | "not ordered" warning, no row written |
| Not applicable | field not shown for this layout |
| Required but missing | blocking error on the field |
| Unsupported by destination | badge "not in Oracle" + approval list (`kept: poppys`) |
| Pending mapping | badge + approval list (`kept: unverified`, names the proposed column) |
| Rejected by validation | blocking error on the field/cell |
| Intentionally excluded | coverage exclusion with its reason |

## 3. Known gaps

- Number formats and cell styles are kept in the cell model only as far as the readers support them; the visual look of
  the workbook is not reproduced (the original file is available instead). **Known risk: low.**
- European decimal text ("1.500") in a text cell reads as 1.5; as a quantity it is then refused as not whole (blocking,
  not silent). **Known risk: low.**
- Coverage counts numbers; unread *text* (for example a comment column) is counted, not listed. **Known risk: medium** —
  a text-only business field that no layout reads is visible only as part of "text cells".
