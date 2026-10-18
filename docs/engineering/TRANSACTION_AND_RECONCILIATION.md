# Transaction and reconciliation

How an approved order reaches Oracle exactly once, and how POPPYS AI knows — rather than guesses — whether it did.

## 1. Before the transaction

| Check | Where | Status |
|---|---|---|
| The approver has the approve permission; **four-eyes on by default** (uploader and anyone who edited or mapped the draft cannot approve it unless an administrator allows self-approval, audited) | `main.accept` | Implemented, Tested |
| The approval names the exact preview (draft version + plan hash) | `main.accept` | Implemented, Tested |
| The preview was made against live Oracle and has no errors | `main.accept` | Implemented, Tested |
| The approver read the "read before approving" list (conversions, new masters, not saved, not read, layout changes): its digest must come with the request | `main.exceptions`, `exceptions_seen` | Implemented, Tested |
| Intent recorded before Oracle is touched: draft outcome `saving` + `approve_started` audit event | `main.accept` | Implemented, Tested |

## 2. The transaction (`oracle_store.commit`)

1. `SELECT … FROM PP_WRITE_LOCK WHERE ID=1 FOR UPDATE WAIT 10` — one save at a time across the database.
2. If `PP_IMPORT_LOG` already has this draft → return that receipt (idempotent).
3. Re-plan against the live masters; refuse on errors or a different plan hash.
4. `PP_ORDER_KEYS` row per order reference (unique: no duplicate orders).
5. INSERT each planned row with bind variables; read it back inside the transaction and compare every value; any
   difference rolls everything back.
6. INSERT the receipt into `PP_IMPORT_LOG` (rows written, masters reused, totals, not saved, exclusions, conversions,
   `complete`, file hash, plan hash).
7. COMMIT. Any error → rollback (never on a connection the driver dropped).

Each statement has a time limit (`WRITE_TIMEOUT_S`, default 60 s).

## 3. Deciding the outcome

| What happened | How it is decided | What the person sees |
|---|---|---|
| Commit returned | receipt | Saved, then verified (below) |
| Error, and Oracle's log has the receipt (the error came after the commit) | `PP_IMPORT_LOG` on a new connection | Saved |
| Error, Oracle reachable, no receipt (asked twice when the connection was lost) | `PP_IMPORT_LOG` | "Oracle confirms nothing was saved" — can approve again |
| Error, Oracle unreachable | — | **Save outcome unknown**: the draft is locked; **Check again** asks Oracle; nothing is ever saved twice (a retry first finds the receipt under the lock) |
| Oracle committed, but writing the local draft failed | receipt | Saved, with a note; the draft heals from Oracle when opened |
| Process stopped mid-save | draft outcome `saving` on restart | reconciled from Oracle when the draft is opened |

Tests: `tests/test_fidelity.py` (unknown outcome, error after commit, refused save, local failure after commit).
**Real network loss on real Oracle: NOT VERIFIED** (simulated with the stand-in).

## 4. Verification after the commit (Stage B)

`GET /api/history/{id}/graph` reads every written and reused row on a **new connection of the read-only account**,
compares it with the receipt, and recomputes control totals from what Oracle returned (pieces = sum of size quantities,
number of orders, number of lines). The draft records the outcome (`verification`), audited as `verified` or
`verification_failed`. Saved orders shows **Verified in Oracle**, **Verified, with exceptions**, **Saved, not verified
yet** or **Saved, differences found**. The Saved screen says "verified" only after this read.

## 5. Import ledger and duplicates

| Check | Status |
|---|---|
| Exact file already committed (file hash) | blocking error — Implemented, Tested |
| Order reference already in Oracle | blocking error — Implemented, Tested |
| Same buyer + PO + style already saved from another workbook (SQLite `import_ledger`) | warning to read: re-sent copy or amendment; amendments are saved as **new** orders | Implemented, Tested |
| Amendment workflow that updates an existing Axpert order | Not implemented (insert-only by design until the write channel is verified) |

## 6. Local store and Oracle are not one transaction

The receipt in Oracle is authoritative. Local state never claims success without it (§3), and the import ledger is
written from the receipt. Audit events and draft revisions are append-only.
