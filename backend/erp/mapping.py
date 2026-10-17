"""Business field -> GARMENT_DEMO column registry with audited local verification overrides."""
from __future__ import annotations
from copy import deepcopy
import hashlib
import json
from functools import lru_cache
from ..config import ROOT

PATH = ROOT / 'config' / 'oracle_mapping.json'
STATUSES = ('verified', 'proposed')


@lru_cache(maxsize=1)
def _default():
    data = json.loads(PATH.read_text(encoding='utf-8'))
    validate(data)
    return data


def validate(data):
    masters = data['masters']
    for table, spec in data['tables'].items():
        if spec['key'] not in spec['columns']:
            raise ValueError(f'{table}: primary key column missing from mapping')
        if spec.get('parent') and spec['parent'] not in data['tables']:
            raise ValueError(f'{table}: unknown parent {spec["parent"]}')
        for col, c in spec['columns'].items():
            if c.get('status') not in STATUSES:
                raise ValueError(f'{table}.{col}: status must be verified or proposed')
            if c.get('master') and c['master'] not in masters:
                raise ValueError(f'{table}.{col}: unknown master {c["master"]}')
            kinds = [k for k in ('key', 'parent', 'field', 'value', 'template', 'copy') if k in c]
            if not kinds:
                raise ValueError(f'{table}.{col}: no value source')


def effective(overrides=()):
    """Overrides: iterable of dicts {table, column ('' for table), status}."""
    data = deepcopy(_default())
    for o in overrides:
        table = data['tables'].get(o['table'])
        if not table or o['status'] not in STATUSES:
            continue
        if not o.get('column'):
            table['status'] = o['status']
        elif o['column'] in table['columns'] and not table['columns'][o['column']].get('key') \
                and 'parent' not in table['columns'][o['column']]:
            table['columns'][o['column']]['status'] = o['status']
    return data


def fingerprint(mapping):
    return hashlib.sha256(json.dumps(mapping, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def tables_in_order(mapping):
    """Parents before children."""
    order, seen = [], set()
    def visit(name):
        if name in seen:
            return
        parent = mapping['tables'][name].get('parent')
        if parent:
            visit(parent)
        seen.add(name); order.append(name)
    for name in mapping['tables']:
        visit(name)
    return order


def referenced_tables(mapping):
    return {m['table'] for m in mapping['masters'].values()} | set(mapping['tables'])
