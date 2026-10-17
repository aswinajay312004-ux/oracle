# OWASP ASVS 5.0 Level 2: requirement → code → test

POPPYS AI is a single-laptop web app for a few staff, at `http://127.0.0.1:8008`. This map follows the chapters of ASVS 5.0. For each control it names the code that implements it and the automated test that proves it. Deviations are listed at the end, each with its reason. Design decisions and sources: [RESEARCH_DECISIONS.md](RESEARCH_DECISIONS.md), "Security: …" rows.

All test names are in `tests/` and run in CI (`.github/workflows/ci.yml`). The "(Oracle)" tests run in the real Oracle Free 23 job.

## V1 Encoding and sanitization

| Control | Code | Test |
|---|---|---|
| Oracle SQL uses bind variables only. Identifiers come from the mapping registry or the live dictionary, never from a request | `backend/oracle_store.py`, `backend/erp/planner.py`, `backend/insight/query.py` | `test_engine.py`, `test_large_db.py`, `test_oracle_insight.py::test_compiled_queries_run_read_only` (Oracle) |
| Questions become a checked intent. Only the compiler writes SQL, and model-written SQL never runs | `backend/insight/understand.py`, `backend/insight/query.py` | `test_insight.py` |
| Formula and DDE text is never evaluated. Text bound for Oracle that would act as a formula when exported is flagged or refused | `backend/erp/planner.py`, `backend/security.py` | `test_security.py::test_formula_risk`, `::test_planner_flags_formulas_on_the_field_and_never_evaluates_them`, `::test_planner_refuses_dde_payloads` |
| File-derived text is escaped before it reaches the page, and there is no inline script | `frontend/js/util.js` (`esc`), `scripts/check.sh js` | `test_ui_browser.py`, `test_csp_static.py` |

## V2 Validation and business logic

| Control | Code | Test |
|---|---|---|
| Every value saved to Oracle is checked first: required fields, whole quantities, totals, unit and pieces per pack, column lengths and precision, known masters | `backend/erp/planner.py` | `test_engine.py`, `test_acceptance.py`, `test_review_findings.py` |
| Approval is bound to the exact plan hash and draft version, and re-planned inside the transaction | `backend/oracle_store.py::commit` | `test_workflow.py`, `test_acceptance.py` (E), `test_oracle*.py` (Oracle) |
| Duplicate orders and files are refused | `backend/erp/catalog.py::order_ref_exists`, planner | `test_large_db.py`, `test_workflow.py` |
| An unclear value is asked for, never guessed: unit, ambiguous dates, decimal-comma quantities | `backend/workbooks.py` | `test_review_findings.py::test_european_thousands_are_never_guessed`, `test_acceptance.py` (C) |

## V3 Web frontend security

| Control | Code | Test |
|---|---|---|
| Strict CSP: `default-src 'self'`, no `unsafe-inline` or `unsafe-eval`, `object-src 'none'`, `base-uri 'none'`, `frame-ancestors 'none'` | `backend/security.py` (`CSP`) | `test_security.py::test_every_response_has_the_security_headers`, `test_csp_static.py`, `test_ui_browser.py::test_app_screens_under_the_strict_policy` |
| nosniff, `Referrer-Policy: no-referrer`, COOP/CORP, Permissions-Policy, `no-store` on API answers | `backend/security.py` (`HEADERS`) | `test_security.py::test_every_response_has_the_security_headers` |
| No resources from other sites; the app works offline | `frontend/`, `scripts/check.sh js` | `scripts/check.sh js` (CI job `js`) |

## V4 API and web service

| Control | Code | Test |
|---|---|---|
| Request bodies are limited while streaming, not only by Content-Length: 14 MiB for uploads and draft saves, 1 MiB elsewhere | `backend/security.py::BodyLimitMiddleware` | `test_security.py::test_chunked_30_mib_upload_without_content_length_is_refused_and_the_temp_file_stays_under_the_limit`, `::test_declared_content_length_over_the_limit_is_refused_before_reading`, `::test_other_routes_have_a_1_mib_limit_streamed_or_declared` |
| CSRF: state-changing requests need a same-origin Origin/Referer or `Sec-Fetch-Site: same-origin` | `backend/security.py::CSRFMiddleware` | `test_security.py::test_csrf_rules_for_state_changing_requests`, `::test_csrf_blocks_a_cross_site_login_and_upload`, `::test_csrf_does_not_block_reads` |
| API docs are off in production | `backend/main.py`, `security.production()` | `test_security.py::test_api_docs_are_off_in_production` |
| Only the loopback host names are answered (protects against DNS rebinding) | `backend/main.py` (`TrustedHostMiddleware`) | `test_security.py::test_only_loopback_host_names_are_answered` |
| Validation errors are one plain sentence per field and never echo the value sent. Draft edits accept only single plain values of bounded length | `backend/main.py::invalid_request`, `local_store._value` | `test_security.py::test_validation_errors_do_not_echo_the_input_or_context`, `::test_validation_errors_are_plain_sentences_for_staff`, `::test_typed_text_in_a_draft_must_be_one_value` |

## V5 File handling

| Control | Code | Test |
|---|---|---|
| The format is decided from the file's bytes. Unknown, encrypted, IRM and non-spreadsheet files are refused with a reason | `backend/readers.py::sniff` | `test_formats.py` (ole2, non-spreadsheet, encrypted cases) |
| Zip-bomb, cell-count, merge, nesting and text-size caps apply before and after parsing | `backend/readers.py` | `test_formats.py::test_values_spread_over_a_huge_area_are_refused_in_every_format`, JSON depth cases |
| Parsing runs in a worker process with a time and memory limit | `backend/isolation.py` | `test_formats.py::test_isolated_*` |
| Uploaded files are stored as `<draft id>.upload` in the data folder. The browser's file name is kept only as text, never used as a path | `backend/main.py` (`/api/uploads`) | by review (no dedicated test yet) |

## V6 Authentication

| Control | Code | Test |
|---|---|---|
| PBKDF2-SHA256 with 600,000 iterations and a per-password salt. Older 300k hashes still sign in and are upgraded on sign-in | `backend/security.py` | `test_security.py::test_new_hashes_are_pbkdf2_600k_with_the_count_stored`, `::test_legacy_300k_hash_still_signs_in_and_is_upgraded` |
| New passwords have at least 12 characters, are not on the common-password list, and do not contain the username | `security.password_problem` | `test_security.py::test_new_passwords_follow_the_policy_everywhere`, `::test_setup_refuses_a_weak_first_password` |
| Throttling per username and per address survives a restart. 20 failures in 15 minutes lock the username, and an admin can unlock it. The address is the real socket peer: every launcher runs uvicorn with `--no-proxy-headers`, so `X-Forwarded-For` cannot fake it | `backend/security.py`, `backend/local_store.py`, `start.ps1`, `deploy/windows/PoppysAI.xml` | `test_security.py::test_after_five_failures_the_next_try_waits_and_is_refused_without_checking`, `::test_the_wait_grows_and_survives_a_restart`, `::test_failures_from_one_address_across_many_usernames_are_throttled`, `::test_twenty_failures_in_fifteen_minutes_lock_the_username_and_an_admin_can_unlock` |
| An unknown user gets the same answer and costs the same time as a wrong password | `security.DUMMY_HASH`, `BAD_LOGIN` | `test_security.py::test_unknown_user_and_wrong_password_look_the_same`, `::test_a_lock_on_an_unknown_username_looks_the_same` |
| First-run setup works only while no user exists | `backend/main.py` `/api/setup` | `test_api.py::test_authentication_required_and_setup_once` |
| Everyone can change their own password, after giving the current one. A password set by an administrator (new user, reset) is temporary: nothing else works until the person chooses their own. A change ends that person's other sessions | `backend/main.py` (`/api/me/password`, `OWN_PASSWORD_FIRST`), `users.must_change` | `test_security.py::test_an_administrator_set_password_is_temporary` |

## V7 Session management

| Control | Code | Test |
|---|---|---|
| 256-bit random tokens, stored as SHA-256. The cookie is `HttpOnly; Secure; SameSite=Strict; Path=/` | `backend/security.py`, `backend/main.py::issue` | `test_security.py::test_session_cookie_flags_in_production` |
| Sessions end after 30 minutes idle and 8 hours absolute. Background polling does not count as activity | `security.IDLE_SECONDS`, `ABSOLUTE_SECONDS`, `PASSIVE` | `test_security.py::test_idle_timeout_and_rate_limited_last_seen`, `::test_absolute_timeout` |
| Signing in never reuses the presented session. A password, role or active change ends that user's sessions. An admin can sign out everyone else | `backend/main.py`, `backend/local_store.py` | `test_security.py::test_sign_in_never_reuses_the_presented_session`, `::test_a_change_of_role_active_or_password_ends_that_users_sessions`, `::test_admin_can_sign_out_everyone_else`, `::test_logout_ends_the_session` |

## V8 Authorization

| Control | Code | Test |
|---|---|---|
| Server-side permission on every route (`require(permission)`). The roles are admin, approver, operator and viewer | `backend/main.py` (`PERMISSIONS`, `require`) | `test_workflow.py::test_roles_and_four_eyes`, `test_api.py` |
| Optional four-eyes rule: no one who uploaded, edited or mapped a draft can approve it (a preview is not a change) | `backend/main.py::accept`, `local_store.draft_editors` | `test_workflow.py::test_roles_and_four_eyes`, `::test_four_eyes_also_refuses_an_approver_who_changed_the_draft` |
| An approver must tick "I have read these notes" when the preview has warnings. How many were acknowledged is kept in the audit log | `backend/main.py::accept` | `test_workflow.py::test_warnings_must_be_acknowledged_before_approval` |
| People can rate only their own Ask AI answers (administrators any) | `backend/main.py::chat_feedback` | `test_security.py::test_rating_someone_elses_answer_is_refused` |
| Least privilege in Oracle: the app account can INSERT into named tables only; the read-only account has READ only | `scripts/bootstrap_local.py`, `scripts/grant_bom_tables.py`, `scripts/create_readonly_user.py`, `scripts/check_oracle.py` | `test_oracle_pool.py::test_real_readonly_account_can_read_but_never_write` (Oracle), `::test_preflight_fails_a_read_only_account_that_can_insert` |

## V11 Cryptography

| Control | Code | Test |
|---|---|---|
| Only standard-library primitives: `secrets`, `hashlib.pbkdf2_hmac`, SHA-256, `hmac.compare_digest` | `backend/security.py`, `backend/local_store.py` | `test_security.py` |
| The audit chain is SHA-256 over each row and the previous hash, plus an HMAC-SHA256 chain keyed with `audit.key`, a file outside the database. The newest entry is anchored in `audit.anchor`, also outside the database. Database triggers forbid UPDATE and DELETE, and verify checks their exact text | `backend/local_store.py` | `test_ops.py::test_audit_rows_cannot_be_updated_or_deleted`, `::test_verify_finds_changes_made_behind_the_apps_back`, `::test_a_chain_rebuilt_without_the_key_is_found`, `::test_removing_the_newest_entries_is_found`, `::test_a_harmless_trigger_with_the_same_name_is_found`, `::test_a_missing_key_file_is_reported_not_ignored`, `::test_concurrent_audit_writes_keep_one_chain` |

## V13 Configuration

| Control | Code | Test |
|---|---|---|
| No secrets in the repository: own patterns, Gitleaks over all history, `.env.poppys` git-ignored | `scripts/check.sh secrets`, `.gitleaksignore` (reviewed fingerprints only) | `test_ci.py`, CI job `secrets` |
| Pinned dependencies, audited on every push and weekly | `requirements*.txt` | CI job `audit`, `test_ci.py::test_runtime_pins_have_no_known_advisories` |
| The server header is hidden; the app binds to 127.0.0.1 only | `start.ps1`, `deploy/windows/PoppysAI.xml` | `test_security.py::test_launchers_hide_the_server_header`, `test_backup.py::test_service_xml_is_well_formed_and_has_the_researched_settings` |

## V14 Data protection

| Control | Code | Test |
|---|---|---|
| Backups never contain `.env.poppys` or anything that looks like a secret | `scripts/backup.py` | `test_backup.py::test_backup_never_contains_env_files_or_secrets` |
| `Cache-Control: no-store` on API answers. Sign-out clears the site's data | `backend/security.py`, `backend/main.py` | `test_security.py::test_every_response_has_the_security_headers`, `::test_logout_ends_the_session` |

## V16 Security logging and error handling

| Control | Code | Test |
|---|---|---|
| JSON-lines logs with a request ID on every line. Sign-in, failures, lockouts, approvals and admin actions are logged | `backend/logs.py`, `backend/main.py` | `test_ops.py::test_every_request_gets_one_json_access_line_with_its_request_id`, `::test_security_events_are_logged_and_passwords_never_are` |
| Passwords, tokens and connection strings are redacted from fields and message text | `backend/logs.py` | `test_ops.py::test_redaction_of_fields_and_message_text` |
| Unexpected errors answer "Something went wrong. Nothing was saved. Reference …"; the traceback goes only to the log | `backend/security.py::problem` | `test_security.py::test_library_value_error_is_a_500_with_a_reference_and_the_traceback_only_in_the_log`, `::test_unexpected_exception_is_a_problem_response_with_headers`, `::test_validation_errors_do_not_echo_the_input_or_context` |
| Log rotation never loses a line and survives Windows file locks | `backend/logs.py` | `test_ops.py::test_rotation_*`, `::test_windows_sharing_violation_falls_back_to_copy_and_truncate` |

## Accepted deviations

| ASVS area | Deviation | Why it is acceptable here | Revisit when |
|---|---|---|---|
| V12 Secure communication (TLS) | Plain HTTP | The app binds to 127.0.0.1 only. Chromium-based browsers treat it as a secure context, so Secure cookies work, and no traffic leaves the machine | The app is opened to other computers. Then add TLS with uvicorn `--ssl-certfile/--ssl-keyfile` and a machine-trusted certificate, plus `__Host-` cookies and HSTS |
| V6 Multi-factor authentication | Not offered | A few staff on one physical laptop. The Windows sign-in is the first factor | Remote access is ever allowed |
| V8 Oracle row-level authorization | Not used | GARMENT_DEMO is a demo schema, and the app account has INSERT only on named tables | Production posting (see `docs/adr/0001-posting-path.md`) |
