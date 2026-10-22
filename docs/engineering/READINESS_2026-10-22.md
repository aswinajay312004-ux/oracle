# POPPYS ORDERENTRY readiness — 2026-10-22

## Scope and branch history

- Active delivery branch: `claude/optimistic-gates-nkfe7f`. The branch was first fast-forwarded to remote commit `671c286` before changes were made.
- The six untracked ERP audit files found at the start belonged to a different, richer branch. They were preserved and pushed as commit `f95876a` on `codex/erp-audit-checkpoint`. That checkpoint has 19 failing and 11 passing tests on its own branch; it is not merged into this ORDERENTRY delivery.
- The current app uses `GARMENT_DEMO.ORDERENTRY` as its default save target. `EXCEL_ORDERS` and the older ORDMAS/ORDSTYLE/ORDSIZE path are historical here. `ORDER_TARGET=erp` does not switch this branch's entry flow.

## Completed in this branch

- Reviewed upload, mapping, approval, saved-order detail/edit, Ask AI, Oracle schema scripts, local launchers, and tests against the current branch.
- Prevented a fresh workbook's matching headings from silently attaching a different Oracle table. Optional sections such as PRINTING stay in the preserved JSON until a reviewer explicitly maps a linked table. Previously reviewed templates may still apply.
- Stored Oracle's canonical ROWID text in new receipts and supported the compact ROWID spelling in older receipts. This fixed false missing-row indications and restored edit previews for existing saved orders.
- Changed ambiguous Oracle connection/error messages so a possible post-commit disconnect does not claim that nothing was saved.
- Made Ask AI decline time-period questions that its fixed queries cannot filter yet.
- Added isolated `POPPYS_DATA_DIR`, `setup-local.bat`, `start-dev.bat`, `update-live.cmd`, and updated `start.bat`, `start.ps1`, `.gitignore`, README, and tests for the current entry flow.

## Verification performed

| Check | Result |
| --- | --- |
| Default Python suite | 31 passed, 11 skipped, 1 Starlette/httpx deprecation warning |
| `POPPYS_TEST_ORACLE=1` against local `FREEPDB1` / `POPPYS_APP` | 41 passed, 1 skipped, same warning |
| JS syntax (`node --check` on all frontend JS) and Git whitespace check | Passed |
| `start.bat` on port 8010 | Served the latest app; browser upload, preview and data mapping worked |
| `start-dev.bat` on port 8011 | Uvicorn reloader and app started; HTTP `/` returned 200 |
| Fictional `Test_A_with_printing.xlsx` browser upload | Preview showed 12 ORDERENTRY rows, no automatic PRINTING table link; section retained in JSON |
| TEST-labelled `test-bom.xlsx` approval/readback/retry | Three Oracle rows saved, total size quantity 21, verified readback, retry returned existing receipt; browser order detail displayed three rows and Excel cell lineage |

The Oracle suite writes only `TEST-`-labelled orders. The DDL integration test remains skipped unless both `POPPYS_TEST_ORACLE=1` and `POPPYS_TEST_DDL=1` are set; it would create a `TEST_PRINT_*` table. No client business order was committed during this verification.

## Remaining external acceptance

- Client-approved workbook samples, target table/column meanings, optional child-table relationships, and Axpert posting/acceptance criteria are still needed before using this against real orders. Similar headings alone cannot prove a business mapping.
- Ask AI's unsupported time filters need a defined date column and accepted query semantics before implementation. Any larger business-question corpus also needs the client's expected answers.
- A public Share link is not part of this local app. Remote access needs a separately reviewed deployment and authentication plan; the supplied launchers bind to `127.0.0.1`.

## Reproduce locally

See README `Start` and `Verify`. In Command Prompt from the repository root: `setup-local.bat` (first run), `set POPPYS_PORT=8010` if 8008 is occupied, then `start.bat`. Open the printed URL. Use `start-dev.bat` for automatic server restarts after source changes and refresh the browser. On another checkout, `update-live.cmd` pulls pushed commits with fast-forward only.
