# POPPYS AI — Large-database rehearsal

The client database is 17 GB+ and growing. POPPYS AI reads it live: Ask AI, the Database explorer, the value
index and schema discovery. This rehearsal ran every one of those paths against a disposable Oracle with
millions of rows, found what grows with table size, fixed what was in reach and lists what is left.

**Short version.** Every path that used to read whole tables now uses an index, a sample, statistics or a
time limit. Lookups by order reference, record ID and explorer pages stay at about 0.1 s at any size.
Discovery takes about 12 s whatever the size (it took 6.5 min at 3M orders). Anything that still has to read a
whole big table is stopped at its time limit with a plain message. Nothing hangs. The Ask AI compiler no longer
filters big tables with `UPPER(TRIM(col)) = :v`: "size wise quantity for order X" now runs on the order-reference
index ([Ask AI text filters](#ask-ai-text-filters-follow-up)). What is left is the cost of `contains` searches.
See [Remaining risks](#remaining-risks).

## Test bed

| | |
|---|---|
| Machine | 4 vCPU / 15 GB container, shared with other test runs (single runs vary by ±30 %, so tables show the best of 2–3 runs) |
| Oracle | Oracle Free 23.26 (`gvenzl/oracle-free:23-slim-faststart`), second disposable container `poppys-replica-big` on port 1522 |
| App | `uvicorn backend.main:app` on port 8023 with `POPPYS_ORACLE_DSN/USER/PASSWORD` pointed at the big replica, read-only `POPPYS_APP` user |
| Data | `tests/oracle_replica.py --orders N`: the 19-table GARMENT_DEMO structure plus N synthetic orders (2 lines × 4 sizes each), with statistics gathered |
| 300k orders | ORDMAS 300,004 · ORDSTYLE 600,005 · ORDSIZE 2,400,010 rows (built in 35 s) |
| 3M orders | ORDMAS 3,000,004 · ORDSTYLE 6,000,005 · ORDSIZE 24,000,010 rows, **4.2 GB** of segments (ORDSIZE 1.4 GB + 1.1 GB of indexes, ORDMAS 0.8 GB). Built in 4 min 43 s |
| Indexes | Primary keys on the ID columns, `ORDMASID` on every child, `ORDMAS.DOCID`. The client's real index set is not known yet, so `ORDMAS.BONO` (buyer PO, no index) stands in for "a column without an index" |

"Before" is the committed code at the start of this work. "After" is this change. Times are wall-clock per
HTTP request from a local client, with a warm cache and one user.

## Results

| Request | 300k · before | 300k · after | 3M · before | 3M · after |
|---|--:|--:|--:|--:|
| Explorer: table list (`/api/explorer/tables`) | 0.15 s | 0.08 s | 0.08 s | 0.08 s |
| Explorer: ORDSIZE first page | 0.14 s | 0.09 s | 0.07 s | 0.06 s |
| Explorer: ORDSIZE next page (`after=`) | 0.12 s | 0.10 s | 0.07 s | 0.07 s |
| Explorer: ORDSIZE filter, size `contains` 33 (6M matches) | 0.48 s | 0.10 s | 4.09 s | **0.07 s** |
| Explorer: ORDSIZE filter, line `contains` 10123456 (8 matches) | 1.63 s | 0.97 s | 14.73 s | 9.99 s |
| Explorer: ORDSIZE filter, `contains` with no match | 1.31 s | 0.83 s | 13.41 s | 8.46 s |
| Explorer: ORDSIZE filter, order ID `is exactly` | 0.08 s | 0.08 s | 0.07 s | 0.09 s |
| Explorer: ORDMAS order reference `is exactly` (indexed) | 0.54 s | 0.09 s | 3.56 s | **0.08 s** |
| Explorer: ORDMAS buyer PO `is exactly` (no index) | 0.29 s | 0.19 s | 2.31 s | 0.62 s |
| Explorer: ORDMAS buyer PO `contains` | 0.40 s | 0.27 s | 1.94 s | 1.70 s |
| Explorer: linked records of one order (`/api/explorer/related/ORDMAS/<id>`) | 0.25 s | 0.11 s | 0.09 s | 0.12 s |
| Setup: refresh dictionary + value index (`/api/knowledge/refresh`) | 3.92 s | 1.37 s | 0.25 s ¹ | 2.49 s |
| Ask: How many orders? | 0.33 s | 0.27 s | 1.55 s | 1.51 s |
| Ask: How many orders for NEXT? | 0.28 s | 0.28 s | 1.28 s | 1.35 s |
| Ask: Monthly order quantity in 2024 | 0.18 s | 0.19 s | 0.96 s | 0.72 s |
| Ask: Top 5 buyers by order quantity | 0.25 s | 0.23 s | 1.08 s | 0.82 s |
| Ask: Size wise quantity for order PK/26-27/S/9001 | 1.11 s | 0.79 s | 14.79 s | 11.39 s ² |
| Ask: Size wise quantity for order HX/0123456 (not in the value index) | 1.23 s | 0.79 s | 13.48 s | 11.00 s ² |
| Ask: Show order PK/26-27/S/9999 (does not exist: live lookup finds nothing) | 0.53 s | 0.30 s | 5.37 s | **1.58 s** |
| Ask: Orders for PO-H123456 (buyer PO, no index) | 0.54 s | 0.33 s | 4.31 s | **1.28 s** |
| Ask: Show order HX/0299999 (a recent order) | 0.55 s | 0.28 s | 4.70 s | **1.30 s** |
| `scripts/discover_schema.py` | 41.5 s | 12.3 s | 387.9 s | **12.4 s** |

¹ Fast but incomplete before: above 2M rows, ORDMAS was left out of the value index entirely (10 columns,
23 values, against 14 columns and 40,027 values after), so "BULK ORDER" and recent order references could
not be recognised.
² The remaining time is the compiled Ask AI query itself (see [Remaining risks](#remaining-risks)). With a
5-second limit (`POPPYS_QUERY_TIMEOUT_S=5`) the browser shows *"Cannot answer safely — The database needed
more than 5 seconds for this, so POPPYS AI stopped the query…"* instead of waiting.

Statement level, 3M orders (best of 3):

| Statement | Before | After |
|---|--:|--:|
| Live lookup of an unknown word (4 × `UPPER(TRIM(col)) = :v` on ORDMAS) | 3.35 s | 0.37 s |
| Live lookup of an order reference not in the value index | 2.89 s | 0.007 s (DOCID index) |
| Live lookup of a buyer PO (no index; stops at the first match) | 2.52 s | 0.02 s |
| Size-wise query as compiled today: `UPPER(TRIM(t2.DOCID)) = :b0` | 9.6–11.3 s | — |
| The same query with `t2.DOCID = :b0` | 0.002 s | — |
| Explorer `contains` rows (no match, walks the whole key index) | 8.5 s | 8.5 s |
| Explorer `contains` COUNT(*) | 4.1 s | not run any more |
| Newest 20,000 order references, `GROUP BY … ORDER BY MAX(id)` | 2.6 s | read through the key index instead |
| COUNT(*) of ORDSIZE (24M) / ORDSTYLE (6M) | 0.84 s / 0.31 s | statistics (≈) |

## Ask AI text filters (follow-up)

Measured on a second disposable replica (`poppys-replica-big2`, port 1524) with `tests/oracle_replica.py --orders
300000` (ORDMAS 300,004 · ORDSTYLE 600,005 · ORDSIZE 2,400,010 rows, statistics gathered), the same machine,
best of 3, statement only unless marked. "Before" forces the old forgiving compile on every table.

| Ask AI question | Before: `UPPER(TRIM(col)) = :b0` | After: plain column | Plan after |
|---|--:|--:|---|
| Size wise quantity for order HX/0123456 | 0.576 s (whole question 0.81 s) | **0.0017 s** (whole question 0.027 s) | `INDEX RANGE SCAN ORDMAS_DOCID_IX`, then `INDEX RANGE SCAN ORDSIZE_ORD_IX` |
| How many orders for PK/26-27/S/9001 | 0.121 s | **0.0012 s** | `INDEX RANGE SCAN ORDMAS_DOCID_IX` |
| Orders for PO-H123456 (buyer PO, no index) | 0.109 s | 0.033 s | full scan, cheaper predicate |
| Value index build (14 columns, 40,027 values) | — | 0.87 s | runs in the background at startup |
| Live lookups for 4 unknown codes in one question | — | 0.22 s | one budget for the question |

Before, the plan was `TABLE ACCESS FULL ORDMAS` + `TABLE ACCESS FULL ORDSIZE` with
`filter(UPPER(TRIM("T2"."DOCID"))=:B0)`; it grows with the table (9.6–11.3 s at 3M orders, above). After:

```
HASH GROUP BY
  HASH JOIN OUTER
    NESTED LOOPS
      NESTED LOOPS
        TABLE ACCESS BY INDEX ROWID BATCHED  ORDMAS
          INDEX RANGE SCAN                   ORDMAS_DOCID_IX   access("T2"."DOCID"=:B0)
        INDEX RANGE SCAN                     ORDSIZE_ORD_IX    access("T2"."ORDMASID"="T0"."ORDMASID")
      TABLE ACCESS BY INDEX ROWID            ORDSIZE
    TABLE ACCESS FULL                        SIZE_MAS          (5 rows)
```

How it compiles (`backend/insight/query.py` `_condition`):
- **Big tables** (more than 100,000 rows by statistics, `FORGIVING_ROWS`), and tables without statistics, get the
  plain column: `col = :b`, or `col IN (:as_given, :spellings…, :capitals)` when they differ. The finder already
  returns the stored value, and the value index adds every stored spelling of the same letters (`Next`, `NEXT`),
  so the answer is the same as the forgiving match whenever the value index knows the value. `CHAR` columns are
  blank-padded (`RPAD(:b, n)`) as Oracle stores them; a value longer than the column matches nothing (`1 = 0`).
- **Small tables** (masters such as buyers, colours, sizes) and the uploaded-files table keep the forgiving
  `UPPER(TRIM(col)) = :b`: a full scan of them costs nothing.
- `contains`, `starts with`, `is not` and comparisons are unchanged (no index can answer them anyway).
- All demo questions give exactly the same rows with both forms (`tests/test_insight.py`); the compiled SQL on big
  tables is checked in `tests/test_large_db.py`.

Other follow-ups from the review:
- **Value index at startup.** It is built in a background thread when the server starts and the index is empty
  (`VALUE_INDEX_AT_STARTUP=0` in `.env.poppys` turns that off). `/api/knowledge` reports it as `value_index`:
  `state` (off, starting, building, ready, empty, offline, failed), `source`, start and finish time, columns, values and the
  error, if any. A question asked while it builds waits at most 5 s and then goes on without it (codes are still
  found live). A question builds it itself only as a fallback (startup build off, Oracle down at startup), and after
  a build that failed or found nothing, at most once every 5 minutes.
- **One lookup budget per question.** `LOOKUP_BUDGET_MS` (6 s) is shared by all code-like words of a question, not
  given to each word. Full scans never use the last second of it, so a later word still gets its index probe.
- **Code columns are never closed lists.** A column counts as a closed category list only when its values at the
  last refresh were words (at most 10 % contain a digit, saved as `words` in `data/value_index_meta.json`). A short
  buyer-PO column is still looked up live. A meta file from before this change closes nothing until the next refresh.
- **`QUERY_TIMEOUT_S` bounds.** Accepted from 1 to 600 seconds. Anything else (`inf`, `nan`, `0`, a typo such as
  `20000`) is refused with a startup log line (`Ignoring QUERY_TIMEOUT_S='inf': use a number of seconds from 1 to
  600. Using the default, 20 s.`); before, `inf` stopped the app from starting.
- **Explorer "Load more".** The filtered `COUNT(*)` runs once, on the first page, and is reused (also when it ran
  out of time) by the following pages for 5 minutes; filtering again counts again, and every save clears it.
- **"Count unavailable".** A count that could not be made is `total: null` with `rows_known: false` in
  `/api/explorer/tables/…` and in each linked table of `/api/explorer/related/…`, so the page can say so.
- **Replica builder.** Order references keep all digits past 10,000,000 orders (`HX/10000000`; `LPAD(n, 7)`
  cut it to `HX/1000000`, a duplicate). Every replica also holds PK/26-27/S/9101 (3 lines × 5 sizes), the
  existing order the real-Oracle acceptance test F opens; CI builds its replica with `--orders 20000`.

## What changed

**Live lookups** (`backend/insight/service.py`: `lookup_plan`, `live_finder`). An order reference typed in
a question that the value index has not seen yet used to cost one `UPPER(TRIM(col)) = :v` full scan per
searchable column. No index can answer that. The cost was about 0.3 s per word at 300k orders and 3 s at 3M.
Lookups now run in three tiers, and a tier runs only when the ones before it found nothing:
1. small tables (≤ 100k rows by statistics) keep the forgiving `UPPER(TRIM())` match;
2. indexed columns of big tables are probed exactly: `col IN (:as_typed, :capitals)`, an index probe;
3. other big-table columns are probed exactly too, which is a full scan, but each probe is limited to 2.5 s.

On big tables, short category lists that the value index holds completely (1,000 values or fewer, such as
sample type) are skipped. Order references and other codes are always probed, because orders saved in Axpert
after the last refresh are not in the index. Number columns are probed only for all-digit words, and a word
longer than the column is never looked up. All lookups of one question share a 6-second budget (one per word before the follow-up above). The dictionary harvest now records the leading index columns
(`ALL_IND_COLUMNS`), so the app knows which columns are cheap.

**Value index** (`refresh_values`). The order header is always indexed, whatever its size. Before, ORDMAS
dropped out completely above 2M rows. A column with more than 20,000 distinct values is marked *partial* and
keeps the values of its newest rows, read cheaply through the record-ID index. That covers recent orders. The
list of complete and partial columns is saved in `data/value_index_meta.json`, next to the value index.

**Time limits** (`OracleExecutor`). Every read has a call timeout: 20 s by default, `QUERY_TIMEOUT_S` in
`.env.poppys` to change it. Counts get 4 s and live lookups 2.5 s. A timeout raises a plain-English
`TimedOut` message instead of a raw `DPY-4024` error. Ask AI shows it as *Cannot answer safely*, and the
explorer shows it as a message. The thin driver keeps the connection usable after a timeout (checked on Oracle
23 with python-oracledb 4.0.1, and again with 4.0.2, the version now pinned).

**Explorer** (`backend/main.py`: `explorer_rows`, `_count`, `table_counts`).
- Rows are read before counting. A result that fits on the first page needs no COUNT at all.
- `is exactly` on a text column tries `col IN (:as_typed, :capitals)` first, so the index answers it: 3.6 s →
  0.08 s at 3M. The forgiving any-case match is used only when that finds nothing, and never on tables over
  1M rows. A cheap existence probe picks the predicate, so every page of a result uses the same one.
- `contains` escapes `%` and `_`, which used to match everything. On a table over 1M rows it skips the
  extra COUNT and shows no total. If it runs out of time, the message suggests "is exactly".
- Counts are limited to 4 s each and 8 s per page. A whole-table count that runs out shows statistics (≈). A
  filtered count that runs out shows no number, never the whole-table figure (the old code showed that).

**Discovery** (`scripts/discover_schema.py`). The old script ran `COUNT(DISTINCT)` plus a `GROUP BY` on every
column of every order table, so ORDMAS's 197 columns meant 394 full scans. Now:
- big tables take their row count from statistics;
- a table over 200k rows is profiled on a block sample (`SAMPLE BLOCK (p) SEED (42)`, about 200k rows);
- fill rate and distinct counts for all columns come from one pass, and the most common values from one
  `UNPIVOT` pass;
- distinct counts of sampled tables come from the optimizer statistics;
- every statement has a time limit (`--timeout`, default 120 s). A statement that runs out is skipped and
  listed, so the run always finishes.

The evidence on the Database mapping screen matched the old full scans (fill % identical for all 478
columns, distinct counts within 1.1 %, same links and references). Figures that came from a sample carry `sampled_pct`.
`--out DIR` writes somewhere other than `data/`.

**Replica builder** (`tests/oracle_replica.py`). It now generates orders in chunks of 250k, so any size loads
(`CONNECT BY` keeps every level in memory), and it prints progress.

## Remaining risks

1. **Ask AI text filters: fixed** (see [above](#ask-ai-text-filters-follow-up)). What remains: on a big table a
   text filter matches the value as given, its value-index spellings and capitals. A value saved after the last
   refresh in mixed case, or with trailing spaces, is not found there (it still is on small tables). The save
   path's duplicate check (`backend/erp/catalog.py` `order_ref_exists`, not part of this work) still uses
   `upper(trim(DOCID)) = :ref`, a full scan of ORDMAS for every order previewed or saved.
2. **`contains` on very large tables** reads every row: 8–10 s on 24M rows. No index can answer
   `LIKE '%x%'`. It is stopped at the time limit with advice to use "is exactly". Oracle Text would be needed
   to make it fast.
3. **Whole-table aggregates grow linearly.** "How many orders?" takes 1.5 s at 3M (`COUNT(DISTINCT ORDMASID)`).
   Expect about 6 s at 4× size. That is acceptable.
4. **First question after install: fixed.** The value index is built in the background at startup (2.5 s at 3M
   orders, 0.9 s at 300k); its progress is in `/api/knowledge` → `value_index`.
5. **Statistics.** Big-table counts and the small/big decision for lookups use `ALL_TABLES.NUM_ROWS`. Keep
   Oracle's nightly statistics job on. Stale statistics only make numbers approximate, because every lookup
   is time-limited anyway.
6. **Concurrency.** All of this is single-user. Each request opens its own connection, and no connection pool
   was measured.

## Settings

| Setting | Where | Default |
|---|---|---|
| Time limit per query | `QUERY_TIMEOUT_S` in `.env.poppys` (or `POPPYS_QUERY_TIMEOUT_S`), 1–600 s | 20 s |
| Live lookup per probe / per question | `LOOKUP_MS`, `LOOKUP_BUDGET_MS` in `backend/insight/service.py` | 2.5 s / 6 s |
| "Small table": forgiving `UPPER(TRIM())` in Ask AI filters and live lookups | `FORGIVING_ROWS` in `backend/insight/query.py` (`SCAN_ROWS` follows it) | 100,000 rows |
| Value index built at startup | `VALUE_INDEX_AT_STARTUP` in `.env.poppys` | on (`0` turns it off) |
| Explorer: filtered count reused by "Load more" | `FILTER_COUNT_TTL` in `backend/main.py` | 5 min |
| Values kept per column | `VALUE_CAP` | 20,000 (newest when more) |
| Detail tables left to live lookups | `BIG_TABLE` | 2,000,000 rows |
| Explorer: statistics instead of COUNT | `BIG_ROWS` in `backend/main.py` | 1,000,000 rows |
| Explorer: count limit / per page | `_count(timeout_ms)`, `table_counts(budget_ms)` | 4 s / 8 s |
| Discovery sample / statement limit | `discover_schema.py --sample-rows --timeout` | 200,000 rows / 120 s |

## Repeating the rehearsal

```bash
docker run -d --name poppys-replica-big -p 1522:1521 -e ORACLE_PASSWORD=<random> \
  -e APP_USER=GARMENT_DEMO -e APP_USER_PASSWORD=<random> gvenzl/oracle-free:23-slim-faststart
# wait for "DATABASE IS READY TO USE" in docker logs
POPPYS_REPLICA_DSN=127.0.0.1:1522/FREEPDB1 POPPYS_REPLICA_PASSWORD=<garment pw> \
  .venv/bin/python tests/oracle_replica.py --orders 3000000
# create POPPYS_APP there with the statements in scripts/bootstrap_local.py (SELECT grants are enough to measure)
POPPYS_ORACLE_DSN=127.0.0.1:1522/FREEPDB1 POPPYS_ORACLE_USER=POPPYS_APP POPPYS_ORACLE_PASSWORD=<pw> \
  .venv/bin/python -m uvicorn backend.main:app --port 8023 --no-proxy-headers
POPPYS_ORACLE_DSN=127.0.0.1:1522/FREEPDB1 POPPYS_DISCOVERY_PASSWORD=<garment pw> \
  .venv/bin/python scripts/discover_schema.py --out /tmp/discovery
POPPYS_TEST_ORACLE=1 POPPYS_ORACLE_DSN=... .venv/bin/python -m pytest -q tests/test_oracle.py tests/test_oracle_insight.py tests/test_large_db.py
# query plan of one Ask AI question: EXPLAIN PLAN FOR <the answer's SQL>, then
# SELECT * FROM TABLE(DBMS_XPLAN.DISPLAY(NULL, NULL, 'BASIC +PREDICATE'))
docker rm -f poppys-replica-big
```

The app shares `data/` with the normal setup, so a refresh against the big replica replaces the local value
index. Run Setup → Refresh against the normal database afterwards. The fast tests in `tests/test_large_db.py`
cover the behaviour above without Oracle.
