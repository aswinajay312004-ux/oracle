# Dynamic templates and mapping

A **saved layout** (template) tells POPPYS AI where a buyer's file keeps each field and its size table. It carries
layout only — never data from the file it was made from — plus the unit and quantity basis that were confirmed for that
buyer.

## 1. Storage and identity

`templates` table in SQLite (`local_store.save_template`): name, buyer, version, family, layout signature, label set,
definition (field cells + table description), anchors (label next to each field and table column), unit, quantity
basis, created by/at, uses, active.

| Rule | Status |
|---|---|
| Saving the same name with the same structure = new version; older versions kept inactive (history and rollback in Setup → Layout templates) | Implemented, Tested |
| Saving a **different** structure under an existing name is refused with a free name offered; replacing only when the person ticks "Replace" | Implemented, Tested |
| A template's buyer is the single buyer of the mapped orders | Implemented |

## 2. Choosing a layout for a new file

1. Score every active template (signature equality, else position-aware label similarity ≥ 0.8), check its anchor labels
   and run the validator on the new sheet.
2. Among those that fit, the template saved for **this file's buyer** wins.
3. If none fits exactly, **re-anchor** (`backend/layouts.py`): every field is found again by its label (value at the
   same offset from the label), the size table by its headings, the size range by its distance to the colour and total
   columns; at least two labelled anchors; any missing or ambiguous label refuses. The result passes the same validator.
4. The changes ("All 6 order details moved 1 row down", "The size table now has 5 size columns") are stored on the
   draft (`layout_changes`) and shown on the approval list, which must be read.

## 3. Buyer scope of business decisions

| Decision | Applied to the same buyer | Applied to another buyer |
|---|---|---|
| Layout (cells) | yes | yes, with a note "saved for buyer …; only the layout was reused" |
| Unit (pieces / packs of N) | yes ("remembered from this buyer's saved layout") | **no** — asked again, quoting the saved choice |
| Quantity basis (sample / order) | yes | **no** — asked |
| Learning a unit answered on the review screen into the template | yes, when the template did not know it | **no** |

This removes the audit's 120 → 360 case (another buyer's "packs of 3"). Test:
`test_acceptance.py::test_B_another_buyers_lookalike_file_reuses_the_layout_but_never_its_unit`.

## 4. Mapping studio (Phase 4)

- Order fields on the left (cell address, value read, confidence + evidence, notes); the sheet on the right.
- Ways to choose a cell: drag the cell onto the field, **Pick** then click, or type the address (keyboard/phone).
- The focused field's cell is outlined in the sheet.
- A file read with a saved layout opens that layout **as applied to this file** for editing.
- Every choice is checked by `matcher.check` on Apply; wrong ones are outlined with the reason; nothing is saved
  until the checks pass.
- Mapping one sheet replaces only that sheet's orders; orders read from other sheets or typed in stay.

## 5. Learning from corrections

| Item | Status |
|---|---|
| Field aliases editable per installation (Setup → Order fields), audited | Implemented |
| A correction becomes a buyer-scoped alias automatically | Not implemented — corrections are saved as a new template version instead |
| Template version history, activate/deactivate (rollback) | Implemented |
| Test a template against sample files from the UI | Not implemented |
| Rename / duplicate / compare templates in the UI | Not implemented |

## 6. Oracle relationship view

The approval and Saved screens draw the tables involved (masters, header, lines, sizes, BOM), their links, the planned
rows and — after saving — the rows read back from Oracle, with the source cell of each written value
(`readback.planned/graph`, `frontend/js/flowview.js`). Unconfirmed links and unverified tables are drawn dashed.
**Implemented, Tested.**
