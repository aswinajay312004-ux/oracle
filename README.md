# POPPYS AI — Excel to Oracle ERP order entry

POPPYS AI is a local, approval-controlled automation tool for POPPYS Garments (Axpert ERP on Oracle). An employee drops a buyer or merchandising file. POPPYS AI then:

1. reads the order, size breakdown, fabric and trims;
2. maps them to the verified GARMENT_DEMO tables and columns;
3. asks only what is missing;
4. shows the exact Oracle entry;
5. after approval, saves it in one transaction and proves it with a live read-back flowchart.

**Upload → Review (Axpert-style entry) → Check & approve → Saved & verified**, plus **Ask AI** (plain-language questions about the database) and a read-only **Database** explorer.

## What it does

- **Documents:** Excel workbooks (`.xlsx`/`.xlsm`, plus `.xls`, `.xlsb`, `.ods`, CSV and ERP HTML exports). PDF, Word, images and scans are deliberately out of scope.
  - Built-in recognition: merch BOM workbooks (order / sample / summary blocks, packs, fabric requirement, trim tables) and buyer contract confirmation reports.
  - Simple tables are matched by column headings.
  - For a new layout, the user shows the fields once (local AI or layout analysis suggests them), and it is saved as a template.
- **Accurate mapping:** a mapping registry (`config/oracle_mapping.json`) defines each business field → `TABLE.COLUMN`. Only columns verified with evidence are written. Fabric (`ORDFAB`), yarn (`ORDYARN`) and accessories (`ORDACC`) are validated and previewed, and saved once an admin verifies their mapping.
- **Business validation:** required fields, quantity and unit checks, total reconciliation, duplicate file, order and size checks, cancelled or ambiguous masters, fabric kg and yarn %, composition mismatch, and the real Oracle column limits.
- **Safe save:** bind variables, a write lock, the exact preview hash, one transaction, row-by-row read-back, and an audit receipt in `POPPYS_APP.PP_IMPORT_LOG`. Existing orders are never overwritten.
- **Field-level validation:** a missing or wrong value is outlined in red on the exact field or table cell, with a one-line reason under it. There is no error list to decode. "Show me the next one" walks through them.
- **Axpert-style entry:** the order header as a form, sizes as a size × colour table (first columns frozen), and fabric/yarn and trims as tables.
- **Visual confirmation:** before and after saving, a flowchart of every affected table. Click a table to see its rows in table format below. After saving, the rows come from a fresh `SELECT`, with master IDs decoded to names.
- **Ask AI:** plain-language questions about the database ("size wise quantity for order PK/26-27/S/9001", "buyer wise order quantity this year").
  - Each answer shows a linked-table flowchart. "+" reveals related tables, clicking a table opens a preview, and any of its columns can be added to the answer, which re-runs with the correct joins.
  - The model never writes SQL. Questions become a validated intent, compiled along known relationships into one read-only query, and the query is shown.
- **Data dictionary:** business names, synonyms and roles for every table and column, plus relationship confirmation. Everything is editable and audited in Setup.
- **Database explorer:** business names, exact row counts (or "≈" statistics for very large tables), master names instead of IDs, linked records (parents and children) and an expandable relationship map.
- **People and audit:** admin, approver, operator and viewer roles, an optional four-eyes rule, and a full audit trail of uploads, corrections, previews, approvals and mapping changes.
- **Offline:** UI, server, document reading, local AI (Ollama) and Oracle all run on the laptop.

## User manual

**[docs/USER_MANUAL.txt](docs/USER_MANUAL.txt)**: every screen, role, command and fix, in Thanglish.

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
  readers.py         every spreadsheet format → one cell model (isolated worker, size caps)
  formulas.py        results for simple formulas saved without them
  evidence.py        evidence ledger for mapping / relationship verification
  ai.py              local layout suggestions (Ollama) + deterministic layout analysis
  local_store.py     SQLite workspace: users, drafts, revisions, audit, templates, mapping verification
  oracle_store.py    Oracle connections (write account + read-only account), transactional writer, live row reads
  erp/
    mapping.py       registry loader + verified/proposed overrides
    catalog.py       live Oracle dictionary vs offline snapshot
    planner.py       business rules → exact row plan
    readback.py      live read-back graph (and the planned graph for the approval view)
    semantic.py      business semantic model
  insight/
    knowledge.py     knowledge catalog: dictionary + registry + curated meaning + admin edits; relationship graph
    understand.py    question → intent (rules first, local AI as a checked fallback)
    query.py         intent → one read-only SELECT (catalog identifiers, graph joins, binds, no double counting)
    engine.py        answers with provenance, confidence and linked-table graph
    service.py       dictionary harvest/cache, value index, read-only executors
config/oracle_mapping.json   field → table/column registry (what may be written)
config/semantic_catalog.json business meaning of tables/columns and their relationships (what questions use)
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
- The local AI can only propose layout cells, explain drafts and propose question intents that must compile against the catalog. It cannot produce SQL or save.
- Ask AI and the explorer run every query in a read-only transaction with a timeout and a row limit.
- Credentials live only in the git-ignored `.env.poppys`. The GARMENT_DEMO password is typed at a prompt for discovery and never stored.
- The app connects as the restricted `POPPYS_APP` account (SELECT plus INSERT on specific tables). It never updates or deletes GARMENT_DEMO rows, and is scoped to GARMENT_DEMO, never the client's live production database.
