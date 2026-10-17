# ADR 0001: How approved orders reach Axpert

- **Status:** Proposed. Waiting for Agile Labs (the Axpert vendor) and the POPPYS DBA.
- **Date:** 2026-10-17
- **Decision makers:** POPPYS IT owner, Agile Labs, POPPYS DBA
- **Related:** [RESEARCH_DECISIONS.md](../RESEARCH_DECISIONS.md), "Oracle access: posting transaction"; checklist item "Axpert posting decision"

## Context

POPPYS AI saves an approved order by inserting rows directly into the Axpert tables of the `GARMENT_DEMO` schema:
- `ORDMAS` (order header);
- `ORDSTYLE` (lines);
- `ORDSIZE` (size quantities);
- new rows in master tables when a buyer, style, colour or size does not exist yet.

The save is careful:
- It runs under a write lock and as one transaction.
- It re-plans inside the transaction and requires the approved hash.
- Keys come from POPPYS's own sequence (`POPPYS_APP.PP_RECORD_SEQ`).
- It reads back every row and keeps a receipt in `POPPYS_APP.PP_IMPORT_LOG`.

The integration tests prove all of this against an Oracle Free 23 replica of the schema.

A direct INSERT still goes around Axpert itself:
- **Numbering.** Axpert assigns document numbers and record IDs through its own logic. Rows numbered by POPPYS's sequence may collide with, or look different from, rows made in the Axpert screens.
- **Gen/MD maps and tstruct scripts.** Axpert screens can run maps and scripts on save: totals, status fields, dependent records, and fields filled from other tables. A direct INSERT runs none of them. Only Oracle triggers run.
- **Standard columns.** Created-by, modified-on, cancel flags and workflow states are filled by Axpert on save. They may be empty or wrong in rows POPPYS inserts.
- **Support.** The vendor may not support data written outside its application.

None of this can be settled from the database alone. It needs the vendor's answer.

## Options

1. **Direct INSERT, as built.** First commit an inventory of `ALL_TRIGGERS` on the six order tables. Then get a written list from Agile Labs of the columns, numbering and maps a direct INSERT must reproduce, and add them to the planner.
   - Fastest, and fully under our control.
   - Risk: we keep up with Axpert changes by hand.
2. **Axpert Generic API / import facility.** POPPYS AI keeps reading, mapping, review and approval, but posts the approved order through Axpert's own API. Axpert then numbers the order and runs its maps.
   - Correct by construction, and supported by the vendor.
   - Depends on the API's availability, licensing, and its error answers mapping back to cells.
3. **Staging table + Axpert-side job.** POPPYS AI writes the approved order into a POPPYS-owned staging schema. An Axpert-side job or screen imports it.
   - Clean separation.
   - Needs Axpert-side development by the vendor.

## Decision

Not taken yet. Until it is:

- This build is scoped to `GARMENT_DEMO`. `backend/config.py` refuses any other schema owner, so the client's live production schema cannot be written by mistake.
- Saving stays enabled for the demo and replica schemas only, for rehearsal and acceptance tests A–F.
- No release may change the owner check without this ADR being marked **Accepted**, naming the chosen option.

## Questions for Agile Labs

1. Which document numbers and record IDs must an order created outside the Axpert screens use, and from which sequence or table?
2. Which Gen/MD maps, tstruct scripts or triggers run when the Sample Order Entry screen saves an order? What must happen for a row inserted directly?
3. Which standard columns (created by/on, modified by/on, cancel, workflow) must be filled, and with what values?
4. Does Axpert have a supported API or import path for orders (header, lines, sizes)? What are its licence and its error format?
5. Is data written directly into these tables supported, or does it void support?

## Consequences

- **Accepted with option 1:** extend the planner and the mapping registry with the vendor's rules. Add the trigger inventory output to the repository. Change the owner check to an explicit allow-list chosen at install time.
- **Accepted with option 2 or 3:** replace `oracle_store.commit` with an API client, or a staging writer. Keep the planner, approval hash and read-back. Read-back then confirms what Axpert created.
- **Either way:** the user-facing flow (upload → review → approve → verified) does not change.
