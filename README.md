# POPPYS AI — Excel to Oracle orders

A local, offline, admin-only application that turns a buyer's Excel order into Oracle order records:

**Upload → Review → Approve.** Nothing is written to Oracle until the admin approves the exact preview.

## Sections

| Section | What it does |
| --- | --- |
| **Overview** | Saved orders, saved quantity, uploads waiting for approval, recent activity and database operations. Every figure comes from stored data; click a figure to see its records. |
| **New Order** | Drag in an `.xlsx`/`.xlsm` file. It is converted to structured JSON (every value keeps its sheet, cell, formula and format), mapped to Oracle tables and shown as a preview grouped by Oracle table and column, with the Excel cell behind each value. **Edit mapping** opens a two-panel editor: Oracle fields on the left, the original workbook on the right. Drag a cell onto a field, click to map, or type a value. It also has undo/redo, suggestions and *Save as buyer template*. |
| **Orders** | All approved orders, read live from Oracle, with search, filter and sort. The ⋮ menu has View details, Edit order, View database flow, Visualization and Change history. An edit shows the original Excel value, the current Oracle value and your proposed value. It updates Oracle only after you approve, then reads the values back and records old/new values in the audit log. |
| **Database** | **Flow diagram** of the real relationships. Select an order to see how many records it has in each table, then click a table to open its *Data* (actual Oracle values with Excel sources) and *Visualization* tabs. **Tables** is a read-only explorer. **Create table** is a spreadsheet-like designer with a DDL preview and typed confirmation. **Relationships** links columns by drag-and-connect, with validation. **Mapping designer** is a node editor that wires Excel cells to Oracle fields. |
| **Settings** | Oracle connection status (no secrets shown), local AI status, verified workspace backup, admin password. |

## Data rules

- **Optional is normal.** A value missing from Excel stays blank (NULL) in Oracle. No zeros, empty strings or defaults are invented. A missing section (for example printing) creates no child records.
- **Blocking issues are kept to real problems:** a value Oracle would reject (type, length, precision), a mandatory database column without a value, an ambiguous or cancelled master, a size quantity without a size, sizes that do not add up to the workbook's own total, a date that can be read two ways, or an order reference that already exists.
- The order reference (`ORDMAS.DOCID`) is the one business value always needed. It identifies the order and prevents duplicate saves.
- **Source values are preserved.** The uploaded file is stored unchanged. Converted values (dates, numbers) keep the raw Excel value beside them. Formulas keep their text and cached value and are never recalculated. Edits are stored as revisions.
- Buyer, style, description, colour, size and merchandiser are saved as links to their master records. Existing masters are reused; a new master is shown in the preview before it is created.
- Pieces-per-unit is optional. Without it, quantities are saved exactly as in Excel and the unit is left blank.

## Safety

- An approved plan is written as **one transaction** (all records or none), then every record is read back on a new connection and compared before success is reported. Retrying after a lost response returns the original receipt; it never inserts twice.
- Order edits use optimistic concurrency. If Oracle changed after the preview, the approval is refused. Totals (`ORDSTYLE.ORDQTY/PRODQTY`, `ORDMAS.OQTY`) are recalculated only when they were consistent before.
- Table creation and foreign keys are separate, explicitly confirmed schema changes. Oracle DDL commits immediately, so it is never mixed with order writes. A guarded procedure accepts only a single `CREATE TABLE` or `ADD FOREIGN KEY` inside `GARMENT_DEMO`.
- Application-level relationships are stored in POPPYS only and never create Oracle constraints.
- Saved buyer templates are reused only where the label beside each mapped cell still matches. A changed layout is reported, never applied blindly.
- The local AI (Ollama, optional) can only suggest which cell holds which field. It cannot write, run SQL or change data. Workbook text is treated as data, never as instructions.
- One admin account, local sessions, same-origin checks, bind variables everywhere, identifiers validated against live metadata. No internet access, telemetry or external assets at runtime.

## Start

1. Start Docker Desktop and confirm the existing `oracle26ai` container is healthy.
2. Run `python scripts/bootstrap_local.py` once. It creates the limited `POPPYS_APP` account and the ignored `.env.poppys` file. It does not reset or delete `GARMENT_DEMO`.
3. Run `python scripts/upgrade_local.py`. It is additive and re-runnable. It grants read access for the explorer and update access to `ORDMAS/ORDSTYLE/ORDSIZE` for approved edits. It also creates the change and schema audit tables and the guarded schema-change procedure.
4. Run `./start.ps1`, open `http://127.0.0.1:8008`, create the admin account and upload a workbook.

For a fresh development or CI container only, `python scripts/create_test_schema.py` builds a **synthetic** `GARMENT_DEMO` from `docs/live_schema_columns.json`. It refuses to run if `GARMENT_DEMO` exists.

## Verify

```
python -m pytest -q                                   # unit and rule tests
$env:POPPYS_TEST_ORACLE='1'; python -m pytest -q      # + local Oracle integration and end-to-end API tests
$env:POPPYS_TEST_DDL='1'                              # + adds a TEST_PRINT_* table to check table/relationship creation
```

The Oracle tests write clearly labelled `TEST-` orders only. Do not run them against a production database.
