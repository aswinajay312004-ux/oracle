# POPPYS AI sample-order workspace

Local-first demo for reviewing buyer or merch Excel workbooks before an explicit Oracle sample-order save.

## Start

1. Start Docker Desktop and confirm the existing `oracle26ai` container is healthy.
2. Run `python scripts/bootstrap_local.py` once. It creates the limited local `POPPYS_APP` account and writes the ignored `.env.poppys` configuration file. It does not reset or delete the existing `GARMENT_DEMO` schema.
3. Run `./start.ps1` from PowerShell, then open `http://127.0.0.1:8008`.
4. Create the local workspace account, drag in an `.xlsx` or `.xlsm` workbook, correct the preview, then select **Accept & save to Oracle**.

## What the demo writes

The initial sample-order slice writes only the verified `GARMENT_DEMO` order path:

`BUYMAS` / `STYLENUM` / `STYLEMAS` / `SIZE_MAS` / `COLORMAS` / `UNITMAS` / `MERCHMAS` → `ORDMAS` → `ORDSTYLE` → `ORDSIZE`.

The application first renders every planned row and Oracle column. It rejects missing or ambiguous masters, invalid size totals, duplicate order references, stale previews, and repeat uploads. An accepted plan writes as one transaction, reads every inserted row back, and records a receipt in `POPPYS_APP.PP_IMPORT_LOG`.

## Safety boundaries

- Upload and preview never write to Oracle.
- A local Ollama model may propose a flat-sheet column mapping or explain a draft, but it cannot issue SQL or save data.
- Formula text, cached values, hidden cells, merged regions, and source coordinates are retained for review.
- The supplied buyer contract quantity is never silently treated as a sample quantity. The reviewer selects the quantity basis and pieces per unit.

## Verify

Run `python -m pytest -q`. To include the local Oracle integration tests, run `$env:POPPYS_TEST_ORACLE='1'; python -m pytest -q` in PowerShell.
