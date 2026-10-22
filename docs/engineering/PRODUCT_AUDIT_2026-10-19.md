# Product audit and development — 19 October 2026

> Checkpoint note (22 October 2026): This report records a prior development session, not the current state of this branch. The newly recovered regression tests expose 19 failures and 11 passes across `test_insight_accuracy.py`, `test_sample_quantity_headers.py`, and `test_schema_reference.py` at this checkpoint. The claimed fixes below require reapplication and verification before this branch is released. The active `claude/optimistic-gates-nkfe7f` branch uses a different single-table architecture.

The product has a working local Excel → draft → verified mapping → approval → Oracle transaction → read-back workflow, plus a catalog-based question compiler and linked-record explorer. This review found and fixed concrete correctness defects. It does **not** establish that direct inserts are accepted by the client's Axpert screens or that every question over the entire client schema is supported.

## Schema evidence supplied for this review

The attached GARMENT text export was parsed in full using `scripts/audit_schema_reference.py`. It was not copied into Git. SHA-256: `f0b097b52fad34582673ede2473c70beadd3cd6122f9601a034137cfdd3636ea`.

| Check | Observed result |
| --- | --- |
| Client reference tables | 11,838 |
| Columns actually listed in table sections | 62,083; every table's local column count reconciles |
| Header's claimed column count | 70,992; **does not reconcile with the sections** |
| Distinct evidenced column links | 4,794 |
| Local snapshot tables | 19 |
| Local snapshot columns missing from reference | None |
| Mapped transaction columns missing from reference | None |
| Master label missing from reference | `FABMAS.FABRIC` |
| Configured masters absent from local snapshot | FABMAS, COUNTMAS, ACCMAS |

The reference lists `FABMAS.FAB` and `FABMAS.FABNAME`. Choosing the correct business label needs an Axpert screen/report example; existence alone is insufficient. The proposed fabric mapping remains unverified. Same-screen membership is not converted into a column join. ORDSIZE's line relationship retains both order identity and the sub-line reference to avoid cross-order collisions.

Reproduce the structural audit with:

```powershell
.\.venv\Scripts\python.exe scripts/audit_schema_reference.py path\to\catalog.txt --output output\playwright\schema-reference-audit.json
```

This command checks structural names/counts, not datatypes, database privileges, row contents, triggers or application posting behavior. It refuses partial/malformed table sections.

## Architecture and responsibility

| Area | Existing implementation | What still matters |
| --- | --- | --- |
| Upload fidelity | Original bytes/hash, bounded isolated parsing, sheet/cell/formula metadata, coverage manifest | Measure extraction on anonymized client files, including revisions and unusual layouts |
| Mapping | Built-in detectors, reusable templates/anchors, explicit field registry, master resolution | Verify missing masters and every new business field before enabling writes |
| Validation | Required fields, units, totals, duplicates, column lengths, exceptions | Client-approved business definitions for sample versus bulk, packs and date conventions |
| Persistence | Local SQLite drafts/revisions/audit; restricted Oracle writer; import receipt and order keys | Retention/restore drills, commit-loss testing under actual network failure, multi-process deployment review |
| Questions | Rule/value linking, local model intent fallback, compiler, SQL AST safety review, read-only account, join/total checks | Domain dictionary and a much larger golden-question corpus across client modules |
| UI | Upload/review/approval, inline issues, read-back, graph, table explorer, roles | Keyboard/mobile acceptance by operators and approvers; real client language examples |

## Correctness changes made

1. Filters on two columns of the same child table now retain each column and datatype inside a shared EXISTS clause. Previously every predicate could use the first column.
2. EXISTS-only tables now appear in provenance and the relationship diagram.
3. Parent SUM/AVG mixed with child MIN/MAX/COUNT now refuses plans that would multiply the parent measure.
4. Group summaries find the actual maximum instead of calling the first ascending row the highest. Limited results are explicitly scoped to shown groups.
5. A date `not equal` filter excludes the entire day, including timestamps after midnight. Non-finite numeric filters are rejected.
6. Unsupported grouped comparisons, missing date relationships and unsupported sorts are explicitly refused. IN selections longer than 50 values are rejected instead of silently shortened.
7. Unconfirmed relationships or unresolved quality checks cannot receive high confidence. Equal values from two differently interpreted queries do not prove semantic agreement.
8. A flat table explicitly headed `Sample Qty` retains sample meaning, sample lines and header-cell provenance. It is no longer defaulted to bulk/order quantities.
9. An ambiguous or invalid source order date stays unresolved. The company default cannot replace it with today's date and erase the source problem.
10. Unexpected server errors no longer falsely promise that nothing was saved; users are directed to check Saved orders after a save failure.
11. Ask AI shows a prominent truncation notice covering the table, chart and CSV. Loading status is exposed to screen readers. Login copy describes supported layouts accurately.
12. Added a reusable project team skill and a tested schema-reference audit tool. Browser artifacts/session state are excluded from Git.

## Validation evidence

- Initial existing suite: **523 passed, 35 skipped**.
- Local Oracle transaction + question integration: **12 passed** on configured `GARMENT_DEMO`, Oracle 23.26.3.0.0. Includes TEST-labelled inserts, rollback, duplicate/retry behavior and read-only query checks. This is a local database, not the client's Oracle 12.2 environment.
- Full suite with local Oracle enabled during development: **550 passed, 18 skipped**; later tests/changes receive a final release check recorded below.
- Independent SQL benchmark: **12/12 correct** against the local Oracle, including totals, buyer/size/colour/month breakdowns and a real stored order reference. The corpus is small and does not establish arbitrary-language accuracy.
- Focused suites cover the new filter, aggregation, date, sample-header and schema-audit failures. Ruff and JavaScript syntax/CSP checks pass at the recorded checkpoints.
- Playwright uses an isolated temporary application workspace and synthetic workbooks. It does not reuse the owner's accounts/drafts. See the final browser evidence below for completed journeys and limitations.
- The existing Starlette/httpx deprecation warning remains. Runtime dependency migration should be assessed separately, not substituted into production during an accuracy fix.

## Research and adoption decisions

| Primary source | Decision for this product |
| --- | --- |
| [python-oracledb transactions](https://python-oracledb.readthedocs.io/en/latest/user_guide/txn_management.html) | Preserve one transaction per linked order graph; avoid independent commits per table. Batch writes may reduce round trips after profiling, but cannot weaken read-back, rollback or idempotency. |
| [WrenAI](https://github.com/Canner/WrenAI) | Its governed context/semantic approach supports investing in the business catalog and known relationships. Replacing the working Oracle compiler is not required for these fixes. |
| [Vanna](https://github.com/vanna-ai/vanna) | Retrieval of schema/examples is relevant to question understanding. A generated query still needs independent access, semantic and execution checks; it is not a data-entry writer. |
| [Great Expectations](https://docs.greatexpectations.io/docs/core/introduction/) | Use explicit, reproducible data-quality expectations. Existing deterministic validations and regression tests cover the current import path; a second validation runtime would need a concrete unmet requirement. |
| [AI Templates agents](https://www.aitmpl.com/agents/) / [upstream project](https://github.com/davila7/claude-code-templates) | These are Claude-oriented development templates. A tailored Codex `oracle-product-team` skill routes this repository's real modules and checks instead of assuming generic agent installation supplies garment expertise. |
| [Playwright skill](https://github.com/openai/skills/tree/main/skills/.curated/playwright) | Installed and used to exercise the real browser. |

These are design decisions informed by the sources, not benchmark claims about those projects. No client records were sent to web research.

## Work required before client production

| Priority | Required work | Acceptance evidence |
| --- | --- | --- |
| P0 | Resolve catalog header discrepancy and fabric/count/accessory masters | Regenerated full schema export; confirmed labels and keys with real screen/report examples |
| P0 | Select the supported Axpert posting channel | Save and reopen a representative order in a test copy of Axpert; confirm triggers, sequences, defaults, linked screens and downstream reports |
| P0 | Approve mappings for all required client fields | Excel cell → canonical field → table.column → read-back matrix, with no unexplained exclusions |
| P0 | Build an anonymized workbook and question acceptance corpus | Every critical quantity/date/ID matches approved expected values; each question matches independent SQL, including ambiguity/refusal cases |
| P1 | Expand beyond order entry by business module | Buyer/order/style → planning/fabric/yarn → production → shipment modules, each with verified joins, grain, units and cancellation rules |
| P1 | Qualify automatic posting | Only a specifically approved stable template, known masters, zero unresolved validation issues and an explicit company posting policy qualify; retain an exception queue |
| P1 | Scale and operations | Measure concurrent users, large-table query latency, upload latency, restore time and failure recovery on a production-sized test copy |
| P1 | Data lifecycle | Define retention for original files, drafts, audit and receipts; encrypt backups, test restoration, and preserve lineage before any approved archival job |
| P2 | Natural Tamil/Thanglish coverage | Collect actual operator questions, expected interpretations and independent SQL; expand synonyms/examples and evaluate the local model |

Reliable future reuse depends on retaining source files, stable business IDs, explicit units, confirmed relationships and versioned mappings. Keep full source evidence even when a field is not yet posted. AI model context is not the system of record: Oracle holds business records, while local storage holds import evidence and workflow history.

The installed `oracle-product-team` skill is versioned at `docs/engineering/skills/oracle-product-team/SKILL.md`. It coordinates product, import/data quality, Oracle, question accuracy, UI and QA roles; when agents are unavailable the same work is performed sequentially. This session's delegated workers stopped at a usage limit, so subsequent implementation and verification were performed by the main agent.
