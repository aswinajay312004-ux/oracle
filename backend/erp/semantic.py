"""Semantic model: one machine-readable description of business entities, verified columns and
join paths, derived from the same registry that drives writes.

It is the foundation for later read-only features (natural-language questions, dashboards):
a question planner may only use entities, columns and joins listed here, so retrieval uses
exactly the same verified meanings as data entry — no free guessing over thousands of columns.
"""
from __future__ import annotations
from .mapping import fingerprint


def model(mapping, columns=None, discovery=None):
    columns = columns or {}
    evidence = (discovery or {}).get('columns', {})
    entities = []
    for name, spec in mapping['tables'].items():
        entities.append(dict(
            table=name, business=spec['business'], screen=spec.get('screen'), grain=spec['level'], key=spec['key'],
            status=spec['status'], parent=spec.get('parent'),
            attributes=[dict(column=c, meaning=s.get('label'), status=s['status'],
                             type=(columns.get(name, {}).get(c) or {}).get('type'),
                             references=mapping['masters'][s['master']]['table'] if s.get('master') else None,
                             evidence=evidence.get(f'{name}.{c}'))
                        for c, s in spec['columns'].items()]))
    for role, m in mapping['masters'].items():
        entities.append(dict(table=m['table'], business=m['business'] + ' master', grain='master', key=m['key'],
                             status='verified' if m['table'] in columns else 'unavailable', role=role,
                             attributes=[dict(column=m['key'], meaning=f'{m["business"]} ID', status='verified'),
                                         dict(column=m['label'], meaning=m['business'], status='verified')]))
    joins = []
    for link in mapping.get('links', []):
        on = link['on']
        if '=' in on:
            left, right = [x.strip() for x in on.split('=')]
            cond = f'{link["from"]}.{left} = {link["to"]}.{right} AND {link["from"]}.ORDMASID = {link["to"]}.ORDMASID'
        else:
            cond = f'{link["from"]}.{on} = {link["to"]}.{on}'
        joins.append(dict(left=link['from'], right=link['to'], condition=cond, meaning=link['label'], cardinality='one-to-many'))
    for name, spec in mapping['tables'].items():
        for c, s in spec['columns'].items():
            if s.get('master'):
                m = mapping['masters'][s['master']]
                joins.append(dict(left=name, right=m['table'], condition=f'{name}.{c} = {m["table"]}.{m["key"]}',
                                  meaning=f'{s.get("label")} → {m["business"]}', cardinality='many-to-one',
                                  verified=s['status'] == 'verified' and spec['status'] == 'verified'))
    return dict(version=1, fingerprint=fingerprint(mapping), owner=mapping.get('owner'), entities=entities, joins=joins,
                rules=['Read-only: generated queries may only SELECT from listed entities.',
                       'Only verified attributes and verified joins may be used to answer a question.',
                       'Child rows are scoped by ORDMASID before matching SUBNO values (SUBNO alone is not unique).',
                       'Reference columns hold master IDs; show the master label, never the raw ID, to users.'])
