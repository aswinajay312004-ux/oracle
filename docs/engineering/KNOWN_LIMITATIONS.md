# Known limitations (2026-10-18)

Listed so that nobody has to discover them in production. Each has an owner action or a phase.

## Blocking for production use against a client's ERP

1. **Axpert compatibility is not verified.** Orders are written by direct INSERT into 13 of ORDMAS's 197 columns (plus
   lines, sizes, masters). Whether Axpert treats them as its own is unknown. → Run `scripts/axpert_gap_report.py` and
   the Axpert screen check on the laptop; decide the write channel (ORACLE_AXPERT_INTEGRATION §4–5).
2. **Laptop: tests pass (`verify_local.ps1` 2026-10-18, 518 passed).** Demo A/B and the real Ollama model have
   not been run with this code. → owner.
3. **BOM rows (fabric, yarn, trims) are not written** — their mapping is not verified. They are shown, checked and
   listed as "not saved".
4. **Exporter, Department, Gender, Against, Packing type, Components, Season are not written** (unverified or no
   column). Listed on every approval and receipt.

## Excel understanding

5. No general region finder: unknown layouts need one mapping by a person (then they are remembered, and re-anchored
   when rows/columns move).
6. Not understood: delivery schedules, prices/currency, transposed matrices, several quantity columns, "Label: value" in
   one cell, nested JSON sizes, some size words (2T, OS, EU 36) without the Oracle size master, month-name dates.
   They are visible as "not read" or asked — not silently dropped.
7. Accuracy on real buyer files is not measured (no anonymised corpus in the repository).
8. Unread text is counted, not listed, in the coverage manifest.

## Units and conversions

9. Only pieces and packs of N. Dozens, pairs, sets, cartons are asked; no company unit dictionary.

## Operations and scale

10. One server process, no Oracle connection pool, `main.py` not yet split into routers.
11. No retention policy: drafts, revisions, uploads and chat history grow without limit.
12. Windows service, offline install, scheduled backup, upgrade and rollback are not verified on Windows.
13. Network loss during a real Oracle commit is handled by design and tested with a stand-in, not on real Oracle.

## Product scope

14. Insert-only: no amendment or update of an existing Axpert order.
15. One company per installation; setup scripts assume the GARMENT_DEMO schema.
16. No PDF, Word, image or email input (by owner decision).

## Smaller

17. (Fixed 2026-10-18: Ask AI CSV export now prefixes formula-like text with an apostrophe.)
18. Audit history shows technical hashes and JSON to approvers.
19. REMARKS written to Oracle contains "quantity basis: order_demo" (internal word) — owner decision to change.
20. The `Secure` cookie flag needs local TLS.
