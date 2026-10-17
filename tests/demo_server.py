"""Development stand-in server for UI checks WITHOUT Oracle (cloud/CI screenshots only).

    python tests/demo_server.py  [port]

Saving uses the in-memory Oracle stand-in (tests/fake_oracle.py); Ask AI and the Database screen use a
SQLite copy of the GARMENT_DEMO *structure* with synthetic rows (tests/demo_db.py). Approved orders are
also inserted into that SQLite copy so they can be asked about. Never use this for real work: the real
app (start.ps1) talks only to the local Oracle database.
"""
from __future__ import annotations
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]

import uvicorn  # noqa: E402
from backend import local_store, main, oracle_store  # noqa: E402
from backend.insight import service, knowledge as K  # noqa: E402
from backend.insight.query import Dialect  # noqa: E402
from backend.erp import mapping as registry  # noqa: E402
import demo_db  # noqa: E402
from fake_oracle import FakeOracle  # noqa: E402


class Patch:
    def setattr(self, obj, name, value):
        setattr(obj, name, value)


def build():
    data = Path(tempfile.mkdtemp(prefix='poppys-demo-'))
    for mod in (local_store, main, service):
        mod.DATA = data
    local_store.init()
    conn = demo_db.create()
    ex = service.SqliteExecutor(conn); d = Dialect('sqlite')
    masters = {}
    for t, key, label in [('BUYMAS', 'BUYMASID', 'BUYERNAME'), ('STYLENUM', 'STYLENUMID', 'STYLENUMBER'), ('STYLEMAS', 'STYLEMASID', 'SDESC'),
                          ('SIZE_MAS', 'SIZE_MASID', 'SIZES'), ('COLORMAS', 'COLORMASID', 'COLOR'), ('UNITMAS', 'UNITMASID', 'UOM'), ('MERCHMAS', 'MERCHMASID', 'NAME')]:
        masters[t] = [dict(id=k, label=v, cancelled=c == 'T') for k, v, c in conn.execute(f'SELECT {key},{label},CANCEL FROM {t}')]
    fake = FakeOracle(masters=masters).install(Patch())
    oracle_store.masters = lambda c, mapping=None, roles=None: {role: [dict(x) for x in masters.get(spec['table'], [])]
                                                                for role, spec in registry.effective()['masters'].items() if roles is None or role in roles}
    commit = fake.commit

    def commit_and_copy(draft, expected_hash, approver, mapping=None, required=()):
        result = commit(draft, expected_hash, approver, mapping, required)
        if not result.get('already_committed'):
            for row in result['rows']:
                vals = dict(row['values'])
                conn.execute(f'INSERT INTO {row["table"]} ({",".join(vals)}) VALUES ({",".join("?" * len(vals))})', list(vals.values()))
            for row in result['reused']:
                pass
            conn.commit()
        return result
    oracle_store.commit = commit_and_copy
    kb = service.build_kb(service.snapshot(), registry.effective())
    kb.live = True; kb.data['live'] = True
    for t in kb.tables.values():
        if t.get('source') != 'local':
            try:
                t['rows'] = conn.execute(f'SELECT COUNT(*) FROM {t["name"]}').fetchone()[0]
            except Exception:
                pass
    main.knowledge_base = lambda: kb
    rows = []
    for t, c in service.indexable(kb):
        for v, n in ex(f'SELECT {c}, COUNT(*) FROM {t} WHERE {c} IS NOT NULL GROUP BY {c}', {}, 5000):
            rows.append((t, c, str(v), K.norm(v), n))
    local_store.replace_values(rows)

    @contextmanager
    def sources():
        local = service.uploads_db()
        try:
            yield {'db': (ex, d), 'local': (service.SqliteExecutor(local), d)}
        finally:
            local.close()
    main.query_sources = sources
    return main.app


if __name__ == '__main__':
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    uvicorn.run(build(), host='127.0.0.1', port=port, log_level='warning')
