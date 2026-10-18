# POPPYS AI — engineering documents

Written for the "Enterprise production engineering" programme (2026-10-18), on top of the eight audit reports in
[`docs/audit/`](../audit/README.md). They describe the product **as it is in this repository**, not as it is planned.
`docs/IMPLEMENTATION_STATUS.md` remains the running log of test results and feature status.

Every report uses the same status words:

| Word | Meaning |
|---|---|
| **Implemented** | The code exists in this branch. |
| **Tested** | Automated tests exercise it and pass (cloud: stand-in Oracle / SQLite; replica: real Oracle Free 23.26 in a throwaway container with the synthetic GARMENT_DEMO replica). |
| **Locally verified** | Run on the owner's Windows laptop against the real local Oracle and recorded. |
| **Environment-blocked** | Needs the laptop, the real Axpert screens, a real Ollama model or Windows; cannot be checked from a cloud session. |
| **Not implemented** | Not in the code. |
| **Known risk** | A weakness that is known and not yet removed. |

| # | Document | Answers |
|---|---|---|
| 1 | [PRODUCT_ARCHITECTURE.md](PRODUCT_ARCHITECTURE.md) | Components, data layers, request flow, what runs where |
| 2 | [EXCEL_DATA_FIDELITY_SPEC.md](EXCEL_DATA_FIDELITY_SPEC.md) | The zero-silent-loss rules and how each is enforced |
| 3 | [EXCEL_EXTRACTION_ENGINE.md](EXCEL_EXTRACTION_ENGINE.md) | How a workbook becomes orders; what is understood and what is escalated |
| 4 | [CANONICAL_BUSINESS_MODEL.md](CANONICAL_BUSINESS_MODEL.md) | The order model, lineage and value states |
| 5 | [DYNAMIC_TEMPLATE_AND_MAPPING_DESIGN.md](DYNAMIC_TEMPLATE_AND_MAPPING_DESIGN.md) | Saved layouts, buyer scope, label anchoring, the mapping studio |
| 6 | [ORACLE_AXPERT_INTEGRATION.md](ORACLE_AXPERT_INTEGRATION.md) | What is written to Oracle, what is proven, the Axpert gap procedure |
| 7 | [TRANSACTION_AND_RECONCILIATION.md](TRANSACTION_AND_RECONCILIATION.md) | Approval, commit, unknown outcomes, read-back, ledger |
| 8 | [UI_UX_IMPLEMENTATION.md](UI_UX_IMPLEMENTATION.md) | The user journey and the screens |
| 9 | [COMPANY_CONFIGURATION.md](COMPANY_CONFIGURATION.md) | Company profile, settings, schema guard |
| 10 | [SECURITY_AND_OFFLINE_RUNTIME.md](SECURITY_AND_OFFLINE_RUNTIME.md) | Sign-in, roles, approvals, offline guarantees |
| 11 | [TEST_STRATEGY_AND_RESULTS.md](TEST_STRATEGY_AND_RESULTS.md) | What is tested where, and the latest results |
| 12 | [DEPLOYMENT_AND_RECOVERY.md](DEPLOYMENT_AND_RECOVERY.md) | Install, run, health, logs, backup, restore, upgrade |
| 13 | [KNOWN_LIMITATIONS.md](KNOWN_LIMITATIONS.md) | What the product does not do, or does not yet prove |
| 14 | [PRODUCTION_READINESS_REPORT.md](PRODUCTION_READINESS_REPORT.md) | Audit-finding matrix, release criteria, final status |
