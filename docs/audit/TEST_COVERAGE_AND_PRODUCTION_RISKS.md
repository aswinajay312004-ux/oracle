# POPPYS AI audit: Ask AI correctness, test coverage and production risks (areas 7, 8, 9)

- Repository: `/home/user/oracle`, branch `claude/fervent-babbage-jqo8r4`, commit `e811402` (2026-10-18).
- Scope: documentation only. The only change to the repository is this file. The `coverage` package was installed into the
  cloud Python only (`pip install coverage`). It was not added to any requirements file.
- Not read: `.env.poppys` and the `data/` folder. The reproduction scripts below build their own in-memory databases.
  Nothing in this report contains a password, a token or a real business record.
- Status labels:
  - **IMPLEMENTED**: code exists and a test or runtime check is cited.
  - **PARTIALLY IMPLEMENTED**: some of it is built.
  - **DOCUMENTED ONLY**: described in docs, with no matching code.
  - **NOT IMPLEMENTED**: no code at all.
  - **NOT VERIFIED**: code may exist, but nobody has run it in the environment that matters.
- No statement here claims 100 % accuracy or production readiness.

## Summary

1. **Why "how many table present in database" became "Number of orders".** Reproduced. The rules parser treats
   "how many" alone as enough to build a count. When no table, column or value is linked, it does not refuse. It
   silently counts the default base table, `ORDMAS` (`backend/insight/understand.py:363-372`, `:469-472`).
   - "table" and "database" are neither stop words nor catalog words. They are only listed as "Not used" and lower
     the score.
   - The engine runs every parse, whatever its score. Confidence is advisory (`backend/insight/engine.py:192-235`,
     `:290-293`).
   - There is no notion of a question about the database itself (tables, columns, schema).
   - Result: a wrong, plausible figure ("3 orders"), marked low (0.45). Close wordings get **medium** ("how many
     tables are there", 0.65) or **high** ("how many columns in ORDMAS", 0.9; "how many users" → uploaded files, 1.0).
   - The local-AI paths and saved examples can raise these wrong readings to high, or answer questions the rules
     refuse.
   - Status: **NOT IMPLEMENTED** (no abstention for count questions that name nothing, no metadata intent). Nothing in
     the code tests this path: coverage shows `understand.py:366-371` never runs.
2. **Tests.** `python -m pytest -q -rs`: **465 passed, 18 skipped, 0 failed** in 89.6 s (483 collected).
   - 14 of the skips need the local Oracle (`POPPYS_TEST_ORACLE=1`). 2 need PowerShell. 2 need client workbooks
     that are not in the repository.
   - Line coverage of `backend/` and `scripts/` is **82 %** (9,383 statements, 1,691 missed).
   - Least covered: the Oracle writer (`oracle_store.py`, 42 %) and upload isolation (47 %, partly a measurement
     limit). Six setup and release scripts are at 0 %.
3. **GitHub CI is red on this branch.** 10 runs failed and 4 were cancelled; no run on this branch has passed.
   - In the latest run, these jobs fail: gitleaks, the Oracle integration job (exit code 139 in "Real-Oracle tests")
     and the non-blocking Windows job.
   - Unit tests, lint, JavaScript and audit pass.
4. **Local Oracle evidence is thin.** Only `setup.ps1 -Dev`, `check_oracle.py` and `discover_schema.py` passed on the
   laptop, on 2026-10-18 (`docs/IMPLEMENTATION_STATUS.md:133-134`). `verify_local.ps1`, the opt-in Oracle tests,
   the benchmark and Demo A/B have not been run (`:135-137`).
5. **Top production risks:**
   - Ask AI answers questions it does not understand.
   - Real-Oracle and Windows paths are unverified.
   - CI is failing.
   - The build is single-tenant and hard-locked to `GARMENT_DEMO` (`backend/config.py:15-17`).
   - Secrets are in a plain settings file.
   - It runs single-user, with no connection pool.

   Full ranked list: [C.9](#c9-ranked-production-risk-list).

---

## Part A — Ask AI correctness (audit area 7)

### A.1 How the reproduction was built

The engine was built the same way `tests/test_insight.py:17-26`, `tests/test_agreement.py:13-36` and
`tests/test_mcp.py` build it:

```text
conn  = demo_db.create()                                   # in-memory SQLite copy of the GARMENT_DEMO structure, 4 synthetic orders
kb    = service.build_kb(service.snapshot(), registry.effective())
ex    = service.SqliteExecutor(conn); d = Dialect('sqlite')
index = {norm(value): hits} for every (table, column) in service.indexable(kb)      # the value finder
engine.ask(question, kb, {'db': (ex, d), 'local': (ex, d)}, finder=..., today=2026-10-17, lex=lexicon(kb))
```

The scripts are outside the repository, in
`/tmp/claude-0/-home-user-oracle/2d743dcf-7bc5-5c44-a352-9294688ce34a/scratchpad/repro.py`, `repro2.py`, `repro3.py` and
`repro4.py`. `repro4.py` swaps in an empty in-memory `UPLOADS` table as the local source, so `uploads_db()`, which
would read `data/`, is never called. No local model was available (`ai_chat=None`), as in the app when Ollama is off
(`backend/main.py:1170`), except where a stand-in model is named.

### A.2 Results (rules only, no local AI)

| Question | Outcome | "Understood as" / message | Confidence | Reasons / checks |
|---|---|---|---|---|
| how many table present in database | **answered** | Number of orders → "3 orders." | **low 0.45** | "Counted orders (nothing specific was named)."; "Not used: “table”, “present”, “database”." Checks: understand **warning**, build passed, safety passed, execute passed |
| how many tables are there | **answered** | Number of orders → "3 orders." | **medium 0.65** | same default; "Not used: “tables”". understand **passed** |
| how many tables in the database | **answered** | Number of orders | medium 0.55 | same; "Not used: “tables”, “database”" |
| how many databases | **answered** | Number of orders | medium 0.65 | same; "Not used: “databases”" |
| number of records | **answered** | Number of orders | medium 0.65 | same; "Not used: “records”" |
| how many columns in ORDMAS | **answered** | Number of orders → "3 orders." | **high 0.9** | only "Not used: “columns”" |
| how many rows in ORDMAS | answered | Number of orders → "3 orders." (the table holds 4 rows, one cancelled) | high 0.9 | "Not used: “rows”" |
| how many (alone) | **answered** | Number of orders | **high 0.75** | "Counted orders (nothing specific was named)." |
| count (alone) | **answered** | Number of orders | **high 0.75** | same |
| how many users | **answered** | Number of uploaded files → "0 uploaded files." | **high 1.0** | none ("users" → singular "user" → `UPLOADS.UPLOADED_BY`) |
| how many users are there in the system | answered | Number of uploaded files | high 0.9 | "Not used: “system”" |
| how many people use this app | answered | Number of orders | low 0.45 | default base; 3 unused words |
| how many buyers | answered (correct) | Number of buyers → "3 buyers." | high 1.0 | — |
| count of colours | answered (correct) | Number of colours → "3 colours." | high 1.0 | — |
| list all tables | **NotUnderstood** | "I could not find order data words in the question." (suggestions: none) | — | API returns `kind='clarify'` with 3 fixed examples (`backend/main.py:1154-1156`) |
| what tables exist / show schema | NotUnderstood | same message | — | — |
| what is the weather | NotUnderstood | same message | — | — |
| hello | NotUnderstood | same message | — | also asserted by `tests/test_insight.py:110-112` |

The compiled SQL is the same for every "Number of orders" row:
`SELECT COUNT(DISTINCT t0.ORDMASID) AS c0 FROM ORDMAS t0 WHERE (t0.CANCEL IS NULL OR t0.CANCEL <> 'T') LIMIT 201`.

Note on the harness: `how many users` raised `sqlite3.OperationalError: no such table: UPLOADS` in `repro.py`. The
test-style source map uses the demo database for both `db` and `local`. With an empty `UPLOADS` table
(`repro4.py`) it answers as shown. In the app the local source is `uploads_db()` (`backend/insight/service.py:267-278`).

### A.3 What makes it worse: saved examples and the local AI (stand-in model, `repro3.py`)

| Situation | Result |
|---|---|
| A user with the `preview` permission clicks "Correct" on the misreading (`backend/main.py:1268-1277`) | The same question then returns `source='saved example'`, **high 0.95**, "Matches a confirmed example" (`engine.py:274-280`) |
| Local AI available, and it returns the same intent (count of ORDMAS) | **high 0.8**, "The question rules and the local AI read it the same way." (`engine.py:283-293`) |
| Local AI available, but it returns non-JSON text (it tries to decline) | The exception is swallowed (`engine.py:288-289`), and the rules answer stays at low 0.45 |
| "list all tables" / "what is the weather" with a local AI that returns any valid intent (here `{"base":"ORDMAS","columns":["ORDMAS.DOCID"]}`) | **Answered**: "List of orders", `source='local AI (checked against the catalog)'`, **medium 0.55** (`engine.py:263-271`) |

The real Ollama model has not been run (`docs/IMPLEMENTATION_STATUS.md:49`, `:58`). So whether `qwen2.5:7b`
actually emits such intents is **NOT VERIFIED**. Still, the prompt gives the model no way to decline, which makes
it likely. The model must return an intent object (`understand.py:545-549`, `ai.json_chat` with `format='json'`,
`backend/ai.py:145-146`).

### A.4 The mechanism, step by step

**1. Routing: rules first, the local AI only in two cases.** `engine.ask` (`backend/insight/engine.py:256-312`):
- The rules (`understand.interpret`) always run first (`engine.py:262`).
- The AI is used only when `ai_chat` is set. The API sets it only when Ollama answers `/api/tags` (`main.py:1170`;
  `ai.available()` caches the answer for 30 s, `backend/ai.py:137-142`). It is then used:
  - when the rules raise `NotUnderstood` → `ai_intent(... tables=[])`, and the answer runs at score 0.55
    (`engine.py:263-271`);
  - when the rules' score is below 0.7 → `ai_intent` with hints (the base table, the filter tables and their
    neighbours), and both readings are compared (`engine.py:283-312`).
- A rules reading of 0.7 or more never meets the AI. "how many columns in ORDMAS" (0.9) and "how many users" (1.0)
  are therefore never second-checked.

**2. Tokenising and stop words.** `Parse` splits the question into tokens (`understand.py:63-71`). `STOP`
(`understand.py:18-23`) holds English function words, plus "how many much", "count number total", "there", "in",
"all", and Tamil/Thanglish words. "table", "tables", "database", "column", "row", "record", "schema", "present" and
"data" are **not** stop words (checked with `repro2.py`).

**3. Aggregation.** `COUNT_RE = \b(how many|count|number of|no.? of|ethana|ethanai|எத்தனை)\b`
(`understand.py:25`). It is checked before every other aggregate (`understand.py:257`). So "how many" alone makes
`agg='count'`, whatever follows.

**4. Schema linking (the lexicon).**
- `lexicon(kb)` (`understand.py:39-60`) holds only table names (business, plural and curated synonyms, weight 3;
  physical names such as `ORDMAS`, weight 5, `:50`) and column synonyms or verified business names.
- `link_words` (`understand.py:210-225`) matches 4- to 1-word phrases, longest first. A single word ending in "s"
  also tries its singular (`:217`). That is why "users" matches the column synonym "user" of the local `UPLOADS`
  table (`backend/insight/service.py:79`).
- Words that match nothing, are not stop words and are longer than 2 letters go to `p.unknown` (`:223-225`).
  "table", "present" and "database" end up there: none of them is a catalog word.
- Nothing in the lexicon describes the catalog itself.

**5. Base-table choice for a count** (`understand.py:363-372`):
- explicit table mention → that table (`:364`);
- otherwise the first column mention → its table (`:365-366`);
- otherwise `_default_base(kb, filter_tables)` (`:369-371`). With no filters it returns `'ORDMAS'` when that table
  exists (`:469-472`). It subtracts 0.25 and adds "Counted orders (nothing specific was named)."

The count branch has **no `NotUnderstood`**. The only rules refusal is in the non-count, non-measure branch, when
there is no table, column, value or group (`understand.py:373-386`). "list all tables" reaches that branch, because
"list" sets no aggregate. "how many tables" does not.

**6. Confidence arithmetic** (`understand.py:260-447`, `engine.py:192-229`). Worked example for "how many table
present in database":

| Step | Score |
|---|---|
| Start (`understand.py:260`) | 1.00 |
| Count with nothing named (`:369-371`) | −0.25 → 0.75 |
| 3 unknown words, `min(0.3, 0.1 × 3)` (`:440-442`) | −0.30 → **0.45** |
| Floor `max(0.05, …)` (`:447`) | 0.45 |
| `run()` level: ≥0.75 high, ≥0.5 medium, else low (`engine.py:229`) | **low** |
| "Question understood" check: `passed` if ≥0.5, else `warning` (`engine.py:200`) | **warning** |

The penalties are additive and capped, so the score cannot reach "refuse". The unknown-words penalty stops at 0.3
however many words were ignored. One known table plus one ignored word still scores 0.9 (high). A question naming
nothing scores 0.75 (high).

**7. Guardrails check safety and arithmetic, not meaning.** Every check passes for these misreadings:
- `policy.review` (`backend/insight/policy.py:104-121`, via `check` at `:71-101`): one SELECT, catalog tables only,
  allowed functions, a row limit, known joins. On Oracle it also refuses every table outside `OWNER`, so the
  dictionary views `ALL_TABLES`, `ALL_TAB_COLUMNS` and so on can never be read (`:95-97`). On SQLite it refuses
  `sqlite_*` (`:98-99`).
- `verify.joins` (`backend/insight/verify.py:55-89`): checks only many-to-one links. A one-table count has none, so
  no check entry is added.
- `verify.cross_check` (`verify.py:92-144`): runs only for breakdowns (`group_by`). A plain count returns `None`
  (`:95-96`).
- `compile_intent` (`backend/insight/query.py:115-332`): validates identifiers against the catalog. `ORDMAS` is
  valid.

So the trace shows 4 checks, 3 of them "passed". The only signal is the `warning` on "Question understood", plus
the reasons.

**8. When the rules refuse** (`NotUnderstood`, `understand.py:34-36`):
- `interpret` raises it for:
  - an empty question (`:244-245`);
  - no table, column, value, group or aggregate in the non-count branch (`:384-386`);
  - a quantity that cannot be linked (`:355-356`).
- The API turns it into `kind='clarify'` with suggestions (`backend/main.py:1154-1156`). `QueryError` (unsafe,
  not linked, timeout) becomes `kind='refused'` (`main.py:1157-1158`).
- There is **no confidence threshold below which an answer is withheld**. Every parsed intent is executed
  (`engine.py:290-293`).

**9. "Did you mean": agreement between the two readings** (`engine.py:283-312`, tests in
`tests/test_agreement.py:39-63`):
- Both compile to the same SQL and binds (`_same_plan`, `:238-245`) → the rules answer, with the score raised to at
  least 0.8.
- Different plans but the same visible rows (`_same_result`, `:248-253`) → the rules answer, at least 0.8 and
  `high`.
- Different results → the **AI answer is shown first**, capped at 0.6, with the rules reading as a "Did you mean"
  button (`:308-312`; UI at `frontend/js/chat.js:102`).
- If the AI's intent cannot be compiled or run → the rules answer, unchanged (`:288-289`, `:297-303`).

Agreement is treated as proof. A model given the same catalog cards, with no way to decline, can "agree" with a
default-base guess, which turns a 0.45 guess into a high-confidence answer (A.3).

**10. Questions about the database itself.** **NOT IMPLEMENTED in Ask AI.**
- An intent has only `base, measures, group_by, columns, filters, order_by, limit, include_cancelled`
  (`understand.py:444-445`).
- The compiler emits only data SELECTs.
- The safety review forbids dictionary views by design (above).
- The catalog does hold the answer: `Knowledge.summary()` (`backend/insight/knowledge.py:212-214`) and
  `GET /api/knowledge` (`main.py:1047-1055`) list tables, column counts and row statistics, and the Database
  explorer shows them. Ask AI just has no route to that information.
- The README describes Ask AI as "plain-language questions about the database" (`README.md:11`, `:25`). Users can
  read that as an invitation to ask about the database's structure.

**11. Presentation** (`frontend/js/chat.js:59-61`, `:95-114`):
- "Understood as: Number of orders" sits next to a red "Low confidence — check" pill (amber "Medium confidence —
  check" at 0.55–0.65).
- The reasons ("Not used: table, present, database") appear only in the pill's tooltip and in the collapsed "How
  this was answered and checked" panel.
- The summary line "3 orders." is shown as the answer.

### A.5 Root cause and status

- **Root cause:**
  - In the count branch, `interpret` falls back to a default base table (`ORDMAS`) instead of asking what to count
    (`understand.py:363-372`, `:469-472`).
  - Unknown words, including words that name the database's own structure, only lower an advisory score
    (`:440-442`). The engine executes every reading regardless of score (`engine.py:290-293`).
  - Ask AI has no intent type for questions about the catalog (A.4 §10).
  - Secondary amplifiers:
    - a singular/plural fallback that matches generic words to obscure column synonyms ("users" → `UPLOADS`);
    - a capped unknown-word penalty that cannot push a reading below "medium";
    - AI agreement and saved examples that raise confidence without checking relevance.
- **Status:** present at commit `e811402`; reproduced 2026-10-18.
  - Not listed in `docs/IMPLEMENTATION_STATUS.md`.
  - Not covered by any test: `understand.py` lines 366, 368 and 370-371 never ran in the 465-test coverage run
    (B.3).
  - Both golden sets contain only answerable questions (`config/golden_questions.json`, 12; `tests/golden/demo.json`,
    16). The golden-file format requires hand-written SQL for every entry (`backend/insight/benchmark.py:44-46`), so
    a "should refuse" question cannot even be expressed.
  - Labels: abstention for count questions that name nothing — **NOT IMPLEMENTED**; metadata questions —
    **NOT IMPLEMENTED**; low-confidence warning — **IMPLEMENTED** (`engine.py:200`, `:229`; `chat.js:59-61`).

### A.6 Directions (suggestions only, no code)

1. A count with nothing named should clarify, not guess. Return "Count of what?" with the catalog's main entities as
   buttons (orders, buyers, styles, uploaded files), or keep the default only when another word in the question
   names an order concept.
2. Add a small set of catalog words (table, tables, column, field, schema, database, row, record) that send the
   question to a catalog answer built from `Knowledge.summary()` and the table list, or to a clarify message. They
   should never be ignored in favour of a data query.
3. Make some unused words disqualifying. When the ignored words include catalog words, or most of the question's
   content words, withhold the figure, or show it only after the user confirms the reading.
4. Show "Not used: …" next to "Understood as", not only in a tooltip.
5. Give the local model an explicit "cannot answer" output. Do not count agreement as confirmation when the rules
   reading came from a default.
6. For the AI fallback after `NotUnderstood`, require the AI's intent to be grounded in words actually present in
   the question.
7. Do not save as an example a reading that had low confidence or used the default base. Consider limiting "remember
   it" to approvers.
8. Extend the benchmark format with negative entries (expected `clarify` or `refused`), and add these questions and
   their variants to the tests.

---

## Part B — Test coverage (audit area 8)

### B.1 Full run

Command: `python -m pytest -q -rs -p no:cacheprovider` (Python 3.13.16, Linux cloud container).

- **465 passed, 18 skipped, 0 failed, 1 warning in 89.57 s** (wall time 1 m 31.7 s). 483 tests were collected
  (`pytest --co -q`).
- This matches the latest entry in `docs/IMPLEMENTATION_STATUS.md:19`.
- The warning is a Starlette/anyio deprecation in the test client.

Skips (the reasons are printed by `-rs`):

| Count | Where | Reason |
|---|---|---|
| 2 | `tests/test_acceptance.py:520`, `:580` | Set POPPYS_TEST_ORACLE=1 to run E/F on the local Oracle replica |
| 1 | `tests/test_api.py:29` | Opt-in local Oracle API preview test |
| 2 | `tests/test_large_db.py:281`, `:294` | Set POPPYS_TEST_ORACLE=1 for local Oracle tests |
| 5 | `tests/test_oracle.py:21`, `:32`, `:49`, `:55`, `:72` | Set POPPYS_TEST_ORACLE=1 for existing local Oracle tests |
| 4 | `tests/test_oracle_insight.py:25`, `:31`, `:46`, `:57` | Set POPPYS_TEST_ORACLE=1 for local Oracle tests |
| 1 | `tests/test_backup.py:479` | PowerShell is not installed |
| 1 | `tests/test_ci.py:168` | PowerShell and git are not installed |
| 2 | `tests/test_import.py:10`, `:24` | Client fixture not installed on this machine |

That is 14 real-Oracle tests, 2 PowerShell tests and 2 client-workbook tests. The Oracle paths are exercised only by
these opt-in tests and by the CI `oracle` job, which is currently failing (B.9).

### B.2 Test inventory (`pytest --co -q`, counts per file)

| File | Tests | What it covers |
|---|--:|---|
| `tests/test_acceptance.py` | 30 | Black-box acceptance A–F of `docs/EXCEL_UNDERSTANDING.md` §8 through the HTTP API, on the in-memory Oracle stand-in and the SQLite demo DB; 2 need Oracle |
| `tests/test_agreement.py` | 5 | Rules vs. stand-in local AI agreement, "Did you mean", M-Schema prompt cards |
| `tests/test_api.py` | 4 | Sign-in required, setup once, cross-origin and unsupported upload refused, merch upload → correction → preview → stale accept, overview figures; 1 needs Oracle |
| `tests/test_auth.py` | 21 | scrypt setting and storage, legacy rehash, password policy, throttling per name and per address, idle timeout, session replacement, logout, role change, cross-site refusal |
| `tests/test_backup.py` | 35 | `scripts/backup.py` backup/verify/restore/retention/drill, `PoppysAI.xml`, PowerShell ASCII/5.1 safety; 1 needs PowerShell |
| `tests/test_benchmark.py` | 6 | Golden demo set and shipped set at 100 % on SQLite, wrong answers reported, golden SQL through the safety review, file validation |
| `tests/test_ci.py` | 21 | `.github/workflows/ci.yml` checked as data (pinned actions, least privilege, no stored passwords), secret-scan behaviour, JS/CSP check; 1 needs PowerShell + git |
| `tests/test_documents.py` | 8 | Synthetic layouts: merch BOM, flat table, unknown layout → heuristic → template, `.xls` parity, non-Excel refused |
| `tests/test_engine.py` | 55 | Fields API and aliases, matcher scores and evidence, validators, quantity-unit evidence, template versioning, anchors, dates |
| `tests/test_evidence.py` | 6 | Evidence ledger: idempotent, conflicts refused, atomic bundles, questions closed only with evidence |
| `tests/test_formats.py` | 167 | Every upload format gives the same order; files written by Excel, LibreOffice and msoffcrypto; hostile and damaged inputs; process isolation (timeout, memory, crash, spawn fallback) |
| `tests/test_graph_view.py` | 5 | `frontend/js/graph.js` zoom/fit/pan maths in Node; CSP-safe module |
| `tests/test_import.py` | 7 | Regression on client workbooks (2 skipped here), leading zeros, no forward-fill, provenance, totals |
| `tests/test_insight.py` | 19 | Catalog merge, join paths, value linking, cancelled rule, fan-out refusal, grouping/dates/comparisons, examples, chat/knowledge/explorer/benchmark APIs |
| `tests/test_large_db.py` | 16 | Lookup tiers, live-finder budget, timeouts, partial value index, index-friendly exact filters, count budgets, sampled discovery; 2 need Oracle |
| `tests/test_masters.py` | 9 | Master fingerprint, similarity, reuse and exact-typing rules |
| `tests/test_mcp.py` | 7 | MCP handshake, read-only tools, governed `ask`, raw SELECT gate, stdio framing and UTF-8 (subprocess) |
| `tests/test_oracle.py` | 5 | All opt-in: preview never inserts, mid-transaction rollback, changed preview refused, commit/read-back/duplicate, read-back graph |
| `tests/test_oracle_insight.py` | 4 | All opt-in: dictionary harvest, compiled queries read-only, READ ONLY transaction blocks writes, value index + question |
| `tests/test_planner.py` | 15 | Registry-driven plan, BOM arithmetic and rules, missing grants, duplicates, plan hash, override limits, semantic model |
| `tests/test_policy.py` | 19 | Hostile SQL refused, compiled Oracle answers pass, review needs a limit and catalog tables, executors refuse before touching the connection |
| `tests/test_readonly.py` | 4 | Read-only account missing or with write privileges refused, checked once, Ask AI reports the setup step |
| `tests/test_reset_login.py` | 2 | `scripts/reset_login.py` with and without the typed confirmation |
| `tests/test_verify.py` | 6 | Check-trace order, repeating parent stops a total, unique keys skip the probe, overlapping groups, totals that do not reconcile, truncated not cross-checked |
| `tests/test_workflow.py` | 7 | End-to-end upload → approve → read-back on the stand-in, stale preview, roles and four-eyes, BOM gating, templates, `.xls`, offline preview |
| **Total** | **483** | |

### B.3 Line coverage

`coverage` was not installed. It was installed into the cloud Python only, then the suite was run under it (data
file kept in the scratchpad):

```text
COVERAGE_FILE=<scratchpad>/.coverage python -m coverage run --source=backend,scripts -m pytest -q -p no:cacheprovider
→ 465 passed, 18 skipped in 106.69 s
python -m coverage report
```

| Module | Stmts | Miss | Cover | | Module | Stmts | Miss | Cover |
|---|--:|--:|--:|---|---|--:|--:|--:|
| `backend/ai.py` | 96 | 27 | 72 % | | `backend/insight/verify.py` | 96 | 13 | 86 % |
| `backend/auth.py` | 80 | 4 | 95 % | | `backend/isolation.py` | 203 | 108 | **47 %** |
| `backend/config.py` | 12 | 1 | 92 % | | `backend/local_store.py` | 299 | 30 | 90 % |
| `backend/db.py` | 2 | 2 | 0 % | | `backend/main.py` | 1150 | 192 | 83 % |
| `backend/erp/catalog.py` | 79 | 35 | 56 % | | `backend/matcher.py` | 496 | 73 | 85 % |
| `backend/erp/mapping.py` | 55 | 17 | 69 % | | `backend/mcp_server.py` | 179 | 21 | 88 % |
| `backend/erp/masters.py` | 42 | 1 | 98 % | | `backend/numfmt.py` | 79 | 4 | 95 % |
| `backend/erp/planner.py` | 476 | 59 | 88 % | | `backend/oracle_store.py` | 160 | 93 | **42 %** |
| `backend/erp/readback.py` | 103 | 1 | 99 % | | `backend/readers.py` | 1736 | 137 | 92 % |
| `backend/erp/semantic.py` | 24 | 0 | 100 % | | `backend/workbooks.py` | 941 | 94 | 90 % |
| `backend/evidence.py` | 95 | 10 | 89 % | | `scripts/backup.py` | 634 | 71 | 89 % |
| `backend/fields.py` | 84 | 4 | 95 % | | `scripts/bootstrap_local.py` | 29 | 29 | 0 % |
| `backend/formulas.py` | 196 | 37 | 81 % | | `scripts/build_release.py` | 82 | 82 | 0 % |
| `backend/insight/benchmark.py` | 116 | 20 | 83 % | | `scripts/check_oracle.py` | 33 | 33 | 0 % |
| `backend/insight/engine.py` | 240 | 35 | 85 % | | `scripts/create_readonly_user.py` | 31 | 31 | 0 % |
| `backend/insight/knowledge.py` | 154 | 20 | 87 % | | `scripts/discover_schema.py` | 179 | 108 | 40 % |
| `backend/insight/policy.py` | 66 | 3 | 95 % | | `scripts/grant_bom_tables.py` | 16 | 16 | 0 % |
| `backend/insight/query.py` | 305 | 45 | 85 % | | `scripts/mcp_server.py` | 6 | 6 | 0 % ¹ |
| `backend/insight/service.py` | 308 | 96 | 69 % | | `scripts/reset_login.py` | 26 | 3 | 88 % |
| `backend/insight/understand.py` | 455 | 111 | 76 % | | `scripts/run_benchmark.py` | 19 | 19 | 0 % |
| **TOTAL** | **9383** | **1691** | **82 %** | | (`__init__.py` files and `insight_api.py`: 100 %) | | | |

¹ Some figures are measurement limits, not gaps:
- `scripts/mcp_server.py` runs as a subprocess in `tests/test_mcp.py:111-135`.
- The worker side of `backend/isolation.py` runs in a spawned process (`tests/test_formats.py:768-822`).
- Subprocess coverage was not enabled.

The 0 % for `build_release.py`, `bootstrap_local.py`, `create_readonly_user.py`, `check_oracle.py`,
`grant_bom_tables.py` and `run_benchmark.py` is real: no test runs them.

### B.4 Fixtures and builders

- `tests/fixtures.py` builds workbooks in code: `merch_bom` (`:11`), `flat_orders` (`:124`), `unknown_matrix`
  (`:132`), `zen_kids_po` (`:286`), `blue_bay_po` (`:308`), `flat_qty_orders` (`:329`) and `wrong_mapping_po`
  (`:339`). It also has format converters: `as_xls` (`:152`), `as_xml2003` (`:204`), `as_html` (`:235`), `as_csv`
  (`:262`) and `as_json_records` (`:274`).
- `tests/fixtures/formats/` holds 22 files written by other programs (LibreOffice `.ods/.xls/.xlsx/.html/.doc`, the
  calamine issue samples, `encrypted_msoffcrypto.xlsx`), plus `README.md`.
- `tests/fixtures/plan_golden.json` is the planner's expected plan (`tests/test_planner.py`).
- `tests/golden/demo.json` holds 16 golden questions for the SQLite demo DB. `config/golden_questions.json` holds the
  12 shipped questions, whose values are picked from the database.
- Client workbooks for `tests/test_import.py` are git-ignored (`.gitignore`: `*.xls`, `*.xlsx`) and absent here, so
  2 tests skip.

### B.5 The stand-ins, and what they do NOT emulate

| Stand-in | What it does | What it does not emulate |
|---|---|---|
| `tests/fake_oracle.py` (`FakeOracle`, `FakeConn`) | Replaces `oracle_store.connect/plan/commit/read_result/live_rows/history` with in-memory dicts. It reuses the real planner on the committed dictionary snapshot (`:24-92`). | Real SQL execution; constraint/type/length enforcement by Oracle; sequences (a Python counter, `:39`, `:67`); the `PP_WRITE_LOCK ... FOR UPDATE WAIT 10` lock (`oracle_store.py:146`); partial failure and rollback inside Oracle; ORA- errors; call timeouts; grants and privileges; CHAR padding and NLS dates; triggers; concurrency |
| `tests/demo_db.py` | In-memory SQLite with the 19-table structure from `docs/live_schema_columns.json`: 4 orders, 3 buyers, synthetic sizes and colours (`:26-79`) | Oracle dialect (LIMIT vs FETCH FIRST); column types (dates as ISO text, `:76`); statistics (so `kb.table(...)['rows']` is `None` and every table counts as "not forgiving", `query.py:32-38`); indexes and unique keys (`kb.unique` is None, so the join check probes); timeouts (`SqliteExecutor` has no `limited`, `service.py:258-264`); data volume and skew; the `UPLOADS` table (tests map `local` to the same connection) |
| `tests/oracle_replica.py` | Builds GARMENT_DEMO on an **empty** throwaway Oracle (refuses if any table exists), with PKs on ID columns, link indexes and optional N synthetic orders (`:1-40`) | The client's real index set, statistics, data distribution and size (17 GB+, `docs/PERFORMANCE.md:3`); Axpert application code or triggers; real NLS settings. It is only used with `POPPYS_TEST_ORACLE=1`, or in the CI `oracle` job (currently failing) |
| `tests/demo_server.py` | Runs the real app with FakeOracle and the SQLite demo DB, for UI screenshots and browser checks (`:1-8`) | Everything above; it is explicitly "never for real work" |
| Stand-in local model (`tests/test_agreement.py:30-35`) | Returns a fixed JSON intent | The real Ollama model's behaviour, latency and refusal style (`IMPLEMENTATION_STATUS.md:58`: "needs a run with the real Ollama model") |

### B.6 Paths no test covers (from `coverage report -m`)

- **Ask AI understanding** (`backend/insight/understand.py`, 76 %):
  - **366-371**: the count default-base path that produced the misreading, and the column-mention base ("how many
    users").
  - 75-90 and 118-133: absolute dates, between/after/before, month names.
  - 154-159: quoted values.
  - 197-207: unseen-code guesses.
  - 339-344: "no quantity named" fallback.
  - 356: unlinked quantity refusal.
  - 407-413: ignored dates and open date ranges.
  - 426-427: comparison on group totals.
  - 471-478: `_default_base` with a master filter.
  - 490-500: `_date_column` walk.
  - 508: `_suggest`.
- **Engine** (`engine.py`): 265-270 (AI fallback after `NotUnderstood`); 297-298 and 302-303 (`QueryError` inside
  the two-reading comparison); 31-37 (LOB/binary cells).
- **Service** (`service.py`, 69 %):
  - 92-129: `harvest` (Oracle dictionary, opt-in only).
  - 143-156 and 165-174: knowledge cache and offline fallback.
  - 191-202: `knowledge()` memo.
  - 269-278: `uploads_db`.
  - 353-375: the real value `finder`. Tests monkeypatch it (`tests/test_insight.py:142-143`).
- **API** (`main.py`): 956-1027 (background value-index build, `_ensure_values`); 260-273 (`/api/health` with
  Oracle); 1103-1107 (evidence on verifying a join); 645-656 (history detail).
- **Oracle writer** (`oracle_store.py`, 42 %): 144-195 (lock, insert, read-back, commit) and 206-219. Only the
  skipped opt-in tests reach them.
- **Oracle catalog** (`erp/catalog.py`, 56 %): `OracleCatalog` (Oracle only).
- **Local AI client** (`ai.py`, 72 %): `_chat`, `assist`, `available` and `json_chat` never talk to a model in
  tests.
- **Upload isolation** (`isolation.py`): the Windows Job Object branch cannot run on Linux.
- **Operational scripts:** `build_release.py`, `bootstrap_local.py`, `create_readonly_user.py`, `check_oracle.py`,
  `grant_bom_tables.py` and `run_benchmark.py` have no automated test.

### B.7 Three-way separation of evidence

**(1) Tested in the cloud (automated, this run).** IMPLEMENTED with cloud tests:
- Excel understanding and all upload formats, including hostile files and process isolation on Linux.
- Planner, validation, masters, evidence ledger.
- The approval workflow and roles against FakeOracle.
- Auth and sessions, CSRF, headers.
- Ask AI on SQLite: catalog, value linking, compile, safety review, join and total checks, the benchmark (shipped
  12/12, demo 16/16, `tests/test_benchmark.py:32-46`).
- Explorer and knowledge APIs, MCP server, backup script (Linux), CI files checked as data.

**(2) Verified on the real Windows laptop and Oracle** (`docs/IMPLEMENTATION_STATUS.md:131-137`):

| Date | Check | Result |
|---|---|---|
| 2026-10-18 | `setup.ps1 -Dev` + `check_oracle.py` | Passed (POPPYS_APP kept, grants, POPPYS_RO with SELECT only, connection) |
| 2026-10-18 | `discover_schema.py` | Passed (19 tables, 727 columns, PK/FK evidence) |
| — | `POPPYS_TEST_ORACLE=1 pytest` / `verify_local.ps1` | **not run** |
| — | `run_benchmark.py` (Ask AI on real data) | **not run** |
| — | Demo A (Excel → Oracle) / Demo B (Ask AI) in the browser | **not run** |

So, on the real database, these are all **NOT VERIFIED**:
- saving orders through the app;
- read-back;
- Ask AI answers and their timing;
- the value index;
- explorer counts;
- the local Ollama model;
- `start.ps1` serving the UI. No run of it is recorded.

`verify_local.ps1` also writes `TEST-` orders into GARMENT_DEMO (`verify_local.ps1:7-8`). That is worth noting
before it is run on a database that holds real records.

**(3) Claims found only in documentation** (no code, or no test):

| Claim | Where | Finding | Label |
|---|---|---|---|
| `/readyz` health endpoint | `deploy/windows/install-service.ps1:163-171`, `rollback.ps1:147-155`, `upgrade.ps1:13`, `docs/RUNBOOK.md:252` | No such route in `backend/main.py`. The scripts fall back to the home page. `IMPLEMENTATION_STATUS.md:106` lists `/healthz`/`/readyz` as not built | NOT IMPLEMENTED |
| `cancel_rule` in `config/semantic_catalog.json:28-31` | config | Not read by any code. `CANCEL <> 'T'` is hardcoded (`query.py:293`, `:296`, `:301`; `understand.py:418`) | DOCUMENTED ONLY (as configuration) |
| RPO 24 h / RTO 1 h | `docs/RUNBOOK.md:21`, `:432` | Targets; never measured on Windows | DOCUMENTED ONLY |
| Golden set of 200–500 staff questions with a ≥95 % CI gate | `docs/RESEARCH_DECISIONS.md:57` | 12 shipped + 16 demo; no CI gate on real data | DOCUMENTED ONLY |
| 14 decided-but-unbuilt items (units, control totals, import ledger, pools, JSON logs, DPAPI, SBOM/signing…) | `docs/IMPLEMENTATION_STATUS.md:86-111` | Self-declared gaps | DOCUMENTED ONLY |
| D1 "Windows service, backup/restore, upgrade/rollback: Implemented, Cloud-tested" | `IMPLEMENTATION_STATUS.md:69` | `RUNBOOK.md:7`, `:439-461`: never run on Windows | NOT VERIFIED |
| D2 "35 wheels verified offline" | `IMPLEMENTATION_STATUS.md:70` | A manual cloud run in an earlier session; no automated test (`build_release.py` 0 %) | NOT VERIFIED (by tests) |
| Large-database timings | `docs/PERFORMANCE.md:29-75` | Measured on a synthetic replica in an earlier session; not reproducible here (no Oracle) | NOT VERIFIED (this audit) |
| `PERFORMANCE.md` "Remaining risk 1" (`UPPER(TRIM())` in the Ask AI compiler) | `docs/PERFORMANCE.md:133-141` | Stale. The code now uses the plain column for big or unanalysed tables (`query.py:28-38`, `:352-362`, `:397-398`), tested by `tests/test_large_db.py:192-208`. The timing is not re-measured | IMPLEMENTED in code; doc out of date |
| B14 "two readings compared" | `IMPLEMENTATION_STATUS.md:58` | Stand-in model only | PARTIALLY IMPLEMENTED (real model NOT VERIFIED) |

### B.8 Manual (browser) checks recorded

These were all run on `tests/demo_server.py`, with stand-ins and no real Oracle (`docs/IMPLEMENTATION_STATUS.md`):
- S2 sign-in in Chromium: weak password refused, cookie flags, logout, 429 after the 6th wrong password (`:20`,
  `:62`);
- A9 undo/redo (`:46`);
- B5 charts rendered (`:50`);
- B9 check trace (`:54`);
- E1 overview on desktop and phone (`:66`);
- E2 phone layout (`:67`);
- B6 evidence dialog (`:68`).

None of these is automated in this branch. The last green CI run (2026-10-17, branch `claude/loving-mccarthy-9p0do9`,
run 39063155070) had a "Browser tests (Chromium…)" job. That job is not in this branch's `.github/workflows/ci.yml`.

### B.9 CI

- **Workflow** (`.github/workflows/ci.yml:35-224`). Jobs:
  - `secrets` (own scan + gitleaks over the full history);
  - `lint` (ruff);
  - `js` (`node --check` + CSP rules);
  - `tests` (Python 3.13 + Node);
  - `oracle` (Oracle Free 23 service, replica, `bootstrap_local.py`, `check.sh oracle`);
  - `audit` (pip-audit);
  - `windows` (non-blocking, `check.ps1 all`).
- Local equivalents: `scripts/check.sh` and `scripts/check.ps1`. `tests/test_ci.py` keeps them consistent.
- **Observed on GitHub** (`gh run list` / `gh run view`, 2026-10-18):
  - Branch `claude/fervent-babbage-jqo8r4`: 10 runs `failure`, 4 `cancelled`, 0 `success`.
  - Latest run 39124073690 (11:38 UTC):

    | Job | Result |
    |---|---|
    | Secret scan | **failed** at "Gitleaks over the whole git history" |
    | Oracle integration | **failed** at "Real-Oracle tests" (annotation: "Process completed with exit code 139") |
    | Unit tests on Windows (non-blocking) | **failed** |
    | Unit tests, Lint, JavaScript, Dependency audit | passed |

  - The earlier run 39108821173 shows the same pattern.
  - Job logs could not be downloaded (HTTP 403), so the causes are **NOT VERIFIED**. In particular, whether the
    gitleaks finding is a real secret in history must be checked by the owner.
- Consequence: on this branch the real-Oracle tests have **no passing run anywhere**, neither in CI nor on the
  laptop.

---

## Part C — Production and multi-company readiness (audit area 9)

### C.1 Offline runtime guarantees

Every network call found (`grep` for `httpx|urllib|requests|socket|http(s)://` in `backend/`, `scripts/` and
`frontend/`):

| Code | Call | Destination | When | Is the destination enforced as local? |
|---|---|---|---|---|
| `backend/ai.py:22-27` | `httpx.get(.../api/tags, timeout=2)` | `OLLAMA_URL`, default `http://127.0.0.1:11434` (`ai.py:14-15`) | `/api/health`; `ai.available()` at most every 30 s (`:137-142`) | **No.** Configurable; any host is accepted |
| `backend/ai.py:70-75` | `httpx.post(.../api/chat, timeout=120)` | same | layout suggestions (`:78-96`), draft assist (`:122-131`), Ask AI (`:145-146`) | **No** (same) |
| `backend/oracle_store.py:27-31`, `:53-68` | python-oracledb thin TCP | `ORACLE_DSN`, default `127.0.0.1:1521/FREEPDB1` | every Oracle read or write | **No.** Configurable (`tcp_connect_timeout=4`) |
| `scripts/backup.py:146-162`, `:618-658` | socket / urllib (proxies disabled) | `127.0.0.1:<port>` | restore refusal and drill | Yes (hardcoded loopback) |
| `scripts/bootstrap_local.py:13-14` (also `create_readonly_user.py`, `grant_bom_tables.py`) | `docker exec -i <container> sqlplus / as sysdba` | local Docker container | setup only | Local by construction |
| `setup.ps1:29-32` | `pip install` from PyPI | internet | setup, unless `-Offline` (`:22-27`, hash-checked wheelhouse) | n/a (install time) |
| `scripts/build_release.py:78-84`, `:101-102` | `uv pip compile`, `pip download`, python.org installer | internet | building a release on a connected computer | n/a (build time) |
| `deploy/windows/install-service.ps1:65-68`, `:432-438` | none: the WinSW URL is printed and the user downloads it manually; SHA-256 pinned | — | install | — |
| Frontend | `fetch` same-origin only (`frontend/js/api.js:16`, `app.js:11`); one stylesheet and one module script from `/static` (`frontend/index.html:9`, `:195`); CSP `connect-src 'self'`, `script-src 'self'` (`backend/main.py:65`); CI rule against off-site `src`/`href` (`scripts/check.sh:78`) | — | runtime | Yes (CSP) |

Verdict: **PARTIALLY IMPLEMENTED.**
- At runtime, the app contacts only the Ollama URL and the Oracle DSN. Both default to loopback, and no CDN is used.
- "Offline" holds by configuration, not by enforcement. A changed `OLLAMA_URL` would send question text, schema
  cards and sample values (`understand.py:513-553`) to that host.
- Normal setup needs internet; the offline path is `setup.ps1 -Offline`.
- The server binds to `127.0.0.1` (`start.ps1:5`; `deploy/windows/PoppysAI.xml:30`).

### C.2 Security (mechanisms; no values)

| Area | Mechanism (evidence) | Tests | Label |
|---|---|---|---|
| Password hashing | scrypt N=2^17, r=8, p=1, 16-byte salt, settings stored in the hash (`backend/auth.py:28`, `:38-42`); constant-time compare (`:55`); legacy PBKDF2 rows re-hashed at sign-in (`:57-62`, `main.py:186-188`); the decision named Argon2id, and scrypt is an explicit deviation (`auth.py:3-8`) | `tests/test_auth.py:37-67` | IMPLEMENTED |
| Password policy | ≥12 chars, common list (`config/common_passwords.txt`), product words, username (`auth.py:85-95`) | `test_auth.py:69-84` | IMPLEMENTED |
| Throttling | Wrong passwords counted per name (existing or not) and per address, stored in SQLite; 5 free, then 2^(n−5) s up to 15 min; 30 per address per 15 min (`auth.py:99-122`); unknown names cost the same (`:65-68`, `main.py:180`) | `test_auth.py:86-119` | IMPLEMENTED |
| Sessions | 32-byte random token, only its SHA-256 stored (`main.py:123-130`); cookie HttpOnly + SameSite=Strict, **no Secure flag** (plain-HTTP loopback, deliberate: `IMPLEMENTATION_STATUS.md:62`); idle 30 min / absolute 8 h (`auth.py:33`, `main.py:96-105`); sign-in ends the presented session (`main.py:127`); role, password or deactivation ends sessions (`main.py:242-249`); logout sends Clear-Site-Data (`:198`) | `test_auth.py:121-153` | IMPLEMENTED |
| CSRF / origin | Non-GET requests refused unless `Sec-Fetch-Site` is same-origin/none and `Origin` matches (`main.py:53-58`); `TrustedHostMiddleware` allows only `127.0.0.1`, `localhost` and `testserver` (`main.py:46`) | `test_auth.py:155`, `test_api.py:24` | IMPLEMENTED |
| Headers / CSP | nosniff, `X-Frame-Options: DENY`, `Referrer-Policy`, `no-store`, CSP with `script-src 'self'` and `frame-ancestors 'none'` (`main.py:59-65`) | `test_graph_view.py:62`, CI js job | IMPLEMENTED |
| Upload limits | 14 MB per request, checked on `Content-Length` (`main.py:51-52`); 12 MB per file (`backend/readers.py:66`); extension allowlist (`main.py:355-356`, `readers.py:100`); content sniff before parsing (`main.py:358`) | `test_formats.py`, `test_workflow.py:140` | IMPLEMENTED |
| Parse isolation | Spawned worker, 60 s wall clock, 1.5 GB cap (`backend/readers.py:91-92`, `backend/isolation.py:1-38`); RLIMIT on POSIX, Job Object on Windows; JSON result over a one-way pipe, never unpickled. **If the worker cannot start, the file is parsed in the server process** and an error is logged (`readers.py:771-775`) | `test_formats.py:768-822` (Linux) | IMPLEMENTED on Linux; Windows Job Object NOT VERIFIED |
| Oracle least privilege | Writer account POPPYS_APP (SELECT/INSERT grants by `bootstrap_local.py`); Ask AI and explorer use POPPYS_RO, refused if it holds any write privilege (`oracle_store.py:39-68`); READ ONLY transaction, call timeout, rollback (`service.py:229-255`); SQLGlot safety review on every executor statement (`service.py:224-226`, `policy.py:71-101`) | `test_readonly.py`, `test_policy.py`; real Oracle: `test_oracle_insight.py` (skipped) | IMPLEMENTED (cloud); NOT VERIFIED on the laptop |
| Secrets | `.env.poppys` is read as plain dotenv at import (`backend/config.py:10-13`); git-ignored (`.gitignore`: `.env.*`, `!.env.example`); secret scan (`scripts/check.sh:26-54`); excluded from backups (`scripts/backup.py:19-20`); file ACL set by the service installer (`RUNBOOK.md:19`, untested on Windows). DPAPI not built (`IMPLEMENTATION_STATUS.md:108`) | `test_ci.py:142-167`, `test_backup.py:112` | PARTIALLY IMPLEMENTED |
| Ask AI feedback | Any `view` user can mark **any** answer id correct or wrong; there is no ownership check (`main.py:1268-1271`). A user with `preview` (operator and above) saves a global example that answers matching questions at 0.95 (`main.py:1274-1275`, `engine.py:274-280`). Examples are visible to all signed-in users (`main.py:1292-1293`); deletion needs `mapping` (`:1296-1299`) | `test_insight.py:151-166` | IMPLEMENTED; design risk (A.3) |
| Approval | `allow_self_approval=True` by default (`main.py:35`); no reject-with-reason (`IMPLEMENTATION_STATUS.md:101`) | `test_workflow.py:83` | PARTIALLY IMPLEMENTED |

### C.3 Performance: documented claims against the code

| Claim (`docs/PERFORMANCE.md`) | Code | Label |
|---|---|---|
| 20 s per query, configurable 1–600 s; plain message on timeout (`:98-102`) | `service.py:19-47`, `:56-67`, `:236-252` | IMPLEMENTED (`tests/test_large_db.py:127-143`) |
| Live lookups: 2.5 s per probe, 6 s per question, three tiers (`:79-91`) | `service.py:48-50`, `:378-458` | IMPLEMENTED (`test_large_db.py:49-125`) |
| Value index: 20,000 values per column, newest kept, ORDMAS always indexed (`:93-96`) | `service.py:42-45`, `:282-335` | IMPLEMENTED (`test_large_db.py:145`) |
| Explorer keyset paging (`:104-112`) | `main.py:1485-1489`. **OFFSET** paging when a table has no key (`main.py:1491`, `query.py:78-81`) | PARTIALLY IMPLEMENTED (`IMPLEMENTATION_STATUS.md:104`) |
| Counts from statistics above 1M rows; 4 s per count, 8 s per page (`:111-112`) | `main.py:1303-1350` | IMPLEMENTED (`test_large_db.py:235-257`) |
| Ask AI row cap | `query.py:22` (max 1000), `:126` (default 200/100), fetch of limit+1 (`engine.py:210-211`) | IMPLEMENTED |
| Exact text filters use the index on big tables (stated as a remaining risk, `:133-141`) | `query.py:28-38`, `:352-362`, `:397-398` | IMPLEMENTED (`test_large_db.py:192-208`); the doc is stale |
| Timings at 300k and 3M orders (`:29-75`) | Synthetic replica in a 4-vCPU container (`:14-27`) | NOT VERIFIED on the client database (about 4× larger) |
| Concurrency: "All of this is single-user… no connection pool was measured" (`:151-152`) | One new Oracle connection per request (`main.py:912-928`); service runs `--workers 1 --limit-concurrency 64` (`PoppysAI.xml:30`) | NOT IMPLEMENTED (pools: `RESEARCH_DECISIONS.md:53`, `:131`) |
| First question builds the value index if it is empty (`:147-149`) | `main.py:990-1027` (startup thread, 5 s wait, retry after 300 s) | IMPLEMENTED; not covered by tests (B.6) |

### C.4 Backup and restore

What `scripts/backup.py` does (`:1-25`):
- an online SQLite backup of `poppys.sqlite3` (`:53`), which holds users, drafts, audit, templates, the evidence
  ledger, examples and the value index (`backend/local_store.py:24-54`);
- `integrity_check` and `foreign_key_check`;
- a zip with the mapping and catalog config plus a manifest of SHA-256 sums;
- fsync and atomic replace;
- retention of 14 daily, 8 weekly and 12 monthly backups;
- verify, restore (refused while the app is running or without `--force`) and a drill that starts the restored app.

Tests: `tests/test_backup.py`, 35 tests (34 passed, 1 skipped for PowerShell). Label: **IMPLEMENTED (Linux).**

What it does not do, or what nobody has verified:
- **Oracle is not backed up** by POPPYS AI (`docs/RUNBOOK.md:20`).
- Uploaded files are included only with `-IncludeUploads` (`deploy/windows/backup-task.ps1:26-27`).
- Scheduling is a Windows Task Scheduler entry (`backup-task.ps1:1-20`). The script says itself "Not yet tested on
  real Windows" (`:18`), and so does the runbook (`RUNBOOK.md:7`, `:439-461`, items 5-6): **NOT VERIFIED**.
- RPO/RTO are targets only (C.3 of B.7).

### C.5 Deployment

| Piece | What it does | Evidence | Label |
|---|---|---|---|
| `setup.ps1` | Needs Python 3.13 + the `py` launcher; creates the venv; installs packages (PyPI, or `-Offline` hash-checked wheelhouse); runs `bootstrap_local.py` (Docker `sqlplus / as sysdba`, expects exactly 19 GARMENT_DEMO tables, `scripts/bootstrap_local.py:26-29`), `grant_bom_tables.py`, `create_readonly_user.py`, `check_oracle.py` | Laptop run passed 2026-10-18 (`IMPLEMENTATION_STATUS.md:133`) | IMPLEMENTED; verified once |
| `start.ps1` | `uvicorn backend.main:app --host 127.0.0.1 --port 8008` (`:5`) | No laptop run recorded | NOT VERIFIED |
| `deploy/windows/install-service.ps1`, `upgrade.ps1`, `rollback.ps1`, `uninstall-service.ps1`, `PoppysAI.xml` | WinSW 2.12.0 service as LOCAL SERVICE, junction-based releases, readiness wait (falls back from the missing `/readyz`) | Parsed and analysed only (`RUNBOOK.md:443-449`); `test_backup.py:447-511` checks the files as data | NOT VERIFIED on Windows |
| `scripts/build_release.py` | Windows wheelhouse via `uv` + `pip download`, `requirements.lock` with hashes, `SHA256SUMS`, zip; optional python.org installer | 0 % test coverage; manual cloud run recorded (`IMPLEMENTATION_STATUS.md:70`); no embeddable Python, no signing (`RUNBOOK.md:461`) | PARTIALLY IMPLEMENTED |
| Health endpoint for supervisors | `/api/health` needs a signed-in user (`main.py:255-256`); no unauthenticated `/healthz` or `/readyz` | — | NOT IMPLEMENTED |

### C.6 Tenant isolation

**NOT IMPLEMENTED.** There is no concept of a company or tenant anywhere in the code (`grep -i tenant|company_id`
finds nothing).
- One local SQLite file, `DATA/poppys.sqlite3` (`backend/local_store.py:26`). No table in it has a company column
  (`:35-54`).
- `DATA` can be redirected with `POPPYS_DATA_DIR` (`backend/config.py:8`). That is the only separation lever.
- One Oracle owner: `OWNER = setting('ORACLE_OWNER', 'GARMENT_DEMO')`, and **any other value raises
  `RuntimeError('This demo build is scoped to GARMENT_DEMO.')` at import** (`backend/config.py:15-17`). The app
  cannot start for another schema without a code change.
- One company default: `company_exporter='POPPYS KNITWEAR PRIVATE LIMITED'` (`backend/main.py:35`).
- The port (8008) is fixed in `start.ps1:5` and `PoppysAI.xml:30`. The host check allows loopback only
  (`main.py:46`), so other PCs on a LAN cannot use one install.
- In practice, a second company means a second machine, or at least a second install with its own data folder and
  port, after code changes.

### C.7 What a second company would have to change

1. **Code:** remove or parameterise the `GARMENT_DEMO` guard (`backend/config.py:15-17`).
2. **Settings** (`.env.poppys` keys, names only): `ORACLE_USER`, `ORACLE_PASSWORD`, `ORACLE_DSN`, `ORACLE_OWNER`,
   `ORACLE_RO_USER`, `ORACLE_RO_PASSWORD`, `OLLAMA_URL`, `OLLAMA_MODEL` (`.env.example`).
3. **Write mapping:** `config/oracle_mapping.json` (`owner`, 6 tables, 11 masters, links). Every column must be
   re-verified with evidence; the planner writes only verified columns.
4. **Semantic catalog:** `config/semantic_catalog.json` (`owner`, 19 curated tables, synonyms, 16 relationships,
   `cancel_rule`, which is currently ignored by the code).
5. **Offline dictionary snapshot:** `docs/live_schema_columns.json`. It is used by `service.snapshot()`
   (`service.py:39`, `:134-139`) and `SnapshotCatalog` (`backend/erp/catalog.py:11`), and the test DB is built from
   it.
6. **Golden questions:** `config/golden_questions.json` (values picked from the DB, so partly portable) and
   `data/golden_questions.json` for the site's own.
7. **Field registry and aliases:** `backend/fields.py:21-53` defaults, plus `field_settings` overrides; buyer
   layout templates.
8. **Setup scripts that hardcode the schema:**
   - `scripts/bootstrap_local.py:28-34` (19-table check, GARMENT_DEMO grants, Docker container, sysdba);
   - `scripts/create_readonly_user.py:45-52`;
   - `scripts/grant_bom_tables.py`;
   - `scripts/discover_schema.py:30` (`OWNER = 'GARMENT_DEMO'`);
   - `scripts/check_oracle.py:39-40`.

   All of them assume a local Docker Oracle where the operator can run `sqlplus / as sysdba`. A company server
   managed by a DBA would need different provisioning.
9. **Settings in the UI:** company exporter, default sample type, self-approval (`main.py:35`).

### C.8 Compatibility with different Oracle schemas

| Hardcoded in code | Where | Effect on another ERP schema |
|---|---|---|
| `GARMENT_DEMO` owner | `config.py:15-17` | App refuses to start |
| `ORDMAS` as the default base, the "header" kind, and the AI's default tables | `understand.py:469-472`, `knowledge.py:59`, `understand.py:543-544` | Counts default to a table that may not exist (falls back to the first table, `:472`) |
| `ORDMAS.BONO` / `STYLENUM.STYLENUMBER` / `ORDMAS.DOCID` for unseen codes | `understand.py:202` | Code-like words are linked to the wrong place or ignored |
| `ORDMAS` / `ORDSTYLE` / `ORDSIZE` plan shape | `backend/erp/planner.py:324-339`, `oracle_store.py:184` | The writer assumes Axpert's header → line → size structure |
| `ORDMAS.DOCID` existing-order check | `backend/erp/catalog.py:86` | Duplicate-reference check is tied to that column |
| `CANCEL` column with value `'T'` | `query.py:292-301`, `understand.py:418` | A different cancellation convention would be silently ignored |
| Sample questions with Axpert values (`NEXT`, `PK/26-27/S/9001`) | `main.py:1156` | Cosmetic |

What is driven by the registry or catalog and so is portable in principle:
- the physical catalog is harvested per `OWNER` (`service.py:90-131`);
- joins come from declared FKs, the registry, curated relationships and naming conventions (`knowledge.py:97-141`);
- every identifier is validated against the catalog before SQL is built (`query.py:115-140`; `policy.review`).

Label: **PARTIALLY IMPLEMENTED.** The read side is mostly catalog-driven. The write side and the defaults are
Axpert/GARMENT_DEMO-specific.

### C.9 Ranked production-risk list

| # | Risk | Severity | Evidence | Mitigation present? |
|---|---|---|---|---|
| 1 | Ask AI returns a confident-looking figure for questions it did not understand (metadata questions, counts that name nothing, generic words matching obscure synonyms); the AI paths and saved examples can raise that to high, or answer questions the rules refuse | **High** | A.2, A.3; `understand.py:363-372`, `:469-472`; `engine.py:263-293` | Partial: low/medium pill, "Not used" reasons (hidden), trace shows SQL; no abstention, no tests, no negative golden questions |
| 2 | Real-Oracle behaviour is unverified: writer commit, read-back, Ask AI on real data, timings, read-only account in use | **High** | `IMPLEMENTATION_STATUS.md:135-137`; 14 opt-in tests skipped; `oracle_store.py` 42 % covered; CI `oracle` job failing (exit 139) | Partial: replica tests exist, `verify_local.ps1` prepared but not run |
| 3 | CI is red on this branch (gitleaks, Oracle integration, Windows); no green run on this branch | **High** | B.9 (`gh run view 39124073690`) | No: logs unavailable here; the owner must inspect, especially the gitleaks finding |
| 4 | Single-tenant hard lock and Axpert/GARMENT_DEMO-specific code and scripts | **High** (for a second company) | `config.py:15-17`; C.6-C.8 | No: needs code changes, new config and new provisioning |
| 5 | Windows service, upgrade/rollback and nightly backup never run on Windows; no `/readyz` | **Medium** | `RUNBOOK.md:7`, `:439-461`; `main.py` has no such route | Partial: scripts analysed, `backup.py` tested on Linux, `start.ps1` fallback |
| 6 | Secrets stored as plain text in `.env.poppys`; ACL hardening untested | **Medium** | `config.py:10-13`; `IMPLEMENTATION_STATUS.md:108`; `RUNBOOK.md:19` | Partial: git-ignored, secret scan, excluded from backups |
| 7 | Scale and concurrency: single worker, a new Oracle connection per request, no pool, OFFSET paging without a key, timings only on a replica | **Medium** | `main.py:912-928`; `PoppysAI.xml:30`; `PERFORMANCE.md:151-152`; `main.py:1491` | Partial: per-query timeouts, budgets, statistics-based counts |
| 8 | "Offline" relies on configuration: `OLLAMA_URL` and `ORACLE_DSN` accept any host | **Medium** | `ai.py:14-15`; `oracle_store.py:30`, `:59-60` | Partial: loopback defaults, CSP, no CDN |
| 9 | Ask AI feedback has no ownership check; operators can save global examples that answer at 0.95 | **Medium** | `main.py:1268-1277`; `engine.py:274-280` | Partial: admins can delete examples; actions audited |
| 10 | Upload parsing falls back to the server process when the worker cannot start; Windows memory cap untested | **Medium** | `readers.py:771-775`; `isolation.py` (Windows branch) | Partial: size caps, sniffing, error logged |
| 11 | Approval defaults (self-approval on, no reject-with-reason) and the other 14 documented production gaps (units, control totals, import ledger, JSON logs, `synchronous=FULL`, hash-chained audit…) | **Medium** | `main.py:35`; `IMPLEMENTATION_STATUS.md:86-111` | Documented; not built |
| 12 | Oracle data itself is outside POPPYS AI backups; RPO/RTO unmeasured | **Medium** | `RUNBOOK.md:20-21` | Documented responsibility only |
| 13 | Documentation drift (`PERFORMANCE.md` remaining risk 1 is stale; D1 "Cloud-tested" vs "never run on Windows"; `cancel_rule` config unused; 15/15 vs 16 demo questions) | **Low** | B.7 (3) | No |
| 14 | No `Secure` cookie flag; loopback-only host check prevents LAN multi-user use | **Low** (by design today) | `main.py:46`, `:130`; `IMPLEMENTATION_STATUS.md:62` | Deliberate deviation, documented |

## Appendix: commands run for this report

```text
python -m pytest -q -rs -p no:cacheprovider                       → 465 passed, 18 skipped, 1 warning in 89.57s
python -m pytest --co -q -p no:cacheprovider                      → 483 tests collected (per-file counts in B.2)
pip install coverage                                              → coverage 7.16.2 (cloud environment only)
COVERAGE_FILE=<scratchpad>/.coverage python -m coverage run --source=backend,scripts -m pytest -q -p no:cacheprovider
                                                                  → 465 passed, 18 skipped in 106.69s
python -m coverage report [-m --include=...]                      → TOTAL 9383 stmts, 1691 missed, 82 %
python <scratchpad>/repro.py, repro2.py, repro3.py, repro4.py     → A.2 / A.3 tables
gh run list --branch claude/fervent-babbage-jqo8r4; gh run view 39124073690 / 39108821173 / 39063155070
gh api .../check-runs/<id>/annotations                            → exit code 139 (oracle), exit code 1 (windows, gitleaks)
grep -rn "httpx|urllib|requests|socket|http(s)://" backend scripts frontend    → C.1
```

`<scratchpad>` is `/tmp/claude-0/-home-user-oracle/2d743dcf-7bc5-5c44-a352-9294688ce34a/scratchpad`.
