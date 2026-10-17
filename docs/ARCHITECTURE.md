# POPPYS AI — Architecture

Goal: an order document goes in, and the correct rows land in the correct Oracle tables and columns. Every value can be explained, nothing is written without approval, and every save is proven by reading it back. The same foundation must later support natural-language questions over a much larger schema without being rebuilt.

```
Excel / PDF ──► 1 Document layer ──► 2 Business draft ──► 3 ERP planner ──► 4 Approval ──► 5 Writer ──► 6 Read-back
                (cells + provenance)   (orders, lines,      (registry +        (exact plan     (one txn,    (fresh SELECT,
                                        sizes, fabric,       catalog +          hash)           bind vars,   flowchart,
                                        trims)               rules)                             lock)        explorer)
                         ▲                                      ▲
                Templates · local AI suggestions        Mapping registry ◄── Schema discovery (evidence)
                                                                 └──► Semantic model (future NL Q&A, reports)
```

## Layers

| # | Layer | Code | Responsibility | Never does |
|---|---|---|---|---|
| 1 | Document | `backend/workbooks.py`, `backend/pdfdocs.py` | Reads every cell with value, formula, hidden state and source address. Detects layouts by structure, in order: saved templates, merch BOM, buyer contract, flat table. Extracts fabric and accessory tables. Text PDFs become virtual sheets that go through the same detectors | Guess from filenames or sheet titles, or execute workbook content |
| 1b | Assistance | `backend/ai.py` | Deterministic layout guesser plus local Ollama proposals for unfamiliar layouts, validated against real cells | Produce SQL or values that skip review |
| 2 | Draft | `backend/local_store.py` | Versioned drafts, field-level correction log. Workbook provenance is kept server-side even when a user edits | Lose the original source cell |
| 3 | Planner | `backend/erp/planner.py` | Business rules: required fields, whole quantities, size and total reconciliation, pack checks, duplicates (file hash, order reference, sizes), cancelled or ambiguous masters, fabric kg = pieces × g ÷ 1000 × rejection, yarn % = 100 with an exact kg split, composition mismatch, accessory scaling, size-name alignment, live column length/precision/scale/nullability. Produces a hashable list of row operations | Write anything |
| 3a | Mapping registry | `config/oracle_mapping.json`, `backend/erp/mapping.py` | Business field → `TABLE.COLUMN`, master references, parent links, SUBNO links. Each column is `verified` or `proposed`; admin overrides are audited | Let an unverified column be written |
| 3b | Catalog | `backend/erp/catalog.py` | `OracleCatalog` (live dictionary, privileges, masters, existing keys) or `SnapshotCatalog` (offline preview and tests; can never approve) | Mix live and offline evidence |
| 4 | Approval | `backend/main.py` | Roles (admin, approver, operator, viewer), optional four-eyes rule, approval bound to the exact preview hash and draft version | Approve a stale, offline or erroneous preview |
| 5 | Writer | `backend/oracle_store.py` | Global write lock. Re-plans inside the transaction and requires the same hash. Sequence keys. Bind variables only; identifiers come from the registry and are checked against the dictionary. Reads back every inserted row; receipt in `PP_IMPORT_LOG`; rollback on any error | Overwrite or update existing rows |
| 6 | Read-back | `backend/erp/readback.py`, explorer endpoints | Fresh `SELECT` by primary key, compared with what was approved. Master IDs decoded to labels. Graph edges from the registry links | Show success from the receipt alone |
| 7 | Semantic model | `backend/erp/semantic.py`, `GET /api/semantic-model` | Entities, verified attributes, master references and scoped join conditions, generated from the registry | Contain anything the writer does not also trust |

## Why this avoids the earlier mapping problems

The previous natural-language attempt tried to find tables and columns across thousands of candidates at question time. Here, meaning is decided **once**, with evidence, and reused everywhere:

1. `scripts/discover_schema.py` collects facts: constraints, triggers, fill rates, examples, link match rates, and whether reference columns resolve.
2. A person verifies the mapping in **Setup → Database mapping**. That is a business decision, recorded with who and when.
3. The planner writes only verified columns. The semantic model exposes only verified attributes and joins.
4. Join rules encode real relationships. For example, a child SUBNO is matched only within the same ORDMASID, because SUBNO alone is not unique.

## Future natural-language Q&A (designed in, not built yet)

Future question answering can use this pipeline:

```
question → entity/attribute retrieval from the semantic model (local embeddings)
         → join path from the semantic model's join list (no free joins)
         → read-only SQL template with binds → row limit → execution as a read-only account
         → answer with table + the exact query + the business labels used
```

The building blocks exist today:

- the semantic model API
- business labels for every column
- discovery evidence
- the read-only explorer queries
- the local model integration

A separate read-only Oracle account (SELECT grants only) should be used for Q&A.

## Scaling to a large enterprise schema

- **Registry per screen or department.** `config/oracle_mapping.json` is one file today. The loader can merge several (order entry, fabric planning `FPLANM`/`FPLANDET`/`FPCOL`/`FABP`/`YARNP`, purchasing…) without code changes. Each new screen is a new registry section plus evidence and verification.
- **Bounded catalog queries.** The catalog reads only the tables the registry references, so the cost does not grow with thousands of unrelated tables.
- **Scoped masters.** Master lookups are capped (5,000 rows). Large masters should switch to indexed lookups by label; this changes only `Catalog.masters`.
- **Pure, testable logic.** Planner and validation are pure functions over a catalog interface, and covered by golden tests. The writer is the only component that touches Oracle data.

## Open-source and research decisions

See `docs/OPEN_SOURCE_RESEARCH.md` (React Spreadsheet Import, TableFlow, Docling, Vanna, DB-GPT, REA). Each was evaluated against the client's actual workflow:

- Their interaction patterns were adopted: column matching, confirm-before-import, evidence ledgers.
- Their code was not embedded. None provides garment business rules, Axpert relationships or approval-bound Oracle transactions, and adding them would bring heavy runtime dependencies that conflict with the offline requirement.
- The runtime uses only permissive, pinned libraries: FastAPI, openpyxl, pdfplumber, python-oracledb, and optionally Ollama.

REA's evidence-ledger idea is reflected in two places: the discovery evidence, and the "source cell → value → Oracle column" chain shown in the preview.
