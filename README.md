# POPPYS AI — Excel to Oracle orders

A local, offline, admin-only application that turns a buyer's Excel order into Oracle order records:

**Upload → Review → Approve.** Nothing is written to Oracle until the admin approves the exact preview.

## Current order entry flow

The current branch previews and writes to `GARMENT_DEMO.ORDERENTRY` by default (`ENTRY_TABLE` in `.env.poppys` can select another approved table). It reads live Oracle columns and maps workbook cells to the selected table; optional linked tables are added in the mapping screen only after their relationship is confirmed. Earlier `EXCEL_ORDERS` and ORDMAS/ORDSTYLE/ORDSIZE records remain readable for history, but `ORDER_TARGET=erp` does not change the current save path.

## Data Expert Agent and Ask AI

- **Data Expert Agent** (`backend/agent.py`) runs on every upload and shows each step on the New Order screen. It reads the workbook, recognises the layout, converts it to JSON, and finds every section (order, sample and summary sizes, fabric, trims, packing; printing, embroidery and costing are reported as "not provided" when absent). It then matches the Oracle columns from the Excel labels. Its **local AI step** (Ollama) gets only the label/value pairs no rule used and the columns still empty. A JSON schema with exactly those enums constrains the answer. Every proposal is re-checked and shown with an **AI** tag. Approval is always yours.
- The local model is picked from what is installed. `OLLAMA_MODEL` is used if present, otherwise `qwen2.5:7b-instruct`, `qwen2.5:latest`, `qwen2.5:14b-instruct`, then `llama3`. Embedding models are never used for chat.
- **Ask AI** (sidebar, or type a question in the top search) answers questions about saved orders. The model, or a keyword reader when no model runs, only picks the question type and the real buyer, style or OC number. The numbers always come from fixed, read-only queries, and each answer shows how it was calculated.

## AI-ready data foundation

A local metadata store beside Oracle (nothing is added to client business tables), ready for a future question-answering assistant:

- **Catalog ID cards** (Database → Catalog). Structure and Oracle structure changes are read automatically. People write the business name, purpose, grain (what one row is), column meanings and synonyms. Status moves one checked step at a time: Discovered → Documented → Joins verified → Business approved → AI ready.
- **Order identity and lineage**: every approved order gets a UUID linked to its Oracle rows. Every saved value records its Excel file (hash), sheet, cell, raw value, conversion, approver and read-back result. Original and current values are both kept after approved edits (order → *Data trail*).
- **Blank meanings**: PROVIDED, NOT_PROVIDED (not in Excel), UNKNOWN (label present, value empty), NOT_APPLICABLE (marked N/A by the reviewer). An absent field is never turned into "no".
- **Quality results** per approval. **Business metrics** are versioned and stay *proposed* until approved; the system never approves its own formulas.
- **Future AI APIs**: `GET /api/catalog/search?q=`, `GET /api/catalog/tables/{name}`, `GET /api/orders/{id}/lineage?column=`, `GET /api/ai/context?q=`. The last one returns only AI-ready tables, verified joins, approved metrics and a read-only query policy.

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
- **Blocking issues are kept to real problems:** a value Oracle would reject (type, length, precision), a mandatory database column without a value, a size quantity without a size, sizes that do not add up to the workbook's own total, an ambiguous date, or an order reference that already exists.
- The current `ORDERENTRY` path uses `IONO` as its order reference. The exact required columns are read from live Oracle metadata before approval.
- **Source values are preserved.** The uploaded file is stored unchanged. Converted values (dates, numbers) keep the raw Excel value beside them. Formulas keep their text and cached value and are never recalculated. Edits are stored as revisions.
- Buyer, style, description, colour, size and merchandiser are mapped to the selected table's actual columns. The current path does not create or link master records automatically.
- Pieces-per-unit is optional. Without it, quantities are saved exactly as in Excel and the unit is left blank.

## Safety

- An approved plan is written as **one transaction** (all records or none), then every record is read back on a new connection and compared before success is reported. Retrying after a lost response returns the original receipt; it never inserts twice.
- Order edits use optimistic concurrency. If Oracle changed after the preview, the approval is refused.
- Table creation and foreign keys are separate, explicitly confirmed schema changes. Oracle DDL commits immediately, so it is never mixed with order writes. A guarded procedure accepts only a single `CREATE TABLE` or `ADD FOREIGN KEY` inside `GARMENT_DEMO`.
- Application-level relationships are stored in POPPYS only and never create Oracle constraints.
- Saved buyer templates are reused only where the label beside each mapped cell still matches. A changed layout is reported, never applied blindly.
- The local AI (Ollama, optional) can only suggest which cell holds which field. It cannot write, run SQL or change data. Workbook text is treated as data, never as instructions.
- One admin account, local sessions, same-origin checks, bind variables everywhere, identifiers validated against live metadata. No internet access, telemetry or external assets at runtime.

## Start

1. Start Docker Desktop and confirm the existing `oracle26ai` container is healthy.
2. Run `setup-local.bat` once to create `.venv` and install Python dependencies. `start.bat` also runs this automatically when `.venv` is missing.
3. If `.env.poppys` does not exist, run `.venv\Scripts\python.exe scripts\bootstrap_local.py` once. It creates the limited `POPPYS_APP` account without resetting `GARMENT_DEMO`.
4. Run `.venv\Scripts\python.exe scripts\upgrade_local.py` to grant access to the current `ENTRY_TABLE` and explorer. This is additive and safe to rerun. `create_order_table.py` is only for the earlier `EXCEL_ORDERS` path and is not required for the current flow.
5. Run `start.bat` (Command Prompt or double-click) or `.\start.ps1` (PowerShell), open the URL printed in the terminal, sign in, and upload a workbook. Keep the terminal open; Ctrl+C stops the server.

For development, run `start-dev.bat`; it restarts the server when Python or frontend files change. Refresh the browser to load the new page. To bring in newly pushed commits on another machine, run `update-live.cmd` and refresh the browser. Both start scripts use port 8008 by default. If another server already owns 8008, in Command Prompt run `set POPPYS_PORT=8010` before `start.bat` or `start-dev.bat`; in PowerShell run `$env:POPPYS_PORT='8010'` before `.\start.ps1`.

The URL is local to this computer. The app has no built-in public sharing service; exposing it to other computers needs a separately reviewed deployment and authentication setup.

For a fresh development or CI container only, `python scripts/create_test_schema.py` builds a **synthetic** `GARMENT_DEMO` from `docs/live_schema_columns.json`. It refuses to run if `GARMENT_DEMO` exists.

## Verify

```
.venv\Scripts\python.exe -m pytest -q                # install requirements-dev.txt first
$env:POPPYS_TEST_ORACLE='1'; .venv\Scripts\python.exe -m pytest -q      # + local Oracle integration tests
$env:POPPYS_TEST_DDL='1'                              # + adds a TEST_PRINT_* table to check table/relationship creation
```

The Oracle tests write clearly labelled `TEST-` orders only. Do not run them against a production database.
