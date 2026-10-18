"""Axpert compatibility gap report: compares the rows Axpert itself created with the rows POPPYS AI saved, column by
column, so the missing columns of a direct INSERT are known instead of assumed (docs/ORACLE_AXPERT_INTEGRATION.md).

    python scripts/axpert_gap_report.py [--sample 5000] [--no-values] [--out data]

Read-only: one READ ONLY transaction on the application's own account (.env.poppys), every statement with a time
limit. Rows saved by POPPYS AI are recognised by their import receipts (PP_IMPORT_LOG), so nothing is guessed.
For each table an order touches (order header, lines, sizes and the masters POPPYS can create) it lists, per column:
  * how often Axpert's own rows fill it, and how often POPPYS rows do;
  * nullability, the column default and triggers on the table (values Oracle fills by itself);
  * for flag-like columns (at most 3 different values among Axpert rows) those values, unless --no-values.
A column Axpert nearly always fills and POPPYS never does is a GAP: whether Axpert needs it must be decided before
direct INSERT is called compatible. The report holds counts and flag values only, never business records.

Output: <out>/axpert_gap_report.md and .json (data/ is not committed).
"""
from __future__ import annotations
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

TABLES = ('ORDMAS', 'ORDSTYLE', 'ORDSIZE', 'BUYMAS', 'STYLENUM', 'STYLEMAS', 'COLORMAS', 'SIZE_MAS', 'MERCHMAS', 'UNITMAS')
GAP_AXPERT = 90.0     # % of Axpert rows filling a column for it to count as "Axpert fills it"
FLAG_DISTINCT = 3
TIMEOUT_MS = 30_000


def poppys_ids(cur, owner_tables):
    """{table: {primary key, ...}} of every row POPPYS AI wrote, from its own import receipts."""
    cur.execute('SELECT RESULT_JSON FROM PP_IMPORT_LOG')
    out = {t: set() for t in owner_tables}
    for (payload,) in cur:
        receipt = json.loads(payload.read() if hasattr(payload, 'read') else payload)
        for row in receipt.get('rows') or []:
            if row.get('table') in out:
                out[row['table']].add(row.get('pk'))
    return out


def dictionary(cur, owner, table):
    cur.execute('''SELECT column_name, data_type, nullable, data_default FROM all_tab_columns
                   WHERE owner = :o AND table_name = :t ORDER BY column_id''', o=owner, t=table)
    cols = []
    for name, dtype, nullable, default in cur:
        text = default.read() if hasattr(default, 'read') else default
        cols.append(dict(column=name, type=dtype, nullable=nullable, default=(str(text).strip() or None) if text is not None else None))
    cur.execute('''SELECT trigger_name, triggering_event, status FROM all_triggers WHERE table_owner = :o AND table_name = :t''',
                o=owner, t=table)
    triggers = [dict(name=n, event=e, status=s) for n, e, s in cur]
    return cols, triggers


def key_of(table):
    from backend.erp import mapping as registry
    m = registry.effective()
    if table in m['tables']:
        return m['tables'][table]['key']
    return next(s['key'] for s in m['masters'].values() if s['table'] == table)


def profile(cur, owner, table, key, cols, mine, sample, values):
    """Fill counts per column for Axpert rows (newest `sample` rows that POPPYS did not write) and for POPPYS rows."""
    ids = sorted(i for i in mine if i is not None)
    fill = ', '.join(f'COUNT("{c["column"]}")' for c in cols)
    # Axpert rows: newest first by key, POPPYS rows left out (bound in chunks; there are few of them)
    exclude = ''; binds = {}
    if ids:
        exclude = ' WHERE ' + ' AND '.join(f'"{key}" NOT IN ({",".join(f":x{i}_{j}" for j in range(len(part)))})'
                                           for i, part in enumerate(chunks(ids, 900)))
        binds = {f'x{i}_{j}': v for i, part in enumerate(chunks(ids, 900)) for j, v in enumerate(part)}
    base = f'SELECT * FROM (SELECT * FROM {owner}."{table}"{exclude} ORDER BY "{key}" DESC) WHERE ROWNUM <= :n'
    cur.execute(f'SELECT COUNT(*), {fill} FROM ({base})', dict(binds, n=sample))
    got = cur.fetchone(); axpert_n = got[0]; axpert = dict(zip([c['column'] for c in cols], got[1:]))
    poppys_n = 0; poppys = {c['column']: 0 for c in cols}
    for part in chunks(ids, 900):
        b = {f'i{j}': v for j, v in enumerate(part)}
        cur.execute(f'SELECT COUNT(*), {fill} FROM {owner}."{table}" WHERE "{key}" IN ({",".join(":" + k for k in b)})', b)
        got = cur.fetchone(); poppys_n += got[0]
        for c, n in zip(cols, got[1:]):
            poppys[c['column']] += n
    flags = {}
    if values and axpert_n:
        for c in cols:
            if axpert[c['column']] and c['type'] in ('CHAR', 'VARCHAR2', 'NUMBER'):
                cur.execute(f'SELECT DISTINCT "{c["column"]}" FROM ({base}) WHERE "{c["column"]}" IS NOT NULL AND ROWNUM <= :m',
                            dict(binds, n=sample, m=FLAG_DISTINCT + 1))
                seen = [r[0] for r in cur]
                if len(seen) <= FLAG_DISTINCT and all(len(str(v)) <= 12 for v in seen):
                    flags[c['column']] = [str(v) for v in seen]
    return axpert_n, axpert, poppys_n, poppys, flags


def chunks(items, size):
    return [items[i:i + size] for i in range(0, len(items), size)] or []


def classify(a_pct, p_pct, col):
    if a_pct is None:
        return 'no Axpert rows'
    if p_pct is None:
        return 'no POPPYS rows'
    if a_pct >= GAP_AXPERT and p_pct == 0:
        return 'GAP' if not col['default'] else 'default only'
    if a_pct > 0 and p_pct == 0:
        return 'sometimes filled by Axpert'
    if a_pct == 0 and p_pct > 0:
        return 'POPPYS only'
    if a_pct == 0 and p_pct == 0:
        return 'empty in both'
    return 'both'


def report(conn, owner, sample=5000, values=True):
    conn.call_timeout = TIMEOUT_MS
    cur = conn.cursor(); cur.execute('SET TRANSACTION READ ONLY')
    try:
        mine = poppys_ids(cur, TABLES)
        tables = []
        for table in TABLES:
            cols, triggers = dictionary(cur, owner, table)
            if not cols:
                tables.append(dict(table=table, visible=False)); continue
            key = key_of(table)
            an, a, pn, p, flags = profile(cur, owner, table, key, cols, mine[table], sample, values)
            rows = []
            for c in cols:
                a_pct = round(100 * a[c['column']] / an, 1) if an else None
                p_pct = round(100 * p[c['column']] / pn, 1) if pn else None
                rows.append(dict(c, axpert_pct=a_pct, poppys_pct=p_pct, status=classify(a_pct, p_pct, c), flags=flags.get(c['column'])))
            tables.append(dict(table=table, visible=True, key=key, axpert_rows=an, poppys_rows=pn, triggers=triggers, columns=rows,
                               gaps=sum(r['status'] == 'GAP' for r in rows)))
        return dict(generated_at=datetime.now().astimezone().isoformat(timespec='seconds'), owner=owner, sample=sample,
                    gap_threshold_pct=GAP_AXPERT, tables=tables)
    finally:
        conn.rollback()


def markdown(r):
    out = [f'# Axpert compatibility gap report ({r["generated_at"]})', '',
           f'Schema {r["owner"]}. Axpert rows: newest {r["sample"]:,} per table not written by POPPYS AI. POPPYS rows: from its import '
           f'receipts. **GAP** = Axpert fills the column in at least {r["gap_threshold_pct"]:g}% of its rows and POPPYS never does, and Oracle has '
           'no default for it. Counts only; flag values are shown for columns with at most 3 different values.', '']
    for t in r['tables']:
        if not t['visible']:
            out += [f'## {t["table"]}', '', 'Not visible to the application account.', '']; continue
        out += [f'## {t["table"]} — {t["gaps"]} gap(s)', '', f'Axpert rows read: {t["axpert_rows"]:,} · POPPYS rows: {t["poppys_rows"]:,} · '
                f'triggers: {", ".join(x["name"] + " (" + x["event"].strip() + ", " + x["status"].lower() + ")" for x in t["triggers"]) or "none"}', '',
                f'{sum(c["status"] == "empty in both" for c in t["columns"])} of {len(t["columns"])} columns are empty in both and not listed.', '',
                '| Column | Type | Null? | Default | Axpert % | POPPYS % | Status | Axpert values |', '|---|---|---|---|---|---|---|---|']
        order = {'GAP': 0, 'sometimes filled by Axpert': 1, 'default only': 2, 'both': 3, 'POPPYS only': 4}
        for c in sorted(t['columns'], key=lambda c: (order.get(c['status'], 5), c['column'])):
            if c['status'] == 'empty in both' or (c['status'] == 'no Axpert rows' and not c['poppys_pct']):
                continue
            out.append(f'| {c["column"]} | {c["type"]} | {c["nullable"]} | {c["default"] or ""} | {"" if c["axpert_pct"] is None else c["axpert_pct"]} | '
                       f'{"" if c["poppys_pct"] is None else c["poppys_pct"]} | {c["status"]} | {", ".join(c["flags"] or [])} |')
        out.append('')
    return '\n'.join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--sample', type=int, default=5000, help='newest Axpert rows read per table')
    ap.add_argument('--no-values', action='store_true', help='do not list the values of flag-like columns')
    ap.add_argument('--out', default=str(ROOT / 'data'))
    args = ap.parse_args(argv)
    from backend import oracle_store
    from backend.config import OWNER
    with oracle_store.connect() as conn:
        r = report(conn, OWNER, args.sample, not args.no_values)
    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    (out / 'axpert_gap_report.json').write_text(json.dumps(r, indent=1, ensure_ascii=False), encoding='utf-8')
    (out / 'axpert_gap_report.md').write_text(markdown(r), encoding='utf-8')
    for t in r['tables']:
        if t['visible']:
            print(f'{t["table"]:10} Axpert rows {t["axpert_rows"]:>6,}  POPPYS rows {t["poppys_rows"]:>4,}  gaps {t["gaps"]:>3}  triggers {len(t["triggers"])}')
    print(f'Written: {out / "axpert_gap_report.md"} (read-only; nothing was changed)')


if __name__ == '__main__':
    main()
