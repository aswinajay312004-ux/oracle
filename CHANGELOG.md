# Changelog

All notable changes to POPPYS AI. The version is in `VERSION` and shown by `/api/version` and Setup → System checks. Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added
- CI runs the browser tests (the diagram and every app screen under the strict CSP) in Chromium. The job fails when the browser is missing, so it cannot pass by skipping.
- `SECURITY.md`, `docs/SECURITY_ASVS_L2.md` (OWASP ASVS 5.0 L2: requirement → code → test), `docs/USER_GUIDE.md` and `docs/adr/0001-posting-path.md` (the Axpert posting decision).

### Fixed
- A reader package missing on the laptop is reported as such ("run setup.ps1 again"), not as a damaged workbook.
- Windows CI: long test IDs and a subprocess without `SYSTEMROOT`. The secret scan's one false alarm (a made-up test password) is now accepted by its exact fingerprint.

## [0.9.0] - 2026-10-17

The first complete build. It has been rehearsed on Oracle Free 23 with a replica of GARMENT_DEMO, but not yet on the client laptop.

### Reading files
- Every common spreadsheet format and JSON goes into one cell model:
  - Excel 2007+ (`.xlsx` `.xlsm` `.xltx` `.xltm`) and 97-2003 (`.xls`), `.xlsb`, `.ods`;
  - Excel 2003 XML, and HTML/MHTML "Excel" exports;
  - CSV/TSV, with encoding and delimiter detection;
  - JSON: records, rows, sheet maps, NDJSON, envelopes, nested documents.
- The format is chosen from the file's bytes. Protected and non-spreadsheet files are refused with a reason.
- Uploads are parsed in a separate worker with time and memory limits, behind zip-bomb, cell, merge, nesting and text caps.
- Number formats are kept: leading zeros such as `00123` survive. Hidden rows and columns are seen and reported. Formulas are never evaluated.
- PDF and OCR were removed from scope.

### Understanding orders
- 20 order fields, each with aliases. An admin can edit aliases and required flags in Setup → Order fields.
- An explainable field matcher (name and value evidence, one-to-one, confidence bands) and a validator that every proposal must pass: matcher, local AI, template or person.
- Versioned buyer templates with field anchors. A template is used only when its labels still match, so nothing carries over between buyers.
- Asked, never guessed:
  - pieces or packs (and pieces per pack);
  - an ambiguous day/month in a month-first file;
  - decimal-comma quantities such as `2.000`;
  - a description in the style column;
  - two possible quantity columns.
- Review items appear under "Please read before approving": total rows that disagree, a second ship date, data in hidden rows, the same order on two sheets.
- Parsing is linear: a 4,000-line file reads in about a second.
- The layout screen checks a person's choices with a dry run. The local AI is asked only from its own button.

### Saving to Oracle
- A registry-driven planner checks every rule before approval: required fields, whole quantities, totals, units, column limits, masters, duplicates.
- One transaction under a write lock, re-planned inside the transaction against the approved hash. Bind variables only; every row read back; receipt in `PP_IMPORT_LOG`; rollback on any error.
- The duplicate order check uses the DOCID index and POPPYS's key table. It falls back to a case-insensitive scan only while ORDMAS is small.
- Connection pools. A read-only `POPPYS_RO` account (`scripts/create_readonly_user.py`) serves Ask AI, the explorer, history and read-back.
- Scoped to `GARMENT_DEMO`. Production posting waits on Agile Labs (ADR 0001).

### Asking and exploring
- Ask AI turns plain-language questions into a checked intent, then one read-only SELECT. Each answer shows its query and a linked-table diagram.
- Database explorer with business names, statistics-based counts for very large tables, keyset paging, linked records and order lookup.
- Rehearsed on 300,000 orders and 2.4 million size rows: lookups use index range scans.

### Security and operations
- Streaming body limits and PBKDF2-SHA256 at 600k iterations with rehash. 12-character passwords and a common-password list.
- Persisted throttling and lockout, with admin unlock. Session limits of 30 minutes idle and 8 hours absolute, and "sign out everyone else".
- CSRF checks. A strict CSP with no inline styles or scripts anywhere. Security headers, and safe error answers that carry a reference ID.
- Append-only, hash-chained audit log, which can be verified from Setup.
- JSON-lines logs with request IDs and redaction. `/healthz`, `/readyz`, `/api/version`.
- SQLite with synchronous=FULL and numbered migrations, each run after a verified backup.
- `scripts/backup.py`: verified online backups, retention, restore and a restore drill.
- Windows service (WinSW, LocalService) with install, upgrade with automatic rollback, rollback, backup task and uninstall scripts.
- CI:
  - lint, a secret scan (own rules plus Gitleaks over all history), a dependency audit;
  - unit tests on Linux and Windows;
  - a real Oracle Free 23 job with 20,000 bulk-loaded orders.
