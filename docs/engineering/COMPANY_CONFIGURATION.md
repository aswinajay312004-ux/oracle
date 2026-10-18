# Company configuration

**One installation serves one company.** Multi-company runtime (several companies in one installation) is deliberately
not built: it adds isolation risks before one company works end to end.

## 1. Where configuration lives

| What | Where | Who changes it | Versioned / audited |
|---|---|---|---|
| Company, starting defaults, ERP adapter, schema, write-mapping file | `config/company.json` (version 1) | administrator (file) | git |
| Oracle connection and accounts | `.env.poppys` (git-ignored, never committed) | setup scripts | — |
| Exporter default, sample type default, four-eyes (allow self-approval) | Setup → Settings (SQLite `settings`) | admin | audited (`settings_changed`) |
| Order field aliases and extra required fields | Setup → Order fields (SQLite `field_settings`) | admin | audited |
| Write mapping: which columns are verified | `config/oracle_mapping.json` + Setup → Write mapping overrides (with evidence) | admin | audited, evidence ledger |
| Data dictionary meanings and links | `config/semantic_catalog.json` + Setup → Data dictionary | admin | audited |
| Saved layouts | Setup → Layout templates (SQLite) | operator+ (save), admin (activate) | versions + audit |
| Golden questions (answer accuracy) | `config/golden_questions.json` + `data/golden_questions.json` | admin | git / file |
| Automatic OC number series (e.g. `PK/{fy}/S/{n}` → PK/26-27/S/1321) | `config/company.json → order_number` | administrator (file) | git |
| Time limits | `QUERY_TIMEOUT_S`, `WRITE_TIMEOUT_S` in `.env.poppys` | admin | — |
| Local AI | `OLLAMA_URL`, `OLLAMA_MODEL`, `AI_ALLOWED_HOST` | admin | — |

## 2. Schema guard

`ORACLE_OWNER` (or `company.json → erp.owner`) names the schema read. Any valid schema name may be configured and
explored read-only; **saving is refused** unless the write mapping's `owner` is the same schema (planner error in words).
The previous start-up stop ("scoped to GARMENT_DEMO") is removed. Setup scripts (`bootstrap_local.py`,
`create_readonly_user.py`, `grant_bom_tables.py`) still create GARMENT_DEMO accounts — **Not implemented** for other
schemas.

## 3. Not yet configurable (spec §7.1)

Default currency, approved unit conversions (dozens, pairs, sets, cartons), date rules, document types, approval
workflow beyond four-eyes, per-buyer style/colour aliases: **Not implemented.** Units other than pieces and packs are
asked on the review screen.

## 4. Isolation

A second company gets its own installation (own data folder, own `.env.poppys`, own `company.json`); nothing is shared.
Within one installation, field aliases are global and templates are buyer-scoped for business decisions
(DYNAMIC_TEMPLATE_AND_MAPPING_DESIGN §3).
