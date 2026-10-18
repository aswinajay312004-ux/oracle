# Oracle / Axpert integration

**Bottom line: POPPYS AI writes orders into the GARMENT_DEMO tables by direct INSERT. That the rows are correct by
POPPYS's own rules is tested on a real Oracle replica. That Axpert accepts them as its own orders is NOT VERIFIED.**

## 1. What is written

The write mapping `config/oracle_mapping.json` (with audited overrides in Setup → Write mapping) lists, per table, the
business field each column receives and whether the column is **verified** (written) or **proposed** (not written).

| Table | Table status | Columns written (verified) | Not written (proposed) |
|---|---|---|---|
| ORDMAS (order header) | verified | ORDMASID, DOCID, BUYER, STYLENO, STYLEDESC, BONO, ODATE, UOM, SAMPLETYPE, ORDCATEGORY, REMARKS, MERCH, OQTY (13 of 197 columns) | ENAME (exporter), OAGAINST, DEPARTMENT, GENDER, COMPTYPE, NOCOMP |
| ORDSTYLE (lines) | verified | 12 columns incl. COMBO, ORDQTY, PRODQTY, SHIPDT, SUBNO | — |
| ORDSIZE (sizes) | verified | 7 columns incl. GSIZE, RQTY, PQTY, SUB3_SUBNO | — |
| ORDFAB, ORDYARN, ORDACC (BOM) | proposed | keys only | all business columns |
| Masters BUYMAS, STYLENUM, STYLEMAS, COLORMAS, SIZE_MAS, UNITMAS, MERCHMAS | created when missing | ID + name | every other column |

Values without a verified column are listed before approval and in the receipt (EXCEL_DATA_FIDELITY_SPEC F11).

## 2. Keys and relationships

- IDs come from POPPYS's own sequence `PP_RECORD_SEQ` (not Axpert's own key generator). **Known risk** until compared
  with Axpert.
- Header ↔ lines by ORDMASID; lines ↔ sizes by ORDMASID + SUBNO (`order_ref:line`); masters by their IDs.
- Duplicate protection: `PP_ORDER_KEYS` (unique order reference), file hash and the import log.
- **OC number (DOCID)**: when the reference is left empty, the save gives the next number of the company series
  (`config/company.json → order_number`, `PK/{fy}/S/{n}`; financial year April–March of the save day): the highest
  number of that series already in ORDMAS (Axpert's and POPPYS's orders) + 1, read while `PP_WRITE_LOCK` is held, so
  two POPPYS saves never take the same number. The preview carries a stand-in (`AUTO-OC#1#`), so its hash does not
  change when another order takes a number first. **Known risk:** Axpert's own order screen does not take
  `PP_WRITE_LOCK`; an Axpert entry and a POPPYS save in the same second could pick the same number (whether DOCID
  has a unique constraint on the client's schema is not checked). Whether Axpert keeps its own counter for this series is NOT VERIFIED.

## 3. What is proven

| Claim | Evidence | Status |
|---|---|---|
| Inserts, keys, links and read-back work on Oracle 23.26 | `bash scripts/check.sh oracle` on the synthetic replica: 529 passed (2026-10-18); CI Oracle job green | Tested (replica) |
| Values read back equal the approved plan (in the transaction and on a fresh read-only connection, with control totals) | `oracle_store.commit`, `readback.graph/control_totals` | Tested (replica, stand-in) |
| The same on the laptop's GARMENT_DEMO | `verify_local.ps1` 2026-10-18: 518 passed, 0 failed | Tested (laptop) |
| Axpert opens and processes POPPYS orders like its own | — | **NOT VERIFIED** |

## 4. Finding the gaps against Axpert (procedure)

`scripts/axpert_gap_report.py` (read-only) compares, per table an order touches, every column's fill rate among
Axpert's own rows and among POPPYS rows (identified by receipts), with nullability, defaults, triggers and flag values.
**GAP** = Axpert fills the column in ≥ 90 % of its rows, POPPYS never, and Oracle has no default.

On the replica it already reports `ORDMAS.CANCEL` (Axpert-style rows hold T/F, POPPYS rows NULL) — the replica is
synthetic, so this is a hint, not evidence about the real Axpert.

Laptop steps (owner):

1. Save one test order with POPPYS (Demo A).
2. Create one comparable order in Axpert's own screen.
3. Run `.venv\Scripts\python.exe scripts\axpert_gap_report.py` → `data\axpert_gap_report.md`.
4. Open the POPPYS order in Axpert's screens (order entry, size breakdown, any reports used daily) and note what is
   missing or wrong.
5. Decide per GAP column: fixed value, value from the file, Axpert procedure, or not needed.

## 5. Write channel decision (owner)

| Option | For | Against |
|---|---|---|
| Direct INSERT (today) | Works now, fully controlled and verified by POPPYS | Every column Axpert needs must be known; Axpert triggers/sequences/business logic bypassed |
| Axpert's own posting procedure / import | Axpert applies its own rules and keys | Needs the procedure's specification and test access; not available in this repository |

Until the decision and the gap report exist, **production writes to a client database must not start** (see
PRODUCTION_READINESS_REPORT).

## 6. Connection safety

- Writer: per-statement `call_timeout` (WRITE_TIMEOUT_S, default 60 s), `FOR UPDATE WAIT 10` on the write lock, rollback
  that never touches a dropped connection.
- Readers: separate read-only account `POPPYS_RO` (no write privileges, refused otherwise), read-only transactions,
  time limits, row caps, independent SQL review.
- The CI crash of 2026-10-18 (segmentation fault) came from setting `call_timeout` on a connection python-oracledb thin
  had dropped after a time limit (DPY-4011); fixed by never touching such a connection and reconnecting
  (`insight/service.usable`, `OracleExecutor`), reproduced and verified on Oracle 23.26.
- The production client database is never touched by any script or test in this repository.
