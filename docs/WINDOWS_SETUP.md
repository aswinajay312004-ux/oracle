# POPPYS AI — Windows setup and run (local Oracle, fully offline)

Everything runs on the laptop: the browser UI, the Python server, document reading, the optional local AI (Ollama) and the Oracle database in Docker. No internet or cloud API is used at runtime.

## 0. What you need once

| Item | Check |
|---|---|
| Docker Desktop running, container `oracle26ai` healthy | `docker ps --filter name=oracle26ai` |
| Python 3.13, 64-bit, from python.org with the `py` launcher (the release package ships embeddable CPython 3.13.x, see [RESEARCH_DECISIONS.md](RESEARCH_DECISIONS.md); older versions are not supported) | `py -3.13 --version` |
| Git checkout of this repository | `git pull` on branch `claude/fervent-babbage-jqo8r4` |
| Optional: Ollama for AI layout suggestions | `ollama --version` |
| Optional, developers only: the test and lint tools for section 10 (`setup.ps1` installs only what the app needs; `.\setup.ps1 -Dev` adds pytest, ruff and pip-audit) | `.\.venv\Scripts\python.exe -m pytest --version` |

## 1. Get the latest code

```powershell
cd C:\path\to\oracle
git fetch origin
git checkout claude/fervent-babbage-jqo8r4
git pull
```

## 2. One-time setup (safe to run again)

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\setup.ps1
```

`setup.ps1` does four things:

1. Creates `.venv` and installs the pinned packages.
2. If `.env.poppys` does not exist yet, runs `scripts\bootstrap_local.py`. That script creates the limited `POPPYS_APP` account and the audit tables (`PP_IMPORT_LOG`, `PP_ORDER_KEYS`, `PP_WRITE_LOCK`, `PP_RECORD_SEQ`). **It never resets or deletes GARMENT_DEMO data.** If you already ran it earlier (your demo order `pk_123` exists), it is skipped.
3. Runs `scripts\grant_bom_tables.py`. This grants `POPPYS_APP` read access to all GARMENT_DEMO tables (for the Database screen and read-back) and INSERT on `ORDFAB`, `ORDYARN` and `ORDACC`. The app still writes these only after you verify their mapping.
4. Runs `scripts\check_oracle.py`, which prints what the app can see and write.

## 3. Discover your real schema (read-only)

```powershell
.\.venv\Scripts\python.exe scripts\discover_schema.py
```

It asks for the **GARMENT_DEMO** password. Type it at the prompt; it is not stored anywhere. The script runs only `SELECT` statements and writes `data\schema_discovery.json` and `data\schema_discovery.md`. Both are git-ignored and never pushed. The results cover:

- columns
- PK/FK/unique constraints
- triggers and their bodies
- sequences
- fill rates and sample values
- whether child `SUBNO` values really link to `ORDSTYLE.SUBNO` within the same `ORDMASID`
- whether reference columns (e.g. `ORDFAB.COLOUR`) resolve in the master tables

## 4. Start the app

```powershell
.\start.ps1
```

Open **http://127.0.0.1:8008**. The first time, create the administrator account. Under **Setup → Users & roles** you can add:

- **operators**: upload and prepare orders
- **approvers**: save to Oracle
- **viewers**: read-only access

To change the administrator's username (for example a new Gmail address) or password, or if the password is lost, stop the app and run `.\.venv\Scripts\python.exe scripts\reset_login.py`. It lists the accounts, asks you to type `DELETE`, removes all sign-in accounts and signs everyone out. Drafts, mappings, the audit history and Oracle are not touched. Start the app again and create the new administrator on the first-run screen.

## 5. Optional: local AI

```powershell
ollama pull qwen2.5:7b
ollama serve
```

The status light turns green. The AI only *suggests* where order fields sit in an unfamiliar layout, and explains drafts. It never writes SQL or saves. Everything else works without it.

## 6. Verify fabric, yarn and accessory mappings (one time, admin)

Out of the box, POPPYS AI saves the order header, style/colour lines and size quantities: `ORDMAS`, `ORDSTYLE` and `ORDSIZE`, plus masters. Fabric, yarn and accessory rows are read, calculated and validated, then shown as **"reference only — not saved"** until you verify their columns.

To verify them, open **Setup → Database mapping** and compare each proposed column with the discovery evidence (fill %, example values, link match %). Then set the table, and each column you agree with, to **Verified**. Every change is recorded in the audit history. Columns whose master table is not in GARMENT_DEMO (fabric quality, yarn count, accessory item) cannot be verified and stay unwritten.

## 7. Ask AI and the data dictionary (first run)

The first time Ask AI is used, POPPYS AI reads the Oracle data dictionary and indexes master names and codes (buyers, styles, colours, sizes, order references). It takes a few seconds and needs only SELECT. To refresh after the database changes, go to **Setup → Data dictionary → Refresh from Oracle**.

In **Setup → Data dictionary** you can:

- confirm or reject the relationships between tables (unconfirmed ones lower an answer's confidence)
- give tables and columns better business names and synonyms (for example, the words your team uses)
- remove confirmed answers

Try asking:

- `How many orders for NEXT?`
- `Size wise quantity for order PK/26-27/S/9001`
- `Buyer wise order quantity this year`
- `Top 5 buyers by quantity`
- `Colour wise quantity for NEXT`

Each answer shows the tables it used. Click a table to preview it and add columns; "+" shows linked tables. Under every answer a check strip shows what was verified (safety review, links, read-only run, total cross-check); open **How this was answered and checked** to see each step and the exact read-only query.

### Check Ask AI's accuracy on your data

```powershell
python scripts\run_benchmark.py
```

Each question in `config\golden_questions.json` is asked as a person would ask it, and its correct answer is worked out separately with hand-written SQL on your database. Expect `12 of 12 correct`; any other line names the question and both results. Add your own questions (with the SQL your team trusts) to `data\golden_questions.json` in the same format, and run the check again after changing the data dictionary or the local AI model. The same check is in **Setup → Answer accuracy**, and the last score is on the Overview.

### Optional: let an AI coding tool study the ERP (read-only)

`scripts\mcp_server.py` is a local MCP server for tools such as Claude Code on this laptop. It uses the read-only account, runs every question through the same checks as Ask AI, has no tool that can write, and records every call in **Setup → Audit history**. Add this to `.mcp.json` in the project folder:

```json
{"mcpServers": {"poppys-erp": {"command": ".venv\\Scripts\\python.exe", "args": ["scripts\\mcp_server.py"]}}}
```

Raw SQL is off. `--allow-sql` adds a `run_select` tool; each statement must still pass the safety review and runs on the read-only account.

## 8. Demo walkthrough

1. **New order** → drop `Merch Dept Worked Manual Excel (2).xlsx` or a buyer's Excel contract report.
2. The reading summary shows what was found: header, sizes, packs, fabric kg and trim tables.
3. **Review** works like the Axpert Sample Order Entry screen. Fields not in the file are outlined in red with a short reason: fill the order reference (OC No) and confirmation date, and choose the quantities to save. **Show me the next one** jumps between them.
4. **Check & continue** validates against Oracle masters, existing orders, units, totals, column sizes and relationships. Anything wrong is outlined on its field or table row.
5. **Check before saving** shows the flowchart of every table that will be written. Click a table to see its rows below, with the Excel cell each value came from. **Accept and save to Oracle** writes everything in one transaction and reads every row back.
6. **Saved** shows the same flowchart from a fresh read of Oracle. Click any table to see the exact rows Oracle returned just now.

## 9. Confirm in SQL Developer (as GARMENT_DEMO)

Replace `PK/26-27/S/9001` with your order reference:

```sql
SELECT ORDMASID, DOCID, BUYER, STYLENO, BONO, ODATE, OQTY FROM ORDMAS WHERE DOCID = 'PK/26-27/S/9001';

SELECT s.SNO, s.SUBNO, c.COLOR, s.ORDQTY, s.SHIPDT
  FROM ORDSTYLE s LEFT JOIN COLORMAS c ON c.COLORMASID = s.COMBO
 WHERE s.ORDMASID = (SELECT ORDMASID FROM ORDMAS WHERE DOCID = 'PK/26-27/S/9001') ORDER BY s.SNO;

SELECT z.SUB3_SUBNO, m.SIZES, z.RQTY, z.PQTY
  FROM ORDSIZE z JOIN SIZE_MAS m ON m.SIZE_MASID = z.GSIZE
 WHERE z.ORDMASID = (SELECT ORDMASID FROM ORDMAS WHERE DOCID = 'PK/26-27/S/9001') ORDER BY z.SUB3_SUBNO, z.ORDSIZEROW;

-- After verifying the BOM mapping:
SELECT FSUBNO, DIA, FGSM, CADWT, COLPCS, POQTY FROM ORDFAB WHERE ORDMASID = (SELECT ORDMASID FROM ORDMAS WHERE DOCID = 'PK/26-27/S/9001');

-- Audit receipt written in the same transaction:
SELECT JOBID, APPROVER, COMMITTED_AT FROM POPPYS_APP.PP_IMPORT_LOG ORDER BY COMMITTED_AT DESC;
```

The same records are visible in the app under **Database** → *Find an order by record ID* (use the ORDMASID shown after saving).

## 10. Tests

All local checks in one command (connection, every test including the live Oracle ones, Ask AI accuracy); the output is
also saved to `data\local_verification.txt`. The Oracle tests save a few orders whose reference starts with `TEST-`
(and any buyer, colour or size names they need); nothing is updated or deleted.

```powershell
.\verify_local.ps1
```

Or step by step:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
$env:POPPYS_TEST_ORACLE='1'; .\.venv\Scripts\python.exe -m pytest -q     # adds live Oracle tests (labelled TEST rows)
$env:POPPYS_TEST_ORACLE='1'; .\.venv\Scripts\python.exe -m pytest -q tests\test_oracle_insight.py   # Ask AI + explorer SQL on your Oracle (read-only)
```

## Troubleshooting

| Message | Fix |
|---|---|
| "Oracle offline" light | Start Docker Desktop, then `docker start oracle26ai`; wait until it is healthy, then refresh |
| "Local Oracle configuration is missing" | Run `.\setup.ps1` (creates `.env.poppys`) |
| "POPPYS_APP already exists. Restore its .env.poppys" | The account was created before but the config file is gone. Copy the old `.env.poppys` back. The password is never reset automatically |
| "POPPYS AI has no INSERT permission" | Run `.\.venv\Scripts\python.exe scripts\grant_bom_tables.py` |
| "formula cells have no calculated value" | Open the workbook in Excel, save, upload again |
| "This exact workbook has already been committed" | Duplicate protection: that file was saved before. See **Saved orders** |
| Ask AI says "Oracle is not connected" | Start Oracle; questions about uploaded files still work offline |
| An answer shows "Medium/Low confidence" | Open **How this was answered**. Confirm the link or add a synonym in Setup → Data dictionary, then ask again |
| Port 8008 busy | Close the other POPPYS window, or end the old `python` process in Task Manager |

## Offline installation (no internet on the laptop)

1. On any computer **with** internet, in a checkout of the release commit: `pip install uv` (only needed on Mac/Linux), then
   `python scripts\build_release.py --python-installer 3.13.5`. It writes `dist\poppys-ai-<version>.zip`.
2. Copy the zip to the laptop (USB / LAN share) and unzip it.
3. If Python 3.13 is not installed: run `installers\python-3.13.5-amd64.exe` (tick "py launcher").
4. In the unzipped folder: `.\setup.ps1 -Offline`. Packages install only from `wheelhouse\`, each checked against the
   SHA-256 recorded when the release was built; a changed or missing file stops the install.
5. Continue with the Oracle steps above (`.\setup.ps1` already created `POPPYS_APP` and `POPPYS_RO`), then `.\start.ps1`.
