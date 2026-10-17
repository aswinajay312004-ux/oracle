# POPPYS AI — Architecture

Goal: an order document goes in, and the correct rows land in the correct Oracle tables and columns. Every value can be explained, nothing is written without approval, and every save is proven by reading it back. The same foundation must later support natural-language questions over a much larger schema without being rebuilt.

```
Excel / CSV / JSON ─► 1 Document layer ─► 2 Business draft ──► 3 ERP planner ──► 4 Approval ──► 5 Writer ──► 6 Read-back
                     (cells + provenance) (orders, lines,     (registry +        (exact plan     (one txn,    (fresh SELECT,
                                        sizes, fabric,       catalog +          hash)           bind vars,   flowchart,
                                        trims)               rules)                             lock)        explorer)
                         ▲                                      ▲
                Templates · local AI suggestions        Mapping registry ◄── Schema discovery (evidence)
                                                                 └──► Semantic model (future NL Q&A, reports)
```

## Layers

| # | Layer | Code | Responsibility | Never does |
|---|---|---|---|---|
| 1 | Readers | `backend/readers.py`, `backend/isolation.py`, `backend/numfmt.py` | Every accepted format (Excel 2007+ and 97-2003, `.xlsb`, `.ods`, Excel 2003 XML, HTML/MHTML exports, CSV/TSV, JSON) becomes one cell model: value, formula, hidden state, number format, flags, source address. The format comes from the file's bytes. Each upload is parsed in a worker process with a time and memory limit, behind size caps applied before any parser runs | Execute formulas, macros or external links; guess a number it cannot read safely (it keeps the text and flags it) |
| 1a | Order understanding | `backend/workbooks.py`, `backend/fields.py`, `backend/matcher.py` | Finds the order in the cell model, in order: saved buyer templates, merch BOM, buyer contract, size matrices and flat tables. Unknown layouts get an explainable matcher proposal, checked by the validator. Decides the unit (pieces or packs) and dates from evidence, and raises review items (total mismatch, second date, hidden data, the same order twice) | Guess from filenames or sheet titles, or fill a field the file does not support |
| 1b | Assistance | `backend/ai.py` | Local Ollama proposals for unfamiliar layouts, always passed through the same validator as the matcher | Produce SQL or values that skip review |
| 2 | Draft | `backend/local_store.py` | Versioned drafts, field-level correction log. Workbook provenance is kept server-side even when a user edits | Lose the original source cell |
| 3 | Planner | `backend/erp/planner.py` | Business rules: required fields, whole quantities, size and total reconciliation, pack checks, duplicates (file hash, order reference, sizes), cancelled or ambiguous masters, fabric kg = pieces × g ÷ 1000 × rejection, yarn % = 100 with an exact kg split, composition mismatch, accessory scaling, size-name alignment, live column length/precision/scale/nullability. Produces a hashable list of row operations | Write anything |
| 3a | Mapping registry | `config/oracle_mapping.json`, `backend/erp/mapping.py` | Business field → `TABLE.COLUMN`, master references, parent links, SUBNO links. Each column is `verified` or `proposed`; admin overrides are audited | Let an unverified column be written |
| 3b | Catalog | `backend/erp/catalog.py` | `OracleCatalog` (live dictionary, privileges, masters, existing keys) or `SnapshotCatalog` (offline preview and tests; can never approve) | Mix live and offline evidence |
| 4 | Approval | `backend/main.py` | Roles (admin, approver, operator, viewer), optional four-eyes rule, approval bound to the exact preview hash and draft version | Approve a stale, offline or erroneous preview |
| 5 | Writer | `backend/oracle_store.py` | Global write lock. Re-plans inside the transaction and requires the same hash. Sequence keys. Bind variables only; identifiers come from the registry and are checked against the dictionary. Reads back every inserted row; receipt in `PP_IMPORT_LOG`; rollback on any error | Overwrite or update existing rows |
| 6 | Read-back | `backend/erp/readback.py`, explorer endpoints | Fresh `SELECT` by primary key, compared with what was approved. Master IDs decoded to labels. Graph edges from the registry links | Show success from the receipt alone |
| 7 | Semantic model | `backend/erp/semantic.py`, `GET /api/semantic-model` | Entities, verified attributes, master references and scoped join conditions, generated from the registry | Contain anything the writer does not also trust |
| 8 | Knowledge catalog & Ask AI | `backend/insight/` | Business meaning of every visible table and column, relationship graph, value index, natural-language questions compiled to read-only SQL | Execute model-written SQL, write data, or join outside the relationship graph |

## Why this avoids the earlier mapping problems

The previous natural-language attempt tried to find tables and columns across thousands of candidates at question time. Here, meaning is decided **once**, with evidence, and reused everywhere:

1. `scripts/discover_schema.py` collects facts: constraints, triggers, fill rates, examples, link match rates, and whether reference columns resolve.
2. A person verifies the mapping in **Setup → Database mapping**. That is a business decision, recorded with who and when.
3. The planner writes only verified columns. The semantic model exposes only verified attributes and joins.
4. Join rules encode real relationships. For example, a child SUBNO is matched only within the same ORDMASID, because SUBNO alone is not unique.

## Data intelligence layer: knowledge catalog, Ask AI and explorer

Built on the same foundation as data entry, so questions use the same meanings as writes.

```
question ─► understand (rules; local AI only as checked fallback) ─► intent (JSON)
         ─► compile (catalog + relationship graph) ─► one read-only SELECT with binds and a row limit
         ─► execute (SET TRANSACTION READ ONLY, call timeout, bounded fetch, rollback)
         ─► answer: summary + result table + linked-table flowchart + exact query + confidence
```

| Part | Code | Responsibility |
|---|---|---|
| Knowledge catalog | `backend/insight/knowledge.py`, `config/semantic_catalog.json` | Merges four layers, in order: the live dictionary (or the offline snapshot), the write registry, the curated business meaning, and admin edits. Every column has a business name, synonyms and a role (label / measure / date / reference / key / attribute / system). Each relationship has a status: verified, declared (Oracle FK), proposed, inferred (naming) or rejected |
| Relationship graph | `Knowledge.path()` | Shortest join path. It prefers confirmed links and many-to-one steps, and scopes child links by ORDMASID (SUBNO alone is not unique) |
| Understanding | `backend/insight/understand.py` | Links values first ("NEXT" → `BUYMAS.BUYERNAME`), then matches table and column words longest-phrase-first. Handles count/total/average/top-N, "by / per / wise", date phrases and comparisons. Picks the base table whose quantity can be totalled without crossing a one-to-many link. Every guess lowers confidence and is reported |
| Compiler | `backend/insight/query.py` | The **only** SQL author. Identifiers come from the catalog, joins from the graph, values are binds, reference IDs are shown as master names, and child filters become `EXISTS`. It refuses totals that would double count |
| Engine | `backend/insight/engine.py` | Runs a question or an edited intent and builds the answer, the provenance and the graph. It reuses confirmed examples with new values |
| Services | `backend/insight/service.py` | Dictionary harvest and cache, value index, read-only executors, and the local "uploaded files" source |
| API | `backend/main.py` | `/api/chat/*`, `/api/knowledge/*` (admin edits audited), `/api/explorer/*` (exact counts, or "≈" statistics for very large tables; keyset paging; linked records) |

Why it avoids the earlier mapping problem:

- **Meaning is decided once, with evidence.** The local model never writes SQL. It can only propose an intent, and that intent must compile against the catalog.
- **Everything is visible.** Each answer shows what was understood, the values matched, the tables and links (confirmed or not), and the exact query.
- **Mistakes are corrected once and reused.** An admin fixes a business name or confirms a link in Setup → Data dictionary. A user marks an answer correct, and similar questions reuse it.

## Scaling to a large enterprise schema

- **Registry per screen or department.** `config/oracle_mapping.json` is one file today. The loader can merge several (order entry, fabric planning `FPLANM`/`FPLANDET`/`FPCOL`/`FABP`/`YARNP`, purchasing…) without code changes. Each new screen is a new registry section plus evidence and verification.
- **Bounded catalog queries.** The catalog reads only the tables the registry references, so the cost does not grow with thousands of unrelated tables.
- **Scoped masters.** Master lookups are capped (5,000 rows). Large masters should switch to indexed lookups by label; this changes only `Catalog.masters`.
- **Pure, testable logic.** Planner and validation are pure functions over a catalog interface, and covered by golden tests. The writer is the only component that writes Oracle data.
- **Questions on a big schema.** Retrieval works over the catalog (thousands of tables are fine in memory), not over the data. The value index stores distinct names and codes only, capped per column; transactional columns of very large tables are looked up live. Queries are bounded by row limits and call timeouts.
- **Explorer on a big schema.** Uses dictionary statistics for very large tables, keyset paging on record IDs, and explicit column lists (never `SELECT *`).

## Security and operations

| Part | Code | What it does |
|---|---|---|
| Web security | `backend/security.py` | Request body limits, PBKDF2-SHA256 passwords (600,000 iterations, rehash on sign-in), 12-character minimum with a common-password list, sign-in throttling and lockout stored in SQLite, idle (30 min) and absolute (8 h) session limits, CSRF checks on Origin/Referer and Sec-Fetch-Site, strict CSP and security headers, error answers with a reference ID instead of internals |
| Audit | `backend/local_store.py` | Append-only, hash-chained audit log; Setup → Audit can verify the chain is untouched |
| Logs and health | `backend/logs.py`, `backend/main.py` | JSON-lines logs with request IDs and redaction, Windows-safe rotation; `/healthz` (process up), `/readyz` (the local store opens and accepts a write; low disk and an unreachable Oracle only warn, because uploads and previews work without Oracle), `/api/version` |
| Oracle accounts | `backend/oracle_store.py`, `scripts/create_readonly_user.py` | Two pools: `POPPYS_APP` for the save, `POPPYS_RO` (CREATE SESSION plus READ grants, and a READ ONLY user on Oracle 23ai) for everything read-only |
| Windows service | `deploy/windows/` | WinSW service as LocalService, side-by-side releases with a `current` junction, upgrade with backup and automatic rollback, scheduled backups |
| Backups | `scripts/backup.py` | Online SQLite backups (never a plain file copy), integrity check, SHA-256 manifest, retention of 14 daily / 8 weekly / 12 monthly, restore into an empty folder or over a stopped app, monthly restore drill |

Full checklist and threat model: [SECURITY.md](../SECURITY.md). Operator steps: [RUNBOOK.md](RUNBOOK.md).

## Open-source and research decisions

The decision record is [RESEARCH_DECISIONS.md](RESEARCH_DECISIONS.md): what was adopted, what was rejected, and why, for each area (readers, matching, posting, security, operations). The earlier survey, [OPEN_SOURCE_RESEARCH.md](OPEN_SOURCE_RESEARCH.md), is kept for history.

- Interaction patterns were adopted: column matching with evidence, confirm-before-import, versioned layout templates, an evidence ledger from source cell to Oracle column.
- Matching ideas came from COMA/Valentine, FlexMatcher and spreadsheet schema matching; no code was copied.
- No large platform was embedded (Apache Hop, DataMosaic, DocETL, Budibase, Windmill …). None knows garment business rules, Axpert relationships or approval-bound Oracle transactions, and each would add heavy runtime dependencies to an offline laptop.
- The runtime uses permissive, pinned libraries only: FastAPI, openpyxl, xlrd, python-calamine, defusedxml, olefile, charset-normalizer, python-oracledb, and optionally Ollama for the local AI.
