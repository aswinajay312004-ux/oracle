"""In-memory stand-in for the Oracle boundary, for API/UI tests that cannot reach the local database.

It reuses the real planner (with the committed dictionary snapshot as catalog) and mirrors the
writer's contract: re-plan, hash check, generated keys, planner.Ref resolution, receipt. It is a
test double only; it never claims to be Oracle in the running application.
"""
from __future__ import annotations
from copy import deepcopy
from datetime import datetime
from backend import oracle_store, main
from backend.erp import planner
from backend.erp.catalog import SnapshotCatalog


class LiveSnapshot(SnapshotCatalog):
    live = True


class FakeConn:
    version = 'fake-23.0'; dsn = 'memory'; username = 'POPPYS_APP'
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def close(self): pass
    def cursor(self): return None


class FakeOracle:
    def __init__(self, masters=None):
        self.masters = masters or {}
        self.rows = {}; self.receipts = {}; self.seq = 1000000000000; self.refs = set(); self.hashes = set()

    def catalog(self):
        return LiveSnapshot(masters=self.masters, existing_refs=self.refs, committed_hashes=self.hashes)

    def plan(self, conn, orders, filehash, mapping=None, required=()):
        return planner.plan(self.catalog(), orders, filehash, mapping or oracle_store.current_mapping(), required=required)

    def read_result(self, cursor, jobid):
        r = self.receipts.get(jobid)
        return dict(deepcopy(r), already_committed=True) if r else None

    def commit(self, draft, expected_hash, approver, mapping=None, required=()):
        if draft['id'] in self.receipts:
            return self.read_result(None, draft['id'])
        mapping = mapping or oracle_store.current_mapping()
        checked = self.plan(None, draft['orders'], draft['filehash'], mapping, required)
        if checked['errors']:
            raise ValueError('; '.join(checked['errors']))
        if checked['hash'] != expected_hash:
            raise ValueError('Database or preview changed. Generate a fresh preview and review it before accepting.')
        refs = {}; inserted = []; reused = []; orders = []
        for op in checked['operations']:
            pk = oracle_store.pk_of(op['table'], mapping)
            if op['action'] == 'reuse':
                reused.append(dict(table=op['table'], key=op['key'], pk=op['values'][pk], business=op['business'], values=op['values']))
                self.rows.setdefault(op['table'], {})[op['values'][pk]] = dict(op['values'])
                continue
            self.seq += 1; refs[op['key']] = self.seq
            vals = {c: refs[v['ref']] if isinstance(v, planner.Ref) else v for c, v in op['values'].items()}
            self.rows.setdefault(op['table'], {})[self.seq] = dict(vals)
            inserted.append(dict(table=op['table'], key=op['key'], pk=self.seq, business=op['business'], where=op['where'], values=vals))
            if op['table'] == 'ORDMAS':
                orders.append(dict(order_ref=vals['DOCID'], ordmasid=self.seq)); self.refs.add(vals['DOCID'].upper())
        self.hashes.add(draft['filehash'])
        result = dict(job_id=draft['id'], orders=orders, rows=inserted, reused=reused, totals=checked['totals'],
                      reference_only=checked['reference_only'], committed_at=datetime.now().astimezone().isoformat(),
                      approver=approver, already_committed=False)
        self.receipts[draft['id']] = deepcopy(result)
        return result

    def live_rows(self, conn, table, pk, ids, mapping):
        return {i: dict(self.rows.get(table, {}).get(i, {})) for i in ids if i in self.rows.get(table, {})}

    def history(self):
        return [dict(job_id=j, plan_hash='', approver=r['approver'], committed_at=r['committed_at']) for j, r in self.receipts.items()]

    def install(self, monkeypatch):
        monkeypatch.setattr(oracle_store, 'connect', lambda *a, **k: FakeConn())
        for name in ('plan', 'read_result', 'commit', 'live_rows', 'history'):
            monkeypatch.setattr(oracle_store, name, getattr(self, name))
        monkeypatch.setattr(oracle_store, 'masters', lambda conn, mapping=None, roles=None: {})
        monkeypatch.setattr(main, 'OracleCatalog', lambda conn: self.catalog())
        return self
