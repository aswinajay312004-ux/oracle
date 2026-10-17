"""Local, read-only MCP server: lets an AI development tool on THIS computer (for example Claude Code) study the
ERP database through POPPYS AI's governed layers instead of a raw database connection.

    .venv\\Scripts\\python.exe scripts\\mcp_server.py [--allow-sql]

Transport: Model Context Protocol over stdio (newline-delimited JSON-RPC 2.0, protocol 2025-11-25; also
answers `server/discover` for newer clients). Nothing listens on the network.

What it can and cannot do (docs/OPEN_SOURCE_RESEARCH.md, "MCP" — design after OracleMCP and SQLcl MCP):
  * every tool is read-only and annotated so (readOnlyHint, not destructive, closed world);
  * data comes only through the separate read-only Oracle account (connect_readonly refuses an account
    that can write), inside a read-only transaction, with time limits and row caps;
  * questions go through the same pipeline as Ask AI: catalog-only intent, compiled SQL, independent safety
    review, join check, total cross-check; the check trace is returned with every answer;
  * raw SQL is off unless started with --allow-sql, and even then each statement must pass the safety
    review (one SELECT over the ERP owner's catalog tables, allowlisted functions), is wrapped in a row cap
    and runs on the read-only account;
  * there is no tool that writes: saving orders stays in the app, behind validation and human approval;
  * every call is written to the local audit history (actor "mcp").
"""
from __future__ import annotations
import argparse
import json
import sys
import traceback

PROTOCOL = '2025-11-25'
SUPPORTED = ('2025-11-25', '2025-06-18', '2025-03-26', '2024-11-05')
MAX_ROWS = 50
READ_ONLY = dict(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)


def _schema(props=None, required=()):
    return dict(type='object', properties=props or {}, required=list(required), additionalProperties=False)


TOOLS = [
    dict(name='search_catalog', title='Search the ERP catalog',
         description='Find ERP tables and columns by business words or physical names (e.g. "buyer", "size quantity", "ORDMAS"). '
                     'Returns business meaning, role and whether the meaning is verified.',
         inputSchema=_schema(dict(query=dict(type='string', minLength=2, maxLength=100)), ['query'])),
    dict(name='describe_table', title='Describe one table',
         description='Columns (type, business name, role, verified or proposed, referenced master), key, label column, '
                     'approximate rows and every known link with its status (verified/declared/proposed/inferred).',
         inputSchema=_schema(dict(table=dict(type='string', pattern='^[A-Za-z0-9_$#]{1,128}$')), ['table'])),
    dict(name='relationship_path', title='How two tables are linked',
         description='The join path POPPYS AI would use from one table to another, with each link\'s condition and status.',
         inputSchema=_schema(dict(from_table=dict(type='string'), to_table=dict(type='string')), ['from_table', 'to_table'])),
    dict(name='evidence', title='Evidence for a mapping or link',
         description='Why POPPYS AI believes a column meaning or relationship is correct: the immutable evidence ledger. '
                     'Subject examples: "column:ORDMAS.BONO", "table:ORDMAS", "join:ORDSIZE->ORDSTYLE:ORDMASID=ORDMASID,SUB3_SUBNO=SUBNO".',
         inputSchema=_schema(dict(subject=dict(type='string', maxLength=300)), ['subject'])),
    dict(name='ask', title='Ask a question about the data',
         description='Answer a business question exactly as Ask AI does (read-only, governed). Returns the answer, up to 50 rows, '
                     'the SQL that ran and the checks it passed.',
         inputSchema=_schema(dict(question=dict(type='string', minLength=2, maxLength=500)), ['question'])),
    dict(name='run_intent', title='Run a structured question',
         description='Run a JSON intent {base, measures[{agg,column}], group_by[], columns[], filters[{column,op,value}], order_by[], limit} '
                     'through the compiler and all checks. Use describe_table to find TABLE.COLUMN names.',
         inputSchema=_schema(dict(intent=dict(type='object')), ['intent'])),
    dict(name='accuracy_report', title='Latest Ask AI accuracy check',
         description='The last benchmark run: golden questions compared with hand-written SQL, per question.',
         inputSchema=_schema()),
]
SQL_TOOL = dict(name='run_select', title='Run one read-only SELECT',
                description='Run ONE SELECT over the ERP owner\'s catalog tables (owner-qualified on Oracle), with bind variables. '
                            'Refused unless it passes the safety review; at most 50 rows; read-only account and transaction.',
                inputSchema=_schema(dict(sql=dict(type='string', minLength=8, maxLength=20000), binds=dict(type='object')), ['sql']))


class ToolError(Exception):
    """Shown to the client as a tool result with isError (not a protocol error)."""


class Server:
    def __init__(self, allow_sql=False, app=None):
        from . import main as app_main   # the app's own knowledge base, read-only sources and audit
        self.app = app or app_main
        self.allow_sql = allow_sql
        self.tools = {t['name']: t for t in TOOLS + ([SQL_TOOL] if allow_sql else [])}
        for t in self.tools.values():
            t['annotations'] = READ_ONLY

    # --- protocol --------------------------------------------------------------------------------
    def handle(self, msg):
        """One JSON-RPC message -> response dict, or None for notifications."""
        if not isinstance(msg, dict) or msg.get('jsonrpc') != '2.0' or not isinstance(msg.get('method'), str):
            return _error(msg.get('id') if isinstance(msg, dict) else None, -32600, 'Invalid request')
        mid, method, params = msg.get('id'), msg['method'], msg.get('params') or {}
        if 'id' not in msg:
            return None   # notification (notifications/initialized, cancelled, ...): nothing to answer
        try:
            if method == 'initialize':
                asked = params.get('protocolVersion')
                return _result(mid, dict(protocolVersion=asked if asked in SUPPORTED else PROTOCOL, capabilities=dict(tools=dict(listChanged=False)),
                                         serverInfo=dict(name='poppys-ai-oracle', title='POPPYS AI read-only ERP tools', version='1.0'),
                                         instructions='Read-only access to the garment ERP through POPPYS AI. Start with search_catalog or '
                                                      'describe_table; use ask for business questions. Nothing here can change data.'))
            if method == 'server/discover':
                return _result(mid, dict(supportedVersions=list(SUPPORTED), capabilities=dict(tools=dict(listChanged=False)),
                                         serverInfo=dict(name='poppys-ai-oracle', version='1.0')))
            if method == 'ping':
                return _result(mid, {})
            if method == 'tools/list':
                return _result(mid, dict(tools=list(self.tools.values())))
            if method == 'tools/call':
                return _result(mid, self.call(params.get('name'), params.get('arguments') or {}))
            return _error(mid, -32601, f'Method not found: {method}')
        except Exception as exc:   # never let one bad call stop the server
            return _error(mid, -32603, f'Internal error: {str(exc)[:200]}')

    def call(self, name, args):
        if name not in self.tools:
            return _text(f'Unknown tool "{name}". Tools: {", ".join(self.tools)}.', error=True)
        if not isinstance(args, dict):
            return _text('Arguments must be an object.', error=True)
        try:
            data = getattr(self, 't_' + name)(**args)
            outcome = 'ok'
        except TypeError as exc:
            data = None; outcome = f'bad arguments: {exc}'
        except ToolError as exc:
            data = None; outcome = str(exc)
        except Exception as exc:
            from .insight.query import QueryError
            data = None; outcome = str(exc) if isinstance(exc, (QueryError, RuntimeError)) else f'{type(exc).__name__}: {str(exc)[:200]}'
        try:
            self.app.store.audit('mcp', 'mcp_tool', name, arguments=json.dumps(args, ensure_ascii=False, default=str)[:500], outcome=outcome[:200])
        except Exception:
            pass
        if data is None:
            return _text(outcome, error=True)
        return dict(content=[dict(type='text', text=json.dumps(data, ensure_ascii=False, default=str, indent=1))], structuredContent=data)

    # --- tools -----------------------------------------------------------------------------------
    def _kb(self):
        return self.app.knowledge_base()

    def _table(self, kb, name):
        t = kb.table(str(name).upper())
        if not t or t.get('source') == 'local':
            raise ToolError(f'{name} is not an ERP table POPPYS AI knows. Use search_catalog.')
        return t

    def t_search_catalog(self, query):
        from .insight.knowledge import norm
        kb = self._kb(); words = norm(query).split(); hits = []
        for name, t in kb.tables.items():
            if t.get('source') == 'local':
                continue
            hay = norm(' '.join([name, t['business'], t['plural'], *t['synonyms']]))
            if all(w in hay for w in words):
                hits.append(dict(table=name, business=t['business'], kind=t['kind'], rows=t.get('rows'), description=t['description']))
            for c, m in t['columns'].items():
                if m['role'] == 'system':
                    continue
                if all(w in norm(' '.join([c, m['business'], *m['synonyms']])) for w in words):
                    hits.append(dict(column=f'{name}.{c}', business=m['business'], role=m['role'], type=m['type'], status=m['status'],
                                     references=m.get('references')))
        return dict(query=query, results=hits[:60], more=max(0, len(hits) - 60))

    def t_describe_table(self, table):
        kb = self._kb(); t = self._table(kb, table); name = str(table).upper()
        from . import evidence
        counts = evidence.counts('')
        links = [dict(direction='to parent' if j['child'] == name else 'from child', other=j['parent'] if j['child'] == name else j['child'],
                      on=' AND '.join(f'{j["child"]}.{c} = {j["parent"]}.{p}' for c, p in j['pairs']), status=j['status'], label=j['label'],
                      evidence=counts.get(f'join:{j["id"]}', 0))
                 for j in kb.joins if name in (j['child'], j['parent'])]
        cols = [dict(column=c, type=m['type'], business=m['business'], role=m['role'], status=m['status'], references=m.get('references'),
                     evidence=counts.get(f'column:{name}.{c}', 0)) for c, m in t['columns'].items()]
        unique = sorted(map(sorted, (getattr(kb, 'unique', None) or {}).get(name, [])))
        return dict(table=name, business=t['business'], kind=t['kind'], description=t['description'], rows=t.get('rows'), key=t.get('key'),
                    label=t.get('label'), unique_keys=unique, columns=cols, links=links, live=kb.live)

    def t_relationship_path(self, from_table, to_table):
        kb = self._kb(); a = str(from_table).upper(); b = str(to_table).upper()
        self._table(kb, a); self._table(kb, b)
        steps = kb.path(a, b)
        if steps is None:
            return dict(path=None, note=f'{a} and {b} are not linked in the relationship map.')
        return dict(path=[dict(step=i + 1, direction='up (many-to-one)' if d == 'up' else 'down (one-to-many)', child=j['child'], parent=j['parent'],
                               on=' AND '.join(f'{j["child"]}.{c} = {j["parent"]}.{p}' for c, p in j['pairs']), status=j['status'], label=j['label'])
                          for i, (j, d) in enumerate(steps)])

    def t_evidence(self, subject):
        from . import evidence
        if not evidence.SUBJECT.match(subject):
            raise ToolError('Subject must look like column:TABLE.COLUMN, table:TABLE or join:CHILD->PARENT:A=B.')
        return dict(subject=subject, records=evidence.for_subject(subject, include_superseded=True), open_questions=evidence.questions(subject))

    def _answer(self, fn):
        kb = self._kb()
        with self.app.query_sources() as sources:
            self.app._require_db(sources)
            r = fn(kb, sources)
        cols = [c for c in r['columns'] if not c.get('hidden')]; idx = [r['columns'].index(c) for c in cols]
        return dict(understood=r['understood'], summary=r['summary'], confidence=r['confidence'], columns=[c['label'] for c in cols],
                    rows=[[row[i] for i in idx] for row in r['rows'][:MAX_ROWS]], rows_returned=r['row_count'], rows_shown=min(MAX_ROWS, r['row_count']),
                    truncated=r['truncated'] or r['row_count'] > MAX_ROWS, checks=r['checks'], notes=r['notes'], sql=r['sql'], binds=r['binds'],
                    intent=r['intent'], tables=r['tables'], joins=r['joins'])

    def t_ask(self, question):
        from .insight.understand import NotUnderstood
        try:
            return self._answer(lambda kb, sources: self.app._ask(question.strip(), kb, sources))
        except NotUnderstood as exc:
            raise ToolError(f'{exc} Suggestions: {"; ".join(exc.suggestions[:5])}' if exc.suggestions else str(exc))

    def t_run_intent(self, intent):
        from .insight import engine
        return self._answer(lambda kb, sources: engine.run(intent, kb, sources, source='mcp'))

    def t_accuracy_report(self):
        runs = self.app.store.benchmark_runs(5)
        return dict(runs=runs) if runs else dict(runs=[], note='No accuracy check has been run yet (Setup → Answer accuracy).')

    def t_run_select(self, sql, binds=None):
        from .insight import engine, policy
        kb = self._kb()
        if binds is not None and not (isinstance(binds, dict) and all(isinstance(v, (str, int, float, type(None))) for v in binds.values())):
            raise ToolError('binds must be an object of plain values.')
        with self.app.query_sources() as sources:
            execute, dialect = self.app._require_db(sources)
            tables = policy.check(sql, dialect.name, dialect.owner)
            unknown = [t for t in tables if not kb.table(t)]
            if unknown:
                raise ToolError(f'{", ".join(unknown)} is not in the catalog.')
            capped = dialect.limit(f'SELECT * FROM ({sql.strip().rstrip(";")}) q', MAX_ROWS + 1)
            policy.check(capped, dialect.name, dialect.owner)
            rows = execute(capped, binds or {}, MAX_ROWS + 1)
        return dict(rows=[[engine.cell(v) for v in r] for r in rows[:MAX_ROWS]], truncated=len(rows) > MAX_ROWS, tables=tables)


def _result(mid, result):
    return dict(jsonrpc='2.0', id=mid, result=result)


def _error(mid, code, message):
    return dict(jsonrpc='2.0', id=mid, error=dict(code=code, message=message))


def _text(text, error=False):
    return dict(content=[dict(type='text', text=text)], isError=error)


def serve(allow_sql=False, stdin=None, stdout=None):
    stdin = stdin or sys.stdin; stdout = stdout or sys.stdout
    from . import evidence, local_store
    local_store.init(); evidence.init()
    server = Server(allow_sql=allow_sql)
    print(f'POPPYS AI MCP server ready (read-only{", raw SELECT allowed" if allow_sql else ""}).', file=sys.stderr)
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            reply = _error(None, -32700, 'Parse error')
        else:
            try:
                reply = server.handle(msg)
            except Exception:
                traceback.print_exc(file=sys.stderr)
                reply = _error(msg.get('id') if isinstance(msg, dict) else None, -32603, 'Internal error')
        if reply is not None:
            stdout.write(json.dumps(reply, ensure_ascii=False, default=str) + '\n'); stdout.flush()


def main(argv=None):
    ap = argparse.ArgumentParser(description='POPPYS AI read-only MCP server (stdio).')
    ap.add_argument('--allow-sql', action='store_true', help='also offer run_select (still read-only and safety-reviewed)')
    a = ap.parse_args(argv)
    serve(allow_sql=a.allow_sql)
