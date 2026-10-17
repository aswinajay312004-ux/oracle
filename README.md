# POPPYS AI — Excel/PDF to Oracle ERP order entry

POPPYS AI is a local, approval-controlled automation tool for POPPYS Garments (Axpert ERP on Oracle). An employee drops a buyer or merchandising file. POPPYS AI then:

1. reads the order, size breakdown, fabric and trims;
2. maps them to the verified GARMENT_DEMO tables and columns;
3. asks only what is missing;
4. shows the exact Oracle entry;
5. after approval, saves it in one transaction and proves it with a live read-back flowchart.

**Upload → Auto-read → Review → Approve → Saved & verified**

## What it does

- **Documents:** Excel `.xlsx`/`.xlsm` and text PDFs.
  - Built-in recognition: merch BOM workbooks (order / sample / summary blocks, packs, fabric requirement, trim tables) and buyer contract confirmation reports.
  - Simple tables are matched by column headings.
  - For a new layout, the user shows the fields once (local AI or layout analysis suggests them), and it is saved as a template.
- **Accurate mapping:** a mapping registry (`config/oracle_mapping.json`) defines each business field → `TABLE.COLUMN`. Only columns verified with evidence are written. Fabric (`ORDFAB`), yarn (`ORDYARN`) and accessories (`ORDACC`) are validated and previewed, and saved once an admin verifies their mapping.
- **Business validation:** required fields, quantity and unit checks, total reconciliation, duplicate file, order and size checks, cancelled or ambiguous masters, fabric kg and yarn %, composition mismatch, and the real Oracle column limits.
- **Safe save:** bind variables, a write lock, the exact preview hash, one transaction, row-by-row read-back, and an audit receipt in `POPPYS_APP.PP_IMPORT_LOG`. Existing orders are never overwritten.
- **Visual confirmation:** a flowchart of every affected table, built from a fresh `SELECT`. Clicking a table shows its exact saved rows and decoded master values. A read-only **Database** explorer covers all GARMENT_DEMO tables.
- **People and audit:** admin, approver, operator and viewer roles, an optional four-eyes rule, and a full audit trail of uploads, corrections, previews, approvals and mapping changes.
- **Offline:** UI, server, document reading, local AI (Ollama) and Oracle all run on the laptop.

## Run it on Windows

See **[docs/WINDOWS_SETUP.md](docs/WINDOWS_SETUP.md)**. In short:

```powershell
.\setup.ps1                                              # venv, packages, POPPYS_APP account, grants, connection check
.\.venv\Scripts\python.exe scripts\discover_schema.py   # read-only schema evidence (asks GARMENT_DEMO password)
.\start.ps1                                              # http://127.0.0.1:8008
```

## Layout

```
backend/
  main.py            API: auth & roles, uploads, drafts, preview, approval, history, mapping, explorer, audit
  workbooks.py       Excel understanding: detectors, BOM extraction, templates
  pdfdocs.py         PDF → virtual sheets
  ai.py              local layout suggestions (Ollama) + deterministic layout analysis
  local_store.py     SQLite workspace: users, drafts, revisions, audit, templates, mapping verification
  oracle_store.py    Oracle connection, transactional writer, live row reads
  erp/
    mapping.py       registry loader + verified/proposed overrides
    catalog.py       live Oracle dictionary vs offline snapshot
    planner.py       business rules → exact row plan
    readback.py      live read-back graph
    semantic.py      business semantic model (foundation for reporting / NL Q&A)
config/oracle_mapping.json   field → table/column registry
frontend/            vanilla JS UI (no CDN; works offline)
scripts/             bootstrap, grants, discovery, connection check
tests/               unit, workflow (in-memory Oracle stand-in) and opt-in live Oracle tests
docs/                Windows setup, architecture, research, ERP video analysis, schema snapshot
```

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest -q
$env:POPPYS_TEST_ORACLE='1'; .\.venv\Scripts\python.exe -m pytest -q   # include the local Oracle tests
```

The tests use synthetic files that reproduce the structure of the client workbooks. Client files are never committed.

## Safety boundaries

- Upload, review and preview never write to Oracle. A preview made while Oracle is offline is clearly marked and cannot be approved.
- The local AI can only propose layout cells and explain drafts. It cannot produce SQL or save.
- Credentials live only in the git-ignored `.env.poppys`. The GARMENT_DEMO password is typed at a prompt for discovery and never stored.
- The app connects as the restricted `POPPYS_APP` account (SELECT plus INSERT on specific tables). It never updates or deletes GARMENT_DEMO rows, and is scoped to GARMENT_DEMO, never the client's live production database.
