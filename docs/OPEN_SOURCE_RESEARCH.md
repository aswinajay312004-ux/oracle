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
