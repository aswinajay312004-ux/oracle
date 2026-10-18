# POPPYS AI: implemented vs missing features (feature ledger)

Audit date: 2026-10-18. Branch `claude/fervent-babbage-jqo8r4`, commit `e811402`. Documentation only: no code, test or
configuration was changed. Nothing here claims production readiness or 100% accuracy.

## Summary

- **218 features and decisions** from the README and the eight docs were checked against code, tests and runtime probes.
  **93 IMPLEMENTED**, **42 PARTIALLY IMPLEMENTED**, **58 DOCUMENTED ONLY**, **24 NOT VERIFIED**, **1 NOT IMPLEMENTED**
  (full counts are at the end).
- The cloud test suite is green: `python3 -m pytest -q` → **465 passed, 18 skipped** (the 18 skips are the local-Oracle
  and client-workbook tests). `ruff check` is clean and `scripts/check.sh js` passes.
- **GitHub Actions CI is red on this branch.** Every completed run since run 15 (2026-10-17, commit `6a62a4e`) failed or was
  cancelled. In the latest run (49, run id 39124073690):
  - **Oracle integration**: one test failed, then the job crashed with a segmentation fault while
    `backend/insight/service.py:251` set `call_timeout`. `tests/test_oracle.py` never ran.
  - **Secret scan**: gitleaks reported "leaks found: 6" over the git history. This audit did not look at them.
  - **Windows unit tests**: failed. The job is marked non-blocking.
- **The real Oracle writer (`oracle_store.commit`) is not exercised by any test that ran.** Every cloud test replaces it
  with `tests/fake_oracle.py:79-80`. The opt-in local-Oracle tests have not been run on the laptop
  (`docs/IMPLEMENTATION_STATUS.md:135-137`).
- **Biggest gaps between the docs and the code:**
  - unit conversion beyond PCS/PACK
  - an import ledger that catches the same order sent again under a new reference
  - VERIFIED / POST_FAILED states with a stored read-back proof
  - a reject flow, with self-approval still on by default
  - master creation inside posting
  - pools, preflight and timeouts on the writer
  - JSON logs and health endpoints
  - formula-injection handling
  - a value-mapping step and an error summary
- **The two recent changes check out.** Master matching (M1) and sign-in security (S2) are implemented and tested, apart from
  their documented deviations. Re-verification of the earlier audit's gap list is in [§ Re-verification](#re-verification-of-the-earlier-quick-audit).

## Method and evidence keys

Commands I ran (all read-only; `POPPYS_DATA_DIR` pointed at a scratch folder for the runtime probes):

| Command | Result |
|---|---|
| `python3 -m pytest -q -p no:cacheprovider` | 465 passed, 18 skipped, 93.9 s |
| `python3 -m ruff check --no-cache .` | All checks passed |
| `bash scripts/check.sh js` | OK: 13 modules parse and follow the content-security rules |
| `bash scripts/check.sh secrets` (local scan; gitleaks not installed locally) | OK: nothing secret is committed |
| Runtime probe: `readers.sniff()` on SYLK (`ID;P…`) and DIF (`TABLE…`) bytes named `order.csv` | both sniffed as `csv` and read as CSV cells |
| Runtime probe: `'x.xlt'/'x.xla'/'x.xlam'/'x.slk'/'x.dif'/'x.prn'.endswith(readers.ACCEPT)` | all False (refused by extension with the generic 415) |
| Runtime probe: AI agreement on a caption-under-value field (`tests/test_engine.py::test_captions_under_values_are_never_preselected` workbook, `ai._chat` stubbed to agree) | matcher alone: style `low`, not pre-selected → with AI vote: `medium`, **pre-selected** |
| Runtime probe: `TestClient(main.app)` GET `/openapi.json`, `/docs`, `/healthz`, `/readyz`, `/api/version` | 200, 200, 404, 404, 404 |
| Runtime probe: response headers of `/` | CSP has `style-src 'self' 'unsafe-inline'`; no COOP, CORP, Permissions-Policy or X-Request-ID; `Referrer-Policy: same-origin` |
| GitHub Actions (MCP `actions_list` / `get_job_logs`) for the branch | run 49: Unit tests, Lint, JS, pip-audit pass; Secret scan, Oracle integration, Windows fail |

Source keys: **README** = `README.md`, **STATUS** = `docs/IMPLEMENTATION_STATUS.md`, **RD** = `docs/RESEARCH_DECISIONS.md`,
**EXU** = `docs/EXCEL_UNDERSTANDING.md`, **ARCH** = `docs/ARCHITECTURE.md`, **WS** = `docs/WINDOWS_SETUP.md`,
**RB** = `docs/RUNBOOK.md`, **UM** = `docs/USER_MANUAL.txt`, **OSR** = `docs/OPEN_SOURCE_RESEARCH.md`. `RD:43` means line 43.
Test names are pytest functions under `tests/`. "Stand-in" means the test uses `tests/fake_oracle.py` instead of Oracle.

Labels:

- **IMPLEMENTED**: code exists and a test or a runtime check I ran exercises it.
- **PARTIALLY IMPLEMENTED**: some parts exist; the missing parts are listed.
- **DOCUMENTED ONLY**: described in the docs (as a decision, a plan or a claim) with no matching code.
- **NOT IMPLEMENTED**: required by the product goal and neither documented as done nor coded.
- **NOT VERIFIED**: code exists but nothing automated that ran exercises it, or it can only be checked on Windows or real Oracle.

---

## 1. File formats & safety

| Feature | Source | Status | Evidence | Missing parts |
|---|---|---|---|---|
| FS-01 .xlsx/.xlsm/.xltx/.xltm full load: formulas + cached values, hidden rows/cols, merges, no macros | RD:32; README:15; STATUS:38 | IMPLEMENTED | `readers.py:1082-1087` (two `load_workbook` calls, `keep_vba=False`, `catch_warnings`); `test_libreoffice_layout_workbook_gives_merges_hidden_rows_columns_and_sheets`, `test_zero_height_rows_and_zero_width_columns_count_as_hidden` | `OPENPYXL_LXML=False` not forced (grep `OPENPYXL_LXML` backend → 0 hits) |
| FS-02 Strict OOXML / damaged file → calamine fallback, flagged reduced fidelity | RD:32 | IMPLEMENTED | `readers.py:1067`; `test_strict_open_xml_is_read_with_the_backup_reader`, `test_damaged_xlsx_falls_back_to_calamine_instead_of_failing`, `test_every_sheet_says_what_its_format_could_provide` | — |
| FS-03 .xls via xlrd with formats, lenient retry, calamine fallback, raw BIFF2–4 | RD:33 | IMPLEMENTED | `readers.py:1146-1173`; `test_xls_corruption_is_retried_leniently`, `test_xls_that_xlrd_cannot_read_falls_back_to_calamine`, `test_raw_biff2_workbook_is_read_by_xlrd` | — |
| FS-04 `.xlt` accepted | RD:33; STATUS:110 | DOCUMENTED ONLY | `readers.py:100-101` ACCEPT has no `.xlt`; `main.py:355` refuses by extension; probe: `'x.xlt'.endswith(ACCEPT)` → False | add `.xlt` to ACCEPT and the upload `accept=` list |
| FS-05 .xlsb values via calamine, capability flags | RD:34 | IMPLEMENTED | `readers.py` READERS `'xlsb': read_zip_calamine`; `test_calamine_reference_workbook_reads_identically_in_four_formats` | XLSB record pass for merges/hidden (deferred in RD:34) |
| FS-06 .ods values + capped content.xml pass (merges, hidden, number styles) | RD:35 | IMPLEMENTED | `readers.py:1295`; `test_ods_layout_pass_counts_repeats_without_expanding_them`, `test_ods_number_styles_become_excel_codes` | — |
| FS-07 SpreadsheetML 2003 (Span, MergeAcross, styles) | RD:36 | IMPLEMENTED | `readers.py:1527-1580`; `test_spreadsheetml_styles_spans_and_limits`, `test_html_that_declares_the_spreadsheet_namespace_is_still_html` | a fixture saved by real Excel (corpus is LibreOffice/synthetic) |
| FS-08 HTML-disguised .xls and MHTML exports | RD:37-38 | IMPLEMENTED | `readers.py:1868`, `:1896`; `test_html_export_keeps_text_formatted_codes_and_hidden_rows_and_columns`, `test_excel_mhtml_gives_one_named_sheet_per_worksheet_part`, `test_browser_mhtml_and_saved_emails` | real Tally/SAP samples |
| FS-09 CSV/TSV: encoding chain, `sep=`, delimiter scoring, `="00123"`, lakh, decimal comma, E+ flags | RD:39 | IMPLEMENTED | `readers.py:1932-2030`; `test_csv_sep_line_excel_text_wrappers_and_lakh_numbers`, `test_text_encodings_are_detected`, `test_csv_long_codes_and_damaged_codes_are_flagged_not_changed` | — |
| FS-10 JSON/NDJSON: Decimal floats, NaN refused, duplicate keys refused, depth cap, envelopes | RD:40 | IMPLEMENTED | `readers.py:2044-2055`, `:2093`; `test_json_that_cannot_be_trusted_is_refused_plainly`, `test_json_decimals_ndjson_and_api_envelopes` | — |
| FS-11 One canonical Pydantic order model; `model_json_schema()` published as the upload spec | RD:12, RD:40, RD:118 | DOCUMENTED ONLY | `grep -rn 'model_json_schema\|model_validate' backend` → 0 hits; `main.py:387-389` `orders: list[dict]` | the model, lax/strict field rules, loc→cell map, published schema |
| FS-12 Other Save-As types: explicit `.xla/.xlam` refusal; detect SYLK/DIF/.prn | RD:41 | PARTIALLY IMPLEMENTED | `main.py:355` refuses unknown extensions with one generic 415; probe: SYLK and DIF bytes named `.csv` → sniffed `csv` and read as data | specific messages; content detection of `ID;P`, `TABLE`, fixed-width `.prn` |
| FS-13 Content sniffing (ZIP/OLE/BIFF/BOM/MIME/XML/JSON/CSV); OLE classification (IRM, Word, PowerPoint, Outlook) | RD:42 | IMPLEMENTED | `readers.py:498-525`, `:718-750`; `test_ole2_files_are_classified_with_exact_messages`, `test_known_non_spreadsheets_are_named`, `test_other_zip_documents_are_named` | — |
| FS-14 FILEPASS .xls `VelvetSweatshop` retry; one-time password for encrypted OOXML | RD:42; STATUS:110 | DOCUMENTED ONLY | `grep -rni 'velvet\|msoffcrypto' backend requirements.txt` → 0; `readers.py:512-513` refuses with MSG_PASSWORD; `test_password_protected_xls_is_refused_with_what_to_do` | decryption path, msoffcrypto-tool dependency |
| FS-15 ZIP: real inflated size measured with raw zlib before calamine; lying sizes refused; 80 MB total / 40 MB per part | RD:43 | IMPLEMENTED | `readers.py:843-907`; `test_zip_member_that_lies_about_its_size_is_refused_before_calamine`, `test_zip_bomb_inside_declared_limits_is_measured` | — |
| FS-16 ZIP: ≤5,000 entries, no `..`/absolute names, ratio >100:1 rule | RD:43; STATUS:105 | PARTIALLY IMPLEMENTED | `readers.py:70` `MAX_ZIP_ENTRIES = 10_000`; `readers.py:424-425` strips a leading `/` without refusing; `grep -n "'\.\.'\|ratio" readers.py` → no ZIP check | 5,000 cap; refuse `..`/absolute names; ratio rule |
| FS-17 Parse in a spawned worker: time limit, memory cap (POSIX), JSON over a pipe, 2 slots | RD:43 | IMPLEMENTED | `isolation.py:40-41`, `:57-171`; `test_isolated_worker_is_stopped_at_the_time_limit`, `test_isolated_worker_memory_is_capped`, `test_isolated_read_gives_the_same_result_as_inline` | — |
| FS-18 Windows Job Object `ProcessMemoryLimit` for the parse worker | RD:43 | NOT VERIFIED | `isolation.py:208-260`; only `test_windows_job_object_structures_match_winnt_h` (struct layout); the Windows CI job fails | run the bomb/hang fixtures on Windows |
| FS-19 Cell, sheet, merge and text caps before allocation | RD:43 | IMPLEMENTED | `readers.py:66-78`; `test_merged_ranges_are_clamped_and_absurd_ones_ignored`, `test_absurd_cell_or_row_counts_are_refused_before_openpyxl_runs`, `test_one_long_shared_string_in_many_cells_is_refused_before_it_reaches_the_server` | — |
| FS-20 Expat gate: refuse XML formats on win32 when Expat < 2.7.2 | RD:43, RD:106 | PARTIALLY IMPLEMENTED | `readers.py:2230-2236` reports it in `selfcheck()` only; `test_isolated_selfcheck_reports_parsers_packages_and_isolation` | the refusal itself |
| FS-21 Formulas never executed; safe arithmetic subset for files saved without results | RD:43; STATUS:32 | IMPLEMENTED | `backend/formulas.py`; `test_formulas_saved_without_results_are_calculated_safely`, `test_hostile_content_is_data_only` | — |
| FS-22 Flag text starting `= + - @ TAB CR LF` (and full-width forms); block `cmd\|`, `=HYPERLINK`, `=WEBSERVICE` in ORDMAS/ORDSTYLE | RD:43; STATUS:105 | DOCUMENTED ONLY | `grep -rn 'HYPERLINK\|WEBSERVICE\|injection' backend frontend` → 0; `test_hostile_content_is_data_only` asserts `=cmd\|calc` is kept as plain text with no flag | flagging and blocking |
| FS-23 CSV export TAB-prefixes risky values; XLSX export writes text cells | RD:43; STATUS:105 | DOCUMENTED ONLY | `frontend/js/util.js:69-74` quotes only `[",\n]`; no XLSX export exists | both |
| FS-24 Workbook strings rendered with `textContent` | RD:43 | PARTIALLY IMPLEMENTED | 73 `innerHTML` assignments across `frontend/js` (grep -c), with values passed through `esc()` | `textContent` rendering; today every template depends on `esc()` being applied |

## 2. Excel understanding

| Feature | Source | Status | Evidence | Missing parts |
|---|---|---|---|---|
| EU-01 Built-in detectors (merch BOM, buyer contract, flat table) after saved templates | README:16-17; EXU:11-14; STATUS:39 | IMPLEMENTED | `workbooks.py:537`, `:597`, `:875`, `:927`; `test_merch_bom_header_blocks_bom_and_provenance`, `test_merch_bom_gives_the_same_order_in_every_format`, `test_flat_table_keeps_leading_zero_style` | — |
| EU-02 Every value keeps its source cell; corrections cannot replace provenance | README:24; ARCH:21; OSR:70 | IMPLEMENTED | `local_store.py:297`; `test_correction_cannot_replace_cell_provenance`, `test_mapping_preserves_leading_zero_text_and_provenance` | — |
| EU-03 Hidden rows/columns/sheets never read or auto-mapped; named in notes | RD:44 | IMPLEMENTED | `workbooks.py:219-285`; `test_hidden_rows_are_not_read_and_are_named` | — |
| EU-04 TOTAL / Sub Total / G.Total rows end a block, are excluded and are cross-checked | RD:44 | IMPLEMENTED | `workbooks.py:102-113`, `:1124-1133`; `test_total_and_subtotal_rows_are_never_sizes_or_lines`, `test_unedited_total_cannot_be_waived` | — |
| EU-05 Key-value header block (`Buyer:`, `PO No`, …) read into the order header | RD:44 | IMPLEMENTED | `workbooks.py:366`; `test_number_fields_and_order_date_are_read_from_header_labels` | — |
| EU-06 Region detection (BFS over occupancy grid) and header-band scoring with thresholds | RD:44; RD:110-111 | DOCUMENTED ONLY | `matcher.py:292-299` `find_table` scans rows 1–120 (`ai.py:40` the same); `grep -rni 'bfs\|occupancy\|header_band' backend` → 0 | regions, band score, two stacked tables per sheet |
| EU-07 Multi-row "Parent / Child" headers | RD:44 | PARTIALLY IMPLEMENTED | `test_merged_two_row_header_keeps_the_colour_column` | general Parent/Child naming |
| EU-08 Reshape operators (ffill, stack, wide_to_long, subtitle→column, transpose) shown and undoable | RD:44; RD:112 | PARTIALLY IMPLEMENTED | size grids become ORDSIZE rows through the matrix definition (`workbooks.py:506-536`, `:1238`); forward fill deliberately refused (`test_missing_style_not_forward_filled`); `grep -rni 'reshape\|transpose\|wide_to_long' backend` → 0 | operator framework, visible undo, wide_to_long, transpose |
| EU-09 Visual mapping of a new layout (person picks header row, columns, cells); validated server-side | README:18; EXU:15, EXU:209-216 | IMPLEMENTED | `POST /api/drafts/{id}/map` (`main.py:481`); `test_D_wrong_person_mapping_is_refused_with_409_naming_each_field`, `test_mapping_new_layout_saves_template_for_next_upload` | — |
| EU-10 Click-to-pick mapping screen in the browser | README:18; UM:306-312 | NOT VERIFIED | `frontend/js/mapping.js:174-187`; no automated UI test | browser test |
| EU-11 Dates to ISO, day-first, ambiguity reported | EXU:179-184 | IMPLEMENTED | `workbooks.py:756-827`; `test_buyer_dates_become_iso_and_ambiguity_is_reported`, `test_month_first_file_does_not_guess_an_ambiguous_date` | per-column override (UX-05) |
| EU-12 Leading-zero codes and digit number formats kept | RD:33; OSR:71 | IMPLEMENTED | `backend/numfmt.py`; `test_number_format_renderer_handles_only_digit_codes`, `test_xls_keeps_leading_zero_style_codes` | — |
| EU-13 Quantity unit taken from file evidence, asked when silent | EXU:135-154 | IMPLEMENTED | `workbooks.py:384-505`; `test_C_missing_unit_is_asked_and_blocks_saving_until_answered`, `test_C_unit_is_taken_from_the_file_when_the_header_says_pieces` | — |
| EU-14 Labelled corpus of ≥30 buyer workbooks + matcher metrics (P/R/F1, AUTO precision) in CI | RD:109 | DOCUMENTED ONLY | `tests/fixtures/formats/` has 22 synthetic/LibreOffice files; no metrics harness (`grep -rn 'recall\|f1' tests` → 0) | corpus and gate |
| EU-15 The two real client workbooks read correctly | STATUS:32; README:86 | NOT VERIFIED | `tests/test_import.py:10`, `:24` skipped: "Client fixture not installed on this machine" | run on the laptop |

## 3. Field matching & templates

| Feature | Source | Status | Evidence | Missing parts |
|---|---|---|---|---|
| FM-01 Name signal: normalisation, garment abbreviations, token Jaccard + trigram Dice | EXU:89-92 | IMPLEMENTED | `matcher.py:19-70`; `test_name_score_exact_abbreviations_and_trigrams`, `test_garment_abbreviations_and_typos` | RD:45 variant (soft-Dice, token_set_ratio) |
| FM-02 Score = 0.45·name + 0.25·master + 0.20·type/pattern + 0.10·context, renormalised; hard vetoes | RD:45 | PARTIALLY IMPLEMENTED | `matcher.py:153` `0.6*name + 0.4*value` (the EXU:97 formula); value 0 rejects | context signal, RD weights, 50%/80% column vetoes, RapidFuzz |
| FM-03 Master-overlap signal | RD:45; EXU:95-96 | PARTIALLY IMPLEMENTED | `matcher.py:101`, `:110`, `:126`: a single value found exactly in the buyer/colour/size master scores 1.0 (vs 0.8/0.9) | share of distinct values, STYLENUM/UNITMAS, fuzzy ≥90 for ≥4 chars |
| FM-04 1:1 Hungarian assignment with COMA DIR_BOTH selection | RD:46 | PARTIALLY IMPLEMENTED | `matcher.py:336-339` greedy best-first, one cell per field; `test_matcher_scores_explain_and_assign_one_to_one`, `test_two_columns_naming_one_field_are_asked_not_picked` | Hungarian, relative-delta selection |
| FM-05 Bands AUTO ≥0.90 with margin ≥0.15 / REVIEW / UNMAPPED; mapping "pre-confirmed" only if all required fields are AUTO | RD:46 | PARTIALLY IMPLEMENTED | `matcher.py:23`, `:130-131` high ≥0.85, medium ≥0.6, low never pre-selected (`test_low_confidence_is_shown_but_never_preselected`) | margin, AUTO band, all-or-nothing gate, calibration |
| FM-06 Validator applied to matcher, AI, heuristic, templates and person mappings | EXU:124-133 | IMPLEMENTED | `matcher.py:369`; `test_D_validator_rule`, `test_validator_rejects_with_reasons_and_keeps_the_rest`, `test_template_whose_cells_no_longer_validate_is_refused` | — |
| FM-07 Repair loop (swap to next-best candidate, ≤2 iterations) | RD:48 | DOCUMENTED ONLY | `grep -rn repair backend frontend` → 0 | all |
| FM-08 Versioned buyer templates with anchors; refused with a reason when labels changed | EXU:156-177; README:18 | IMPLEMENTED | `workbooks.py:911-982`; `test_A_known_buyer_next_file_is_read_with_the_saved_layout_and_unit`, `test_A_saving_the_layout_again_creates_a_new_version`, `test_changed_anchor_label_refuses_the_template_and_records_why`, `test_B_new_buyer_gets_no_template_and_no_data_from_another_buyer` | — |
| FM-09 Immutable template spec (reshape_ops, value_maps, validators, matcher_version, source sha256, approved_by); near match rematches only changed columns with a diff | RD:49 | PARTIALLY IMPLEMENTED | `local_store.py:40` templates table; near match ≥0.8 applies only if every anchor still matches (EXU:165-167), otherwise the file is read as new | value maps, reshape ops, per-column rematch and diff, approver |
| FM-10 Admin-editable global aliases and required fields (Setup → Order fields), audited | EXU:62-75 | IMPLEMENTED | `main.py:850-885`; `test_fields_api_lists_changes_and_audits`, `test_alias_customisation_changes_parsing_without_global_state`, `test_admin_required_field_is_checked_by_the_planner` | — |
| FM-11 Buyer-scoped aliases; buyer/global history layers; global promotion after admin approval or 3 buyers | RD:46, RD:49, RD:52; STATUS:92 | DOCUMENTED ONLY | `local_store.py:51` `field_settings` holds global aliases only | all |

## 4. Local LLM

| Feature | Source | Status | Evidence | Missing parts |
|---|---|---|---|---|
| AI-01 Ollama optional; the system works identically without it | README:31, README:91; WS:68-75 | IMPLEMENTED | `ai.py:99-119` (matcher first); `test_ai_agreement_is_both_and_ai_only_fields_are_scored` (broken model answer → `source == 'matcher'`), `test_an_unusable_ai_reading_falls_back_to_the_rules` | — |
| AI-02 AI proposals validated against real cells and cross-checked by the matcher | EXU:13-14, EXU:24-25 | IMPLEMENTED | `matcher.py:590-646`; `test_D_ai_proposal_is_cross_checked_and_wrong_fields_rejected` | — |
| AI-03 The model never sees hidden cells | ai.py docstring; RD:44 | IMPLEMENTED | `ai.py:82-83`; `test_ai_is_never_shown_hidden_cells` | — |
| AI-04 N-to-1 prompt (one column vs ≤5 candidates + NONE, enum JSON schema, reason ≤200) | RD:47 | DOCUMENTED ONLY | `ai.py:82-92` sends up to 600 cells of rows ≤80 and asks for a whole-sheet mapping with `format='json'` | per-column candidate prompt, enum schema |
| AI-05 Call: temperature 0, fixed seed, `num_ctx` 4096, 20 s timeout | RD:47 | PARTIALLY IMPLEMENTED | `ai.py:71` temperature 0; `ai.py:74` `timeout=120`; no seed or num_ctx | seed, num_ctx, 20 s |
| AI-06 Bounded vote: ≤+0.10, never lifts a column to AUTO, two-prompt agreement | RD:47; STATUS:109 | DOCUMENTED ONLY | `matcher.py:607-609` lifts a `low` field to `medium` and pre-selects it; AI-only cells pre-selected when matcher-scored ≥0.6 (`matcher.py:617-621`); runtime probe confirmed the lift | the cap, the two-prompt vote |
| AI-07 Log model digest, prompt hash and output | RD:47 | DOCUMENTED ONLY | `grep -n 'digest\|prompt_hash\|sha256' backend/ai.py backend/insight/understand.py` → 0 | all |
| AI-08 Real qwen2.5:7b run (layout suggestions and Ask AI second reading) | STATUS:49, STATUS:58; UM:680 | NOT VERIFIED | every test stubs `ai._chat` or the chat function (`test_engine.py:211`, `tests/test_agreement.py`) | laptop run with Ollama |
| AI-09 Draft explanation (`/api/drafts/{id}/assist`) | WS:75; ARCH:20 | NOT VERIFIED | `main.py:1578`, `ai.py:122-131`; no test references `/assist` | test |

## 5. Validation

| Feature | Source | Status | Evidence | Missing parts |
|---|---|---|---|---|
| VA-01 Required/locked fields; ship date ≥ order date | README:20; EXU:45-60 | IMPLEMENTED | `planner.py:276-292`; `test_admin_required_field_is_checked_by_the_planner`, `test_registry_planner_reproduces_verified_order_mapping` | — |
| VA-02 Σ sizes = declared total (error), duplicate size (error), 1–50 orders / 1–100 lines / 1–100 sizes | README:20; ARCH:22; UM:300 | IMPLEMENTED | `planner.py:17`, `:297-316`; `test_unedited_total_cannot_be_waived`, `test_registry_planner_reproduces_verified_order_mapping` | — |
| VA-03 Live Oracle column length/precision/scale/nullability | README:20; ARCH:22 | IMPLEMENTED | `planner.py:552-595`; `test_accessory_quantities_scale_to_saved_pieces_and_too_long_text_is_grouped` | contracts generated from ALL_CONSTRAINTS (RD:48) |
| VA-04 Errors block approval; warnings listed | RD:48; ARCH:25 | IMPLEMENTED | `main.py:592-593`; `test_stale_or_changed_preview_cannot_be_approved` | — |
| VA-05 Warnings need explicit acknowledgement | RD:48, RD:68; STATUS:100 | DOCUMENTED ONLY | `main.py:581-595` checks errors only; `workspace.js:650` shows "Please read before approving" with no tick | acknowledgement stored with the approval |
| VA-06 Delivery/ex-factory within 18 months | RD:48; STATUS:99 | DOCUMENTED ONLY | `planner.py:288` checks ship < order only | window check |
| VA-07 Per-buyer PO/style regex | RD:45, RD:48; STATUS:99 | DOCUMENTED ONLY | no pattern configuration in `fields.py`, `local_store.py` or `planner.py` (`grep -n 're.compile' backend/erp/planner.py` → none for PO/style) | all |
| VA-08 Column-level date parse ≥95% and numeric ≥80% vetoes | RD:45, RD:48 | PARTIALLY IMPLEMENTED | quantity column ≥80% numeric (EXU:130; `matcher.py:447-492`); dates checked per cell | 95% date rate, 50% veto |
| VA-09 Control-total vector (n_lines, Σ per colour and size, Σ value, tuple SHA-256) at extraction, approval and read-back | RD:48; STATUS:95 | PARTIALLY IMPLEMENTED | `planner.py:159` totals {orders, lines, pieces, fabrics, fabric_kg, yarns, accessories} inside the hashed plan (`planner.py:358`) | per-colour/size sums, tuple digest, comparison at read-back |
| VA-10 BOM arithmetic: fabric kg, yarn % = 100, composition mismatch, workbook arithmetic | README:20; ARCH:22 | IMPLEMENTED | `planner.py:410-493`; `test_fabric_kg_yarn_split_and_relationships_when_verified`, `test_yarn_percent_and_rejection_rules_block_when_verified`, `test_composition_mismatch_and_workbook_arithmetic_warnings` | — |
| VA-11 Each issue carries a field path for the UI | README:22; EXU:218-219 | IMPLEMENTED | `planner.py:71-98` (Issues with paths); `test_C_missing_unit_is_asked_and_blocks_saving_until_answered` | UI rendering: see UX-02 |

## 6. Units & BOM

| Feature | Source | Status | Evidence | Missing parts |
|---|---|---|---|---|
| UB-01 PCS / PACK with pieces per pack; packs converted to pieces before saving | EXU:135-154; UM:242-243 | IMPLEMENTED | `planner.py:114-140`; `test_planner_unit_rules_and_legacy_drafts`, `test_C_missing_unit_is_asked_and_blocks_saving_until_answered` | — |
| UB-02 Unit dictionary (UN/ECE Rec 20/21): DZN, PR, SET, CTN, GRO conversion | RD:50; STATUS:94; UM:685 | DOCUMENTED ONLY | `grep -rn 'DZN\|DOZ\|PAIR\|CTN' backend` → 0; `workbooks.py:378` treats set/sets/ratio as PACK | all |
| UB-03 Stored per line: entered_qty, entered_uom, conversion_factor, qty_in_pieces | RD:50 | DOCUMENTED ONLY | `grep -rn 'entered_qty\|conversion_factor' backend` → 0 | all |
| UB-04 packs × pcs_per_pack must equal pieces exactly (error) | RD:50; STATUS:94 | PARTIALLY IMPLEMENTED | `planner.py:380-399` `_check_packs` raises warnings only | error severity |
| UB-05 Ratio packs: largest-remainder split, "incomplete pack" | RD:50 | DOCUMENTED ONLY | `grep -rni 'largest' backend` → 0 | all |
| UB-06 BOM consumption with unit (MTR/YRD/KGM/GRM/cone) and basis (per 1 or 12 pcs) | RD:51 | PARTIALLY IMPLEMENTED | `planner.py:427`, `:441`: grams per piece × pieces ÷ 1000 × rejection factor; formula text in preview (`planner.py:463`) | other units, per-dozen basis |
| UB-07 Wastage bands (>10% amber, outside 0–25% red); 12× style-median "basis?" check | RD:51; STATUS:100 | PARTIALLY IMPLEMENTED | `planner.py:432-433` accepts a rejection factor of 1–2 (0–100%) | bands, percentile plausibility |
| UB-08 Accessory quantities scale to saved pieces | ARCH:22 | IMPLEMENTED | `planner.py:503-550`; `test_accessory_quantities_scale_to_saved_pieces_and_too_long_text_is_grouped` | — |

## 7. Master data

| Feature | Source | Status | Evidence | Missing parts |
|---|---|---|---|---|
| MD-01 Exact or fingerprint match (case, spacing, punctuation, &/AND, leading zeros) reuses the record, with a note | RD:52; STATUS:61; UM:274-283 | IMPLEMENTED | `masters.py:23-58`; `planner.py:197-209`; `test_fingerprint_ignores_case_spacing_punctuation_and_leading_zeros`, `test_a_value_written_differently_reuses_the_existing_master_and_says_so` | — |
| MD-02 Codes ≤3 chars exact only; style numbers never fuzzy | RD:52 | IMPLEMENTED | `masters.py:18-20`, `:56-63`; `test_short_codes_and_style_numbers_are_never_guessed` | — |
| MD-03 Value matching two records must be typed exactly | STATUS:61; UM:288-289 | IMPLEMENTED | `planner.py:200-205`; `test_a_value_matching_two_existing_records_must_be_typed_exactly` | — |
| MD-04 Tier 3 fuzzy suggestions (top 5, cutoff 80), never auto-accepted, a click required | RD:52 | PARTIALLY IMPLEMENTED | `masters.py:60-69` difflib token-set ≥0.8 listed as a warning only when a new record would be created (`planner.py:224-228`); `test_a_new_name_close_to_an_existing_one_is_created_with_a_note_not_blocked` | pick-from-suggestions UI; RapidFuzz |
| MD-05 Missing master → red; creation only as a separate, permission-gated command | RD:13, RD:52, RD:211; STATUS:91-93 | DOCUMENTED ONLY | `config/oracle_mapping.json:6-12` `"create": true` for 7 masters; `planner.py:223-229` adds them to the order plan; `scripts/bootstrap_local.py:34` grants INSERT on masters | separate step (STATUS:93: owner prefers none for now) |
| MD-06 Masters cached in SQLite with an FTS5 trigram index | RD:52 | DOCUMENTED ONLY | `catalog.py:67-77` reads masters live per preview; `grep -rni fts5 backend` → 0 | all |
| MD-07 Masters larger than 5,000 rows | ARCH:75 | PARTIALLY IMPLEMENTED | `catalog.py:12`, `:73-76` raise "master catalogue exceeds 5000 rows"; on a large ERP every preview needing that master fails | scoped/indexed lookup |
| MD-08 Cancelled masters refused | README:20 | NOT VERIFIED | `planner.py:214-216`; no test plans against a cancelled master (`grep -rn 'is cancelled' tests` → 0) | test |

## 8. Mapping registry & verification

| Feature | Source | Status | Evidence | Missing parts |
|---|---|---|---|---|
| MR-01 Registry field → TABLE.COLUMN, validated on load; overrides cannot unlock keys/parents | README:19; ARCH:23; STATUS:41 | IMPLEMENTED | `erp/mapping.py:20-49`; `test_mapping_overrides_cannot_unlock_keys_or_parents` | — |
| MR-02 Only verified columns written; fabric/yarn/trims reference-only until verified | README:19; WS:77-81 | IMPLEMENTED | `test_unverified_bom_is_previewed_but_never_planned_for_insert`, `test_bom_rows_saved_only_after_admin_verifies_mapping` | — |
| MR-03 "Verified" needs a reviewer statement + evidence ledger record | STATUS:68; ARCH:88 | IMPLEMENTED | `backend/evidence.py`; `test_verifying_a_mapping_needs_a_statement_and_records_the_known_facts`, `test_bundle_is_all_or_nothing` | — |
| MR-04 Plan hash changes when the mapping changes | ARCH:25 | IMPLEMENTED | `planner.py:354-358`; `test_plan_hash_changes_when_mapping_changes` | — |
| MR-05 Read-only schema discovery (constraints, triggers, fill rates, link match) | WS:38-52; ARCH:39 | IMPLEMENTED | `scripts/discover_schema.py:190-194`; laptop run passed (STATUS:134); `test_discovery_samples_big_tables_and_profiles_all_columns_in_two_passes` | — |
| MR-06 Semantic model of verified entities and scoped joins | ARCH:28 | IMPLEMENTED | `erp/semantic.py`; `test_semantic_model_lists_entities_and_scoped_joins` | endpoint `/api/semantic-model` itself untested |
| MR-07 Axpert posting-path decision: ADR 0001, trigger inventory, production posting behind a flag | RD:15, RD:55, RD:92 | PARTIALLY IMPLEMENTED | triggers collected by `discover_schema.py:190-194` (laptop: 0 triggers, STATUS:134) into git-ignored `data/`; `ls docs/adr` → missing; `grep -rn 'POPPYS_ENV\|posting' backend` → 0 | Agile Labs answer, ADR, flag |

## 9. Oracle accounts & driver

| Feature | Source | Status | Evidence | Missing parts |
|---|---|---|---|---|
| OR-01 `oracledb==4.0.2` pinned | RD:17, RD:53 | IMPLEMENTED | `requirements.txt`; runtime `oracledb.__version__` → 4.0.2 | test refusing 26.0.x (RD:93) |
| OR-02 Restricted writer `POPPYS_APP`: SELECT + INSERT only, no UPDATE/DELETE | README:94; WS:31-36 | IMPLEMENTED | `bootstrap_local.py:34-37`, `grant_bom_tables.py:28-31`; laptop `setup.ps1` + `check_oracle.py` passed (STATUS:133); CI run 49 grant steps passed | RD:54 shape (READ, INSERT on 6 order tables only; no master INSERT) |
| OR-03 Separate read-only `POPPYS_RO`; app refuses an account with write privileges | ARCH:90; STATUS:51 | IMPLEMENTED | `create_readonly_user.py`; `oracle_store.py:39-68`; laptop: "0 other privileges" (STATUS:133); `test_account_with_write_privileges_is_refused` | — |
| OR-04 READ (not SELECT) grants, `ALTER USER … READ ONLY`, profiles, `resource_limit` | RD:54; STATUS:102 | DOCUMENTED ONLY | `create_readonly_user.py:47` grants SELECT; `grep -rni 'profile\|read only' scripts/*.py` → only the transaction statement | all |
| OR-05 Unified Auditing policy script | RD:54, RD:137 | DOCUMENTED ONLY | `grep -rn 'AUDIT POLICY' scripts` → 0 | all |
| OR-06 Two fixed connection pools (TIMEDWAIT, ping, lifetimes) | RD:53; STATUS:102 | DOCUMENTED ONLY | `oracle_store.py:30`, `:59` `oracledb.connect` per call; `grep -rn create_pool backend` → 0 | all |
| OR-07 Startup preflight (version ≥12.1, DB_UNIQUE_NAME, DPY-3015 message) | RD:53, RD:93 | DOCUMENTED ONLY | `grep -rni 'preflight\|DB_UNIQUE_NAME' backend` → 0 | all |
| OR-08 `oracledb.defaults.fetch_decimals = True` | RD:53 | DOCUMENTED ONLY | `grep -rn fetch_decimals backend` → 0 (`oracle_store.py:120-128` compares via `Decimal(str())`) | setting |
| OR-09 Read-only executor on real Oracle: READ ONLY transaction, `call_timeout`, bounded fetch | README:92; RD:19 | NOT VERIFIED | `insight/service.py:229-255`; unit test `test_oracle_executor_turns_a_call_timeout_into_a_plain_message_and_restores_the_limit` (stand-in); **CI run 49 crashed with a segfault in `service.py:251` (`call_timeout` setter) during `test_oracle_call_timeout_is_plain_and_connection_stays_usable`** | root-cause the crash; laptop run |
| OR-10 Sessions named MODULE='POPPYS AI' / ACTION in V$SESSION | STATUS:63 | NOT VERIFIED | `oracle_store.py:17-24`; needs real Oracle | V$SESSION check |
| OR-11 Writer `call_timeout`; `client_identifier` / `clientinfo='upload:<id>'` per acquire | RD:53; STATUS:103 | DOCUMENTED ONLY | `commit()` (`oracle_store.py:143-195`) sets no timeout; `grep -rn client_identifier backend` → 0 | all |

## 10. Posting / commit / read-back

| Feature | Source | Status | Evidence | Missing parts |
|---|---|---|---|---|
| PC-01 Approval bound to the exact preview hash and draft version (API) | README:21; ARCH:25 | IMPLEMENTED | `main.py:588-589`; `test_stale_or_changed_preview_cannot_be_approved` (stand-in) | — |
| PC-02 A preview made without Oracle cannot be approved | README:90 | IMPLEMENTED | `main.py:590-591`; `test_offline_preview_is_marked_and_cannot_be_approved` | — |
| PC-03 Real writer: write lock, re-plan + same hash, sequence keys, PP_ORDER_KEYS, binds, receipt, rollback | README:21; ARCH:26; STATUS:44 | NOT VERIFIED | `oracle_store.py:143-195`; all cloud tests replace `commit` (`tests/fake_oracle.py:79-80`); `tests/test_oracle.py` 5 tests skipped; CI Oracle job aborted before reaching them | laptop `verify_local.ps1`; green CI Oracle job |
| PC-04 Row-by-row read-back inside the transaction, compared with the approved values | README:21; ARCH:26 | NOT VERIFIED | `oracle_store.py:178-181`; same reason as PC-03 | as PC-03 |
| PC-05 Fresh-connection read-back after save, shown as the "Saved" flowchart with decoded masters | README:24; ARCH:27; UM STEP 4 | PARTIALLY IMPLEMENTED | `readback.py:36-64`; `main.py:659-666` (new connection); the Saved screen requests it (`workspace.js:695-699`); `test_full_order_flow_with_readback_graph` (stand-in) | server-side automatic run; stored result; the hero already says "Saved and verified" (`workspace.js:686`) before the read-back answers |
| PC-06 Ledger states POSTING → POSTED → VERIFIED / POST_FAILED | RD:49, RD:55; STATUS:96 | DOCUMENTED ONLY | `grep -rn 'POST_FAILED' backend frontend` → 0 | all |
| PC-07 `setinputsizes` from DATA_LENGTH, `executemany(arraydmlrowcounts=True)`, `RETURNING` | RD:55 | DOCUMENTED ONLY | `oracle_store.py:165-181` one NEXTVAL + INSERT + SELECT per row; `setinputsizes` only for the CLOB receipt (`oracle_store.py:189`) | all |
| PC-08 On a commit-time error, check the outcome instead of re-inserting | RD:55; RB:357-373 | PARTIALLY IMPLEMENTED | receipt written in the same transaction; `accept()` and `commit()` look it up first (`main.py:585-587`, `oracle_store.py:147-149`) | business-key or Transaction Guard check; POSTED/POST_FAILED states |
| PC-09 Pre-approval "Test against ERP" dry run | RD:55, RD:126 | DOCUMENTED ONLY | no route (`grep -n '@app' backend/main.py`) | all |
| PC-10 Reversal as an approved command | RD:55, RD:127 | DOCUMENTED ONLY | no route or code | all |
| PC-11 Never UPDATE/DELETE GARMENT_DEMO rows | README:94 | IMPLEMENTED | only INSERT/SELECT statements in `oracle_store.py`; grants without UPDATE/DELETE verified on the laptop (STATUS:133) | — |
| PC-12 "Saved orders" lists every import | UM:316-319 | PARTIALLY IMPLEMENTED | `oracle_store.py:200` `FETCH FIRST 100 ROWS ONLY`; Overview marks `capped` (`main.py:629`) | paging beyond 100 receipts |

## 11. Idempotency & duplicates

| Feature | Source | Status | Evidence | Missing parts |
|---|---|---|---|---|
| ID-01 Exactly the same file bytes (SHA-256) refused | README:20; WS:181 | IMPLEMENTED | `planner.py:171-172`; `catalog.py:79-82`; `test_duplicate_file_and_existing_order_reference_are_blocked` | — |
| ID-02 Existing or duplicated order reference refused; never overwritten | README:21; UM:298-299 | IMPLEMENTED | `planner.py:262-271`; `test_duplicate_file_and_existing_order_reference_are_blocked` | — |
| ID-03 Approving again after an interruption returns the receipt, no double save | RB:372 | IMPLEMENTED | `main.py:585-587`; `test_full_order_flow_with_readback_graph` asserts `already_committed` (`test_workflow.py:62`, stand-in) | real-writer case: PC-03 |
| ID-04 Same order in another format or under a new reference caught (content hash) | RD:49, RD:123; STATUS:97-98; UM:688-689 | DOCUMENTED ONLY | `grep -rn 'content_sha\|business_key' backend` → 0 | canonical content hash |
| ID-05 Import ledger: business key, spec snapshot, recovery states, never purged | RD:49 | DOCUMENTED ONLY | PP_IMPORT_LOG holds file hash + plan hash only (`bootstrap_local.py:39-42`) | all |
| ID-06 Same key with changed content forces Reject or Amendment | RD:49; STATUS:98 | DOCUMENTED ONLY | no amendment route or state | all |
| ID-07 Same style and PO on two sheets flagged | product goal (duplicates) | IMPLEMENTED | `test_same_style_and_po_on_two_sheets_is_flagged` | — |

## 12. Approval workflow & roles

| Feature | Source | Status | Evidence | Missing parts |
|---|---|---|---|---|
| AP-01 Roles admin / approver / operator / viewer | README:30; ARCH:25 | IMPLEMENTED | `main.py:28-34`; `test_roles_and_four_eyes`, `test_authentication_required_and_setup_once` | — |
| AP-02 Four-eyes enforced by the server when switched on | README:30; UM:92-93 | IMPLEMENTED | `main.py:594-595`; `test_roles_and_four_eyes` | — |
| AP-03 No self-approval by default; audited single-operator mode with a banner | RD:68, RD:124; STATUS:101; UM:691 | DOCUMENTED ONLY | `main.py:35` `allow_self_approval=True` default | default off; audited mode |
| AP-04 Reject with a reason (≥10 chars), Rejected → Draft | RD:68; STATUS:101; UM:691 | DOCUMENTED ONLY | no reject route in `main.py` | all |
| AP-05 States Draft → Submitted → Approved → Posted | RD:68 | PARTIALLY IMPLEMENTED | computed queue states (`main.py:610`); `test_overview_counts_real_drafts_and_never_invents_oracle_figures` | Submitted / Rejected states, explicit submit |
| AP-06 Any edit after preview invalidates the approval | UM:302-304 | IMPLEMENTED | `main.py:401-409`; `test_stale_or_changed_preview_cannot_be_approved` | — |
| AP-07 Checker view (totals, pack factor, maker edits Excel→new, warnings to tick); confirm dialog repeats the numbers | RD:68 | PARTIALLY IMPLEMENTED | `workspace.js:668-670` dialog shows refs/style/buyer and records per table; planned flowchart with source cells | pieces totals, maker-edit list, warning ticks |
| AP-08 Exclusive draft lock per user with expiry | RD:68 | DOCUMENTED ONLY | optimistic version check only (`local_store.py:238-241`) | lock and "Being edited by" notice |
| AP-09 Audit trail of uploads, edits (diff), previews, approvals, mapping changes | README:30 | IMPLEMENTED | `main.py:375`, `:420-427`, `:575`; `/api/audit` asserted in `test_accuracy_benchmark_api_runs_saves_and_reports_on_the_overview`, `test_fields_api_lists_changes_and_audits` | hash chain: OP-09 |
| AP-10 Undo / redo of corrections before approval | STATUS:46; UM:245-246 | NOT VERIFIED | `workspace.js:444-460`; manual browser check only (STATUS:46) | automated UI test |

## 13. Ask AI

| Feature | Source | Status | Evidence | Missing parts |
|---|---|---|---|---|
| AA-01 Question → intent → one compiled read-only SELECT; the model never writes SQL | README:27; ARCH:49-53; STATUS:48 | IMPLEMENTED | `insight/query.py`; `test_only_select_statements_run`, `test_counts_with_value_linking_and_cancelled_rule`, `test_totals_never_double_count_across_child_rows` | — |
| AA-02 Independent SQLGlot safety review on every executor statement | STATUS:52; ARCH:30 | IMPLEMENTED | `insight/policy.py`; `test_hostile_statements_are_refused_before_reaching_oracle`, `test_executors_refuse_unsafe_sql_without_touching_the_connection` | — |
| AA-03 Join check + total cross-check | STATUS:53 | IMPLEMENTED | `insight/verify.py`; `test_a_parent_with_repeated_keys_stops_a_total_instead_of_double_counting`, `test_a_total_that_does_not_reconcile_is_flagged_and_lowers_confidence` | — |
| AA-04 Check trace on every answer | STATUS:54 | IMPLEMENTED | `engine.py` `run()`; `test_every_answer_carries_the_specialist_checks_in_order` | — |
| AA-05 Linked-table graph; adding a column recompiles with the right joins | README:26 | IMPLEMENTED | `test_adding_a_column_recompiles_with_the_right_join`, `test_chat_api_answer_feedback_example_and_preview` | — |
| AA-06 Model returns only an intent id + slots under a schema-constrained enum | RD:47, RD:57 | PARTIALLY IMPLEMENTED | `understand.py:541-562`: the model returns a free intent object (base, measures, group_by, filters…) that must compile against the catalog | intent allow-list, enum schema |
| AA-07 Weak questions read twice (rules + AI) and compared | STATUS:58 | IMPLEMENTED | `engine.ask`; `test_same_reading_raises_confidence`, `test_different_result_offers_the_other_reading` (stand-in model) | real model: AI-08 |
| AA-08 Value index: FTS5 trigram, CHESS column filter, nightly refresh, "as of" | RD:57 | PARTIALLY IMPLEMENTED | `local_store.py:46` plain `value_index` table; `service.py:303-330` refresh with `refreshed_at`; `test_value_index_keeps_newest_values_of_partial_columns_and_indexes_big_headers` | FTS5, nightly schedule |
| AA-09 Golden set 200–500 real questions; ≥95% CI gate | RD:20, RD:57; STATUS:104 | PARTIALLY IMPLEMENTED | `config/golden_questions.json` = 12 questions (runtime count), `tests/golden/demo.json` = 16; `test_every_golden_demo_question_is_answered_correctly`; laptop run not done (STATUS:136) | larger set, CI gate |
| AA-10 Charts from the returned rows | STATUS:50 | NOT VERIFIED | `frontend/js/chart.js`; no test references it | test |
| AA-11 Confirmed answers reused with new values | ARCH:69 | IMPLEMENTED | `test_saved_examples_generalise_to_new_values`, `test_two_values_of_the_same_column_mean_either` | — |
| AA-12 Ask AI + explorer SQL on the real Oracle | WS:169; STATUS:135 | NOT VERIFIED | `tests/test_oracle_insight.py` 4 tests skipped; CI Oracle job never creates POPPYS_RO ("Read-only account for Ask AI: NOT READY", run 49 log) | laptop run; RO account in CI |
| AA-13 Local read-only MCP server, every call audited | STATUS:57; WS:111-119 | IMPLEMENTED | `backend/mcp_server.py`; `test_handshake_and_tool_list_are_read_only`, `test_raw_select_only_when_allowed_and_only_through_the_safety_review`, `test_stdio_is_utf8_even_when_the_pipe_is_not` | — |

## 14. Database explorer

| Feature | Source | Status | Evidence | Missing parts |
|---|---|---|---|---|
| DE-01 Business names, decoded master names, linked parents/children | README:29; STATUS:64 | IMPLEMENTED | `test_explorer_uses_catalog_names_decoded_masters_and_links`, `test_explorer_order_view_nests_sizes_under_lines`, `test_F_saved_order_opens_in_database_with_lines_sizes_and_decoded_masters` | — |
| DE-02 Exact counts, "≈" statistics for very large tables, time-limited | README:29 | IMPLEMENTED | `main.py:1308-1320`; `test_explorer_counts_are_approximate_only_for_very_large_unfiltered_tables`, `test_counts_are_time_limited_and_fall_back_honestly` | — |
| DE-03 Statistics-first counts; COUNT(*) only on request; 24 h cache | RD:56; STATUS:104 | PARTIALLY IMPLEMENTED | exact COUNT(*) by default unless stats exceed BIG_ROWS (`main.py:1314-1318`); cache 30 s (`main.py:1324-1326`) | stats-first default, 24 h cache |
| DE-04 Keyset paging only, never OFFSET | RD:56; OSR:119; ARCH:78 | PARTIALLY IMPLEMENTED | keyset when a numeric key exists (`main.py:1485-1489`); otherwise `dialect.page` → OFFSET (`main.py:1491`, `insight/query.py:78-81`) | keyset for other keys; refuse OFFSET |
| DE-05 Expandable relationship map ("+") | README:29 | IMPLEMENTED | `/api/knowledge/neighbours` exercised in `test_insight.py` | — |
| DE-06 Plan-regression guard in CI; prefetch tuning; IN lists in chunks of 1000 | RD:56, RD:136 | DOCUMENTED ONLY | no EXPLAIN PLAN job in `ci.yml`; `oracle_store.py:211` chunks of 500 | guard, tuning |

## 15. Data dictionary & evidence

| Feature | Source | Status | Evidence | Missing parts |
|---|---|---|---|---|
| DD-01 Knowledge catalog merges dictionary, registry, curated meaning and admin edits | ARCH:57; STATUS:47 | IMPLEMENTED | `insight/knowledge.py`; `test_catalog_merges_dictionary_registry_and_curated_meaning` | — |
| DD-02 Business names, synonyms, relationship confirm/reject, all audited | README:28; WS:87-91 | IMPLEMENTED | `main.py:1092-1118`; `test_knowledge_api_and_admin_edits` | — |
| DD-03 Evidence ledger (immutable, idempotent, conflict-refusing, atomic bundles, open questions) | STATUS:68; OSR:136 | IMPLEMENTED | `backend/evidence.py`; `tests/test_evidence.py` (6 tests) | — |
| DD-04 "Refresh from Oracle" dictionary harvest (comments, PK/unique keys, indexes) | WS:85 | NOT VERIFIED | `main.py:1119-1129`; no test calls `/api/knowledge/refresh`; `test_oracle_harvest_lists_indexes_and_live_lookups_stay_within_budget` is Oracle-only (skipped) | laptop run |

## 16. Security

| Feature | Source | Status | Evidence | Missing parts |
|---|---|---|---|---|
| SE-01 Password hashing: scrypt N=2^17 (deviation from Argon2id, documented), legacy PBKDF2 re-hashed | RD:60; STATUS:62 | IMPLEMENTED | `auth.py:28-62`; `test_the_product_hashes_at_the_owasp_scrypt_setting`, `test_an_account_from_an_earlier_version_signs_in_once_and_is_rehashed` | — |
| SE-02 New passwords: ≥12 chars, not common, not product words, not the username | RD:60; UM:143-148 | IMPLEMENTED | `auth.py:85-95`; `test_weak_new_passwords_are_refused_with_the_reason`, `test_setup_refuses_a_weak_first_password` | — |
| SE-03 Wrong-password throttling in SQLite per name and per address | RD:60 | IMPLEMENTED | `auth.py:99-122`; `test_wrong_passwords_slow_down_that_account_and_survive_a_restart`, `test_one_address_trying_many_names_is_paused`, `test_unknown_names_are_throttled_the_same_way_so_refusals_reveal_nothing` | — |
| SE-04 Lock after 50 consecutive failures | RD:60 | DOCUMENTED ONLY | deliberately not built (STATUS:62) | (accepted deviation) |
| SE-05 Idle 30 min / absolute 8 h; sign-in, role, password and deactivation end sessions; Clear-Site-Data | RD:58 | IMPLEMENTED | `main.py:94-131`, `:193-199`, `:239-248`; `test_sessions_end_after_30_idle_minutes_but_not_while_used`, `test_sign_in_replaces_the_presented_session_and_logout_clears_the_browser`, `test_a_role_change_signs_that_user_out` | — |
| SE-06 Admin "sign out everyone" | RD:58 | PARTIALLY IMPLEMENTED | only `scripts/reset_login.py`, which also deletes every account; `test_reset_removes_accounts_and_sessions_and_the_first_run_screen_returns` | in-app sign-out-all |
| SE-07 `Secure` cookie flag / local TLS | RD:58 | DOCUMENTED ONLY | `main.py:130` sets HttpOnly, SameSite=Strict, Path=/ only; deferred (STATUS:62) | TLS + Secure |
| SE-08 CSRF: Sec-Fetch-Site / Origin check on unsafe methods | RD:59 | IMPLEMENTED | `main.py:53-58`; `test_changes_from_another_site_or_port_are_refused`, `test_cross_origin_and_unsupported_upload_rejected` | — |
| SE-09 Required `X-Requested-With: poppys` header | RD:59 | DOCUMENTED ONLY | deferred (STATUS:62) | header check |
| SE-10 Strict CSP (no inline styles), COOP, CORP, Permissions-Policy, `no-referrer` | RD:61, RD:143 | PARTIALLY IMPLEMENTED | `main.py:60-65`; runtime: CSP allows `style-src 'unsafe-inline'`, no COOP/CORP/Permissions-Policy; 83 `style="` attributes in `frontend/` (grep) | all listed headers; remove inline styles |
| SE-11 Body limit that counts streamed bytes | RD:61, RD:94 | PARTIALLY IMPLEMENTED | `main.py:51-52` checks the Content-Length header only; upload reads MAX_BYTES+1 after Starlette has spooled the body (`main.py:357`) | streamed limit for chunked bodies |
| SE-12 No `str(exc)` in errors; problem+json with request_id; docs/openapi off in production | RD:61, RD:95 | PARTIALLY IMPLEMENTED | Oracle errors mapped to a fixed text (`main.py:81-84`); ValueError/RuntimeError return `str(exc)` (`main.py:73-74`, `:87-88`); runtime: `/openapi.json` 200, `/docs` 200 | problem+json, request_id, hide docs |
| SE-13 Coarse route limits on uploads and questions | RD:60 | PARTIALLY IMPLEMENTED | at most 2 parse workers, a 3rd upload gets "busy" (`isolation.py:40-41`, `:63`) | per-user limits on uploads and Ask AI |
| SE-14 DPAPI-protected Oracle secret, `poppys.toml`, fail-closed settings validation | RD:62; STATUS:108; UM:695 | DOCUMENTED ONLY | `config.py:10` reads plain-text `.env.poppys`; `grep -rni 'cryptprotect\|dpapi\|poppys.toml' backend scripts` → 0 | all |
| SE-15 Data directory from `POPPYS_DATA_DIR` | RD:62 | IMPLEMENTED | `config.py:8`; `test_entry_script_speaks_mcp_over_stdio` (starts the server with `POPPYS_DATA_DIR` set, `tests/test_mcp.py:112`) | — |
| SE-16 No credentials in the repository (secret scan gate) | README:93 | PARTIALLY IMPLEMENTED | local scan passes (`check.sh secrets`); **CI gitleaks over history: "leaks found: 6" (run 49)** | triage the 6 findings (may be false positives); this audit did not open them |

## 17. Operations (logs, backup, service, upgrade)

| Feature | Source | Status | Evidence | Missing parts |
|---|---|---|---|---|
| OP-01 Backup (online API, integrity + FK check, zip + SHA-256, 14/8/12 retention, second copy), verify, restore, drill | RD:64; RB:132-233; STATUS:69 | IMPLEMENTED | `scripts/backup.py`; `tests/test_backup.py` (26 tests, e.g. `test_backup_writes_a_verified_zip_with_manifest_and_checksum`, `test_restore_round_trip_equals_source_rows`, `test_drill_starts_the_restored_app_without_oracle_settings`) | Windows run: OP-02 |
| OP-02 Nightly backup via Task Scheduler as LOCAL SERVICE | RB:148-158 | NOT VERIFIED | `deploy/windows/backup-task.ps1`; RB:7, RB:456 "not yet run on Windows" | Windows rehearsal |
| OP-03 WinSW service (LocalService, delayed start, restart policy, log rolling) | RD:65; RB:41-103 | NOT VERIFIED | `deploy/windows/PoppysAI.xml`; static tests only (`test_service_xml_is_well_formed_and_has_the_researched_settings`, `test_powershell_scripts_parse`) | Windows rehearsal |
| OP-04 Upgrade with backup, junction switch and automatic rollback | RD:65; RB:235-296 | NOT VERIFIED | `upgrade.ps1`, `rollback.ps1`; RB:452 | Windows rehearsal |
| OP-05 JSON logs, request IDs, redaction, rotation | RD:63; STATUS:106; UM:694 | DOCUMENTED ONLY | `grep -rn 'dictConfig\|request_id\|X-Request-ID' backend` → 0; `PoppysAI.xml:21` "added when JSON logging … ships" | all |
| OP-06 `/healthz` and `/readyz` | RD:63; RB:355 | DOCUMENTED ONLY | runtime 404 for both; deploy scripts fall back to the home page (`install-service.ps1:163-171`) | both endpoints |
| OP-07 Detailed `/api/health` behind sign-in | RD:63 | IMPLEMENTED | `main.py:255-274`; called in `test_offline_preview_is_marked_and_cannot_be_approved` | — |
| OP-08 SQLite `closing()`, `synchronous=FULL`, `user_version` migrations | RD:64, RD:96; STATUS:107 | DOCUMENTED ONLY | `local_store.py:24-29` `with sqlite3.connect()` (never closes), only `foreign_keys=ON`; schema patched via `PRAGMA table_info` (`local_store.py:58-70`) | all |
| OP-09 Hash-chained audit with UPDATE/DELETE triggers and "verify chain" | RD:64, RD:127; STATUS:107 | DOCUMENTED ONLY | `local_store.py:39` plain `audit` table | all |
| OP-10 `/api/version`, SemVer, CHANGELOG | RD:64, RD:150 | DOCUMENTED ONLY | runtime 404; `ls CHANGELOG*` → none | all |
| OP-11 Oracle password rotation procedure | RB:397-428 | NOT VERIFIED | runbook only; RB:458 | rehearsal |
| OP-12 `setup.ps1`, `check_oracle.py`, `discover_schema.py` | WS:24-52; STATUS:133-134 | IMPLEMENTED | passed on the laptop's real Oracle 2026-10-18 (STATUS:133-134); CI run 49 ran bootstrap/grants/`check_oracle.py` successfully | — |
| OP-13 `verify_local.ps1` one-command local check | STATUS:124; WS:156-162 | NOT VERIFIED | not run on the laptop (STATUS:135-137) | laptop run |
| OP-14 `reset_login.py` | STATUS:60; WS:66 | IMPLEMENTED | `test_reset_removes_accounts_and_sessions_and_the_first_run_screen_returns`, `test_nothing_changes_without_the_typed_confirmation` | — |
| OP-15 Offline runtime: no CDN, local Ollama, local Oracle | README:31, README:73 | IMPLEMENTED | `bash scripts/check.sh js` (refuses other-site resources); `ai.py:15` default `127.0.0.1`; `grep -rn 'https\?://' backend` → only loopback | — |

## 18. Packaging & CI

| Feature | Source | Status | Evidence | Missing parts |
|---|---|---|---|---|
| PK-01 Offline release: hashed Windows wheelhouse, `requirements.lock`, SHA256SUMS; `setup.ps1 -Offline --require-hashes` | STATUS:70; WS:186-194 | NOT VERIFIED | `scripts/build_release.py`, `setup.ps1:22-27`; no test references `build_release` (grep); STATUS:70 cites a manual cloud run; Windows pending | automated test; Windows run |
| PK-02 Embeddable CPython 3.13.x inside the release | RD:65; WS:10 | DOCUMENTED ONLY | `build_release.py:53` can only add the python.org installer; RB:459 | all |
| PK-03 SBOM, THIRD_PARTY_LICENSES, licence gate | RD:65-66, RD:148 | DOCUMENTED ONLY | `grep -n 'sbom\|cyclonedx\|licen' scripts/build_release.py` → 0 | all |
| PK-04 Authenticode signing of exe/ps1 | RD:65 | DOCUMENTED ONLY | not in `build_release.py` or `deploy/` | all |
| PK-05 Runtime dependency hygiene: plain `uvicorn`, httpx out of runtime, explicit starlette/pydantic pins | RD:66 | PARTIALLY IMPLEMENTED | `requirements.txt`: `uvicorn[standard]==0.46.0`, `httpx==0.27.2` (used by `ai.py:7`); starlette/pydantic not pinned | as listed |
| PK-06 CI jobs (secrets, lint, js, tests, oracle, audit, windows) exist and pass | RD:66 | PARTIALLY IMPLEMENTED | `.github/workflows/ci.yml`; `test_workflow_has_every_job_and_least_privilege`; run 49: tests/lint/js/pip-audit pass, secret scan / Oracle / Windows fail; last green run: 15 | a green pipeline |
| PK-07 SHA-pinned actions, pinned Oracle image digest, zizmor, CodeQL, coverage gates, Dependabot, provenance | RD:66, RD:149 | DOCUMENTED ONLY | `ci.yml:41` `actions/checkout@v7` (tags); `ci.yml:115` image without digest; `test_actions_and_images_are_pinned` accepts tags; `.github/` holds only `workflows/` | all |
| PK-08 Oracle Free CI integration job green | STATUS:44 ("replica-tested") | NOT VERIFIED | run 49: one test failure, then segfault (exit 139) in `test_large_db.py:290`; `test_oracle.py` never reached | fix and re-run |
| PK-09 Lint clean | STATUS:19 | IMPLEMENTED | `python3 -m ruff check --no-cache .` → All checks passed | — |
| PK-10 Frontend: no CDN, no inline scripts/handlers (CSP-safe) | README:73 | IMPLEMENTED | `bash scripts/check.sh js` → OK; `test_js_check_finds_syntax_errors_and_csp_breaks` | — |
| PK-11 Dependency vulnerability audit (pip-audit) | RD:66 | IMPLEMENTED | CI run 49 "Dependency audit (pip-audit)" success | runs on requirements.txt, not a hashed lock |

## 19. UX & accessibility

| Feature | Source | Status | Evidence | Missing parts |
|---|---|---|---|---|
| UX-01 Axpert-style review (header form, size × colour grid, frozen columns, fabric/trims tables) | README:23 | NOT VERIFIED | `frontend/js/workspace.js`; no automated UI test | browser test |
| UX-02 Red outline on the exact field with a one-line reason; "Show me the next one" | README:22; STATUS:45 | NOT VERIFIED | `workspace.js:391`, `:546`; `aria-invalid` used 8 times; backend paths tested (VA-11) | UI test |
| UX-03 Mapping table: header + sample values, status chips, duplicate targets blocked, unmatched required field blocks Next | RD:67 | PARTIALLY IMPLEMENTED | click-to-pick screen (`mapping.js`); server refuses one cell for two fields (EXU:132) | sample-value table, chips |
| UX-04 Value-mapping step (distinct unmatched master values with counts, cap 50) | RD:52, RD:67; STATUS:92 | DOCUMENTED ONLY | `grep -rni 'value.map\|Map to existing' frontend backend` → 0 | all |
| UX-05 Per-column date-format override (dd/mm vs mm/dd) | RD:67 | DOCUMENTED ONLY | `grep -rni 'date format\|dd/mm' frontend` → 0 | all |
| UX-06 Error summary above the grid ("There is a problem: N errors") | RD:68 | DOCUMENTED ONLY | `grep -rni 'There is a problem\|error summary' frontend` → 0 | all |
| UX-07 APG grid keyboard model (roving tabindex, F2/Escape, Ctrl+Home/End) | RD:68 | DOCUMENTED ONLY | `grep -rn 'roving' frontend` → only "approving" text | all |
| UX-08 Filter chips All / Errors / Warnings / Edited | RD:68 | PARTIALLY IMPLEMENTED | "only rows that need attention" checkbox (`workspace.js:353`) | Warnings/Edited chips, per-column filter |
| UX-09 "Download file with problems marked" (comments, fills, Errors sheet) | RD:68 | DOCUMENTED ONLY | grep → 0 | all |
| UX-10 Deterministic bulk fixes logged as maker edits | RD:68 | DOCUMENTED ONLY | `grep -rni bulk frontend backend` → 0 | all |
| UX-11 Explorer tables: `<caption>`, `aria-sort`, focusable region, `scroll-padding` | RD:69 | PARTIALLY IMPLEMENTED | sticky headers (13 `sticky`), `tabular-nums` (10); `caption` only as a chart `figcaption`; `aria-sort` 0; `scroll-padding` 0 | caption, aria-sort, region, scroll-padding |
| UX-12 Dark-mode token set; contrast pytest; axe-core checks | RD:66, RD:69 | DOCUMENTED ONLY | `grep -rn prefers-color-scheme frontend` → 0; no axe/contrast tests | all |
| UX-13 Graph zoom/pan (Ctrl+wheel, +/−, fit), CSP-safe | RD:70; STATUS:65 | IMPLEMENTED | `frontend/js/graph.js`; `tests/test_graph_view.py` (5 tests, run in Node) | — |
| UX-14 Graph minimap, arrow keys between nodes, list view (treeview), live region | RD:70 | PARTIALLY IMPLEMENTED | `graph.js:128-160` focusable nodes (Enter), `role="group"`, help text; `grep -rn 'minimap\|treeview' frontend` → 0 | minimap, list view, node-to-node arrows |
| UX-15 Phone layout without horizontal page scroll | STATUS:67 | NOT VERIFIED | `styles.css` `minmax(0,1fr)` grids; manual check only | test |
| UX-16 Overview screen with real counts only | STATUS:66 | IMPLEMENTED | `main.py:613`; `test_overview_counts_real_drafts_and_never_invents_oracle_figures` | — |

## 20. Multi-company / multi-schema

| Feature | Source | Status | Evidence | Missing parts |
|---|---|---|---|---|
| MC-01 Point the app at a schema other than GARMENT_DEMO (client test, standby or production ERP) | product goal (verified Oracle writes for the company); README:94 says scoped to the demo | NOT IMPLEMENTED | `config.py:15-17` raises "This demo build is scoped to GARMENT_DEMO"; `bootstrap_local.py:28` expects exactly 19 tables | owner configuration, grants scripts per owner |
| MC-02 Several mapping registries (per screen/department) merged "without code changes" | ARCH:73 | DOCUMENTED ONLY | `erp/mapping.py:9` one fixed `PATH`; `_default()` loads one file | loader for several files |
| MC-03 Per-site settings (exporter, sample type, four-eyes) | UM:440-443 | IMPLEMENTED | `main.py:286-313`; `/api/settings` used in `test_roles_and_four_eyes` | — |
| MC-04 Per-site golden questions alongside the shipped set | WS:109 | IMPLEMENTED | `main.py:1180-1185`; `test_accuracy_benchmark_api_runs_saves_and_reports_on_the_overview` | — |

---

## Re-verification of the earlier quick audit

The earlier audit was written before two changes (master matching M1, sign-in security S2). Each item, re-checked against
commit `e811402`:

| Earlier claim | Now | Row |
|---|---|---|
| `.xlt` not in readers ACCEPT | Still true | FS-04 |
| No canonical Pydantic order model / `model_json_schema` | Still true | FS-11 |
| No explicit `.xla/.xlam` rejection, no SYLK/DIF/.prn detection | Mostly true: refused by extension with a generic 415; SYLK/DIF content in a `.csv` is read as CSV | FS-12 |
| No VelvetSweatshop retry; encrypted files refused | Still true | FS-14 |
| ZIP entry cap 10,000; no `..`/ratio checks | Still true; but real inflated sizes are measured, which covers bombs and lying sizes | FS-15, FS-16 |
| Expat check only reported | Still true | FS-20 |
| No formula-injection flagging; CSV export not TAB-prefixed; `innerHTML` + `esc` widely used | Still true (73 `innerHTML` assignments) | FS-22–24 |
| No BFS header detection; `find_table` rows 1–120; no reshape operators | Still true; size grids are stacked by the matrix definition | EU-06, EU-08 |
| Matcher 0.6·name + 0.4·value, no RapidFuzz, **no master overlap** | Partly wrong: a per-value master membership signal exists (`matcher.py:101`) | FM-02, FM-03 |
| Greedy assignment, no AUTO/margin bands, no buyer/global history | Still true; global admin aliases exist | FM-04, FM-05, FM-11 |
| LLM whole-sheet prompt, `format='json'`, 120 s, no vote cap, AI-only field pre-selectable (~617), no logging, free Ask AI intent | Still true. Also: AI agreement lifts a `low` field to pre-selected (`matcher.py:607-609`, runtime probe) | AI-04–07, AA-06 |
| Validators missing (18 months, regex, ≥95% dates, acknowledgement, repair loop); no control-total vector; packs×pieces only a warning | Still true; a basic totals dict is part of the plan hash | VA-05–09, FM-07, UB-04 |
| No import ledger (content hash, business key), no amendment/reject | Still true | ID-04–06 |
| Units only PCS/PACK; nothing stored about the entered unit | Still true | UB-02, UB-03 |
| BOM g/pc only, wastage 0–100% accepted | Still true | UB-06, UB-07 |
| Masters created inside posting | **Changed:** writing differences now reuse the existing record and similar names are noted (M1); creation inside posting remains | MD-01–05 |
| No pool, preflight, `fetch_decimals`, writer `call_timeout`, `client_identifier` | Still true | OR-06–08, OR-11 |
| RO account gets SELECT not READ; no READ ONLY user, profiles, unified audit | Still true | OR-04, OR-05 |
| Posting lacks `setinputsizes`/`executemany`; no automatic fresh read-back, VERIFIED/POST_FAILED, dry run, reversal | Still true; the Saved screen does request a fresh-connection read-back, but nothing is stored | PC-05–10 |
| OFFSET paging without a numeric key; exact COUNT(*) by default | Still true (with a stats fallback for very large tables) | DE-03, DE-04 |
| Value index not FTS5; golden set 12 | Still true | AA-08, AA-09 |
| No DPAPI / `poppys.toml` | Still true | SE-14 |
| No JSON logs / request_id / `/healthz` / `/readyz` | Still true (runtime 404) | OP-05, OP-06 |
| SQLite without `closing()` / `synchronous=FULL` / `user_version`; audit not hash-chained; no `/api/version` | Still true | OP-08–10 |
| No embeddable CPython / SBOM / licences / signing | Still true | PK-02–04 |
| CI actions not SHA-pinned | Still true; **CI is also red** | PK-06–08 |
| UX: no value mapping, date override, error summary; self-approval on; no reject; no draft locks | Still true | UX-04–06, AP-03, AP-04, AP-08 |
| Tables lack caption/aria-sort; graph lacks minimap/list view | Still true | UX-11, UX-14 |
| Sign-in (PBKDF2 300k, in-memory per-IP throttle, no timeouts) | **Resolved by S2**, except the documented deviations: no 50-failure lock, no `Secure` flag, no `X-Requested-With` | SE-01–09 |

## Biggest gaps between documentation and code

1. **The Oracle write path is unproven by any test that ran.** README:21 and ARCH:26 describe a locked, hash-bound, read-back
   transaction. The code exists (`oracle_store.py:143-195`), but:
   - every cloud test swaps it for a stand-in;
   - the opt-in tests are skipped;
   - the laptop run has not happened;
   - the CI Oracle job is failing (segfault).

   STATUS:44 calls it "replica-tested", which the current CI does not support.
2. **"Saved and verified" is weaker than documented.**
   - There are no VERIFIED / POST_FAILED states and no control-total vector.
   - The read-back result is not stored.
   - The Saved screen's heading says "Saved and verified" before the read-back has answered (`workspace.js:686`).
3. **Units.** UM:685 and RD:50 plan DZN/PR/SET/CTN and stored entered units; the code knows PCS and PACK only, and a packs ×
   pieces mismatch is only a warning.
4. **Duplicates and amendments.** Only byte-identical files and identical order references are caught. The same order sent
   again as `.xls` instead of `.xlsx` under a new OC No is not caught, and there is no amendment or reject flow.
5. **Approval controls.**
   - Self-approval is on by default.
   - There is no reject-with-reason and no draft lock.
   - Warnings need no acknowledgement.
6. **Master creation inside posting** (`"create": true` for 7 masters), and a hard failure for any master above 5,000 rows on a
   large ERP.
7. **Local AI bounds.** RD:47 says the model's vote can never lift a column, but agreement lifts a low-confidence field into
   the pre-selected mapping (runtime-confirmed). Prompt and digest logging, seed, `num_ctx` and the 20 s timeout are absent.
8. **Upload hygiene and front-end hardening.**
   - Formula-injection flagging and the CSV TAB prefix are missing.
   - The CSP keeps `'unsafe-inline'` styles (83 inline `style=` attributes).
   - The body limit only reads Content-Length.
   - `/docs` and `/openapi.json` are public.
9. **Operations promised in RB/RD but absent:**
   - JSON logs with request IDs
   - `/healthz` and `/readyz` (the deploy scripts already probe `/readyz`)
   - SQLite durability settings and migrations
   - a hash-chained audit
   - `/api/version`
   - DPAPI secrets
   - every Windows service, backup-task and upgrade script, none of them rehearsed on Windows
10. **Understanding and matching.**
    - RD:44-46 describe region detection, header-band scoring, reshape operators, Hungarian assignment, AUTO bands and buyer
      history. The code is a simpler greedy matcher with a 120-row scan.
    - There is no labelled corpus to measure accuracy, and the two client workbook tests are skipped.
11. **Docs claim more than the code does in three places:**
    - ARCH:73 says registries merge "without code changes" (one file is hard-coded).
    - UM:319 says Saved orders lists all imports (capped at 100).
    - README:93 implies a clean secret history, but CI gitleaks reports 6 findings that need triage.

## Counts per status label

| Area | IMPLEMENTED | PARTIALLY IMPLEMENTED | DOCUMENTED ONLY | NOT IMPLEMENTED | NOT VERIFIED | Rows |
|---|---|---|---|---|---|---|
| 1 File formats & safety | 14 | 4 | 5 | 0 | 1 | 24 |
| 2 Excel understanding | 9 | 2 | 2 | 0 | 2 | 15 |
| 3 Field matching & templates | 4 | 5 | 2 | 0 | 0 | 11 |
| 4 Local LLM | 3 | 1 | 3 | 0 | 2 | 9 |
| 5 Validation | 6 | 2 | 3 | 0 | 0 | 11 |
| 6 Units & BOM | 2 | 3 | 3 | 0 | 0 | 8 |
| 7 Master data | 3 | 2 | 2 | 0 | 1 | 8 |
| 8 Mapping registry & verification | 6 | 1 | 0 | 0 | 0 | 7 |
| 9 Oracle accounts & driver | 3 | 0 | 6 | 0 | 2 | 11 |
| 10 Posting / commit / read-back | 3 | 3 | 4 | 0 | 2 | 12 |
| 11 Idempotency & duplicates | 4 | 0 | 3 | 0 | 0 | 7 |
| 12 Approval workflow & roles | 4 | 2 | 3 | 0 | 1 | 10 |
| 13 Ask AI | 8 | 3 | 0 | 0 | 2 | 13 |
| 14 Database explorer | 3 | 2 | 1 | 0 | 0 | 6 |
| 15 Data dictionary & evidence | 3 | 0 | 0 | 0 | 1 | 4 |
| 16 Security | 6 | 6 | 4 | 0 | 0 | 16 |
| 17 Operations | 5 | 0 | 5 | 0 | 5 | 15 |
| 18 Packaging & CI | 3 | 2 | 4 | 0 | 2 | 11 |
| 19 UX & accessibility | 2 | 4 | 7 | 0 | 3 | 16 |
| 20 Multi-company / multi-schema | 2 | 0 | 1 | 1 | 0 | 4 |
| **Total** | **93** | **42** | **58** | **1** | **24** | **218** |

**IMPLEMENTED** means "code plus a passing cloud test or a runtime check", usually against synthetic files and an in-memory
Oracle stand-in. It does not mean verified on the laptop's Oracle or on real buyer files. Per STATUS:133-137, only `setup.ps1`,
`check_oracle.py` and `discover_schema.py` have been run against the real local Oracle.
