from __future__ import annotations
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime
import hashlib
import hmac
import json
import re
import secrets
import threading
import time
import uuid
from fastapi import FastAPI, UploadFile, File, Request, Response, HTTPException, Depends, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel, Field
import oracledb
from . import local_store as store, oracle_store as oracle, workbooks, pdfdocs, ai
from .config import DATA, ROOT, OWNER
from .erp import mapping as registry, readback, semantic
from .erp.catalog import SnapshotCatalog, OracleCatalog
from .erp import planner
from . import insight_api as insight
from .insight import engine, understand
from .insight.query import Dialect, QueryError, compile_intent

LOCK = threading.RLock()
ATTEMPTS = {}
RANK = {'viewer': 0, 'operator': 1, 'approver': 2, 'admin': 3}
PERMISSIONS = {
    'viewer': ['view'],
    'operator': ['view', 'upload', 'edit', 'preview'],
    'approver': ['view', 'upload', 'edit', 'preview', 'approve', 'audit'],
    'admin': ['view', 'upload', 'edit', 'preview', 'approve', 'audit', 'users', 'mapping', 'templates', 'settings'],
}
DEFAULT_SETTINGS = dict(allow_self_approval=True, company_exporter='POPPYS KNITWEAR PRIVATE LIMITED', default_sample_type='DEVELOPMENT SAMPLE')


@asynccontextmanager
async def lifespan(app):
    store.init()
    yield

app = FastAPI(title='POPPYS AI — Order entry automation', lifespan=lifespan)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=['127.0.0.1', 'localhost', 'testserver'])


@app.middleware('http')
async def protect(request, call_next):
    if int(request.headers.get('content-length', '0') or 0) > 14 * 1024 * 1024:
        return JSONResponse({'detail': 'Request exceeds 14 MB.'}, status_code=413)
    if request.method not in ('GET', 'HEAD', 'OPTIONS'):
        origin = request.headers.get('origin')
        if origin and origin != str(request.base_url).rstrip('/'):
            return JSONResponse({'detail': 'Cross-origin changes are not permitted.'}, status_code=403)
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Referrer-Policy'] = 'same-origin'
    response.headers['Cache-Control'] = 'no-store'
    response.headers['Content-Security-Policy'] = "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; script-src 'self'; connect-src 'self'; font-src 'self'; frame-ancestors 'none'"
    return response


@app.exception_handler(ValueError)
async def bad_value(request, exc): return JSONResponse({'detail': str(exc)}, status_code=409)


@app.exception_handler(KeyError)
async def missing(request, exc): return JSONResponse({'detail': 'Not found. Reload and try again.'}, status_code=404)


@app.exception_handler(oracledb.Error)
async def db_error(request, exc):
    code = getattr(exc.args[0], 'full_code', 'Oracle error') if exc.args else 'Oracle error'
    return JSONResponse({'detail': f'Oracle could not complete the request ({code}). Nothing was partially saved. Check the database connection, then refresh the preview.'}, status_code=503)


@app.exception_handler(RuntimeError)
async def runtime_error(request, exc): return JSONResponse({'detail': str(exc)}, status_code=503)


# --- authentication & roles ------------------------------------------------------
def _user(request):
    token = request.cookies.get('poppys_session', '')
    with store.db() as c:
        row = c.execute('''SELECT u.username,u.role FROM sessions s JOIN users u ON u.username=s.username
            WHERE s.token_hash=? AND s.expires>? AND u.active=1''', (hashlib.sha256(token.encode()).hexdigest(), time.time())).fetchone()
    return dict(username=row['username'], role=row['role']) if row else None


def authenticated(request: Request):
    user = _user(request)
    if not user:
        raise HTTPException(401, 'Sign in to continue.')
    return user


def require(permission):
    def check(user=Depends(authenticated)):
        if permission not in PERMISSIONS[user['role']]:
            raise HTTPException(403, f'Your role ({user["role"]}) cannot {permission.replace("_", " ")}. Ask an administrator.')
        return user
    return check


def issue(response, username):
    token = secrets.token_urlsafe(32)
    with store.db() as c:
        c.execute('DELETE FROM sessions WHERE expires<?', (time.time(),))
        c.execute('INSERT INTO sessions VALUES (?,?,?)', (hashlib.sha256(token.encode()).hexdigest(), username, time.time() + 28800))
    response.set_cookie('poppys_session', token, httponly=True, samesite='strict', max_age=28800)


class Credentials(BaseModel):
    username: str = Field(min_length=2, max_length=50, pattern=r'^[A-Za-z0-9._@-]+$')
    password: str = Field(min_length=10, max_length=256)


def password_hash(password, salt): return hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), 300000).hex()


def settings():
    return {k: store.get_setting(k, v) for k, v in DEFAULT_SETTINGS.items()}


@app.get('/api/session')
def who(request: Request):
    with store.db() as c:
        needs = not c.execute('SELECT 1 FROM users LIMIT 1').fetchone()
    user = _user(request)
    return dict(authenticated=bool(user), needs_setup=needs, username=user and user['username'], role=user and user['role'],
                permissions=PERMISSIONS[user['role']] if user else [])


@app.post('/api/setup')
def setup(data: Credentials, response: Response):
    with LOCK, store.db() as c:
        if c.execute('SELECT 1 FROM users').fetchone():
            raise HTTPException(409, 'Setup is already complete. Sign in.')
        salt = secrets.token_hex(16)
        c.execute('INSERT INTO users(username,salt,password_hash,role,active,created_at) VALUES (?,?,?,?,1,?)',
                  (data.username, salt, password_hash(data.password, salt), 'admin', store.now()))
    store.audit(data.username, 'setup', 'workspace', role='admin')
    issue(response, data.username); return dict(authenticated=True, username=data.username, role='admin')


@app.post('/api/login')
def login(data: Credentials, request: Request, response: Response):
    host = request.client.host if request.client else 'local'
    with LOCK:
        ATTEMPTS[host] = [t for t in ATTEMPTS.get(host, []) if time.time() - t < 60]
        if len(ATTEMPTS[host]) >= 10:
            raise HTTPException(429, 'Wait a minute before trying again.')
        ATTEMPTS[host].append(time.time())
    with store.db() as c:
        row = c.execute('SELECT * FROM users WHERE username=?', (data.username,)).fetchone()
    calculated = password_hash(data.password, row['salt'] if row else '00' * 16)
    if not row or not hmac.compare_digest(calculated, row['password_hash']) or not row['active']:
        store.audit(data.username, 'login_failed', 'workspace')
        raise HTTPException(401, 'Username or password is incorrect.')
    store.audit(data.username, 'login', 'workspace')
    issue(response, data.username); return dict(authenticated=True, username=data.username, role=row['role'])


@app.post('/api/logout')
def logout(request: Request, response: Response):
    with store.db() as c:
        c.execute('DELETE FROM sessions WHERE token_hash=?', (hashlib.sha256(request.cookies.get('poppys_session', '').encode()).hexdigest(),))
    response.delete_cookie('poppys_session'); return {'ok': True}


class NewUser(Credentials):
    role: str = Field(pattern='^(admin|approver|operator|viewer)$')


class UserChange(BaseModel):
    role: str | None = Field(default=None, pattern='^(admin|approver|operator|viewer)$')
    active: bool | None = None
    password: str | None = Field(default=None, min_length=10, max_length=256)


@app.get('/api/users')
def users(user=Depends(require('users'))):
    with store.db() as c:
        return [dict(username=r['username'], role=r['role'], active=bool(r['active']), created_at=r['created_at'])
                for r in c.execute('SELECT * FROM users ORDER BY username')]


@app.post('/api/users')
def add_user(data: NewUser, user=Depends(require('users'))):
    with LOCK, store.db() as c:
        if c.execute('SELECT 1 FROM users WHERE lower(username)=lower(?)', (data.username,)).fetchone():
            raise HTTPException(409, 'That username already exists.')
        salt = secrets.token_hex(16)
        c.execute('INSERT INTO users(username,salt,password_hash,role,active,created_at) VALUES (?,?,?,?,1,?)',
                  (data.username, salt, password_hash(data.password, salt), data.role, store.now()))
    store.audit(user['username'], 'user_created', data.username, role=data.role)
    return {'ok': True}


@app.patch('/api/users/{username}')
def change_user(username: str, data: UserChange, user=Depends(require('users'))):
    with LOCK, store.db() as c:
        row = c.execute('SELECT * FROM users WHERE username=?', (username,)).fetchone()
        if not row:
            raise HTTPException(404, 'User not found.')
        admins = c.execute("SELECT count(*) FROM users WHERE role='admin' AND active=1").fetchone()[0]
        demoting = row['role'] == 'admin' and ((data.role and data.role != 'admin') or data.active is False)
        if demoting and admins <= 1:
            raise HTTPException(409, 'At least one active administrator is required.')
        if data.role:
            c.execute('UPDATE users SET role=? WHERE username=?', (data.role, username))
        if data.active is not None:
            c.execute('UPDATE users SET active=? WHERE username=?', (1 if data.active else 0, username))
            if not data.active:
                c.execute('DELETE FROM sessions WHERE username=?', (username,))
        if data.password:
            salt = secrets.token_hex(16)
            c.execute('UPDATE users SET salt=?,password_hash=? WHERE username=?', (salt, password_hash(data.password, salt), username))
            c.execute('DELETE FROM sessions WHERE username=?', (username,))
    store.audit(user['username'], 'user_changed', username, role=data.role, active=data.active, password_reset=bool(data.password))
    return {'ok': True}


# --- status ----------------------------------------------------------------------
@app.get('/api/health')
def health(user=Depends(authenticated)):
    status = {'connected': False, 'owner': OWNER}
    try:
        with oracle.connect() as conn:
            m = oracle.current_mapping(); cat = OracleCatalog(conn)
            visible = cat.columns(registry.referenced_tables(m))
            status.update(connected=True, tables=len(visible), insertable=sorted(cat.insertable(registry.referenced_tables(m))),
                          version=conn.version, dsn=conn.dsn)
    except Exception as exc:
        status['error'] = ('Local Oracle is not reachable. Start Docker Desktop and the oracle26ai container, '
                           'then refresh. Uploads and previews still work; saving needs Oracle.') if not isinstance(exc, RuntimeError) else str(exc)
    return dict(database=status, ollama=ai.status(), settings=settings())


@app.get('/api/masters')
def get_masters(user=Depends(authenticated)):
    try:
        with oracle.connect() as conn:
            return oracle.masters(conn)
    except Exception:
        return {}


@app.get('/api/settings')
def get_settings(user=Depends(authenticated)): return settings()


class Settings(BaseModel):
    allow_self_approval: bool | None = None
    company_exporter: str | None = Field(default=None, max_length=100)
    default_sample_type: str | None = Field(default=None, max_length=30)


@app.put('/api/settings')
def put_settings(data: Settings, user=Depends(require('settings'))):
    changed = {k: v for k, v in data.model_dump().items() if v is not None}
    for k, v in changed.items():
        store.set_setting(k, v)
    store.audit(user['username'], 'settings_changed', 'workspace', **changed)
    return settings()


# --- uploads & drafts ---------------------------------------------------------------
def _new_order_defaults(orders):
    s = settings()
    for o in orders:
        if not o.get('exporter'):
            o['exporter'] = s['company_exporter']
            o.setdefault('provenance', {})['exporter'] = {'derivation': 'Company default (Settings)'}
        if not o.get('sample_type'):
            o['sample_type'] = s['default_sample_type']
    return orders


@app.post('/api/uploads')
def upload(file: UploadFile = File(...), user=Depends(require('upload'))):
    name = str(file.filename or '').replace('\\', '/').split('/')[-1]
    lower = name.lower()
    if not lower.endswith(('.xlsx', '.xlsm', '.pdf')):
        raise HTTPException(415, 'Upload an Excel workbook (.xlsx, .xlsm) or a PDF. Save legacy .xls files as .xlsx first.')
    content = file.file.read(12 * 1024 * 1024 + 1)
    templates = store.templates()
    if lower.endswith('.pdf'):
        sheets, scanned = pdfdocs.inspect(content)
        parsed = workbooks.parse_sheets(sheets, templates); kind = 'pdf'
        if scanned:
            parsed.setdefault('notes', []).append(f'Pages {", ".join(map(str, scanned))} have no text layer (scanned) and were skipped.')
    else:
        parsed = workbooks.parse(content, templates); kind = 'excel'
    for tid in parsed.pop('templates_used', []):
        store.template_used(tid)
    id = uuid.uuid4().hex
    suggestion = None
    if not parsed['orders']:
        for sheet in parsed['sheets']:
            if not sheet['hidden']:
                suggestion = ai.heuristic(sheet)
                if suggestion:
                    suggestion = dict(sheet=sheet['name'], definition=suggestion, source='layout analysis',
                                      explanation='Proposed from the layout. Check each highlighted cell before applying.')
                    break
    draft = dict(id=id, version=1, filename=name, kind=kind, filehash=hashlib.sha256(content).hexdigest(),
                 created_at=datetime.now().astimezone().isoformat(), uploaded_by=user['username'],
                 **parsed, suggestion=suggestion, plan=None, result=None)
    _new_order_defaults(draft['orders'])
    store.identify(draft['orders'])
    (DATA / (id + ('.pdf' if kind == 'pdf' else '.xlsx'))).write_bytes(content)
    store.save(draft, user['username'])
    store.audit(user['username'], 'upload', id, filename=name, format=draft['format'], orders=len(draft['orders']),
                sha256=draft['filehash'])
    return draft


@app.get('/api/drafts')
def drafts(user=Depends(authenticated)): return store.summaries()


@app.get('/api/drafts/{id}')
def draft(id: str, user=Depends(authenticated)): return store.get(id)


class Edit(BaseModel):
    version: int
    orders: list[dict] = Field(max_length=50)


def oracle_committed(id):
    """Oracle remains authoritative after an HTTP/network interruption."""
    try:
        with oracle.connect() as conn:
            return oracle.read_result(conn.cursor(), id)
    except (RuntimeError, oracledb.Error):
        return None


def editable(id, version):
    d = store.get(id)
    if d['version'] != version:
        raise HTTPException(409, 'Draft has changed. Reload it before continuing.')
    if d.get('result'):
        raise HTTPException(409, 'This import is already saved to Oracle.')
    if oracle_committed(id):
        raise HTTPException(409, 'This import is already saved to Oracle. Open Saved orders.')
    return d


@app.put('/api/drafts/{id}')
def edit(id: str, data: Edit, user=Depends(require('edit'))):
    with LOCK:
        d = editable(id, data.version)
        if sum(len(o.get('lines', [])) for o in data.orders) > 500:
            raise ValueError('Import exceeds 500 lines.')
        if sum(len(l.get('sizes', [])) for o in data.orders for l in o.get('lines', [])) > 5000:
            raise ValueError('Import exceeds 5,000 size rows.')
        new = store.edited_orders(d['orders'], data.orders)
        diff = store.changes(d['orders'], new)
        d['orders'] = new; d['version'] += 1; d['plan'] = None
        store.save(d, user['username'], data.version)
        if diff:
            store.audit(user['username'], 'edit', id, changes=diff)
        return d


class Mapping(BaseModel):
    version: int
    sheet: str
    header_row: int | None = Field(default=None, ge=1)
    columns: dict[str, int] | None = None
    definition: dict | None = None
    save_template: bool = False
    template_name: str | None = Field(default=None, max_length=80)


@app.post('/api/drafts/{id}/map')
def mapping(id: str, data: Mapping, user=Depends(require('edit'))):
    with LOCK:
        d = editable(id, data.version)
        sheet = next((s for s in d['sheets'] if s['name'] == data.sheet), None)
        if not sheet:
            raise ValueError('Select a worksheet.')
        definition = data.definition or dict(fields={}, table=dict(type='flat', header_row=data.header_row, columns=data.columns or {}))
        orders = workbooks.apply_template(d['sheets'], sheet, definition)
        workbooks.attach_bom(workbooks.Grid(sheet), orders)
        d['orders'] = store.identify(_new_order_defaults([workbooks.finish(o) for o in orders]))
        d['format'] = 'Reviewed layout mapping'; d['version'] += 1; d['plan'] = None
        if data.save_template:
            name = (data.template_name or '').strip() or f'{d["filename"]} · {sheet["name"]}'
            tid = store.save_template(name, workbooks.signature(sheet), workbooks.labels(sheet), definition, user['username'])
            d['format'] = f'Template: {name}'
            store.audit(user['username'], 'template_saved', tid, name=name, definition=definition)
        store.save(d, user['username'], data.version)
        store.audit(user['username'], 'mapping_applied', id, sheet=data.sheet, definition=definition)
        return d


class Suggestion(BaseModel):
    sheet: str


@app.post('/api/drafts/{id}/suggest')
def suggest(id: str, data: Suggestion, user=Depends(require('edit'))):
    d = store.get(id); sheet = next((s for s in d['sheets'] if s['name'] == data.sheet), None)
    if not sheet:
        raise ValueError('Select a worksheet.')
    return ai.suggest(sheet)


class Version(BaseModel):
    version: int


class Approval(Version):
    plan_hash: str = Field(min_length=64, max_length=64)


def _plan(d):
    m = oracle.current_mapping()
    try:
        conn = oracle.connect()
    except Exception:
        conn = None
    if conn is None:
        plan = planner.plan(SnapshotCatalog(), d['orders'], d['filehash'], m)
        plan['warnings'].insert(0, 'Oracle is not connected: this preview uses the saved database structure only. '
                                   'Existing master records cannot be checked and saving is disabled until Oracle is reachable.')
        return plan
    with conn:
        return oracle.plan(conn, d['orders'], d['filehash'], m)


@app.post('/api/drafts/{id}/preview')
def preview(id: str, data: Version, user=Depends(require('preview'))):
    with LOCK:
        d = editable(id, data.version)
        d['plan'] = _plan(d)
        d['plan']['graph'] = readback.planned(d['plan'], oracle.current_mapping())
        # Preview itself is a revision, preventing an earlier accept from approving a replaced preview.
        old = d['version']; d['version'] += 1; store.save(d, user['username'], old)
        store.audit(user['username'], 'preview', id, errors=len(d['plan']['errors']), warnings=len(d['plan']['warnings']),
                    live=d['plan']['live'], plan_hash=d['plan']['hash'])
        return d


@app.post('/api/drafts/{id}/accept')
def accept(id: str, data: Approval, user=Depends(require('approve'))):
    with LOCK:
        d = store.get(id)
        previous = oracle_committed(id)
        if previous:
            return previous
        if d['version'] != data.version or not d.get('plan') or d['plan']['hash'] != data.plan_hash:
            raise HTTPException(409, 'Approval requires the exact current preview. Prepare the preview again.')
        if not d['plan'].get('live'):
            raise HTTPException(409, 'This preview was made without Oracle. Connect Oracle and prepare the preview again before approving.')
        if d['plan']['errors']:
            raise HTTPException(409, 'Resolve all questions before approving.')
        if d.get('uploaded_by') == user['username'] and not settings()['allow_self_approval']:
            raise HTTPException(403, 'A different approver must approve this upload (four-eyes rule is on).')
        try:
            result = oracle.commit(d, data.plan_hash, user['username'])
        except Exception as exc:
            store.audit(user['username'], 'approve_failed', id, error=str(exc)[:500])
            raise
        d['result'] = result; d['version'] += 1
        store.save(d, user['username'], data.version)
        store.audit(user['username'], 'approved_saved', id, orders=result.get('orders'), rows=len(result.get('rows', [])),
                    plan_hash=data.plan_hash)
        return result


# --- history, read-back graph ---------------------------------------------------------
@app.get('/api/history')
def history(user=Depends(authenticated)):
    records = oracle.history()
    for record in records:
        try:
            d = store.get(record['job_id'])
            record.update(filename=d['filename'], format=d['format'], orders=[o.get('order_ref') for o in d['orders']],
                          styles=[o.get('style') for o in d['orders']], status='Saved')
        except KeyError:
            record['status'] = 'Saved'
    return records


@app.get('/api/history/{id}')
def history_result(id: str, user=Depends(authenticated)):
    with oracle.connect() as conn:
        result = oracle.read_result(conn.cursor(), id)
        if not result:
            raise HTTPException(404, 'Import receipt not found.')
        return result


@app.get('/api/history/{id}/graph')
def history_graph(id: str, user=Depends(authenticated)):
    m = oracle.current_mapping()
    with oracle.connect() as conn:
        result = oracle.read_result(conn.cursor(), id)
        if not result:
            raise HTTPException(404, 'Import receipt not found.')
        return readback.graph(result, m, lambda table, pk, ids: oracle.live_rows(conn, table, pk, ids, m))


# --- mapping registry, templates, audit --------------------------------------------------
def _discovery():
    path = DATA / 'schema_discovery.json'
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def _type(meta):
    if not meta:
        return None
    if meta['type'] != 'NUMBER':
        return f'{meta["type"]}({meta["length"]})' if meta['type'] in ('VARCHAR2', 'CHAR') else meta['type']
    if meta['precision']:
        return f'NUMBER({meta["precision"]}{"," + str(meta["scale"]) if meta["scale"] else ""})'
    return 'NUMBER'


@app.get('/api/mapping')
def get_mapping(user=Depends(authenticated)):
    m = oracle.current_mapping(); live = None; insertable = []
    try:
        with oracle.connect() as conn:
            cat = OracleCatalog(conn); live = cat.columns(registry.referenced_tables(m))
            insertable = sorted(cat.insertable(registry.referenced_tables(m)))
    except Exception:
        live = None
    columns = live or SnapshotCatalog().columns(registry.referenced_tables(m))
    discovery = _discovery() or {}
    evidence = discovery.get('columns', {})
    tables = []
    for name, spec in m['tables'].items():
        cols = []
        for col, c in spec['columns'].items():
            meta = columns.get(name, {}).get(col)
            ev = evidence.get(f'{name}.{col}', {})
            master = m['masters'].get(c.get('master')) if c.get('master') else None
            cols.append(dict(column=col, label=c.get('label'), status=c['status'], fixed=bool(c.get('key') or 'parent' in c),
                             source=c.get('field') or c.get('value') or c.get('template') or c.get('copy') or
                             ('generated key' if c.get('key') else f'link to {c.get("parent")}'),
                             master=master and f'{master["table"]}.{master["key"]}', master_available=bool(master and master['table'] in columns),
                             exists=meta is not None, type=_type(meta),
                             nullable=meta and meta['nullable'], evidence=c.get('evidence'), discovery=ev))
        tables.append(dict(table=name, business=spec['business'], screen=spec.get('screen'), level=spec['level'],
                           parent=spec.get('parent'), status=spec['status'], visible=name in columns,
                           insertable=name in insertable if live is not None else None, columns=cols))
    return dict(live=live is not None, tables=tables, masters=m['masters'], links=m.get('links', []),
                overrides=store.mapping_overrides(), discovery=dict(available=bool(discovery), generated_at=discovery.get('generated_at')))


@app.get('/api/semantic-model')
def semantic_model(user=Depends(authenticated)):
    """Read-only business model (entities, verified attributes, join paths) for reporting and future Q&A."""
    m = oracle.current_mapping()
    try:
        with oracle.connect() as conn:
            cols = OracleCatalog(conn).columns(registry.referenced_tables(m))
    except Exception:
        cols = SnapshotCatalog().columns(registry.referenced_tables(m))
    return semantic.model(m, cols, _discovery())


class MappingStatus(BaseModel):
    table: str
    column: str | None = None
    status: str = Field(pattern='^(verified|proposed)$')
    note: str | None = Field(default='', max_length=500)


@app.post('/api/mapping/status')
def set_mapping_status(data: MappingStatus, user=Depends(require('mapping'))):
    m = registry.effective()
    if data.table not in m['tables'] or (data.column and data.column not in m['tables'][data.table]['columns']):
        raise HTTPException(404, 'Unknown table or column.')
    if data.column and (m['tables'][data.table]['columns'][data.column].get('key') or 'parent' in m['tables'][data.table]['columns'][data.column]):
        raise HTTPException(409, 'Keys and parent links are fixed by the registry.')
    store.set_mapping_status(data.table, data.column, data.status, user['username'], data.note or '')
    store.audit(user['username'], 'mapping_status', f'{data.table}.{data.column or "*"}', status=data.status, note=data.note)
    return get_mapping(user)


@app.get('/api/templates')
def get_templates(user=Depends(authenticated)): return store.templates(active_only=False)


class Active(BaseModel):
    active: bool


@app.post('/api/templates/{tid}/active')
def template_active(tid: str, data: Active, user=Depends(require('templates'))):
    store.set_template_active(tid, data.active)
    store.audit(user['username'], 'template_active', tid, active=data.active)
    return store.templates(active_only=False)


@app.get('/api/audit')
def audit(target: str | None = None, user=Depends(require('audit'))): return store.audit_events(300, target)


# --- data intelligence: knowledge catalog, explorer, AI chat ---------------------------------------
IDENT = re.compile(r'^[A-Z][A-Z0-9_$#]{0,127}$')
_LEX = {}


def knowledge_base():
    return insight.knowledge(oracle.connect, oracle.current_mapping())


def lexicon(kb):
    if _LEX.get('kb') is not kb:
        _LEX.update(kb=kb, lex=understand.lexicon(kb))
    return _LEX['lex']


@contextmanager
def query_sources():
    """{'db': (read-only executor, dialect) or None, 'local': uploaded-files source}. Tests replace this."""
    conn = None
    try:
        conn = oracle.connect()
    except Exception:
        conn = None
    local = insight.uploads_db()
    try:
        yield {'db': (insight.OracleExecutor(conn), Dialect('oracle', OWNER)) if conn else None,
               'local': (insight.SqliteExecutor(local), Dialect('sqlite'))}
    finally:
        local.close()
        if conn:
            conn.close()


def _require_db(sources):
    if not sources.get('db'):
        raise QueryError('Oracle is not connected, so database questions cannot be answered right now. '
                         'Start Docker Desktop and the oracle26ai container. Questions about uploaded files still work.')
    return sources['db']


def _ensure_values(kb, sources):
    if sources.get('db') and kb.live and not store.value_stats():
        execute, dialect = sources['db']
        insight.refresh_values(kb, execute, dialect)


def _table_meta(kb, name):
    t = kb.table(name)
    if not t:
        raise HTTPException(404, 'Table not available.')
    return t


def _neighbours(kb, name):
    out = []
    for n in kb.neighbours(name):
        t = kb.table(n['table'])
        out.append(dict(table=n['table'], business=t['business'], kind=t['kind'], rows=t.get('rows'), direction=n['direction'],
                        label=n['join']['label'], status=n['join']['status'], join=n['join']['id'],
                        on=' AND '.join(f'{n["join"]["child"]}.{c} = {n["join"]["parent"]}.{p}' for c, p in n['join']['pairs'])))
    return sorted(out, key=lambda x: (x['status'] != 'verified', x['kind'] == 'master', x['table']))


@app.get('/api/knowledge')
def knowledge_summary(user=Depends(authenticated)):
    kb = knowledge_base()
    tables = [dict(table=n, business=t['business'], kind=t['kind'], rows=t.get('rows'), columns=len(t['columns']), status=t['status'],
                   description=t['description'], synonyms=t['synonyms'], source=t.get('source', 'oracle'),
                   links=sum(1 for j in kb.joins if n in (j['child'], j['parent'])))
              for n, t in kb.tables.items()]
    return dict(summary=kb.summary(), tables=tables, values=store.value_stats(), examples=len(store.examples()))


@app.get('/api/knowledge/tables/{table}')
def knowledge_table(table: str, user=Depends(authenticated)):
    kb = knowledge_base(); t = _table_meta(kb, table.upper())
    return dict(t, neighbours=_neighbours(kb, t['name']), columns=list(t['columns'].values()))


@app.get('/api/knowledge/neighbours/{table}')
def knowledge_neighbours(table: str, user=Depends(authenticated)):
    kb = knowledge_base(); t = _table_meta(kb, table.upper())
    return dict(table=t['name'], business=t['business'], kind=t['kind'], neighbours=_neighbours(kb, t['name']))


@app.get('/api/knowledge/joins')
def knowledge_joins(user=Depends(authenticated)):
    return knowledge_base().data['joins']


class KnowledgeEdit(BaseModel):
    object: str = Field(min_length=3, max_length=300)
    business: str | None = Field(default=None, max_length=80)
    description: str | None = Field(default=None, max_length=500)
    synonyms: list[str] | None = Field(default=None, max_length=30)
    tags: list[str] | None = Field(default=None, max_length=20)
    role: str | None = Field(default=None, pattern='^(label|measure|date|reference|attribute|key|system)$')
    status: str | None = Field(default=None, pattern='^(verified|proposed|rejected|unreviewed)$')
    searchable: bool | None = None


@app.put('/api/knowledge/objects')
def knowledge_edit(data: KnowledgeEdit, user=Depends(require('mapping'))):
    kb = knowledge_base(); obj = data.object
    if obj.startswith('join:'):
        if not any(j['id'] == obj[5:] for j in kb.data['joins']) or data.status not in ('verified', 'proposed', 'rejected'):
            raise HTTPException(404, 'Unknown relationship or status.')
    else:
        t, _, c = obj.partition('.')
        if not kb.table(t) or (c and not kb.column(obj)):
            raise HTTPException(404, 'Unknown table or column.')
    change = {k: v for k, v in data.model_dump().items() if v is not None and k != 'object'}
    merged = store.set_knowledge_override(obj, change, user['username'])
    insight.invalidate()
    store.audit(user['username'], 'knowledge_edit', obj, **change)
    return dict(object=obj, override=merged)


@app.post('/api/knowledge/refresh')
def knowledge_refresh(user=Depends(require('mapping'))):
    try:
        phys = insight.refresh_physical(oracle.connect)
    except Exception as exc:
        raise HTTPException(503, f'Could not read the Oracle data dictionary: {str(exc)[:200]}')
    kb = knowledge_base()
    with query_sources() as sources:
        execute, dialect = _require_db(sources)
        values = insight.refresh_values(kb, execute, dialect)
    store.audit(user['username'], 'knowledge_refresh', OWNER, tables=len(phys['physical']), **values)
    return dict(tables=len(phys['physical']), values=values, summary=kb.summary())


class Ask(BaseModel):
    question: str = Field(min_length=1, max_length=500)


class Run(BaseModel):
    intent: dict
    question: str | None = Field(default='', max_length=500)


def _answer(fn, question, user):
    kb = knowledge_base()
    cid = uuid.uuid4().hex
    with query_sources() as sources:
        try:
            if sources.get('db'):
                _ensure_values(kb, sources)
            result = fn(kb, sources)
            result.update(kind='answer')
        except understand.NotUnderstood as exc:
            result = dict(kind='clarify', question=question, message=str(exc), suggestions=exc.suggestions +
                          ['How many orders for NEXT?', 'Size wise quantity for order PK/26-27/S/9001', 'Buyer wise order quantity this year'])
        except QueryError as exc:
            result = dict(kind='refused', question=question, message=str(exc))
    result['id'] = cid; result['live'] = kb.live
    store.log_chat(cid, user['username'], question, result)
    return result


@app.post('/api/chat/ask')
def chat_ask(data: Ask, user=Depends(require('view'))):
    q = data.question.strip()

    def go(kb, sources):
        db = sources.get('db')
        safe = dict(sources, db=db or (_offline, Dialect('oracle', OWNER)))
        live = insight.live_finder(kb, *db) if db else None
        return engine.ask(q, kb, safe, finder=insight.finder(kb), live_finder=live, examples=store.examples(),
                          ai_chat=ai.json_chat if ai.available() else None, lex=lexicon(kb))
    return _answer(go, q, user)


def _offline(sql, binds, n):
    raise QueryError('Oracle is not connected, so database questions cannot be answered right now. '
                     'Start Docker Desktop and the oracle26ai container. Questions about uploaded files still work.')


@app.post('/api/chat/run')
def chat_run(data: Run, user=Depends(require('view'))):
    q = (data.question or '').strip()

    def go(kb, sources):
        safe = dict(sources, db=sources.get('db') or (_offline, Dialect('oracle', OWNER)))
        return engine.run(data.intent, kb, safe, question=q, source='edited', confidence=dict(score=0.9, reasons=['Adjusted by you.']))
    return _answer(go, q, user)


class Preview(BaseModel):
    table: str = Field(min_length=1, max_length=128)
    intent: dict | None = None
    limit: int = Field(default=20, ge=1, le=100)


@app.post('/api/chat/preview')
def chat_preview(data: Preview, user=Depends(require('view'))):
    """Rows of one table, scoped to the current answer's filters when the table is linked to them."""
    kb = knowledge_base(); t = _table_meta(kb, data.table.upper())
    cols = list(dict.fromkeys(t['display'] + [c for c, m in t['columns'].items() if m['role'] != 'system']))[:40]
    filters = []; scoped = False
    for f in (data.intent or {}).get('filters') or []:
        ftable = str(f.get('column', '')).split('.')[0]
        if kb.path(t['name'], ftable) is not None:
            filters.append(f); scoped = True
    intent = dict(base=t['name'], columns=[f'{t["name"]}.{c}' for c in cols], filters=filters, limit=data.limit)
    with query_sources() as sources:
        safe = dict(sources, db=sources.get('db') or (_offline, Dialect('oracle', OWNER)))
        try:
            execute, dialect = engine.pick(kb, intent, safe)
            comp = compile_intent(intent, kb, dialect)
            raw = execute(comp['sql'], comp['binds'], data.limit)
        except QueryError as exc:
            raise HTTPException(409, str(exc))
    rows = [[engine.cell(v) for v in r] for r in raw]
    return dict(table=t['name'], business=t['business'], description=t['description'], kind=t['kind'], rows_total=t.get('rows'),
                scoped=scoped, scope=[engine.describe_filter(f, kb) for f in filters],
                columns=[dict(key=c['key'], column=c['ref'].split('.')[1], ref=c['ref'], label=c['label'], role=(kb.column(c['ref']) or {}).get('role'),
                              type=(kb.column(c['ref']) or {}).get('type'), references=(kb.column(c['ref']) or {}).get('references'),
                              hidden=c.get('hidden', False)) for c in comp['columns']],
                rows=rows, neighbours=_neighbours(kb, t['name']))


class Feedback(BaseModel):
    correct: bool


@app.post('/api/chat/{cid}/feedback')
def chat_feedback(cid: str, data: Feedback, user=Depends(require('view'))):
    entry = store.chat_entry(cid)
    store.chat_feedback(cid, 'correct' if data.correct else 'wrong')
    saved = None
    p = entry['payload']
    if data.correct and p.get('kind') == 'answer' and p.get('masked') and 'preview' in PERMISSIONS[user['role']]:
        saved = store.save_example(p['masked'], p['question'], engine.generalize(p['intent'], p.get('linked_values') or []), user['username'])
    store.audit(user['username'], 'chat_feedback', cid, correct=data.correct, question=entry['question'], example=saved)
    return dict(ok=True, example=saved)


@app.get('/api/chat/history')
def chat_history(user=Depends(authenticated)): return store.chat_history(user['username'])


@app.get('/api/chat/history/{cid}')
def chat_history_item(cid: str, user=Depends(authenticated)):
    entry = store.chat_entry(cid)
    if entry['username'] != user['username'] and user['role'] != 'admin':
        raise HTTPException(404, 'Not found.')
    return entry['payload'] | dict(feedback=entry['feedback'])


@app.get('/api/chat/examples')
def chat_examples(user=Depends(authenticated)): return store.examples()


@app.delete('/api/chat/examples/{eid}')
def chat_example_delete(eid: int, user=Depends(require('mapping'))):
    store.delete_example(eid); store.audit(user['username'], 'example_deleted', str(eid))
    return {'ok': True}


# --- read-only database explorer (same read-only executor and catalog as Ask AI) ---------------
def _db(sources):
    if not sources.get('db'):
        raise HTTPException(503, 'Oracle is not connected. Start Docker Desktop and the oracle26ai container, then refresh.')
    return sources['db']


def _oracle_tables(kb):
    return {n: t for n, t in kb.tables.items() if t.get('source') != 'local'}


BIG_ROWS = 1_000_000


def _count(execute, dialect, table, meta, cond='', binds=None):
    """Exact COUNT(*) unless dictionary statistics say the table is very large (then approximate)."""
    stats = meta.get('rows')
    if stats is not None and stats > BIG_ROWS and not cond:
        return stats, True
    try:
        return execute(f'SELECT COUNT(*) FROM {dialect.table(table)}{cond}', binds or {}, 1)[0][0], False
    except Exception:
        return stats, True


@app.get('/api/explorer/tables')
def explorer_tables(user=Depends(authenticated)):
    """Table list. Exact row counts for normal tables; dictionary statistics (marked approximate) for very
    large ones or very large schemas, so the list stays fast on a big database."""
    kb = knowledge_base(); tables = _oracle_tables(kb)
    counts = {}
    with query_sources() as sources:
        execute, dialect = _db(sources)
        for t, m in tables.items():
            counts[t] = _count(execute, dialect, t, m) if len(tables) <= 300 else (m.get('rows'), True)
    return [dict(table=t, columns=len(m['columns']), rows=counts[t][0], approx=counts[t][1], business=m['business'],
                 kind=m['kind'], description=m['description'], key=m.get('key'),
                 links=sum(1 for j in kb.joins if t in (j['child'], j['parent']))) for t, m in tables.items()]


def _decode(kb, execute, dialect, table, rows):
    """{column: {id: label}} for reference columns on this page (master labels instead of raw IDs)."""
    out = {}
    for col, m in kb.table(table)['columns'].items():
        master = kb.table(m.get('references') or '')
        if not master or not master.get('label') or not master.get('key'):
            continue
        ids = sorted({r[col] for r in rows if isinstance(r.get(col), (int, float))})[:500]
        if not ids:
            continue
        binds = {f'i{n}': v for n, v in enumerate(ids)}
        try:
            found = execute(f'SELECT {master["key"]}, {master["label"]} FROM {dialect.table(master["name"])} WHERE {master["key"]} IN ({",".join(":" + k for k in binds)})', binds, 500)
        except Exception:
            continue
        out[col] = {str(engine.cell(k)): v for k, v in found}
    return out


def _rows(execute, sql, binds, names, n):
    return [{k: engine.cell(v) for k, v in zip(names, r)} for r in execute(sql, binds, n)]


@app.get('/api/explorer/tables/{table}')
def explorer_rows(table: str, column: str | None = None, value: str | None = Query(default=None, max_length=100),
                  limit: int = Query(default=50, ge=1, le=200), offset: int = Query(default=0, ge=0, le=100000),
                  after: str | None = Query(default=None, max_length=40), user=Depends(authenticated)):
    table = table.upper(); kb = knowledge_base(); meta = _oracle_tables(kb).get(table)
    if not IDENT.match(table) or not meta:
        raise HTTPException(404, 'Table not available.')
    names = list(meta['columns'])
    binds = {}; where = []
    with query_sources() as sources:
        execute, dialect = _db(sources)
        if column and value:
            column = column.upper()
            if column not in names:
                raise HTTPException(404, 'Column not available.')
            where.append(f'{dialect.text(column)} LIKE :v'); binds['v'] = f'%{value.upper()}%'
        key = meta.get('key') if meta.get('key') in names and meta['columns'][meta['key']]['type'] == 'NUMBER' else None
        cond = (' WHERE ' + ' AND '.join(where)) if where else ''
        total, approx = _count(execute, dialect, table, meta, cond, binds)
        select = f'SELECT {", ".join(names)} FROM {dialect.table(table)}'
        if key:   # keyset paging: constant cost on big tables
            if after not in (None, ''):
                where.append(f'{key} < :after'); binds['after'] = int(after)
            sql = dialect.limit(select + (' WHERE ' + ' AND '.join(where) if where else '') + f' ORDER BY {key} DESC', limit)
        else:
            sql = dialect.page(select + cond + f' ORDER BY {names[0]} DESC', limit, offset)
        rows = _rows(execute, sql, binds, names, limit)
        decoded = _decode(kb, execute, dialect, table, rows)
    return dict(table=table, business=meta['business'], description=meta['description'], key=key,
                columns=[dict(name=c, type=m['type'], label=m['business'], role=m['role'], references=m.get('references')) for c, m in meta['columns'].items()],
                total=total, approx=approx, rows=rows, offset=offset, limit=limit, decoded=decoded,
                next_after=rows[-1][key] if key and len(rows) == limit else None)


@app.get('/api/explorer/related/{table}/{pk}')
def explorer_related(table: str, pk: int, user=Depends(authenticated)):
    """Records linked to one record through the relationship map (parents and children)."""
    kb = knowledge_base(); t = _table_meta(kb, table.upper())
    if not t.get('key') or t.get('source') == 'local':
        raise HTTPException(409, 'This table has no single record ID.')
    out = []
    with query_sources() as sources:
        execute, dialect = _db(sources)
        names = list(t['columns'])
        found = _rows(execute, dialect.limit(f'SELECT {", ".join(names)} FROM {dialect.table(t["name"])} WHERE {t["key"]} = :id', 1), {'id': pk}, names, 1)
        if not found:
            raise HTTPException(404, 'Record not found.')
        record = found[0]
        for n in _neighbours(kb, t['name']):
            j = next(x for x in kb.joins if x['id'] == n['join'])
            other = kb.table(n['table'])
            conds = [(p, record.get(ch)) for ch, p in j['pairs']] if n['direction'] == 'up' else [(ch, record.get(p)) for ch, p in j['pairs']]
            if any(v is None for _, v in conds):
                continue
            binds = {f'v{i}': v for i, (_, v) in enumerate(conds)}
            where = ' AND '.join(f'{col} = :v{i}' for i, (col, _) in enumerate(conds))
            cols = list(dict.fromkeys([c for c in other['display']] + [c for c, m in other['columns'].items() if m['role'] not in ('system', 'key')]))[:24]
            if other.get('key'):
                cols.append(other['key'])
            cols += [ch for ch, _ in j['pairs'] if ch not in cols and ch in other['columns']] + [p for _, p in j['pairs'] if p not in cols and p in other['columns']]
            try:
                rows = _rows(execute, dialect.limit(f'SELECT {", ".join(cols)} FROM {dialect.table(other["name"])} WHERE {where}', 21), binds, cols, 21)
            except Exception:
                continue
            if rows:
                out.append(dict(n, rows=rows[:20], more=len(rows) > 20, key=other.get('key'), label=other.get('label'),
                                columns=[dict(name=c, label=other['columns'][c]['business'], role=other['columns'][c]['role']) for c in cols],
                                decoded=_decode(kb, execute, dialect, other['name'], rows[:20])))
        decoded = _decode(kb, execute, dialect, t['name'], [record])
    return dict(table=t['name'], business=t['business'], record=record, related=out, decoded=decoded)


# --- assistant --------------------------------------------------------------------------
class Question(BaseModel):
    question: str = Field(min_length=1, max_length=1000)


@app.post('/api/drafts/{id}/assist')
def assist(id: str, data: Question, user=Depends(authenticated)):
    d = store.get(id)
    plan = d.get('plan') or {}
    facts = {'filename': d['filename'], 'orders': [{k: o.get(k) for k in ('style', 'buyer', 'po_number', 'quantity_basis', 'warnings')} |
             {'lines': [{'color': l.get('color'), 'sizes': [(s.get('size'), s.get('quantity')) for s in l.get('sizes', [])]} for l in o.get('lines', [])],
              'fabrics': len(o.get('fabrics', [])), 'accessories': len(o.get('accessories', []))} for o in d['orders']],
             'preview': {k: plan.get(k) for k in ('errors', 'warnings', 'totals', 'reference_only')}}
    if len(json.dumps(facts, default=str)) > 48000:
        raise ValueError('This draft is too large for the local helper. Ask about a smaller import.')
    answer = ai.assist(facts, data.question)
    return {'answer': answer or 'The local AI helper is offline. Everything else still works: the questions panel lists each issue with the Excel cell it came from.'}


@app.get('/')
def home(): return FileResponse(ROOT / 'frontend' / 'index.html')

app.mount('/static', StaticFiles(directory=ROOT / 'frontend', check_dir=False), name='static')
