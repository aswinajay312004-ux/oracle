"""Local read-only MCP server (backend/mcp_server.py): protocol handshake, read-only tools, governed answers,
raw SELECT only when allowed and only through the safety review, audit of every call."""
from contextlib import contextmanager
import io
import json
import os
import subprocess
import sys
from pathlib import Path
import pytest
from backend import local_store, main, mcp_server
from backend.erp import mapping as registry
from backend.insight import service, knowledge as K
from backend.insight.query import Dialect
import demo_db

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def server(tmp_path, monkeypatch):
    for mod in (local_store, main, service):
        monkeypatch.setattr(mod, 'DATA', tmp_path)
    local_store.init(); main.evidence.init(); service.invalidate()
    conn = demo_db.create()
    kb = service.build_kb(service.snapshot(), registry.effective())
    ex = service.SqliteExecutor(conn); d = Dialect('sqlite')
    idx = {}
    for t, c in service.indexable(kb):
        for v, n in ex(f'SELECT {c}, COUNT(*) FROM {t} WHERE {c} IS NOT NULL GROUP BY {c}', {}, 5000):
            idx.setdefault(K.norm(v), []).append(dict(table=t, column=c, value=str(v), n=n))

    @contextmanager
    def sources():
        yield {'db': (ex, d), 'local': (ex, d)}
    monkeypatch.setattr(main, 'query_sources', sources)
    monkeypatch.setattr(main, 'knowledge_base', lambda: kb)
    monkeypatch.setattr(main.insight, 'finder', lambda kb: lambda p: idx.get(K.norm(p), []))
    monkeypatch.setattr(main.ai, 'available', lambda: False)
    monkeypatch.setattr(main, '_ensure_values', lambda kb, sources: None)
    return lambda allow_sql=False: (mcp_server.Server(allow_sql=allow_sql), conn)


def rpc(s, method, params=None, mid=1):
    return s.handle(dict(jsonrpc='2.0', id=mid, method=method, params=params or {}))


def call(s, name, **args):
    return rpc(s, 'tools/call', dict(name=name, arguments=args))['result']


def test_handshake_and_tool_list_are_read_only(server):
    s, _ = server()
    init = rpc(s, 'initialize', dict(protocolVersion='2025-11-25', capabilities={}, clientInfo=dict(name='t', version='1')))['result']
    assert init['protocolVersion'] == '2025-11-25' and 'tools' in init['capabilities'] and init['serverInfo']['name']
    assert rpc(s, 'initialize', dict(protocolVersion='1999-01-01'))['result']['protocolVersion'] == mcp_server.PROTOCOL
    assert s.handle(dict(jsonrpc='2.0', method='notifications/initialized')) is None
    tools = rpc(s, 'tools/list')['result']['tools']
    assert {t['name'] for t in tools} == {'search_catalog', 'describe_table', 'relationship_path', 'evidence', 'ask', 'run_intent', 'accuracy_report'}
    assert all(t['annotations']['readOnlyHint'] and not t['annotations']['destructiveHint'] for t in tools)
    assert all(t['inputSchema']['type'] == 'object' for t in tools)
    assert rpc(s, 'resources/write')['error']['code'] == -32601
    assert 'supportedVersions' in rpc(s, 'server/discover')['result']


def test_catalog_tools(server):
    s, _ = server()
    found = call(s, 'search_catalog', query='buyer')['structuredContent']['results']
    assert any(r.get('table') == 'BUYMAS' for r in found)
    t = call(s, 'describe_table', table='ordmas')['structuredContent']
    assert t['key'] == 'ORDMASID' and any(l['other'] == 'BUYMAS' for l in t['links'])
    path = call(s, 'relationship_path', from_table='ORDSIZE', to_table='COLORMAS')['structuredContent']['path']
    assert [p['parent'] for p in path] == ['ORDSTYLE', 'COLORMAS']
    assert call(s, 'describe_table', table='SYS_USERS')['isError']
    assert call(s, 'evidence', subject='drop table x')['isError']


def test_questions_go_through_the_governed_pipeline(server):
    s, _ = server()
    r = call(s, 'ask', question='Buyer wise order quantity')['structuredContent']
    assert {tuple(x) for x in r['rows']} == {('NEXT', 950), ('ZARA', 100)}
    assert [c['stage'] for c in r['checks']][:3] == ['understand', 'build', 'safety']
    bad = call(s, 'run_intent', intent={'base': 'ORDMAS', 'columns': ['ORDMAS.NOPE']})
    assert bad['isError'] and 'not a column' in bad['content'][0]['text']
    assert call(s, 'ask', question='hello there')['isError']


def test_raw_select_only_when_allowed_and_only_through_the_safety_review(server):
    s, conn = server()
    assert call(s, 'run_select', sql='SELECT DOCID FROM ORDMAS')['isError']          # not offered by default
    s, conn = server(allow_sql=True)
    ok = call(s, 'run_select', sql='SELECT DOCID FROM ORDMAS WHERE OQTY > :q ORDER BY DOCID', binds={'q': 400})['structuredContent']
    assert ok['rows'] == [['PK/26-27/S/9001'], ['PK/26-27/S/9002']]
    for sql in ('DELETE FROM ORDMAS', 'SELECT 1 FROM ORDMAS; DELETE FROM ORDMAS', 'SELECT * FROM sqlite_master', 'UPDATE ORDMAS SET OQTY=0'):
        assert call(s, 'run_select', sql=sql)['isError'], sql
    assert conn.execute('SELECT COUNT(*), SUM(OQTY) FROM ORDMAS').fetchone() == (4, 1051)
    events = [e for e in local_store.audit_events() if e['action'] == 'mcp_tool']
    assert len(events) >= 5 and all(e['actor'] == 'mcp' for e in events)


def test_stdio_framing(server):
    s, _ = server()
    out = io.StringIO()
    lines = '\n'.join([json.dumps(dict(jsonrpc='2.0', id=1, method='ping')), 'not json',
                       json.dumps(dict(jsonrpc='2.0', method='notifications/initialized')), json.dumps(dict(jsonrpc='2.0', id=2, method='tools/list'))])
    mcp_server.serve(stdin=io.StringIO(lines + '\n'), stdout=out)
    replies = [json.loads(x) for x in out.getvalue().splitlines()]
    assert [r.get('id') for r in replies] == [1, None, 2] and replies[1]['error']['code'] == -32700


def test_entry_script_speaks_mcp_over_stdio(tmp_path):
    env = dict(os.environ, POPPYS_DATA_DIR=str(tmp_path))
    msgs = [dict(jsonrpc='2.0', id=1, method='initialize', params=dict(protocolVersion='2025-11-25', capabilities={}, clientInfo=dict(name='t', version='1'))),
            dict(jsonrpc='2.0', method='notifications/initialized'), dict(jsonrpc='2.0', id=2, method='tools/list')]
    p = subprocess.run([sys.executable, str(ROOT / 'scripts' / 'mcp_server.py')], input='\n'.join(map(json.dumps, msgs)) + '\n',
                       capture_output=True, text=True, timeout=60, env=env, cwd=tmp_path)
    replies = [json.loads(x) for x in p.stdout.splitlines()]
    assert [r['id'] for r in replies] == [1, 2] and len(replies[1]['result']['tools']) == 7, p.stderr
