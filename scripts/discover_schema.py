"""READ-ONLY discovery of the local GARMENT_DEMO schema. Run on the Windows laptop:

    python scripts/discover_schema.py [--out DIR] [--sample-rows N] [--timeout SECONDS]

Connects as GARMENT_DEMO (password asked with getpass, or POPPYS_DISCOVERY_PASSWORD), runs only
SELECT statements against the data dictionary and the order tables, and writes the evidence to
data/schema_discovery.json + data/schema_discovery.md (git-ignored, may contain sample values).
The Database mapping screen shows this evidence next to every proposed column.

Safe on a big database (17 GB+): row counts of big tables come from Oracle statistics; a table with
more than --sample-rows rows is profiled on a block sample (SAMPLE BLOCK, about that many rows), and all
its columns are profiled together in two passes instead of two full scans per column; distinct counts
of sampled tables come from the optimizer statistics when Oracle has them. Every statement has a time
limit: one that runs out is skipped and reported, so the run always finishes. Figures that came from a
sample say so (sampled_pct). Timings per table are saved in the 'profile' section.
"""
from __future__ import annotations
from datetime import datetime
import argparse
import getpass
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / 'data'
OWNER = 'GARMENT_DEMO'
SAMPLE_ROWS = 200_000      # profile about this many rows per table at most
STATEMENT_S = 120          # time limit per statement
CHUNK = 60                 # columns profiled per statement
SKIP_TYPES = ('BLOB', 'CLOB', 'NCLOB', 'LONG', 'RAW', 'LONG RAW', 'BFILE')
FOCUS = ('ORDMAS', 'ORDSTYLE', 'ORDSIZE', 'ORDFAB', 'ORDYARN', 'ORDACC', 'ORDCOMP', 'ORDSHIP', 'ORDCMTFAB', 'ORDERENTRY', 'ORDDOC')
# (child table, child column, parent table, parent column) — tested within the same ORDMASID.
LINKS = [
    ('ORDSIZE', 'SUB3_SUBNO', 'ORDSTYLE', 'SUBNO'),
    ('ORDFAB', 'FSUBNO', 'ORDSTYLE', 'SUBNO'),
    ('ORDYARN', 'SUB9_FSUBNO', 'ORDFAB', 'FSUBNO'),
    ('ORDYARN', 'SUB9_YDET', 'ORDFAB', 'YDET'),
    ('ORDACC', 'ASUBNO', 'ORDSTYLE', 'SUBNO'),
    ('ORDCOMP', 'SUB5_SUBNO', 'ORDSTYLE', 'SUBNO'),
    ('ORDSHIP', 'SUB6_SUBNO', 'ORDSTYLE', 'SUBNO'),
    ('ORDCMTFAB', 'SUB8_SUBNO', 'ORDSTYLE', 'SUBNO'),
]
# Numeric reference columns: which master does the value resolve in? (candidate, key)
REFERENCES = {
    'ORDMAS.BUYER': ('BUYMAS', 'BUYMASID'), 'ORDMAS.STYLENO': ('STYLENUM', 'STYLENUMID'), 'ORDMAS.STYLEDESC': ('STYLEMAS', 'STYLEMASID'),
    'ORDMAS.UOM': ('UNITMAS', 'UNITMASID'), 'ORDMAS.MERCH': ('MERCHMAS', 'MERCHMASID'), 'ORDMAS.ENAME': ('PARTYMAS', 'PARTYMASID'),
    'ORDSTYLE.COMBO': ('COLORMAS', 'COLORMASID'), 'ORDSIZE.GSIZE': ('SIZE_MAS', 'SIZE_MASID'),
    'ORDFAB.COLOUR': ('COLORMAS', 'COLORMASID'), 'ORDFAB.FUOM': ('UNITMAS', 'UNITMASID'), 'ORDYARN.YCOLOR': ('COLORMAS', 'COLORMASID'),
    'ORDACC.ACCCOLOR': ('COLORMAS', 'COLORMASID'), 'ORDACC.AUOM': ('UNITMAS', 'UNITMASID'), 'ORDACC.ACCSIZES': ('SIZE_MAS', 'SIZE_MASID'),
}


def connect():
    """(connection, reopen): reopen() opens a new connection with the same password, without asking again."""
    import oracledb
    from dotenv import dotenv_values
    cfg = dotenv_values(ROOT / '.env.poppys')
    dsn = os.environ.get('POPPYS_ORACLE_DSN') or cfg.get('ORACLE_DSN') or '127.0.0.1:1521/FREEPDB1'
    password = os.environ.get('POPPYS_DISCOVERY_PASSWORD') or getpass.getpass(f'{OWNER} password for {dsn}: ')
    reopen = lambda: oracledb.connect(user=OWNER, password=password, dsn=dsn, tcp_connect_timeout=6)   # noqa: E731
    return reopen(), reopen


def rows(c, sql, **binds):
    c.execute(sql, binds)
    names = [d[0].lower() for d in c.description]
    return [dict(zip(names, r)) for r in c]


def text(v):
    if v is None:
        return None
    if hasattr(v, 'read'):
        v = v.read()
    if isinstance(v, datetime):
        return v.isoformat()[:19]
    return v if isinstance(v, (int, float)) else str(v)[:120]


def q(name):
    return f'"{name}"'


def source(table, total, sample_rows):
    """FROM clause and sample percentage: the whole table, or a block sample of about sample_rows rows."""
    if not total or total <= sample_rows:
        return f'{OWNER}.{q(table)}', None
    pct = max(0.000001, min(99.0, 100 * sample_rows / total))
    return f'{OWNER}.{q(table)} SAMPLE BLOCK ({pct:.6f}) SEED (42)', round(pct, 4)


def as_text(col):
    """One VARCHAR2 per column so every column can be grouped in one UNPIVOT pass; dates as ISO text."""
    name = q(col['column_name'])
    if col['data_type'] == 'DATE' or col['data_type'].startswith('TIMESTAMP'):
        expr = f"TO_CHAR({name}, 'YYYY-MM-DD\"T\"HH24:MI:SS')"
    else:
        expr = f'TO_CHAR({name})'
    return f'CAST(SUBSTR({expr}, 1, 120) AS VARCHAR2(480))'


def typed(value, col):
    if value is not None and col['data_type'] == 'NUMBER':
        try:
            return int(value)
        except ValueError:
            try:
                return float(value)
            except ValueError:
                return value
    return value


class Run:
    """Statements with a time limit; a slow or failing one is recorded and skipped instead of stopping the run."""

    def __init__(self, conn, timeout_s, reopen=None):
        self.timeout_s = timeout_s; self.statements = 0; self.errors = []; self.reopen = reopen
        self._use(conn)

    def _use(self, conn):
        self.conn = conn; self.c = conn.cursor(); conn.call_timeout = int(self.timeout_s * 1000)

    def __call__(self, what, sql, **binds):
        self.statements += 1
        if not self.conn.is_healthy():
            # The driver dropped the connection after an earlier time limit. It must not be touched again (setting
            # call_timeout on it crashes python-oracledb 4.0.2), so the rest of the run continues on a new one.
            if not self.reopen:
                self.errors.append(f'{what}: skipped, the connection was lost after an earlier time limit')
                return None
            self._use(self.reopen())
        started = time.time()
        try:
            self.c.execute(sql, binds)
            return self.c.fetchall()
        except Exception as exc:
            code = getattr(exc.args[0], 'full_code', '') if exc.args else ''
            limit = code == 'DPY-4024' or (not self.conn.is_healthy() and time.time() - started >= self.timeout_s)
            self.errors.append(f'{what}: ' + (f'took longer than {self.timeout_s:g} s, skipped' if limit else str(exc)[:160]))
            return None


def profile(run, table, cols, total, colstats, sample_rows):
    """Fill rate, distinct values and the five most common values of every column of one table."""
    frm, pct = source(table, total, sample_rows)
    out = {}
    for i in range(0, len(cols), CHUNK):
        part = cols[i:i + CHUNK]
        counts = ', '.join(f'COUNT({q(c["column_name"])}), COUNT(DISTINCT {q(c["column_name"])})' for c in part)   # no reused quotes: parses on 3.11+
        got = run(f'{table} fill/distinct', f'SELECT COUNT(*), {counts} FROM {frm}')
        if not got:
            continue
        n, *vals = got[0]
        for j, col in enumerate(part):
            filled, distinct = vals[2 * j], vals[2 * j + 1]
            name = col['column_name']; st = colstats.get((table, name)) or {}
            if pct and st.get('num_distinct') is not None:
                distinct, from_ = st['num_distinct'], 'statistics'
            else:
                from_ = 'sample' if pct else 'count'
            out[name] = dict(rows=total, filled=round(total * filled / n) if (pct and n) else filled,
                             fill_pct=round(100 * filled / n, 1) if n else None, distinct=distinct, samples=[],
                             **(dict(sampled_pct=pct, distinct_from=from_) if pct else {}))
        top = run(f'{table} common values', f'''SELECT k, v FROM (
              SELECT k, v, ROW_NUMBER() OVER (PARTITION BY k ORDER BY COUNT(*) DESC, v) r
                FROM (SELECT {", ".join(f"{as_text(c)} x{j}" for j, c in enumerate(part))} FROM {frm})
             UNPIVOT (v FOR k IN ({", ".join(f"x{j} AS {j}" for j in range(len(part)))}))
               GROUP BY k, v) WHERE r <= 5 ORDER BY k, r''')
        for k, v in top or []:
            col = part[int(k)]
            if col['column_name'] in out:
                out[col['column_name']]['samples'].append(typed(v, col))
    return out, frm, pct


def main(argv=None):
    ap = argparse.ArgumentParser(description='Read-only GARMENT_DEMO discovery (writes evidence for the Database mapping screen).')
    ap.add_argument('--out', default=str(OUT), help='folder for schema_discovery.json/.md (default: data/)')
    ap.add_argument('--sample-rows', type=int, default=SAMPLE_ROWS, help='profile bigger tables on a sample of about this many rows')
    ap.add_argument('--timeout', type=float, default=STATEMENT_S, help='seconds allowed per statement')
    args = ap.parse_args(argv)
    out_dir = Path(args.out)
    started = time.time()
    conn, reopen = connect(); c = conn.cursor(); run = Run(conn, args.timeout, reopen)
    tables = rows(c, 'select table_name, num_rows, last_analyzed from all_tables where owner=:o order by table_name', o=OWNER)
    columns = rows(c, '''select table_name, column_name, data_type, data_length, data_precision, data_scale, nullable, column_id
        from all_tab_columns where owner=:o order by table_name, column_id''', o=OWNER)
    colstats = {(r['table_name'], r['column_name']): r for r in rows(c, '''select table_name, column_name, num_distinct, num_nulls
        from all_tab_col_statistics where owner=:o''', o=OWNER)}
    constraints = rows(c, '''select c.table_name, c.constraint_name, c.constraint_type, c.status, c.search_condition_vc,
            r.table_name ref_table, (select listagg(cc.column_name, ',') within group (order by cc.position) from all_cons_columns cc
              where cc.owner=c.owner and cc.constraint_name=c.constraint_name) cols,
            (select listagg(rc.column_name, ',') within group (order by rc.position) from all_cons_columns rc
              where rc.owner=c.r_owner and rc.constraint_name=c.r_constraint_name) ref_cols
        from all_constraints c left join all_constraints r on r.owner=c.r_owner and r.constraint_name=c.r_constraint_name
        where c.owner=:o and c.constraint_type in ('P','U','R','C')''', o=OWNER)
    # NOT NULL is already visible as column nullability; keep business CHECK constraints only.
    constraints = [x for x in constraints if not (x['constraint_type'] == 'C' and str(x['search_condition_vc'] or '').upper().endswith('IS NOT NULL'))]
    triggers = rows(c, '''select trigger_name, table_name, triggering_event, trigger_type, status, substr(description,1,400) description
        from all_triggers where owner=:o order by table_name''', o=OWNER)
    for t in triggers:
        body = rows(c, 'select trigger_body from all_triggers where owner=:o and trigger_name=:n', o=OWNER, n=t['trigger_name'])
        t['body'] = text(body[0]['trigger_body'])[:2000] if body and body[0]['trigger_body'] is not None else None
    sequences = rows(c, 'select sequence_name, last_number, increment_by from all_sequences where sequence_owner=:o', o=OWNER)
    objects = rows(c, '''select object_name, object_type, status from all_objects where owner=:o
        and object_type in ('PACKAGE','PROCEDURE','FUNCTION','VIEW','TRIGGER') order by object_type, object_name''', o=OWNER)
    indexes = rows(c, '''select table_name, index_name, listagg(column_name, ',') within group (order by column_position) cols
        from all_ind_columns where table_owner=:o group by table_name, index_name order by table_name, index_name''', o=OWNER)

    evidence = {}; timing = {}; frm = {}
    stats = {t['table_name']: t['num_rows'] for t in tables}
    by_table = {}
    for col in columns:
        by_table.setdefault(col['table_name'], []).append(col)
    for table in FOCUS:
        if table not in by_table:
            continue
        t0 = time.time(); n0 = run.statements; e0 = len(run.errors)
        total = stats.get(table)
        rows_from = 'statistics'
        if total is None or total <= args.sample_rows:   # small or never analysed: count it (time-limited)
            got = run(f'{table} row count', f'select count(*) from {OWNER}.{q(table)}')
            total, rows_from = (got[0][0], 'count') if got else (total, 'statistics')
        cols = [x for x in by_table[table] if x['data_type'] not in SKIP_TYPES]
        found, frm[table], pct = profile(run, table, cols, total, colstats, args.sample_rows)
        evidence.update({f'{table}.{k}': v for k, v in found.items()})
        timing[table] = dict(rows=total, rows_from=rows_from, columns=len(cols), sampled_pct=pct, seconds=round(time.time() - t0, 1),
                             statements=run.statements - n0, errors=run.errors[e0:])
        print(f'  {table}: {len(cols)} columns, {total if total is not None else "?"} rows ({rows_from})'
              f'{f", {pct}% block sample" if pct else ""} — {timing[table]["seconds"]} s', flush=True)
    for key, (master, mkey) in REFERENCES.items():
        table, col = key.split('.')
        if table in by_table and master in by_table and key in evidence:
            got = run(f'{key} → {master}', f'''select count(*), sum(case when exists (select 1 from {OWNER}.{master} m where m.{mkey}=t.{col}) then 1 else 0 end)
                from {frm[table]} t where t.{col} is not null''')
            if got:
                n, ok = got[0]
                evidence[key]['resolves_in'] = dict(table=master, key=mkey, rows=n, matched=ok or 0, sampled_pct=timing[table]['sampled_pct'],
                                                    match_pct=round(100 * (ok or 0) / n, 1) if n else None)
    links = []
    for child, ccol, parent, pcol in LINKS:
        if child in by_table and parent in by_table and all(x in [k['column_name'] for k in by_table[t]] for t, x in ((child, ccol), (parent, pcol))):
            got = run(f'{child}.{ccol} → {parent}.{pcol}', f'''select count(*), sum(case when exists (select 1 from {OWNER}.{parent} p where p.ORDMASID=t.ORDMASID and p.{pcol}=t.{ccol}) then 1 else 0 end)
                from {frm.get(child, f"{OWNER}.{child}")} t where t.{ccol} is not null''')
            if not got:
                continue
            n, ok = got[0]
            link = dict(child=f'{child}.{ccol}', parent=f'{parent}.{pcol}', rows=n, matched=ok or 0, match_pct=round(100 * (ok or 0) / n, 1) if n else None,
                        sampled_pct=(timing.get(child) or {}).get('sampled_pct'))
            links.append(link)
            evidence.setdefault(f'{child}.{ccol}', {})['link'] = link
    for con in constraints:
        if con['constraint_type'] == 'R':
            for col, rc in zip((con['cols'] or '').split(','), (con['ref_cols'] or '').split(',')):
                evidence.setdefault(f'{con["table_name"]}.{col}', {})['foreign_key'] = f'{con["ref_table"]}.{rc}'

    seconds = round(time.time() - started, 1)
    result = dict(generated_at=datetime.now().astimezone().isoformat(timespec='seconds'), owner=OWNER, dsn=conn.dsn,
                  database_version=conn.version, tables=tables, dictionary=columns, constraints=constraints, triggers=triggers,
                  sequences=sequences, objects=objects, indexes=indexes, links=links, columns=evidence,
                  profile=dict(seconds=seconds, sample_rows=args.sample_rows, statement_timeout_s=args.timeout, tables=timing, errors=run.errors))
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / 'schema_discovery.json').write_text(json.dumps(result, indent=1, default=str, ensure_ascii=False), encoding='utf-8')

    md = [f'# GARMENT_DEMO discovery — {result["generated_at"]}', '', f'Oracle {conn.version}, {len(tables)} tables, '
          f'{len(columns)} columns, {len(triggers)} triggers, {len(objects)} code objects. Took {seconds} s.', '', '## Tables', '']
    md += [f'- {t["table_name"]} ({t["num_rows"] if t["num_rows"] is not None else "?"} rows by statistics)' for t in tables]
    md += ['', '## Declared constraints', ''] + [f'- {x["table_name"]}: {x["constraint_type"]} {x["cols"]}'
                                                  + (f' → {x["ref_table"]}({x["ref_cols"]})' if x['ref_table'] else '') for x in constraints]
    md += ['', '## Indexes', ''] + [f'- {x["table_name"]}.{x["index_name"]} ({x["cols"]})' for x in indexes]
    md += ['', '## Triggers (save-time behaviour)', ''] + [f'- {t["table_name"]}.{t["trigger_name"]}: {t["triggering_event"]} {t["status"]}' for t in triggers]
    md += ['', '## Child → parent link evidence (same ORDMASID)', ''] + [
        f'- {l["child"]} → {l["parent"]}: {l["matched"]}/{l["rows"]} rows match ({l["match_pct"]}%)' for l in links]
    md += ['', '## Reference columns resolving in masters', ''] + [
        f'- {k}: {v["resolves_in"]["matched"]}/{v["resolves_in"]["rows"]} in {v["resolves_in"]["table"]}' for k, v in evidence.items() if 'resolves_in' in v]
    md += ['', '## Profiling (how each table was read)', ''] + [
        f'- {t}: {v["rows"]} rows ({v["rows_from"]}), {v["columns"]} columns, '
        + (f'{v["sampled_pct"]}% block sample' if v['sampled_pct'] else 'whole table') + f', {v["statements"]} statements, {v["seconds"]} s'
        for t, v in timing.items()]
    if run.errors:
        md += ['', '## Skipped (time limit or error)', ''] + [f'- {e}' for e in run.errors]
    (out_dir / 'schema_discovery.md').write_text('\n'.join(md) + '\n', encoding='utf-8')
    print('\n'.join(md))
    print(f'\nSaved {out_dir / "schema_discovery.json"} (git-ignored). Open POPPYS AI → Database mapping to review the evidence.')
    print('No data was changed: this script only runs SELECT statements.')


if __name__ == '__main__':
    main()
