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
| Check & approve | Business KPIs (orders, colour lines, pieces, new names); **Order to be saved** — header and colour × size table from the plan, OC No "Assigned when saved"; **Before you approve** — one table, one row per value with a status chip (Check / New / Not set up yet / No Oracle field / Skipped), reason on the chip, required tick; flowchart under **Technical details** | Implemented, browser-tested (desktop, 390 px) |
| Saved | Hero with the OC No; **Order in Oracle** — header and colour × size table from the fresh read-back (`readback.order_sheets`), "Verified in Oracle" badge; **View the order in Oracle** opens it full screen with Print; not-saved table; flowchart, IDs and Database link under **Technical details** | Implemented, browser-tested |
| Saved orders | Receipts from Oracle with Verified / with exceptions / not verified / differences; **View order** | Implemented |
| Ask AI | Catalog answers from the data dictionary; "Not sure I understood — did you mean …?" instead of a low-confidence number; checks strip; chart + table | Implemented, browser-tested (earlier sessions) |
| Database | Business-named tables, paging, linked records, relationship graph | Implemented |
| Setup | Write mapping, data dictionary, order fields, layout templates, users (password dialog, confirmed role changes), audit, settings, answer accuracy | Implemented |

### Copy rules (2026-10-19)

Following Shopify Polaris content guidance ("too much content makes a tool feel cheap") and ERPNext-style status
indicators (one label and colour per state): screens carry short labels and status chips; explanations, Oracle table
and column names, connection fixes (Docker, container name) and SQL wording live in tooltips or under **Technical
details**, never in the main reading path.

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
