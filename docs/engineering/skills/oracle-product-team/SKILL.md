---
name: oracle-product-team
description: Improve the POPPYS garment Excel-to-Oracle project through coordinated import, Oracle, question accuracy, UI, and QA work. Use for this project's audits, development, and release readiness; not for unrelated databases.
---

# Oracle product team

Work in the supplied POPPYS repository (currently D:/oracle). Read its README, applicable AGENTS.md, current Git diff, config/oracle_mapping.json and docs/engineering/KNOWN_LIMITATIONS.md before selecting work. Treat existing reports as dated evidence, not current test results.

Turn a request into measurable outcomes: which workbook values must reach which verified columns, which natural-language questions must match independently written SQL, and which browser journeys must succeed. Keep client credentials and workbooks outside Git and external research tools.

For broad work, assign independent scopes to available agents when delegation is authorized. Each worker must name owned files, return reproducible evidence, and identify unresolved requirements. Use these roles as needed; if agents are unavailable, perform them sequentially:

| Role | Responsibility | Primary files |
| --- | --- | --- |
| Product/garment analyst | Order versus sample meaning, packs/pieces, missing values, client acceptance cases | config/, docs/, supplied schema reference |
| Import/data quality engineer | Original file hash, cell lineage, templates, totals, precision, ambiguity | backend/readers.py, workbooks.py, layouts.py, coverage.py |
| Oracle engineer | Verified mappings, composite joins, privileges, atomic writes, retries, read-back | backend/erp/, oracle_store.py |
| Question accuracy engineer | Schema/value linking, explicit constraints, fan-out, provenance, golden SQL | backend/insight/, config/golden_questions.json |
| UI/accessibility engineer | Upload/review/approval, inline errors, answer evidence, linked records, small screens | frontend/ |
| QA/release engineer | Focused regression tests, full suite, local Oracle opt-ins, Playwright, safe Git delivery | tests/, scripts/check.ps1 |

Preserve these invariants:

- File upload extracts a draft. Ambiguous mappings, unconfirmed units and required data must be resolved before an authorized save. Automatic saving needs a separately validated template/policy and measured acceptance cases.
- One order graph is one Oracle transaction. Preserve idempotency, stale-preview checks and durable outcome reconciliation. A missing response does not prove rollback.
- Table/column existence is not proof of business mapping. Client report joins, screen co-membership and declared keys are different evidence. Keep composite keys; never infer joins from same-screen membership alone.
- Question constraints must be applied or explicitly refused. Totals must not multiply across detail joins; truncated results must not be described as complete. Show SQL, binds, tables, assumptions and quality checks.
- Local GARMENT_DEMO success is not client Axpert acceptance. Verify schema identity before opt-in Oracle tests; only run approved TEST-labelled writes against a local/test copy. Client posting logic and ERP screen acceptance remain separate gates.
- Prefer the existing deterministic compiler and validators. Research primary sources before adding dependencies. REA is for artifact/runtime evidence; use ordinary tools for source analysis.

Validate with focused regressions and scripts/check.ps1; use the Playwright skill for actual browser interactions. Record environment, fixtures, command, result, skips and limitations. Run tests/test_oracle.py and tests/test_oracle_insight.py only after confirming the configured test schema. Compare golden questions to independent SQL via scripts/run_benchmark.py.

Deliver code, research decisions and a dated readiness report. State actual pass/fail counts, remaining client inputs, and Git commit/push status. Never claim universal question accuracy or production readiness from synthetic examples.
