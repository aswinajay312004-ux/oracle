from __future__ import annotations
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime
import hashlib
import hmac
import json
import os
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
from . import local_store as store, oracle_store as oracle, workbooks, readers, ai, matcher, fields as F
from .config import DATA, ROOT, OWNER, setting
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
    start_value_index()   # background: the server answers at once, the first question does not pay for the build
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


@app.exception_handler(readers.Unsupported)
async def unsupported(request, exc): return JSONResponse({'detail': str(exc)}, status_code=415)


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
            status.update(connected=True, tables=len(cat.visible_tables()), mapped_tables=len(visible), insertable=sorted(cat.insertable(registry.referenced_tables(m))),
                          version=conn.version, dsn=conn.dsn)
    except Exception as exc:
        status['error'] = ('Local Oracle is not reachable. Start Docker Desktop and the oracle26ai container, '
                           'then refresh. Uploads and previews still work; saving needs Oracle.') if not isinstance(exc, RuntimeError) else str(exc)
    return dict(database=status, ollama=ai.status(), settings=settings(), readers=_reader_checks())


_reader_check = {}


def _reader_checks():
    """Versions that matter for reading uploads safely (Python, Expat, reader packages); computed once."""
    if not _reader_check:
        try:
            _reader_check.update(readers.selfcheck())
        except Exception as exc:          # never let a diagnostic break the health page
            _reader_check.update(ok=False, checks=[dict(name='Readers', ok=False, message=str(exc)[:200])])
    return _reader_check


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


def current_fields():
    """Effective business fields: defaults (backend/fields.py) + the administrator's saved changes."""
    return F.Fields(F.effective(store.field_settings()))


def master_labels():
    """Known buyer / colour / size names from the Oracle masters, so the matcher can recognise values.
    Empty when Oracle is not reachable: matching then relies on labels and value shapes only. Only these three
    masters are read, each on its own, so a large style or party master never hides the buyer names."""
    try:
        with oracle.connect() as conn:
            found = oracle.masters(conn, roles=('buyer', 'color', 'size')) or {}
    except Exception:
        return {}
    return {role: [m.get('label') for m in found.get(role) or [] if not m.get('cancelled')] for role in ('buyer', 'color', 'size')}


def _suggestion(parsed, fields):
    """Matcher proposal for an unknown layout (or a sheet whose saved template was refused). No AI here:
    uploads stay fast and work offline; the person can ask the local AI from the mapping screen."""
    refused = [r['sheet'] for r in parsed.get('templates_refused') or []]
    sheets = sorted((s for s in parsed['sheets'] if not s['hidden']), key=lambda s: s['name'] not in refused)
    masters = master_labels(); first = None
    for sheet in sheets:
        try:
            s = ai.suggest(sheet, fields, masters, use_ai=False)
        except ValueError:
            continue
        if s['definition'].get('table'):
            return s
        first = first or s
    return first


@app.post('/api/uploads')
def upload(file: UploadFile = File(...), user=Depends(require('upload'))):
    name = str(file.filename or '').replace('\\', '/').split('/')[-1]
    lower = name.lower()
    if not lower.endswith(readers.ACCEPT):
        raise HTTPException(415, 'Upload the buyer Excel file (.xlsx, .xlsm, .xls, .xlsb, .ods, .csv, Excel XML/HTML/web-page export) or JSON data.')
    content = file.file.read(readers.MAX_BYTES + 1)
    kind = readers.sniff(content, name)   # rejects an empty, oversized or non-spreadsheet file (415) before parsing
    fields = current_fields()
    parsed = workbooks.parse(content, store.templates(), name, fields)
    used = parsed.pop('templates_used', [])
    for tid in used:
        store.template_used(tid)
    id = uuid.uuid4().hex
    suggestion = _suggestion(parsed, fields) if not parsed['orders'] or parsed.get('templates_refused') else None
    template = next((dict(id=t['id'], name=t['name'], version=t['version']) for t in store.templates() if t['id'] in used), None)
    draft = dict(id=id, version=1, filename=name, kind=kind, filehash=hashlib.sha256(content).hexdigest(),
                 created_at=datetime.now().astimezone().isoformat(), uploaded_by=user['username'],
                 **parsed, suggestion=suggestion, template=template, plan=None, result=None)
    _new_order_defaults(draft['orders'])
    store.identify(draft['orders'])
    (DATA / (id + '.upload')).write_bytes(content)
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
        _learn_unit(d, user)
        return d


def _decided_unit(orders):
    """(unit, basis) when every order of the draft has the same complete unit decision, else (None, None)."""
    def whole(v):
        try:
            n = float(str(v).strip())
            return int(n) if n.is_integer() else None
        except (TypeError, ValueError):
            return None
    units = {(o.get('qty_unit'), 1 if o.get('qty_unit') == 'PCS' else whole(o.get('pieces_per_unit'))) for o in orders}
    bases = {o.get('quantity_basis') for o in orders}
    unit = None
    if len(units) == 1:
        u, per = units.pop()
        if u == 'PCS' or (u == 'PACK' and per and per >= 2):
            unit = dict(qty_unit=u, pieces_per_unit=per)
    basis = bases.pop() if len(bases) == 1 and bases <= {'sample', 'order_demo'} else None
    return unit, basis


def _learn_unit(d, user):
    """A unit answered on the review screen of a draft read with a template is remembered by that template
    when the template did not know it yet (a saved decision is never overwritten silently)."""
    tid = (d.get('template') or {}).get('id')
    if not tid or not d.get('orders'):
        return
    unit, basis = _decided_unit(d['orders'])
    learned = store.fill_template_unit(tid, unit, basis)
    if learned:
        store.audit(user['username'], 'template_unit_learned', tid, draft=d['id'], **learned)


class Mapping(BaseModel):
    version: int
    sheet: str
    header_row: int | None = Field(default=None, ge=1)
    columns: dict[str, int] | None = None
    definition: dict | None = None
    save_template: bool = False
    template_name: str | None = Field(default=None, max_length=80)
    qty_unit: str | None = Field(default=None, pattern='^(PCS|PACK|)$')
    pieces_per_unit: int | None = Field(default=None, ge=1, le=10000)
    quantity_basis: str | None = Field(default=None, pattern='^(sample|order_demo)$')


def _refused(rejected, fields):
    """409 body for a wrong mapping: one message naming each wrong field, plus the list for the screen."""
    text = ' '.join(f'{fields.label(r["field"]) if r["field"] in fields else r["field"]}: {r["reason"]}.' for r in rejected)
    return JSONResponse(status_code=409, content={'detail': f'This layout does not fit the file. {text}',
                                                  'rejected': [dict(r, source='person') for r in rejected]})


@app.post('/api/drafts/{id}/map')
def mapping(id: str, data: Mapping, user=Depends(require('edit'))):
    with LOCK:
        d = editable(id, data.version)
        sheet = next((s for s in d['sheets'] if s['name'] == data.sheet), None)
        if not sheet:
            raise ValueError('Select a worksheet.')
        fields = current_fields()
        definition = data.definition or dict(fields={}, table=dict(type='flat', header_row=data.header_row, columns=data.columns or {}))
        if data.qty_unit == 'PACK' and data.pieces_per_unit is not None and data.pieces_per_unit < 2:
            raise HTTPException(409, 'A pack holds at least 2 pieces. How many pieces are in one pack?')
        _, rejected = matcher.check(sheet, definition, fields, master_labels())
        if rejected:
            return _refused(rejected, fields)
        orders = workbooks.apply_template(d['sheets'], sheet, definition, fields)
        workbooks.attach_bom(workbooks.Grid(sheet, fields), orders)
        for o in orders:
            _confirm_unit(o, data)
        d['orders'] = store.identify(_new_order_defaults([workbooks.finish(o) for o in orders]))
        d['format'] = 'Reviewed layout mapping'; d['version'] += 1; d['plan'] = None; d['template'] = None
        if data.save_template:
            name = (data.template_name or '').strip() or f'{d["filename"]} · {sheet["name"]}'
            unit, basis = _decided_unit(d['orders'])
            buyers = {o.get('buyer') for o in d['orders'] if o.get('buyer')}
            saved = store.save_template(name, workbooks.layout_signature(sheet, definition), workbooks.layout_labels(sheet, definition),
                                        definition, user['username'], buyer=buyers.pop() if len(buyers) == 1 else '',
                                        anchors=matcher.anchors(sheet, definition, fields), unit=unit, quantity_basis=basis)
            d['format'] = f'Template: {name}'; d['template'] = dict(id=saved['id'], name=name, version=saved['version'])
            store.audit(user['username'], 'template_saved', saved['id'], name=name, version=saved['version'], family=saved['family'],
                        definition=definition, unit=unit, quantity_basis=basis)
        store.save(d, user['username'], data.version)
        store.audit(user['username'], 'mapping_applied', id, sheet=data.sheet, definition=definition)
        return d


def _confirm_unit(o, data):
    """The unit / basis the person chose while confirming the layout (overrides what the file suggested)."""
    prov = o.setdefault('provenance', {})
    if data.qty_unit is not None:
        before = dict(prov.get('qty_unit') or {})
        o['qty_unit'] = data.qty_unit
        o['pieces_per_unit'] = 1 if data.qty_unit == 'PCS' else data.pieces_per_unit if data.qty_unit == 'PACK' else None
        if data.qty_unit:
            prov['qty_unit'] = dict(derivation='Chosen when the layout was confirmed', reason='person')
        else:   # "ask on review": keep what the file said, so the question on the review screen quotes it
            prov['qty_unit'] = {k: v for k, v in before.items() if k in ('sheet', 'cell', 'value', 'evidence', 'said')}
            prov['qty_unit'].update(derivation='Left to decide on the review screen', reason='review')
        if data.qty_unit == 'PACK' and data.pieces_per_unit:
            prov['pieces_per_unit'] = dict(derivation='Chosen when the layout was confirmed', reason='person')
    if data.quantity_basis:
        o['quantity_basis'] = data.quantity_basis
        prov['quantity_basis'] = dict(derivation='Chosen when the layout was confirmed', reason='person')
    workbooks.review_notes(o)


class Suggestion(BaseModel):
    sheet: str


@app.post('/api/drafts/{id}/suggest')
def suggest(id: str, data: Suggestion, user=Depends(require('edit'))):
    d = store.get(id); sheet = next((s for s in d['sheets'] if s['name'] == data.sheet), None)
    if not sheet:
        raise ValueError('Select a worksheet.')
    return ai.suggest(sheet, current_fields(), master_labels())


class Version(BaseModel):
    version: int


class Approval(Version):
    plan_hash: str = Field(min_length=64, max_length=64)


def _plan(d):
    m = oracle.current_mapping()
    # Fields an administrator made required (Setup → Order fields); Axpert-locked ones are always checked.
    extra = dict(required=list(current_fields().extra_required)) if current_fields().extra_required else {}
    try:
        conn = oracle.connect()
    except Exception:
        conn = None
    if conn is None:
        plan = planner.plan(SnapshotCatalog(), d['orders'], d['filehash'], m, **extra)
        plan['warnings'].insert(0, 'Oracle is not connected: this preview uses the saved database structure only. '
                                   'Existing master records cannot be checked and saving is disabled until Oracle is reachable.')
        return plan
    with conn:
        return oracle.plan(conn, d['orders'], d['filehash'], m, **extra)


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
            result = oracle.commit(d, data.plan_hash, user['username'], required=list(current_fields().extra_required))
        except Exception as exc:
            store.audit(user['username'], 'approve_failed', id, error=str(exc)[:500])
            raise
        d['result'] = result; d['version'] += 1
        clear_counts()
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
def get_templates(user=Depends(authenticated)):
    """Every saved layout version (newest first per layout); one version per layout is active."""
    return store.templates(active_only=False)


FIELD_KEYS = ('key', 'label', 'kind', 'where', 'aliases', 'default_aliases', 'locked', 'required', 'column')


def _field_view(f, saved=None):
    out = {k: f.get(k) for k in FIELD_KEYS}
    out['aliases'] = list(f['aliases']); out['default_aliases'] = list(f.get('default_aliases') or [])
    s = (saved or {}).get(f['key']) or {}
    out.update(changed_by=s.get('actor'), changed_at=s.get('at'))
    return out


@app.get('/api/fields')
def get_fields(user=Depends(authenticated)):
    """The effective order fields: defaults + saved alias / required changes."""
    saved = store.field_settings()
    return [_field_view(f, saved) for f in F.effective(saved)]


class FieldChange(BaseModel):
    required: bool | None = None
    aliases: list[str] | None = Field(default=None, max_length=F.MAX_ALIASES)


@app.put('/api/fields/{key}')
def put_field(key: str, data: FieldChange, user=Depends(require('mapping'))):
    with LOCK:
        saved = store.field_settings(); current = F.Fields(F.effective(saved)); f = current.get(key)
        if not f:
            raise HTTPException(404, 'Unknown order field.')
        if data.required is False and f['locked']:
            raise HTTPException(409, f'{f["label"]} is needed by Axpert and is always required.')
        aliases = F.clean_aliases(key, data.aliases, current) if data.aliases is not None else None
        before = list(f['aliases'])
        store.set_field_setting(key, user['username'], required=None if f['locked'] else data.required, aliases=aliases)
        saved = store.field_settings()
        after = next(x for x in F.effective(saved) if x['key'] == key)
    norm = {F.norm(a) for a in before}
    store.audit(user['username'], 'field_changed', key, required=after['required'],
                added=[a for a in after['aliases'] if F.norm(a) not in norm],
                removed=[a for a in before if F.norm(a) not in {F.norm(x) for x in after['aliases']}])
    return _field_view(after, saved)


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


# --- value index: built in the background at startup; inside a question only as a fallback -------------------
VALUES_WAIT_S = 5      # a question asked while the background build runs waits at most this long for it
VALUES_RETRY_S = 300   # after a build that failed or found no values, a question tries again only after this
_VALUES = dict(state='not started', source=None, started_at=None, finished_at=None, seconds=None, columns=None, values=None,
               error=None, at=0.0)
_values_lock = threading.Lock()
_values_idle = threading.Event(); _values_idle.set()


def value_index_status():
    """For /api/knowledge: not started | off | starting | building | ready | empty | offline | failed, with when and how many."""
    with _values_lock:
        return {k: v for k, v in _VALUES.items() if k != 'at'}


def _build_values(source, kb=None, sources=None):
    """Builds the value index once; a second caller while it runs returns at once. Never raises: a failed build
    is reported in value_index_status() and the question goes on with the live finder."""
    with _values_lock:
        if _VALUES['state'] == 'building':
            return
        _VALUES.update(state='building', source=source, started_at=datetime.now().isoformat(timespec='seconds'), finished_at=None,
                       seconds=None, error=None, at=time.time())
        _values_idle.clear()
    started = time.time(); result = dict(state='failed')
    try:
        kb = kb or knowledge_base()
        if not kb.live:
            result = dict(state='offline', error='Oracle was not reachable, so the data dictionary is the offline snapshot.')
        elif sources is not None:
            result = _fill_values(kb, sources)
        else:
            with query_sources() as own:
                result = _fill_values(kb, own)
    except Exception as exc:   # e.g. Oracle went away in the middle: the next question can try again
        result = dict(state='failed', error=str(exc)[:200])
    finally:
        with _values_lock:
            _VALUES.update(result, finished_at=datetime.now().isoformat(timespec='seconds'), seconds=round(time.time() - started, 1),
                           at=time.time())
        _values_idle.set()


def _fill_values(kb, sources):
    if not sources.get('db'):
        return dict(state='offline', error='Oracle is not connected.')
    execute, dialect = sources['db']
    r = insight.refresh_values(kb, execute, dialect)
    return dict(state='ready' if r['values'] else 'empty', columns=r['columns'], values=r['values'],
                error='; '.join(r['skipped'])[:300] or None)


def _startup_values():
    try:
        stats = store.value_stats()
        if stats:
            with _values_lock:
                _VALUES.update(state='ready', source='earlier refresh', columns=len(stats), values=sum(x['values'] for x in stats),
                               at=time.time())
            return
        _build_values('startup')
    except Exception as exc:   # the local store cannot be read: report it, questions fall back as usual
        with _values_lock:
            _VALUES.update(state='failed', source='startup', error=str(exc)[:200], at=time.time())
    finally:
        _values_idle.set()


def start_value_index():
    """Starts the background build when the value index is empty. VALUE_INDEX_AT_STARTUP=0 in .env.poppys turns
    it off (the first question then builds it). Off under pytest: a thread outliving a test would write into
    whichever data folder the next test points at; tests call _build_values themselves."""
    if setting('VALUE_INDEX_AT_STARTUP', '1').strip().lower() in ('0', 'false', 'no', 'off') or os.environ.get('PYTEST_CURRENT_TEST'):
        with _values_lock:
            if _VALUES['state'] == 'not started':
                _VALUES.update(state='off')
        return None
    with _values_lock:   # questions asked from now until the thread has looked wait for it (briefly), not build it twice
        _VALUES.update(state='starting', source='startup', at=time.time())
        _values_idle.clear()
    thread = threading.Thread(target=_startup_values, name='poppys-value-index', daemon=True)
    thread.start()
    return thread


def _ensure_values(kb, sources):
    """Value linking needs the value index. Normally the startup thread has built it. A question asked while that
    build runs waits for it at most VALUES_WAIT_S and then goes on without it (codes are still found by the live
    finder). Only when no build ran (startup build off, Oracle down at startup, index emptied) does the question
    build it itself, and after a build that failed or found nothing, at most once per VALUES_RETRY_S."""
    if not (sources.get('db') and kb.live):
        return
    if not _values_idle.is_set():
        _values_idle.wait(VALUES_WAIT_S)
        return
    if store.value_stats():
        return
    with _values_lock:
        recent_failure = _VALUES['state'] in ('failed', 'empty') and time.time() - _VALUES['at'] < VALUES_RETRY_S
    if not recent_failure:
        _build_values('first question', kb, sources)


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
    return dict(summary=kb.summary(), tables=tables, values=store.value_stats(), value_index=value_index_status(),
                examples=len(store.examples()))


@app.get('/api/knowledge/tables/{table}')
def knowledge_table(table: str, user=Depends(authenticated)):
    kb = knowledge_base(); t = _table_meta(kb, table.upper())
    return dict(t, neighbours=_neighbours(kb, t['name']), columns=list(t['columns'].values()))


@app.get('/api/knowledge/neighbours/{table}')
def knowledge_neighbours(table: str, user=Depends(authenticated)):
    kb = knowledge_base(); t = _table_meta(kb, table.upper())
    out = _neighbours(kb, t['name'])
    with query_sources() as sources:
        counted = table_counts(kb, sources, [n['table'] for n in out])
    for n in out:
        n['rows'], n['approx'] = counted.get(n['table'], (n['rows'], True))
    return dict(table=t['name'], business=t['business'], kind=t['kind'], neighbours=out)


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
            counted = table_counts(kb, sources, [n['table'] for n in result.get('graph', {}).get('nodes', [])])
            for n in result.get('graph', {}).get('nodes', []):
                n['rows'], n['approx'] = counted.get(n['table'], (None, True))
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
            total, approx = table_counts(kb, safe, [t['name']]).get(t['name'], (None, True))
        except QueryError as exc:
            raise HTTPException(409, str(exc))
    rows = [[engine.cell(v) for v in r] for r in raw]
    return dict(table=t['name'], business=t['business'], description=t['description'], kind=t['kind'], rows_total=total, rows_approx=approx,
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
BIG_ROWS = 1_000_000
COUNT_TTL = 30
_counts = {}; _counts_lock = threading.Lock()


def _count(execute, dialect, table, meta, cond='', binds=None, timeout_ms=4000):
    """(rows, approximate). Exact COUNT(*) unless dictionary statistics say the table is very large (then approximate).
    A count gets a few seconds at most: a whole-table count that runs out falls back to statistics (≈),
    a filtered one to None, so a page never waits on counting a big table. None always means 'count unavailable';
    the API says so explicitly with rows_known=false, so the page can show that instead of a number."""
    stats = meta.get('rows')
    if stats is not None and stats > BIG_ROWS and not cond:
        return stats, True
    try:
        run = execute.limited(timeout_ms) if hasattr(execute, 'limited') else execute   # Oracle executor: tighter time limit
        return run(f'SELECT COUNT(*) FROM {dialect.table(table)}{cond}', binds or {}, 1)[0][0], False
    except Exception:
        return (None if cond else stats), True


def table_counts(kb, sources, tables, budget_ms=8000):
    """{table: (rows, approximate)} shared by Ask AI, the explorer and the data dictionary. Exact counts
    (cached 30 s, cleared after every save) so a number on screen never silently disagrees with Oracle;
    very large tables use statistics and are shown with ≈. All counts of one call share a time budget;
    tables left when it runs out show statistics (≈) and are counted on a later call."""
    out = {}; now = time.time(); deadline = now + budget_ms / 1000
    for t in dict.fromkeys(tables):
        meta = kb.table(t)
        if not meta:
            continue
        with _counts_lock:
            hit = _counts.get(t)
        if hit and now - hit[0] < COUNT_TTL:
            out[t] = hit[1:]; continue
        src = sources.get('local') if meta.get('source') == 'local' else sources.get('db')
        if not src:
            out[t] = (meta.get('rows'), True); continue
        left = (deadline - time.time()) * 1000
        if meta.get('source') != 'local' and left < 200:
            out[t] = (meta.get('rows'), True); continue
        out[t] = _count(*src, t, meta, timeout_ms=int(min(4000, max(left, 200))))
        if meta.get('source') != 'local':   # uploaded-file table is in memory: always counted fresh
            with _counts_lock:
                _counts[t] = (now, *out[t])
    return out


FILTER_COUNT_TTL = 300   # a filtered count is reused by the 'Load more' pages of the same filter for this long
_filter_counts = {}


def _filtered_count(execute, dialect, table, meta, cond, binds, first):
    """The filtered COUNT(*) is made on the first page and kept, also when it ran out of time (None), so each
    'Load more' page does not pay up to 4 s again for a count that already timed out. A later page counts only
    when nothing is kept (expired, or the server restarted). Cleared, like the table counts, after every save."""
    key = (table, cond, tuple(sorted((k, str(v)) for k, v in (binds or {}).items())))
    now = time.time()
    with _counts_lock:
        hit = _filter_counts.get(key)
        if hit and now - hit[0] < FILTER_COUNT_TTL and not first:
            return hit[1:]
    out = _count(execute, dialect, table, meta, cond, binds)
    with _counts_lock:
        if len(_filter_counts) > 500:   # a long session of different filters: keep only the fresh ones
            for k in [k for k, v in _filter_counts.items() if now - v[0] >= FILTER_COUNT_TTL]:
                del _filter_counts[k]
        _filter_counts[key] = (now, *out)
    return out


def clear_counts():
    with _counts_lock:
        _counts.clear(); _filter_counts.clear()


def _db(sources):
    if not sources.get('db'):
        raise HTTPException(503, 'Oracle is not connected. Start Docker Desktop and the oracle26ai container, then refresh.')
    return sources['db']


def _oracle_tables(kb):
    return {n: t for n, t in kb.tables.items() if t.get('source') != 'local'}


@app.get('/api/explorer/tables')
def explorer_tables(user=Depends(authenticated)):
    """Table list. Exact row counts for normal tables; dictionary statistics (marked approximate) for very
    large ones or very large schemas, so the list stays fast on a big database."""
    kb = knowledge_base(); tables = _oracle_tables(kb)
    with query_sources() as sources:
        _db(sources)
        counts = table_counts(kb, sources, list(tables)) if len(tables) <= 300 else {t: (m.get('rows'), True) for t, m in tables.items()}
    return [dict(table=t, columns=len(m['columns']), rows=counts[t][0], approx=counts[t][1], business=m['business'],
                 kind=m['kind'], description=m['description'], key=m.get('key'), label=m.get('label'),
                 links=sum(1 for j in kb.joins if t in (j['child'], j['parent']))) for t, m in tables.items()]


def _decode(kb, execute, dialect, table, rows):
    """{column: {id: label}} for columns on this page that point at a named record: master references, and
    single-column confirmed links to a parent (an order line's order ID shows the order reference)."""
    out = {}; t = kb.table(table)
    targets = {c: m['references'] for c, m in t['columns'].items() if m.get('references')}
    for j in kb.joins:
        if j['child'] == table and len(j['pairs']) == 1 and j['status'] in ('verified', 'declared'):
            col, pcol = j['pairs'][0]
            if col != t.get('key') and (kb.table(j['parent']) or {}).get('key') == pcol:
                targets.setdefault(col, j['parent'])
    for col, ref in targets.items():
        master = kb.table(ref)
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


def _columns(meta, names=None):
    return [dict(name=c, type=m['type'], label=m['business'], role=m['role'], references=m.get('references'))
            for c, m in ((c, meta['columns'][c]) for c in (names or meta['columns']))]


@app.get('/api/explorer/tables/{table}')
def explorer_rows(table: str, column: str | None = None, value: str | None = Query(default=None, max_length=100),
                  exact: bool = False, limit: int = Query(default=50, ge=1, le=200), offset: int = Query(default=0, ge=0, le=100000),
                  after: str | None = Query(default=None, max_length=40), user=Depends(authenticated)):
    """One page of a table. column + value filter by 'contains' (any case); exact=true matches the whole
    value instead (a record ID or an order reference). Exact matches use the plain column (col = :v, as typed
    and in capitals) so an index answers them on a big table; the any-case/any-spaces form is used only when
    that finds nothing on a table that is not very large. Rows are read before counting: a result that fits
    on the first page needs no COUNT at all."""
    table = table.upper(); kb = knowledge_base(); meta = _oracle_tables(kb).get(table)
    if not IDENT.match(table) or not meta:
        raise HTTPException(404, 'Table not available.')
    names = list(meta['columns'])
    big = (meta.get('rows') or 0) > BIG_ROWS
    key = meta.get('key') if meta.get('key') in names and meta['columns'][meta['key']]['type'] == 'NUMBER' else None
    paging = bool(key) and after not in (None, '')
    if paging and not re.fullmatch(r'-?\d{1,38}', after):
        raise HTTPException(422, 'Invalid page position.')
    choices = [('', {})]   # (WHERE clause, binds) alternatives: the first that finds a row is used
    with query_sources() as sources:
        execute, dialect = _db(sources)
        if column and value:
            column = column.upper(); value = value.strip()
            if column not in names:
                raise HTTPException(404, 'Column not available.')
            kind = meta['columns'][column]['type']
            if exact and kind == 'NUMBER' and re.fullmatch(r'-?\d{1,38}', value):
                choices = [(f'{column} = :v', {'v': int(value)})]
            elif exact and kind in ('VARCHAR2', 'CHAR', 'NVARCHAR2', 'NCHAR', 'VARCHAR'):
                plain = {f'v{i}': v for i, v in enumerate(dict.fromkeys([value, value.upper()]))}
                choices = [(f'{column} IN ({", ".join(":" + k for k in plain)})', plain)]
                if not big:
                    choices.append((f'{dialect.text(column)} = :v', {'v': value.upper()}))
            elif exact:
                choices = [(f'{dialect.text(column)} = :v', {'v': value.upper()})]
            else:
                like = re.sub(r'([\\%_])', r'\\\1', value.upper())
                choices = [(f"{dialect.text(column)} LIKE :v ESCAPE '\\'", {'v': f'%{like}%'})]
        if len(choices) > 1:   # a cheap existence probe picks the predicate, the same one on every page
            pick = execute.limited(4000) if hasattr(execute, 'limited') else execute
            try:
                found = pick(dialect.limit(f'SELECT 1 FROM {dialect.table(table)} WHERE {choices[0][0]}', 1), choices[0][1], 1)
            except QueryError:
                found = []
            choices = choices[:1] if found else choices[1:]
        where, binds = choices[0]
        cond = f' WHERE {where}' if where else ''
        select = f'SELECT {", ".join(names)} FROM {dialect.table(table)}'
        page_binds = dict(binds)
        if key:   # keyset paging: constant cost on big tables
            clauses = [w for w in (where, f'{key} < :after' if paging else '') if w]
            if paging:
                page_binds['after'] = int(after)
            sql = dialect.limit(select + (' WHERE ' + ' AND '.join(clauses) if clauses else '') + f' ORDER BY {key} DESC', limit)
        else:
            sql = dialect.page(select + cond + f' ORDER BY {names[0]} DESC', limit, offset)
        try:
            rows = _rows(execute, sql, page_binds, names, limit)
        except QueryError as exc:
            if where and not exact and big:
                raise HTTPException(409, f'Looking for “{value}” anywhere inside {meta["columns"][column]["business"]} means reading every row '
                                         f'of {meta["business"]} (a very large table), which took too long. Choose “is exactly” to look up a '
                                         'whole value (an order reference or record ID), or filter a smaller linked table.')
            raise HTTPException(409, str(exc))
        if not paging and not offset and len(rows) < limit:
            total, approx = len(rows), False   # everything is on this page
        elif where and not exact and big:
            total, approx = None, True         # 'contains' on a very large table: counting would read every row again
        elif where:   # a filtered count is made once, on the first page; 'Load more' pages reuse it
            total, approx = _filtered_count(execute, dialect, table, meta, cond, binds, first=not paging and not offset)
        else:
            total, approx = _count(execute, dialect, table, meta, cond, binds)
        decoded = _decode(kb, execute, dialect, table, rows)
    return dict(table=table, business=meta['business'], description=meta['description'], key=key, label=meta.get('label'),
                display=meta['display'], columns=_columns(meta),
                total=total, approx=approx, rows_known=total is not None, rows=rows, offset=offset, limit=limit, decoded=decoded,
                next_after=rows[-1][key] if key and len(rows) == limit else None)


REL_ROWS = 50   # child rows listed per linked table in a record view (a big order has many size rows)
_LINK_RANK = {'verified': 0, 'declared': 0, 'proposed': 1, 'inferred': 2}


@app.get('/api/explorer/related/{table}/{pk}')
def explorer_related(table: str, pk: int, user=Depends(authenticated)):
    """One full record plus the records linked to it through the relationship map (parents and children).
    A child table that also hangs under another child (size rows under order lines) says so in 'under',
    so the page can show which line each size row belongs to."""
    kb = knowledge_base(); t = _table_meta(kb, table.upper())
    if not t.get('key') or t.get('source') == 'local':
        raise HTTPException(409, 'This table has no single record ID.')
    neighbours = _neighbours(kb, t['name'])
    downs = {n['table'] for n in neighbours if n['direction'] == 'down'} - {t['name']}
    under = {}
    for j in sorted(kb.joins, key=lambda j: _LINK_RANK.get(j['status'], 9)):
        if j['child'] in downs and j['parent'] in downs and j['child'] != j['parent'] and j['child'] not in under:
            under[j['child']] = dict(table=j['parent'], pairs=j['pairs'], status=j['status'], label=j['label'])
    need = {}   # columns each side of an 'under' link needs so rows can be matched
    for child, u in under.items():
        need.setdefault(child, []).extend(c for c, _ in u['pairs']); need.setdefault(u['table'], []).extend(p for _, p in u['pairs'])
    out = []
    with query_sources() as sources:
        execute, dialect = _db(sources)
        names = list(t['columns'])
        found = _rows(execute, dialect.limit(f'SELECT {", ".join(names)} FROM {dialect.table(t["name"])} WHERE {t["key"]} = :id', 1), {'id': pk}, names, 1)
        if not found:
            raise HTTPException(404, 'Record not found.')
        record = found[0]
        for n in neighbours:
            j = next(x for x in kb.joins if x['id'] == n['join'])
            other = kb.table(n['table'])
            conds = [(p, record.get(ch)) for ch, p in j['pairs']] if n['direction'] == 'up' else [(ch, record.get(p)) for ch, p in j['pairs']]
            if any(v is None for _, v in conds):
                continue
            binds = {f'v{i}': v for i, (_, v) in enumerate(conds)}
            where = ' AND '.join(f'{col} = :v{i}' for i, (col, _) in enumerate(conds))
            cols = list(dict.fromkeys(other['display'] + [c for c, m in other['columns'].items() if m['role'] not in ('system', 'key')]))[:24]
            cols = list(dict.fromkeys(cols + ([other['key']] if other.get('key') else []) + need.get(other['name'], []) +
                                      [c for pair in j['pairs'] for c in pair if c in other['columns']]))
            limit = REL_ROWS if n['direction'] == 'down' else 20
            try:
                rows = _rows(execute, dialect.limit(f'SELECT {", ".join(cols)} FROM {dialect.table(other["name"])} WHERE {where}', limit + 1), binds, cols, limit + 1)
            except Exception:
                continue
            if not rows:
                continue
            more = len(rows) > limit; rows = rows[:limit]
            total = _count(execute, dialect, other['name'], other, f' WHERE {where}', binds)[0] if more else len(rows)
            out.append(dict(n, rows=rows, more=more, total=total, rows_known=total is not None, key=other.get('key'), label=other.get('label'), display=other['display'], pairs=j['pairs'],
                            columns=_columns(other, cols), decoded=_decode(kb, execute, dialect, other['name'], rows),
                            under=under.get(other['name']) if n['direction'] == 'down' else None,
                            filter=dict(column=conds[0][0], value=conds[0][1]) if len(conds) == 1 else None))
        decoded = _decode(kb, execute, dialect, t['name'], [record])
    return dict(table=t['name'], business=t['business'], key=t['key'], label=t.get('label'), display=t['display'],
                columns=_columns(t), record=record, related=out, decoded=decoded)


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
