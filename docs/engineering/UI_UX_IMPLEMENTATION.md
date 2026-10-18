# UI / UX implementation

Main journey: **Upload → Understand → Review exceptions → Approve → Save → Verify.** Light interface, green accent,
no external fonts or scripts.

## 1. Screens

| Screen | What it does now | Status |
|---|---|---|
| Overview | Work queue (with "Save outcome unknown" first), real counts, recently saved orders from Oracle receipts, connections | Implemented |
| New order · upload | Drag-and-drop or choose; recent files with truthful statuses; viewers see a note instead of the upload area | Implemented, browser-tested |
| Reading summary | Format, layout used (or re-anchored), details found, size breakdown, BOM, unit question, "N of M numbers in the file are in the order" | Implemented, browser-tested |
| Review | Axpert-style header and size table; problems outlined on their field; source chip per value; "not in Oracle" badge on fields Oracle will not receive; banner "Not read from the file"; **Show the file** (the sheet beside the order; focusing a field outlines its cell); **Original file**; undo/redo | Implemented, browser-tested |
| Mapping studio | Fields left, sheet right; drag a cell onto a field, Pick + click, or type the address; focused field's cell outlined; edit the saved layout as applied; name collisions handled | Implemented, browser-tested |
| Check & approve | KPIs; warnings; **Read before approving** (converted, new masters, not saved, not read, layout changes) with a required tick box; the table flowchart of planned rows; confirmation in business words | Implemented, browser-tested |
| Saved | "Saved · reading it back…", then **Saved and verified** (or differences / not verified); what was not saved; the flowchart from a fresh Oracle read | Implemented, browser-tested |
| Saved orders | Receipts from Oracle with Verified / with exceptions / not verified / differences | Implemented |
| Ask AI | Catalog answers from the data dictionary; "Not sure I understood — did you mean …?" instead of a low-confidence number; checks strip; chart + table | Implemented, browser-tested (earlier sessions) |
| Database | Business-named tables, paging, linked records, relationship graph | Implemented |
| Setup | Write mapping, data dictionary, order fields, layout templates, users (password dialog, confirmed role changes), audit, settings, answer accuracy | Implemented |

## 2. Navigation and accessibility

| Item | Status |
|---|---|
| Browser Back/Forward between screens and from Check & approve to the review | Implemented, browser-tested |
| Keyboard: clickable rows, sort headers, sheet cells reachable and usable with Enter/Space; `aria-sort` | Implemented |
| Dialogs: confirm and password dialogs (native `<dialog>`), drawer keeps and returns focus | Implemented |
| Non-colour indicators: icons and words with every status colour | Implemented |
| Screen-reader live regions for busy and pick hints | Implemented (partial) |
| Full keyboard walk-through with a screen reader | Not verified |

## 3. Responsive

Measured in Chromium at 390 × 844: Overview, New order (upload, reading, review, approve), Saved orders, Ask AI,
Database, Setup — page width 390 px on all (review was 664 px before). Dense tables scroll inside their cards. Phone
navigation is a compact two-row bar. **Implemented, browser-tested (demo server).** Tablets and browser zoom levels:
not separately measured.

## 4. Open UX items (audit §12)

Audit history still shows hashes and raw JSON to approvers (#11); toasts may cover content on phones (#15 partly);
the Ask AI chart on phones (#17); dictionary grid on phones (#18); starter questions tied to demo data (#20); tab
semantics (#22 partly); Saved orders as cards on phones and search (#23); REMARKS text written to Oracle contains
an internal key ("quantity basis: order_demo", #21 — changing it changes what is written, owner decision).
