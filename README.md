# POPPYS AI — buyer order files to Axpert ERP (Oracle), with approval

POPPYS AI is a local, approval-controlled order-entry tool for POPPYS Garments (Axpert ERP on Oracle). Staff upload the order file a buyer sent. POPPYS AI then:

1. reads it, in any common spreadsheet format or JSON;
2. finds the order fields (buyer, style, PO, order reference, dates, colours, sizes, quantities, pieces or packs) and shows the source cell of every value;
3. asks only what the file does not say, or says unclearly;
4. shows the exact Oracle rows it will write (ORDMAS → ORDSTYLE → ORDSIZE and the master tables);
5. after an approver approves, saves everything in one transaction, reads it back from Oracle and draws the saved records as a diagram.

**Upload → Review → Check & approve → Saved & verified**, plus **Ask AI** (plain-language questions about orders, answered with checked, read-only SQL) and a read-only **Database** explorer.

Scope: spreadsheets and JSON with a local AI and the local Oracle database. No PDF, OCR, CAD, images or cloud AI.

## What it reads

| Format | Extensions | Reader |
|---|---|---|
| Excel workbook | `.xlsx` `.xlsm` `.xltx` `.xltm` | openpyxl (formulas with cached results, number formats, hidden rows/columns, merged cells; macros never run) |
| Excel 97-2003 | `.xls` (BIFF2–BIFF8) | xlrd, then python-calamine for damaged files |
| Excel binary | `.xlsb` | python-calamine |
| OpenDocument | `.ods` | python-calamine + a bounded pass for merges and hidden rows |
| Excel 2003 XML | `.xml` | defusedxml (no entity expansion) |
| Web-page "Excel" exports | `.xls` `.htm` `.html` `.mht` `.mhtml` | HTML parser (what many ERP "Export to Excel" buttons produce) |
| Text exports | `.csv` `.tsv` `.txt` | encoding detection (UTF-8/16, Windows-1252 …), `sep=` line and delimiter detection |
| JSON | `.json` | records, row arrays, sheet maps, NDJSON, API envelopes, nested documents (flattened to columns and rows) |

The format is decided from the file's bytes, not its name. Password-protected or rights-managed workbooks and other Office documents are refused with a message saying what to do. Every upload is parsed in a separate worker process with a time and memory limit. Details: [docs/EXCEL_UNDERSTANDING.md](docs/EXCEL_UNDERSTANDING.md).

## How it understands a file

- **Known buyer:** a layout confirmed once is saved as that buyer's versioned template (field anchors, table, unit). The next file with the same layout is read with no questions. A template is used only when each anchor's label still says the same thing, so data from one buyer never carries over to another.
- **New layout:** a deterministic field matcher scores every candidate cell by its label (aliases, abbreviations, fuzzy match) and by its values (a date field must hold a date, quantities must be numbers). It explains each choice. The optional local AI (Ollama) can add proposals, and every proposal goes through the same validator.
- **Refuses to guess:**
  - A unit the file does not state: "pieces or packs?"
  - A date with day and month unclear, in a file that writes month first.
  - A quantity like `2.000` in a decimal-comma file.
  - A description sitting in the style column.
  - Two columns that could both be the quantity.

  Each one becomes a question on the exact field.
- **Review items:** a total row that disagrees with the sizes, a second ship date, data in hidden rows, or the same order on two sheets. These are shown under "Please read before approving", on the field they concern.

## How it saves

- Nothing is written to Oracle until a person with the approver role approves the exact preview, after ticking "I have read these notes" when there are warnings. The approval is bound to a hash of the planned rows. With the four-eyes rule on, no one who uploaded or changed the order can approve it.
- The save runs under a write lock and re-plans inside the transaction. It refuses if anything changed, uses bind variables only, reads back every inserted row and compares it, then writes a receipt (`PP_IMPORT_LOG`). Any failure rolls everything back.
- Duplicate files, order references and repeated sizes or colours are refused. Existing ERP rows are never updated or deleted.
- The app writes as the restricted `POPPYS_APP` account. Ask AI, the explorer, history and read-back use the read-only `POPPYS_RO` account. This build is scoped to the `GARMENT_DEMO` schema and refuses any other owner.
- Saving is refused unless the database is on this computer: every host in the connection setting must be loopback. `ORACLE_DB_IDENTITY` can pin the database by name.

> **Before production use:** a direct INSERT bypasses Axpert's own numbering and Gen/MD maps. Agile Labs (the Axpert vendor) must confirm the posting path. See [docs/adr/0001-posting-path.md](docs/adr/0001-posting-path.md).

## Run it on Windows

See **[docs/WINDOWS_SETUP.md](docs/WINDOWS_SETUP.md)** for first-time setup and **[docs/RUNBOOK.md](docs/RUNBOOK.md)** for the permanent Windows-service install, upgrades, rollback and backups. In short:

```powershell
git pull
.\setup.ps1                                                   # venv, packages, POPPYS_APP account, grants, connection check
.\.venv\Scripts\python.exe scripts\create_readonly_user.py   # read-only POPPYS_RO account for questions and the explorer
.\start.ps1                                                   # http://127.0.0.1:8008
```

Staff guide in plain language: [docs/USER_GUIDE.md](docs/USER_GUIDE.md).

## Layout

```
backend/
  main.py            API: sign-in & roles, uploads, drafts, layout mapping, preview, approval, history, explorer, admin
  readers.py         every spreadsheet format and JSON → one cell model (format sniffing, size caps, fallbacks)
  isolation.py       parses each upload in a separate worker with time and memory limits
  numfmt.py          renders Excel number formats (keeps leading zeros such as 00123)
  workbooks.py       order understanding: layouts, size matrices, flat tables, units, dates, review items, templates
  fields.py          the order fields, their aliases and which are required (editable by an admin)
  matcher.py         explainable field matcher and the validator every proposal must pass
  ai.py              local AI (Ollama) layout proposals, always checked by the validator
  security.py        passwords, sign-in throttling, sessions, CSRF, body limits, security headers, safe errors
  logs.py            JSON-lines logs with request IDs and redaction
  local_store.py     SQLite workspace: users, sessions, drafts, templates, settings, hash-chained audit log
  oracle_store.py    Oracle pools (app + read-only), the transactional writer, live row reads
  erp/               mapping registry, catalog (live or snapshot), planner (business rules → row plan), read-back
  insight/           knowledge catalog, question understanding, read-only SQL compiler, answer engine
config/              oracle_mapping.json (what may be written), semantic_catalog.json (business meaning)
frontend/            vanilla JS, no CDN, works offline, strict Content Security Policy
scripts/             bootstrap, read-only account, grants, schema discovery, Oracle check, backup, local checks
deploy/windows/      Windows service (WinSW), install / upgrade / rollback / backup task / uninstall
tests/               unit, workflow, browser (Playwright) and opt-in real-Oracle tests
docs/                setup, runbook, architecture, Excel spec, research decisions, performance, security
```

## Tests and checks

```powershell
.\scripts\check.ps1                 # secret scan, lint, JavaScript syntax, unit tests
.\scripts\check.ps1 oracle          # the same plus the real-Oracle tests (needs .env.poppys)
```

CI (`.github/workflows/ci.yml`) runs lint, a secret scan (own rules plus Gitleaks over the whole history), a dependency audit, the unit and browser tests, a Windows run and a real Oracle Free 23 job. The Oracle job builds a replica of GARMENT_DEMO with 20,000 bulk-loaded orders and runs the save, read-back, Ask AI and explorer tests against it.

The tests build synthetic files that reproduce the structure of the client's workbooks. Client files are never committed.

## Safety boundaries

- Upload, review and preview never write to Oracle. A preview made while Oracle is offline is marked as such and cannot be approved.
- The local AI can only propose layout cells and question intents. Both are checked. It cannot write SQL, change values or save.
- Ask AI and the explorer run each query read-only, with a timeout and a row limit, on the read-only account.
- Passwords live only in the git-ignored `.env.poppys`, or are typed at a prompt. They are never logged, shown or committed. A password an administrator sets for someone is temporary: that person chooses their own at the next sign-in.
- Security details, and how to report a problem: [SECURITY.md](SECURITY.md). Changes per release: [CHANGELOG.md](CHANGELOG.md).
