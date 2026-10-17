# Security policy

POPPYS AI runs on one Windows laptop inside POPPYS Garments. It is not offered as an internet service. Its users are a few staff members using a browser on the same machine (`http://127.0.0.1:8008`).

## Reporting a problem

Do not open a public GitHub issue for a security problem. Tell the POPPYS AI administrator in person or by private message, with:
- what you did;
- what happened;
- the reference ID the app showed, if there was one.

The administrator can find the matching log line by that ID in `data\logs\app.jsonl`, or `C:\ProgramData\PoppysAI\data\logs` for the service. Passwords and session tokens are never written to the log.

## What is protected, and how

| Risk | Control | Where |
|---|---|---|
| Wrong data saved into the ERP | Nothing is written until an approver approves the exact preview (plan hash). The save re-plans inside one transaction, reads every row back, and rolls everything back on any difference | `backend/oracle_store.py`, `backend/erp/planner.py` |
| Changing the live production ERP | This build refuses any schema owner except `GARMENT_DEMO`. The app account can only INSERT into named tables; it never updates or deletes | `backend/config.py`, `scripts/bootstrap_local.py` |
| A crafted question or explorer request changing data | Read-only pages use `POPPYS_RO` (READ grants only; a READ ONLY user on Oracle 23ai), a read-only transaction, a call timeout and a row limit. SQL is compiled from a checked intent; model-written SQL never runs | `backend/insight/`, `scripts/create_readonly_user.py` |
| A hostile buyer file | Format sniffed from the bytes; sizes capped before parsing; each upload parsed in a separate worker with time and memory limits. Formulas, macros, external links and DDE are never run. Formula-looking text bound for Oracle is flagged or refused | `backend/readers.py`, `backend/isolation.py`, `backend/erp/planner.py` |
| Script injection through file contents | Every file- or server-derived string is escaped before it reaches the page. A strict Content-Security-Policy is set: no inline scripts or styles, nothing from other sites | `frontend/js/util.js`, `backend/security.py`, `tests/test_csp_static.py` |
| Password guessing | PBKDF2-SHA256 with 600,000 iterations, a 12-character minimum and a common-password list. Throttling and a 20-failure lockout are stored in the database, so a restart does not reset them. The answer is the same for an unknown user and a wrong password. Passwords an administrator sets are temporary: the person must choose their own at the next sign-in | `backend/security.py`, `backend/main.py` |
| Stolen or forgotten sessions | 256-bit tokens, stored hashed. Cookies are HttpOnly, Secure and SameSite=Strict. Sessions end after 30 minutes idle or 8 hours, on sign-out, and on any password, role or active change | `backend/security.py`, `backend/local_store.py` |
| Cross-site requests | Origin/Referer and Sec-Fetch-Site checks on every state-changing request | `backend/security.py` |
| Disputed actions | Every upload, edit, preview, approval and admin change goes into an append-only audit log. It is hash-chained and HMAC-keyed with a key file outside the database, and its newest entry is anchored outside the database too. Setup → Audit can verify it. Approvers must acknowledge the preview's warnings; with four-eyes on, no one who uploaded or changed an order can approve it | `backend/local_store.py`, `backend/main.py` |
| Leaked credentials | Passwords live only in the git-ignored `.env.poppys` or are typed at a prompt. Logs and error answers are redacted. CI runs its own secret rules and Gitleaks over the whole history | `backend/logs.py`, `scripts/check.sh` |
| Vulnerable packages | Every package is pinned, and `pip-audit` runs on each push and weekly | `requirements*.txt`, `.github/workflows/ci.yml` |

Requirement-by-requirement mapping to OWASP ASVS 5.0 Level 2, with the test that proves each control: [docs/SECURITY_ASVS_L2.md](docs/SECURITY_ASVS_L2.md).

## Known limits

- **Plain HTTP on 127.0.0.1.** Browsers treat `127.0.0.1` and `localhost` as secure contexts, so Secure cookies work without TLS. Opening the app to other computers on the network would need TLS first, and is not supported.
- **Oracle Free in Docker is a rehearsal database.** It is unpatched. Do not put production data in it.
- **Direct INSERT into Axpert tables** skips Axpert's own numbering and maps. Production posting needs Agile Labs' sign-off: [docs/adr/0001-posting-path.md](docs/adr/0001-posting-path.md).
- **Windows-side scripts** (service install, upgrade, rollback, backup task) are checked by parsing and static rules in CI. They have not yet been rehearsed on a clean Windows machine: see [docs/RUNBOOK.md](docs/RUNBOOK.md).
