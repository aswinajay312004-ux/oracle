"""Security building blocks for backend/main.py: passwords, sign-in throttling, sessions, CSRF, request body
limits, response headers, request IDs and error messages that never leak internals.

Decisions: docs/RESEARCH_DECISIONS.md, "Security: sessions and cookies", "Security: CSRF", "Security:
passwords and throttling", "Security: headers, CSP, uploads, errors", "Logging and ops", and checklist items
3, 4, 39-42 and 44. Where this file deviates from that record, the comment at that spot says why.
"""
from __future__ import annotations

import hashlib
import hmac
import math
import os
import re
import secrets
import time
import traceback
import uuid
from pathlib import Path
from urllib.parse import urlsplit

from starlette.datastructures import Headers, MutableHeaders
from starlette.middleware.body_limit import RequestBodyLimitMiddleware
from starlette.responses import JSONResponse

from . import logs
from .config import ROOT, setting

BACKEND = os.path.normcase(str(ROOT / 'backend')) + os.sep


# --- configuration -----------------------------------------------------------------------------------------
def environment():
    """POPPYS_ENV: 'production' (the default) or 'development' (opt-in: API docs on)."""
    return 'development' if setting('ENV', 'production').strip().lower() in ('development', 'dev') else 'production'


def production():
    return environment() == 'production'


def cookie_secure():
    """POPPYS_COOKIE_SECURE, default true. Chromium (Edge, Chrome) treats http://localhost and http://127.0.0.1 as
    secure contexts, so a Secure cookie works there without TLS. Tests turn it off: TestClient talks to
    http://testserver, which is not a secure context, and its cookie jar would drop the cookie."""
    return setting('COOKIE_SECURE', 'true').strip().lower() not in ('0', 'false', 'no', 'off')


# --- sessions ------------------------------------------------------------------------------------------------
COOKIE = 'poppys_session'
IDLE_SECONDS = 30 * 60            # signed out after 30 minutes without activity
ABSOLUTE_SECONDS = 8 * 60 * 60    # and after 8 hours in any case
TOUCH_SECONDS = 60                # last_seen is written at most once a minute
# Requests the page makes on its own (the 30 s status poll, the session check on load) do not count as
# activity; otherwise an open browser tab would keep a session alive until the 8-hour limit.
PASSIVE = {('GET', '/api/health'), ('GET', '/api/session')}


def new_token():
    return secrets.token_urlsafe(32)


def token_hash(token):
    return hashlib.sha256(str(token or '').encode()).hexdigest()


def cookie_options():
    return dict(httponly=True, samesite='strict', path='/', secure=cookie_secure())


# --- passwords -------------------------------------------------------------------------------------------------
# PBKDF2-HMAC-SHA256 at OWASP's 600,000 iterations, stored as pbkdf2_sha256$<iterations>$<salt hex>$<hash hex>
# so the cost can be raised later and older hashes still verify. Deviation from the record: it prefers Argon2id
# (pwdlib + argon2-cffi), with this format as its documented fallback when a new dependency is refused; this
# keeps the offline wheelhouse unchanged. Hashes from before (bare hex, 300,000 iterations, salt in its own
# column) still verify and are re-hashed at the next successful sign-in.
SCHEME = 'pbkdf2_sha256'
ITERATIONS = 600_000
LEGACY_ITERATIONS = 300_000
MIN_LENGTH = 12
MAX_LENGTH = 256


def _pbkdf2(password, salt_hex, iterations):
    return hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), bytes.fromhex(salt_hex), iterations).hex()


def hash_password(password):
    """(salt, encoded hash) for the users table; the salt is also kept in its own column (NOT NULL)."""
    salt = secrets.token_hex(16)
    return salt, f'{SCHEME}${ITERATIONS}${salt}${_pbkdf2(password, salt, ITERATIONS)}'


_DUMMY_SALT = '00' * 16
DUMMY_HASH = f'{SCHEME}${ITERATIONS}${_DUMMY_SALT}${"0" * 64}'   # unknown users cost the same time as real ones


def verify_password(password, stored, legacy_salt=None):
    """(matches, needs_rehash). Never raises for a malformed stored value (it simply does not match)."""
    stored = str(stored or '')
    try:
        if stored.startswith(SCHEME + '$'):
            _, iterations, salt, expected = stored.split('$')
            iterations = int(iterations)
            if not 1000 <= iterations <= 10_000_000:
                raise ValueError('iterations out of range')
        else:
            iterations, salt, expected = LEGACY_ITERATIONS, legacy_salt or _DUMMY_SALT, stored
        actual = _pbkdf2(password, salt, iterations)
    except (ValueError, TypeError):
        _pbkdf2(password, _DUMMY_SALT, ITERATIONS)
        return False, False
    ok = hmac.compare_digest(actual, expected)
    return ok, ok and iterations < ITERATIONS


# A small offline list of passwords that attackers try first, limited to ones long enough to pass the length
# rule (shorter ones are refused anyway), plus common words that people pad with digits and symbols.
COMMON_PASSWORDS = frozenset('''
123456789012 1234567890123 12345678901234 123456789000 123412341234 123123123123 121212121212 111111111111
000000000000 999999999999 112233445566 123456123456 098765432109 147258369147 987654321098 1234567890qw
qwertyuiop12 qwertyuiop123 qwertyuiopasdf qwertyuiopasdfgh qwerty123456 qwerty12345678 qwertyqwerty
asdfghjkl123 asdfghjkl;' zxcvbnm123456 1q2w3e4r5t6y 1q2w3e4r5t6y7u 1qaz2wsx3edc 1qazxsw23edc zaq12wsxcde3
qazwsxedcrfv qwe123qwe123 abc123abc123 abcdefghijkl abcdefghijklm abc123456789 a1b2c3d4e5f6 aaaaaaaaaaaa
password1234 password12345 password123456 passwordpassword password@123 password@1234 password!123
p@ssw0rd1234 p@ssword1234 passw0rd1234 pa55word1234 pa$$word1234 password2024 password2025 password2026
iloveyou1234 iloveyouiloveyou letmein12345 welcome12345 welcome@1234 welcome@123 welcome123456
changeme1234 changeme123 administrator admin1234567 admin@123456 admin@12345 administrator1 adminadmin12
superman1234 batman123456 football1234 baseball1234 basketball12 dragon123456 monkey123456 sunshine1234
princess1234 starwars1234 trustno1trustno1 whatever1234 freedom12345 master123456 shadow123456
michael12345 jennifer1234 charlie12345 computer1234 internet1234 secret123456 iloveindia123 india@123456
india1234567 tirupur12345 garments1234 knitwear1234 poppys123456 poppys@12345 poppysknitwear oracle123456
oracle@12345 sample123456 test12345678 testtest1234 guest1234567 user12345678 login1234567 default12345
'''.split())
COMMON_WORDS = frozenset('''
password passw0rd passwd qwerty qwertyuiop asdfgh asdfghjkl zxcvbnm iloveyou welcome letmein admin administrator
changeme monkey dragon football baseball master shadow superman batman sunshine princess secret login guest
default test user oracle poppys poppysknitwear knitwear garment garments india tirupur sample abc abcdef
'''.split())


def password_problem(password, username=''):
    """Plain-language reason a NEW password is refused, or None (ASVS 6.2: length, common list, no username)."""
    password = str(password or '')
    if len(password) < MIN_LENGTH:
        return f'Use at least {MIN_LENGTH} characters for the password. A short phrase of three or four words is easiest.'
    if len(password) > MAX_LENGTH:
        return f'Use at most {MAX_LENGTH} characters for the password.'
    lowered = password.lower()
    letters = re.sub(r'[^a-z]', '', lowered)
    if lowered in COMMON_PASSWORDS or letters in COMMON_WORDS or len(set(lowered)) < 4:   # 'Password@2026' is 'password'
        return 'This password is too common or too simple. Choose a phrase that is not a well-known password.'
    name = str(username or '').lower()
    if name and (lowered == name or (len(name) >= 3 and name in lowered)):
        return 'The password must not contain the username.'
    return None


# --- sign-in throttling and lockout --------------------------------------------------------------------------
# Failures are kept in SQLite (auth_failures), per username and per client address, so they survive a restart.
# Up to 5 failures in 15 minutes cost nothing; after that each further try must wait 2^(n-5) seconds after the
# last failure (1, 2, 4 ... capped at 60 s), answered at once with 429 and Retry-After: nothing sleeps on the
# server. 20 failures in 15 minutes lock that username for 15 minutes (audited; an administrator can unlock).
# Deviation from the record (cap 15 minutes, lock after 50 until an administrator unlocks): with a 15-minute cap
# the delays alone keep a username under 20 failures per 15 minutes, so the lockout the brief asks for could
# never trigger; a 60 s cap lets it, and a timed lock does not leave staff stranded when no administrator is
# around. Every address on this laptop is 127.0.0.1, so the per-address limit is effectively a limit for the
# whole laptop; its free allowance is the same 5 failures, as briefed.
WINDOW_SECONDS = 15 * 60
FREE_FAILURES = 5
MAX_DELAY_SECONDS = 60
LOCK_AFTER = 20
LOCK_SECONDS = 15 * 60
BAD_LOGIN = 'Username or password is incorrect.'


def wait_for(failures, last_failure, now):
    """Seconds to wait before the next try, given n failures in the window and the time of the last one."""
    if not failures or failures < FREE_FAILURES or last_failure is None:
        return 0
    delay = min(2 ** (failures - FREE_FAILURES), MAX_DELAY_SECONDS)
    return max(0, math.ceil(last_failure + delay - now))


def throttle_message(reason, seconds):
    if reason == 'locked':
        minutes = max(1, math.ceil(seconds / 60))
        return (f'Too many failed sign-ins for this username. It is locked for {minutes} more minute{"s" if minutes != 1 else ""}; '
                'an administrator can unlock it sooner under Setup → Users & roles.')
    return f'Too many sign-in attempts. Wait {seconds} second{"s" if seconds != 1 else ""}, then try again.'


# --- response headers ---------------------------------------------------------------------------------------
# No 'unsafe-inline' anywhere: inline styles and scripts are not allowed (the frontend sets styles by class
# or through the CSSOM). frame-ancestors also covers X-Frame-Options for current browsers.
CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; "
       "font-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")
PERMISSIONS_POLICY = ('accelerometer=(), camera=(), display-capture=(), geolocation=(), gyroscope=(), hid=(), '
                      'magnetometer=(), microphone=(), midi=(), payment=(), serial=(), usb=()')
HEADERS = {
    'Content-Security-Policy': CSP,
    'X-Content-Type-Options': 'nosniff',
    'X-Frame-Options': 'DENY',
    'Referrer-Policy': 'no-referrer',
    'Cross-Origin-Opener-Policy': 'same-origin',
    'Cross-Origin-Resource-Policy': 'same-origin',
    'Permissions-Policy': PERMISSIONS_POLICY,
}
NO_STORE_PREFIXES = ('/api/', '/healthz', '/readyz')


def _security_headers(path, message):
    headers = MutableHeaders(scope=message)
    for name, value in HEADERS.items():
        headers[name] = value
    if path.startswith(NO_STORE_PREFIXES):
        headers['Cache-Control'] = 'no-store'
    elif 'cache-control' not in headers:
        headers['Cache-Control'] = 'no-cache'      # the page and its scripts: always revalidated (ETag), so an upgrade shows at once
    if 'server' in headers:
        del headers['server']


# --- error messages ----------------------------------------------------------------------------------------------
_WIN_PATH = re.compile(r'(?<![\w])[A-Za-z]:[\\/][^\s"\'<>|,;)]*')
_UNC_PATH = re.compile(r'\\\\[^\s"\'<>|,;)\\]+\\[^\s"\'<>|,;)]*')
_POSIX_PATH = re.compile(r'(?<![\w.:/~\\-])/(?:[^\s/"\'<>|,;)]+/)+[^\s"\'<>|,;)]*')
_FILE_URL = re.compile(r'(?i)\bfile:/+[^\s"\'<>]*')
GENERIC = 'Something went wrong. Nothing was saved. Reference {rid}.'


def safe_text(text, limit=2000):
    """A user-facing message with file paths and configured secrets removed (a message written for people
    never needs a server path; a library's message might carry one)."""
    text = str(text or '')
    for key in ('ORACLE_PASSWORD', 'ORACLE_DSN'):
        value = setting(key, '')
        if value and len(value) >= 4:
            text = text.replace(value, '[hidden]')
    for pattern in (_FILE_URL, _WIN_PATH, _UNC_PATH, _POSIX_PATH):
        text = pattern.sub('[path]', text)
    return logs.scrub(text)[:limit]


def raised_by_our_code(exc):
    """True when the innermost Python frame of the traceback is in backend/: the message was written by us for
    people (a plain-language ValueError / RuntimeError). Library exceptions (json, pydantic, sqlite3,
    openpyxl...) raise from their own frames and are treated as unexpected."""
    tb = getattr(exc, '__traceback__', None)
    if tb is None:
        return False
    while tb.tb_next is not None:
        tb = tb.tb_next
    return os.path.normcase(os.path.abspath(tb.tb_frame.f_code.co_filename)).startswith(BACKEND)


def problem(status=500, request_id=None):
    rid = request_id or logs.request_id() or uuid.uuid4().hex
    return JSONResponse({'type': 'about:blank', 'title': 'Internal Server Error', 'status': status,
                         'detail': GENERIC.format(rid=rid), 'request_id': rid},
                        status_code=status, media_type='application/problem+json')


def log_unexpected(exc, where=''):
    logs.app.error('unhandled_error', exc_info=(type(exc), exc, exc.__traceback__),
                   extra={'fields': {'event': 'unhandled_error', 'where': where}})


# --- CSRF -------------------------------------------------------------------------------------------------------------
SAFE_METHODS = frozenset({'GET', 'HEAD', 'OPTIONS'})
CSRF_MESSAGE = 'Cross-origin changes are not permitted. Open POPPYS AI from its own address and try again.'


def _origin_of(url):
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    if not parts.scheme or not parts.netloc:
        return None
    return f'{parts.scheme}://{parts.netloc}'.lower()


def csrf_problem(scope):
    """Why this state-changing request looks cross-site, or None.

    * Sec-Fetch-Site (all current browsers send it): only 'same-origin' and 'none' (typed by the person) pass;
      'same-site' is refused too, because another program on this laptop (http://localhost:3000) is same-site.
    * Origin, or Referer when there is no Origin, must be this server's own scheme://host:port.
    * Neither header: allowed. A CSRF attack needs a victim's browser, and every browser sends Sec-Fetch-Site
      and/or Origin on a cross-site POST/PUT/PATCH/DELETE; requests without them come from programs on this
      laptop (PowerShell, curl, tests), which hold no victim's cookie (SameSite=Strict, HttpOnly).
      Deviation from the record, which also asks for an X-Requested-With header and so fails closed: the brief
      keeps such programs working."""
    if scope['method'] in SAFE_METHODS:
        return None
    headers = Headers(scope=scope)
    site = headers.get('sec-fetch-site')
    if site is not None and site.strip().lower() not in ('same-origin', 'none'):
        return f'Sec-Fetch-Site {site.strip().lower()[:20]}'
    own = f'{scope.get("scheme", "http")}://{headers.get("host", "")}'.lower()
    origin = headers.get('origin')
    if origin is not None:
        if origin.strip().lower() != own:
            return 'foreign Origin'
    else:
        referer = headers.get('referer')
        if referer is not None and _origin_of(referer.strip()) != own:
            return 'foreign Referer'
    return None


class CSRFMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] == 'http':
            why = csrf_problem(scope)
            if why:
                logs.security_event('csrf_rejected', 30, method=scope['method'], path=scope['path'], reason=why)
                response = JSONResponse({'detail': CSRF_MESSAGE, 'code': 'csrf', 'request_id': logs.request_id()}, status_code=403)
                return await response(scope, receive, send)
        await self.app(scope, receive, send)


# --- request body limit -----------------------------------------------------------------------------------------------
# Starlette's RequestBodyLimitMiddleware counts the bytes as they stream in (chunked bodies too, not only the
# Content-Length) and stops the request as soon as the limit is passed, so a spooled upload temp file never
# grows past it. Uploads get 14 MiB (the reader's own cap); every other request 1 MiB, except saving edits to a
# draft. Deviation from the record (1 MiB for everything but uploads): the page sends a draft's whole orders back
# when it saves, and a measured one-order BOM draft (126 accessories, 30 size rows) was already 178 KB; the
# server accepts up to 50 orders with 300 accessories each, so 1 MiB would refuse real edits. They get 14 MiB too.
UPLOAD_LIMIT = 14 * 1024 * 1024
DEFAULT_LIMIT = 1024 * 1024
UPLOAD_ROUTES = {('POST', '/api/uploads')}
_DRAFT_EDIT = re.compile(r'^/api/drafts/[^/]+$')


def body_limit(scope):
    method, path = scope.get('method'), scope.get('path') or ''
    if (method, path) in UPLOAD_ROUTES or (method == 'PUT' and _DRAFT_EDIT.match(path)):
        return UPLOAD_LIMIT
    return DEFAULT_LIMIT


def too_large_message(limit, scope=None):
    if limit >= UPLOAD_LIMIT and scope is not None and scope.get('method') == 'PUT':
        return (f'These changes are larger than {limit // (1024 * 1024)} MB, so they were not saved. '
                'Split the workbook into smaller files and upload them one at a time.')
    if limit >= UPLOAD_LIMIT:
        return (f'The file is larger than {limit // (1024 * 1024)} MB, so it was not read. Save only the order sheets '
                '(delete unused sheets, pictures and blank rows), then upload it again.')
    return f'The request is larger than {max(1, limit // 1024)} KB, so it was not processed.'


class BodyLimitMiddleware:
    def __init__(self, app):
        self.app = app
        self.uploads = RequestBodyLimitMiddleware(app, UPLOAD_LIMIT)
        self.others = RequestBodyLimitMiddleware(app, DEFAULT_LIMIT)

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        limit = body_limit(scope)
        declared = Headers(scope=scope).get('content-length')
        if declared is not None and declared.strip().isdigit() and int(declared) > limit:
            # Refused before a byte is read (the streamed count below catches bodies without a length).
            logs.security_event('body_too_large', 30, method=scope['method'], path=scope['path'], declared=int(declared), limit=limit)
            response = JSONResponse({'detail': too_large_message(limit, scope), 'request_id': logs.request_id()}, status_code=413)
            return await response(scope, receive, send)
        await (self.uploads if limit == UPLOAD_LIMIT else self.others)(scope, receive, send)


# --- request ID, access log, headers, last-resort error handler ---------------------------------------------------------
_REQUEST_ID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._:-]{7,63}$')


def incoming_request_id(scope):
    """A well-formed X-Request-ID from the caller is kept (so a script can correlate), anything else replaced."""
    value = Headers(scope=scope).get('x-request-id', '')
    return value if _REQUEST_ID.match(value) else uuid.uuid4().hex


class RequestContextMiddleware:
    """Outermost layer: gives every request an ID (X-Request-ID response header and every log line), adds the
    security headers to every response (errors included), writes one access line per request, and turns any
    exception that escaped everything else into the 500 problem response (traceback only in the log)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        rid = incoming_request_id(scope)
        token = logs.begin(rid)
        scope.setdefault('state', {})['request_id'] = rid
        started = time.perf_counter()
        sent = {'status': None}
        path = scope.get('path', '')

        async def send_with_headers(message):
            if message['type'] == 'http.response.start':
                sent['status'] = message['status']
                _security_headers(path, message)
                MutableHeaders(scope=message)['X-Request-ID'] = rid
            await send(message)

        try:
            await self.app(scope, receive, send_with_headers)
        except Exception as exc:
            log_unexpected(exc, f'{scope.get("method")} {path}')
            if sent['status'] is not None:
                raise                                   # the answer has started: the server closes the connection
            await problem(500, rid)(scope, receive, send_with_headers)
        finally:
            user = (scope.get('state') or {}).get('user') or logs.current_user()
            logs.access.info('request', extra={'fields': {
                'method': scope.get('method'), 'path': path[:300], 'status': sent['status'],
                'ms': round((time.perf_counter() - started) * 1000, 1), 'user': user}})
            logs.end(token)


def format_exception(exc):
    return ''.join(traceback.format_exception(type(exc), exc, exc.__traceback__))


# --- version ------------------------------------------------------------------------------------------------------------------
def git_sha(root=ROOT):
    """The release's GIT_SHA file (install-service.ps1 writes it), else the checkout's HEAD; no git process."""
    root = Path(root)
    try:
        text = (root / 'GIT_SHA').read_text(encoding='utf-8').strip()
        if re.fullmatch(r'[0-9a-f]{40}', text):
            return text
    except OSError:
        pass
    git = root / '.git'
    try:
        head = (git / 'HEAD').read_text(encoding='utf-8').strip()
        if re.fullmatch(r'[0-9a-f]{40}', head):
            return head
        if head.startswith('ref: '):
            ref = head[5:].strip()
            if '..' in ref:
                return None
            try:
                sha = (git / ref).read_text(encoding='utf-8').strip()
            except OSError:
                sha = next((line.split()[0] for line in (git / 'packed-refs').read_text(encoding='utf-8').splitlines()
                            if line.endswith(' ' + ref)), '')
            return sha if re.fullmatch(r'[0-9a-f]{40}', sha) else None
    except OSError:
        return None
    return None


def app_version(root=ROOT):
    try:
        text = (Path(root) / 'VERSION').read_text(encoding='utf-8').strip().splitlines()
        return text[0].strip()[:40] if text else 'unknown'
    except OSError:
        return 'unknown'
