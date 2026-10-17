# Open-source research for POPPYS sample-order import

Reviewed 17 October 2026. Scope: local Excel upload, editable sample-order preview, affected Oracle rows, explicit acceptance, atomic save. Natural-language reporting is a later feature. Public documentation and source were read; these projects were not installed or benchmarked against the client workbooks during this research.

## Decision

Build a small local application around the actual GARMENT_DEMO schema and the two supplied workbook layouts. Reuse established import interaction patterns, but keep table selection, foreign-key resolution, quantity checks, approval and transactions in a deterministic server component. None of the reviewed projects establishes the client's garment business rules automatically.

The useful separation is:

`Excel cells + provenance → business order draft → validated Oracle row plan → editable preview → accept that plan version → transaction → read-back`

An optional local model can suggest header meanings or explain unresolved fields. A suggestion must refer to actual source cells and known business fields. It must not generate executable insert SQL or choose arbitrary database tables. Unknown information should remain a visible issue the client can fix.

## Candidate findings

| Project | Verified evidence | Fit and decision |
| --- | --- | --- |
| React Spreadsheet Import | MIT; Excel/CSV upload, header matching and editable validation hooks. | Good UI reference or optional component. Does not provide our Oracle business transaction. |
| TableFlow CSV Import | Current official `tableflowhq/csv-import` is MIT; JavaScript and React components, CSV/TSV/XLS/XLSX, template mappings and completion callback. | Good lightweight alternative, including plain JavaScript. Use locally bundled assets if adopted. |
| Docling | MIT code; Excel backend uses openpyxl and extracts separate regions, merged cells and positional provenance. | Useful later for mixed documents. Direct openpyxl is a better starting point for exact formulas, cached values and workbook coordinates here. |
| Vanna | MIT; schema/documentation/example retrieval for SQL questions; official repository is archived. | Reference for later query assistance, not the import writer. |
| DB-GPT | MIT; broad agentic data-analysis platform, database connectors, models and workflows. | Too much infrastructure for the first sample-order import; useful architectural reference for later reporting. |
| REA | MIT; downloaded source, skill and evidence-ledger implementation inspected. | Investigation tool and provenance model. It is not an Excel semantic mapper or Oracle import engine. |

These fit judgments are engineering recommendations, not benchmark results.

### React Spreadsheet Import: useful mapping, insufficient save authority

The [README](https://github.com/UgnisSoftware/react-spreadsheet-import/blob/master/README.md) describes upload, header selection, column matching and validation hooks. `rowHook` operates on changed rows; `tableHook` operates across the table. It also documents `allowInvalidSubmit` defaulting to true, so strict import acceptance cannot rely on an unconfigured UI component.

Actual [getMatchedColumns.ts](https://github.com/UgnisSoftware/react-spreadsheet-import/blob/master/src/steps/MatchColumnsStep/utils/getMatchedColumns.ts) uses header matching and Levenshtein distance when competing columns map to one field. This is lexical matching, not evidence that a column has the correct ERP meaning. For example, order quantity and production quantity can have similar names and different semantics.

Actual [MatchColumnsStep.tsx](https://github.com/UgnisSoftware/react-spreadsheet-import/blob/master/src/steps/MatchColumnsStep/MatchColumnsStep.tsx) distinguishes empty, ignored and matched columns, preserves raw data alongside normalized data, warns on duplicate mapping and offers a confirmation path for unmatched required fields. Adopt the visible mapping/correction pattern; enforce mandatory business fields again on the server. [License](https://github.com/UgnisSoftware/react-spreadsheet-import/blob/master/LICENSE).

### TableFlow: follow the current upstream

The requested research name has two generations. The old `tableflowhq/tableflow` URL returned 404 during this review. A surviving [daq-tools fork](https://github.com/daq-tools/tableflow) identifies its upstream as `tableflowhq/csv-import`; its older README describes a multi-service platform and marks validation as forthcoming. Those older claims should not be attributed to the current component.

The [current official repository](https://github.com/tableflowhq/csv-import) and [README](https://github.com/tableflowhq/csv-import/blob/main/README.md) describe template fields, suggested mappings, required indicators, multiple-source-column combiners and an `onComplete` result containing rows and column definitions. These are useful for flat tabular imports. The client workbooks contain blocks, formulas, sizes across columns and several business entities; converting those layouts into an order draft still needs our parser. No Oracle sample-order implementation was established by this review. [MIT license](https://github.com/tableflowhq/csv-import/blob/main/LICENSE).

### Docling: retain source-cell semantics ourselves

The inspected [Excel backend](https://github.com/docling-project/docling/blob/main/docling/backend/msexcel_backend.py) defines data regions and a merged-cell index, produces worksheet-position provenance, and calls `load_workbook(..., data_only=True)`. Therefore, this inspected load path alone does not retain formula text. It also documents LibreOffice conversion for legacy XLS. These are source observations, not a test of all Docling options.

Recommendation: load each XLSX twice in our importer, preserving formula text and cached values, record sheet/cell coordinates, and detect missing cached results explicitly. Do not silently turn a missing formula result into zero. Keep derived quantities distinguishable from buyer-entered quantities.

Docling supports [multiple document formats](https://docling-project.github.io/docling/usage/supported_formats/) and documents [offline operation with local model artifacts](https://github.com/docling-project/docling/blob/main/docs/faq/index.md). Its [code license is MIT](https://github.com/docling-project/docling/blob/main/LICENSE); individual model licenses must be checked separately if models are adopted. This review did not install models.

### Vanna and DB-GPT: later read-only assistance

[Vanna's official repository](https://github.com/vanna-ai/vanna) reports that it was archived on 29 March 2026. Its [legacy documentation](https://github.com/vanna-ai/vanna/blob/main/README_LEGACY.md) describes retrieving DDL, business documentation and known question/SQL examples, and lists Oracle and Ollama support. [MIT license](https://github.com/vanna-ai/vanna/blob/main/LICENSE).

In the inspected [legacy base source](https://github.com/vanna-ai/vanna/blob/main/src/vanna/legacy/base/base.py), `generate_sql` retrieves related examples/schema/documentation, while `is_sql_valid` returns true when it finds a parsed SELECT statement. That check alone does not establish a single safe statement or correct business meaning. Do not copy it as an authorization boundary. This observation applies to the inspected legacy implementation, not every Vanna version or integration.

[DB-GPT](https://github.com/eosphoros-ai/DB-GPT) documents agents, SQL/code execution, spreadsheet analysis and local-model deployment options. Its actual [Oracle connector](https://github.com/eosphoros-ai/DB-GPT/blob/main/packages/dbgpt-ext/src/dbgpt_ext/datasource/rdbms/conn_oracle.py) uses python-oracledb through SQLAlchemy and reads Oracle dictionary metadata. Some inspected metadata methods interpolate table names into SQL strings. Our application should instead bind metadata filter values and use an allowlist for SQL identifiers. This is a source-level integration observation, not a claim of an exploitable deployment. [MIT license](https://github.com/eosphoros-ai/DB-GPT/blob/main/LICENSE).

For later conversational questions, use a separate read-only database role, bounded supported queries, schema/business definitions, and answers grounded in query results. The write path should continue to require the explicit preview acceptance flow.

## REA review and investigation boundary

The downloaded local checkout is `D:\oracle\tools\rea`, commit `4f3b573b0922bfe04fb8246f52449629ea42c2ce`; `package.json` identifies `rea-agents` 6.5.0 and the license file is MIT. [Upstream](https://github.com/morluto/rea).

The local skill at `.agents/skills/reverse-engineer-anything/SKILL.md` says complete source-repository analysis should use ordinary repository tools, and reserves REA analysis engines for shipped artifacts or runtime claims. Accordingly this research read source directly. The parent's REA MCP attempt was blocked by client approval policy; this research did not retry or bypass that block and does not claim successful MCP analysis, decompilation or installed native engines.

Actual local `src/application/investigation/EvidenceLedger.ts` stores immutable records, treats identical evidence IDs idempotently, rejects conflicting content, validates an entire bundle before merging, and tracks unresolved questions separately. `src/application/javascript/JavaScriptApplicationWorkflowService.ts` validates evidence inputs and derives bounded feature traces. Adapt the principles: immutable source hashes, revisioned drafts, explicit unresolved mappings, acceptance tied to one plan, and an audit result linked to that plan. The product does not need REA as a runtime dependency.

## Concrete build requirements derived from this review

1. Recognize known buyer/manual layouts; preserve source file hash, sheet, cell, original value, formula and any transformation for each mapped field.
2. Display order and production quantities separately. Reconcile size totals, pack/piece conversion and component totals before save. Preserve textual style numbers such as `0008`.
3. Resolve buyer/style/size master IDs against actual Oracle rows. Show ambiguous matches and missing business information for correction.
4. Show each affected table, insert count and exact column values. Rebuild validation and invalidate acceptance after any edit.
5. Use bound parameters, actual schema constraints, concurrency-safe IDs, one transaction for the order graph, idempotency, rollback on failure, and read-back verification.
6. Benchmark the supplied workbooks and deliberate failure cases. No reviewed project's marketing or fuzzy matching establishes a zero-error guarantee for arbitrary future client layouts.

No source code from the reviewed projects was copied into application code by this research task. If a dependency or substantial code is later incorporated, pin its version and retain its license notices. The research report is not evidence that the application implementation or Oracle end-to-end tests have already passed.

## Update — natural-language questions, metadata catalog, explorer and entry UX

Reviewed 17 October 2026 from public repositories, licences, papers and vendor documentation. Nothing was installed or run against client data. The question is narrower than "which NL-to-SQL product is best": **how do these systems avoid picking the wrong tables and columns on a large schema**, since that is what failed in the earlier POPPYS attempt.

### What the reviewed systems do about accuracy

| Project (licence) | Accuracy mechanism | Adopted in POPPYS AI |
|---|---|---|
| Vanna 2.0 (MIT) | Retrieval over DDL, documentation and saved question→SQL pairs; successful queries are remembered | **Confirmed answers**: a user marks an answer correct, and later questions with the same wording but different values reuse its validated plan |
| WrenAI (Apache-2.0 core) | Semantic layer (MDL): models, approved relationships, metrics, value profiling, dry-plan validation, row limits | **Knowledge catalog + relationship graph**: joins only along known links; every query has a row limit |
| Dataherald (Apache-2.0) | "Golden SQL" few-shot examples; distinct values of low-cardinality columns are scanned into the prompt | **Value index**: names like "NEXT" or "N44441" are matched to stored values before anything else |
| CHESS (Apache-2.0) | Value retrieval (LSH) + schema pruning before generation; unit tests on candidates | Value linking first; only a retrieved slice of the catalog is shown to the local model |
| DIN-SQL / MAC-SQL / RESDSQL | Schema linking → question classification → generation → self-correction | Rule-based schema linking from curated synonyms; low confidence is reported, not hidden |
| Oracle Select AI (proprietary) | `enforce_object_list`, table/column comments in the prompt, `showsql`/`explainsql` | Oracle comments are harvested; every answer shows the exact query, tables and links ("How this was answered") |
| Spider 2.0 / BIRD findings | Enterprise schemas average ~800 columns; schema linking is the main failure, and domain hints add roughly 10 points | Curated business names, synonyms and roles per column (`config/semantic_catalog.json`), editable in Setup |
| LangChain SQL agent (MIT) | Lets the model write SQL and retries on errors | **Not adopted.** The model never writes SQL here (see below) |
| OpenMetadata / DataHub / Amundsen (Apache-2.0) | Descriptions, glossary, tags, owners, profiling, usage | Minimal subset: business name, description, synonyms, role, status (verified/proposed/rejected), row statistics, audit of every edit |
| Azimutt (MIT); ChartDB, drawDB (AGPL — ideas only) | Explore a large schema from one table outward: "+" to add related tables, find-path, row cards with linked rows | Relationship map with "+" expansion, linked-record drawer, table preview popup with column picking |
| react-spreadsheet-import (MIT), OneSchema, Flatfile, CSVBox | Errors shown on the cell with a reason; "only rows with errors" filter; never a bare "Row 5: error" | Red outline on the exact field or cell, a one-line reason under it, "Show me the next one", "Only rows that need attention" |

### Design decision: the model proposes an intent, never SQL

The research shows two broad approaches. One lets a language model write SQL and then validates or repairs it. The other has the model, or rules, produce a structured request that a compiler turns into SQL along a known join graph (semantic-layer products). With a 7B local model and a schema of cryptic names, the second is far safer. In POPPYS AI:

1. **Rules first.** Deterministic rules understand the question: value linking, curated synonyms, aggregation words, "by / wise / per" grouping, date phrases, "top N" and comparisons. This works offline and is fully explainable.
2. **Local AI only as a fallback.** It is consulted only when the rules are unsure. It may only return a JSON *intent* restricted to a retrieved slice of the catalog, and the intent must compile against the catalog or it is discarded.
3. **The compiler is the only SQL author.** It:
   - takes tables and columns only from the catalog;
   - takes joins only from the relationship graph, preferring confirmed many-to-one links;
   - uses bind variables;
   - always produces a single `SELECT` with a row limit;
   - turns child-table filters into `EXISTS`, so rows are counted once;
   - counts with `COUNT(DISTINCT key)`;
   - refuses any total that would double count.
4. **Execution is read-only.** Every query runs in `SET TRANSACTION READ ONLY`, with a call timeout and a bounded fetch, and is always rolled back.
5. **Provenance on every answer:** what was understood, a confidence level with reasons, the values matched, the tables, the links used with their status, the SQL with binds, and the row count.

### Large databases (17 GB+)

- Row counts in the explorer are exact for normal tables and taken from `ALL_TABLES.NUM_ROWS` (shown with "≈") for very large ones.
- Paging uses keyset paging on the record ID (`WHERE id < :last ORDER BY id DESC FETCH FIRST n`) instead of `OFFSET`.
- The catalog is harvested once from the dictionary (`ALL_TAB_COLUMNS`, `ALL_TABLES`, `ALL_*_COMMENTS`, `ALL_CONSTRAINTS`) and cached. Lookups scale with the size of the catalog, not of the data.
- The value index covers master names and searchable codes only, capped per column. Codes saved after the last refresh are looked up live.

### REA (Reverse Engineer Anything)

Repository: `morluto/rea` (MIT). I read the repository, including `.agents/skills/reverse-engineer-anything/SKILL.md`, but did not run or install it.

- REA is an MCP server and CLI for analysing **shipped artifacts without source**: binaries, Electron bundles, APKs, firmware and packet captures. Its own skill says to skip it for ordinary source-repository analysis. The open-source products above publish their source, so REA's tooling does not apply.
- Its official installer downloads unpinned code (`npx rea-agents@latest`, `curl | bash`). That conflicts with this project's offline and pinned-dependency rules, so it was not installed into the project.
- **Its method was adopted.** Each finding above is a claim backed by evidence: the repository and licence, the mechanism, and what we took from it. What we observed is kept separate from our own design choices. Unknowns stay open: real-Oracle behaviour of the new screens is verified only by the opt-in tests on the client's machine (`tests/test_oracle_insight.py`).
