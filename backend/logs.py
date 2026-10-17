"""JSON-lines application log: one object per line in DATA/logs/app.jsonl, rotated by size.

Decisions: docs/RESEARCH_DECISIONS.md, "Logging and ops" and checklist item 44.

* Records are formatted in the thread that logs them (so the request ID and user of that request are known)
  and handed through a queue to one writer thread (logging.handlers.QueueListener). Requests never wait on
  the disk, and only that one thread ever rotates the file. Threads only: nothing here forks, so it behaves
  the same on the Windows laptop as on Linux.
* Rotation: 10 MB x 8 files (app.jsonl, app.jsonl.1 ... app.jsonl.8). On Windows a file cannot be renamed
  while another program holds it open without FILE_SHARE_DELETE (an editor, or Get-Content -Wait in some
  setups). Then the current file is copied to app.jsonl.1 and truncated instead; if even that fails the
  log keeps appending to the current file and rotation is tried again a minute later. Logging never raises
  into a request and never prints a traceback per line.
* Redaction: any field whose name contains password, passwd, pwd, token, secret, cookie, authorization,
  api key or credential is replaced by "[redacted]" (at any depth), and the same names written as
  ``name=value`` / ``name: value`` inside message text are masked. The configured Oracle password is masked
  wherever it appears. Request bodies are never logged: callers pass only the fields they choose.

Fields of every line: ts (UTC, ...Z), level, logger, msg, request_id, user (when known), plus the event's
own fields. Access lines (logger poppys.access) carry method, path (never the query string), status and ms.
"""
from __future__ import annotations

import atexit
import contextvars
import json
import logging
import logging.handlers
import os
import queue
import re
import shutil
import threading
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

# The current request: {'request_id': ..., 'user': ...}. One dict per request, set by the request middleware;
# FastAPI runs sync dependencies and endpoints in worker threads with a *copy* of the context, and the copy
# still points at this same dict, so the user found by the sign-in check is known to every later line.
CONTEXT = contextvars.ContextVar('poppys_request', default=None)


def begin(request_id):
    """Start a request context; returns the token for end()."""
    return CONTEXT.set({'request_id': request_id, 'user': None})


def end(token):
    CONTEXT.reset(token)


def request_id():
    ctx = CONTEXT.get()
    return ctx['request_id'] if ctx else None


def set_user(username):
    ctx = CONTEXT.get()
    if ctx is not None:
        ctx['user'] = username


def current_user():
    ctx = CONTEXT.get()
    return ctx['user'] if ctx else None


MAX_BYTES = 10 * 1024 * 1024
BACKUPS = 8
FILENAME = 'app.jsonl'
REDACTED = '[redacted]'

SENSITIVE_KEY = re.compile(r'pass(?:word|wd|phrase)?|pwd|token|secret|cookie|authori[sz]ation|api[_-]?key|credential', re.I)
_INLINE = [
    re.compile(r'(?i)\b((?:[a-z_]*pass(?:word|wd)?|pwd|[a-z_]*secret|[a-z_]*token|api[_-]?key)\s*[=:]\s*)("[^"]*"|\'[^\']*\'|[^\s,;&]+)'),
    re.compile(r'(?i)\b((?:authorization|cookie|set-cookie)\s*:\s*)([^\r\n]+)'),
    re.compile(r'(?i)\b(poppys_session=)([^;\s]+)'),
    re.compile(r'(?i)\b(bearer\s+)([A-Za-z0-9._~+/=-]+)'),
]
_SECRETS: list[str] = []       # literal values to mask everywhere (the Oracle password), set by add_secret()


def add_secret(value):
    """Mask this literal value wherever it appears in a log line (ignored when short or empty)."""
    value = str(value or '')
    if len(value) >= 4 and value not in _SECRETS:
        _SECRETS.append(value)


def scrub(text):
    """Message text with inline secrets masked."""
    text = str(text)
    for secret in _SECRETS:
        if secret in text:
            text = text.replace(secret, REDACTED)
    for pattern in _INLINE:
        text = pattern.sub(lambda m: m.group(1) + REDACTED, text)
    return text


def redact(value, key=None, depth=0):
    """A JSON-safe copy with sensitive fields replaced. Unknown objects become their (scrubbed) text."""
    if key is not None and SENSITIVE_KEY.search(str(key)):
        return REDACTED
    if depth > 8:
        return '...'
    if isinstance(value, dict):
        return {str(k): redact(v, k, depth + 1) for k, v in list(value.items())[:200]}
    if isinstance(value, (list, tuple, set)):
        return [redact(v, None, depth + 1) for v in list(value)[:200]]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    text = scrub(value)
    return text if len(text) <= 2000 else text[:2000] + '...'


class JsonFormatter(logging.Formatter):
    """One JSON object per record. Runs in the thread that logs (QueueHandler.prepare), so the request's context applies."""

    RESERVED = set(vars(logging.LogRecord('', 0, '', 0, '', None, None))) | {'message', 'asctime', 'fields', 'taskName', 'color_message'}

    def format(self, record):
        out = {'ts': datetime.fromtimestamp(record.created, timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z',
               'level': record.levelname, 'logger': record.name, 'msg': scrub(record.getMessage())}
        ctx = CONTEXT.get() or {}
        fields = dict(getattr(record, 'fields', None) or {})
        rid = getattr(record, 'request_id', None) or ctx.get('request_id')
        if rid:
            out['request_id'] = rid
        user = fields.pop('user', None) or getattr(record, 'user', None) or ctx.get('user')
        if user:
            out['user'] = scrub(user)[:80]
        fields.update({k: v for k, v in vars(record).items() if k not in self.RESERVED and k not in ('request_id', 'user')})
        for k, v in redact(fields).items():
            out.setdefault(k, v)
        if record.exc_info:
            out['exc_type'] = record.exc_info[0].__name__ if record.exc_info[0] else None
            out['traceback'] = scrub(''.join(traceback.format_exception(*record.exc_info)))[-20000:]
        return json.dumps(out, ensure_ascii=False, default=str)


class SafeRotatingFileHandler(logging.handlers.RotatingFileHandler):
    """RotatingFileHandler that survives a file held open by a reader on Windows (see the module docstring)."""

    retry_after = 60.0

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._next_try = 0.0

    def shouldRollover(self, record):
        if time.monotonic() < self._next_try:
            return False
        return super().shouldRollover(record)

    def rotate(self, source, dest):
        try:
            os.replace(source, dest)
        except OSError:
            # Renaming is refused while another program has the file open: copy, then empty it in place.
            shutil.copyfile(source, dest)
            with open(source, 'r+b') as f:
                f.truncate(0)

    def doRollover(self):
        try:
            super().doRollover()
        except OSError:
            self._next_try = time.monotonic() + self.retry_after
        if self.stream is None:
            self.stream = self._open()

    def handleError(self, record):      # never a traceback per line on the console; the next write tries again
        self._next_try = max(self._next_try, time.monotonic() + 5)


class _State:
    lock = threading.Lock()
    path = None
    listener = None
    queue_handler = None
    file_handler = None


LOGGERS = ('poppys', 'uvicorn.error')


def setup(directory, max_bytes=MAX_BYTES, backups=BACKUPS, level=logging.INFO):
    """Send the poppys.* loggers (and uvicorn's own messages) to directory/app.jsonl. Calling it again with the
    same directory does nothing; with another directory the old file is flushed and closed first."""
    path = Path(directory) / FILENAME
    with _State.lock:
        if _State.path == path and _State.listener is not None:
            return path
        _stop()
        path.parent.mkdir(parents=True, exist_ok=True)
        fh = SafeRotatingFileHandler(path, maxBytes=max_bytes, backupCount=backups, encoding='utf-8', delay=True)
        fh.setFormatter(logging.Formatter('%(message)s'))
        q = queue.Queue(-1)
        qh = logging.handlers.QueueHandler(q)
        qh.setFormatter(JsonFormatter())
        listener = logging.handlers.QueueListener(q, fh, respect_handler_level=False)
        listener.start()
        for name in LOGGERS:
            lg = logging.getLogger(name)
            lg.addHandler(qh)
            if name == 'poppys':      # propagation stays on (pytest's caplog and any root handler still see records)
                lg.setLevel(level)
        _State.path, _State.listener, _State.queue_handler, _State.file_handler = path, listener, qh, fh
        return path


def _stop():
    if _State.listener is None:
        return
    for name in LOGGERS:
        logging.getLogger(name).removeHandler(_State.queue_handler)
    _State.listener.stop()          # writes what is queued, then ends the thread
    _State.file_handler.close()
    _State.path = _State.listener = _State.queue_handler = _State.file_handler = None


def shutdown():
    with _State.lock:
        _stop()


def flush(timeout=5.0):
    """Wait until every queued line is written (tests, and before a planned stop)."""
    listener = _State.listener
    if listener is None:
        return
    deadline = time.monotonic() + timeout
    while listener.queue.unfinished_tasks and time.monotonic() < deadline:
        time.sleep(0.01)
    if _State.file_handler is not None and _State.file_handler.stream is not None:
        _State.file_handler.flush()


def current_path():
    return _State.path


atexit.register(shutdown)

app = logging.getLogger('poppys.app')
security = logging.getLogger('poppys.security')
access = logging.getLogger('poppys.access')


def event(name, level=logging.INFO, logger=app, **fields):
    """One application or security event: logs.event('upload', draft=id, bytes=n)."""
    logger.log(level, name, extra={'fields': dict(fields, event=name)})


def security_event(name, level=logging.INFO, **fields):
    event(name, level, security, **fields)
