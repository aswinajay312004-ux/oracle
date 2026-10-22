"""Compare a GARMENT text catalog with the local snapshot and shipped mapping (no DB connection).

Usage: python scripts/audit_schema_reference.py path/to/catalog.txt [--output report.json]
Only structural counts and mapping gaps are emitted. The source file remains local.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
TABLE = re.compile(r'^TABLE (\d+) of (\d+): ([A-Z][A-Z0-9_$#]*)(?:\s+\(|\s*$)')
COLUMN = re.compile(r'^    ([A-Z][A-Z0-9_$#]*)\s{2,}(\S.*)$')
LINK = re.compile(r'^    ([A-Z][A-Z0-9_$#]*)\.([A-Z][A-Z0-9_$#]*)\s+<->\s+([A-Z][A-Z0-9_$#]*)\.([A-Z][A-Z0-9_$#]*)\s+\[([^]]+)\]')


def parse_catalog(text):
    tables = {}; links = set(); current = None; in_columns = False; expected = None
    declared_columns = {}
    for line in text.splitlines():
        hit = TABLE.match(line)
        if hit:
            ordinal, total, current = hit.groups()
            if current in tables or int(ordinal) != len(tables) + 1:
                raise ValueError('Duplicate or out-of-order table in the catalog.')
            if expected is not None and expected != int(total):
                raise ValueError('Inconsistent table count in the catalog.')
            expected = int(total); tables[current] = set(); in_columns = False
        elif current and (hit := re.match(r'^  Columns \((\d+)\):', line)):
            declared_columns[current] = int(hit[1]); in_columns = True
        elif line.startswith('  Links') or line.startswith('  Also saved'):
            in_columns = False
        elif in_columns and (hit := COLUMN.match(line)):
            if hit[1] in tables[current]:
                raise ValueError(f'Duplicate column: {current}.{hit[1]}')
            tables[current].add(hit[1])
        if hit := LINK.match(line):
            a, ac, b, bc, evidence = hit.groups()
            left, right = sorted((f'{a}.{ac}', f'{b}.{bc}'))
            links.add((left, right, evidence))
    if not tables or len(tables) != expected:
        raise ValueError(f'Incomplete catalog: expected {expected} tables, read {len(tables)}.')
    for name, columns in tables.items():
        if len(columns) != declared_columns.get(name):
            raise ValueError(f'{name}: column count does not match its declaration.')
    return tables, links


def audit(text, snapshot, mapping):
    tables, links = parse_catalog(text)
    headline = re.search(r'Tables:\s*([\d,]+)\s+Columns:\s*([\d,]+)', text)
    declared_total = int(headline[2].replace(',', '')) if headline else None
    actual_total = sum(map(len, tables.values()))
    local = {}
    for col in snapshot:
        local.setdefault(col['table'], set()).add(col['column'])
    missing_local = sorted(f'{t}.{c}' for t, cols in local.items() for c in cols if c not in tables.get(t, set()))
    missing_mapping = sorted(f'{t}.{c}' for t, spec in mapping['tables'].items()
                             for c in spec['columns'] if c not in tables.get(t, set()))
    missing_masters = sorted(f'{m["table"]}.{m[k]}' for m in mapping['masters'].values()
                             for k in ('key', 'label') if m[k] not in tables.get(m['table'], set()))
    return dict(reference_tables=len(tables), reference_columns=actual_total, header_columns=declared_total,
                warnings=([f'Header declares {declared_total} columns but table sections contain {actual_total}.']
                          if declared_total is not None and declared_total != actual_total else []),
                unique_evidenced_links=len(links), local_tables=len(local),
                local_columns_missing_in_reference=missing_local, mapped_columns_missing_in_reference=missing_mapping,
                master_columns_missing_in_reference=missing_masters,
                mapped_masters_absent_locally=sorted({m['table'] for m in mapping['masters'].values() if m['table'] not in local}),
                limitations=['Structural existence only; does not verify types, business meaning, privileges, triggers or Axpert acceptance.',
                             'Report joins and screen relationships are not promoted to declared foreign keys.',
                             'No client records or database connections are read.'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('catalog', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    raw = args.catalog.read_bytes()
    try:
        report = audit(raw.decode('utf-8-sig'), json.loads((ROOT / 'docs/live_schema_columns.json').read_text(encoding='utf-8')),
                       json.loads((ROOT / 'config/oracle_mapping.json').read_text(encoding='utf-8')))
    except ValueError as exc:
        parser.error(str(exc))
    report['source_sha256'] = hashlib.sha256(raw).hexdigest()
    rendered = json.dumps(report, indent=2, ensure_ascii=False)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + '\n', encoding='utf-8')
    print(rendered)


if __name__ == '__main__':
    main()
