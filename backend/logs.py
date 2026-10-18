"""Structured logging for operations (docs/DEPLOYMENT_AND_RECOVERY.md): one JSON line per event in data/logs/poppys.log
(rotated, 5 files of 5 MB) and on the console. Every request gets an ID that is returned in the X-Request-ID header and
shown with server errors, so a person can quote it and the administrator can find the line.

Never logged: passwords, tokens, cookies, query strings, request or response bodies, cell values.
"""
from __future__ import annotations
import contextvars
import json
import logging
import logging.handlers
import os
import time
from .config import DATA

request_id = contextvars.ContextVar('request_id', default='-')
MAX_BYTES = 5 * 1024 * 1024
BACKUPS = 5
_configured = {'done': False}


class JsonLines(logging.Formatter):
    def format(self, record):
        entry = dict(at=time.strftime('%Y-%m-%dT%H:%M:%S', time.localtime(record.created)) + f'.{int(record.msecs):03d}',
                     level=record.levelname.lower(), logger=record.name, request=request_id.get(), message=record.getMessage())
        for key in ('method', 'path', 'status', 'ms', 'user', 'event'):
            if hasattr(record, key):
                entry[key] = getattr(record, key)
        if record.exc_info:
            entry['error'] = self.formatException(record.exc_info)[-4000:]
        return json.dumps(entry, ensure_ascii=False, default=str)


def setup(directory=None):
    """Idempotent: the app's lifespan calls it; tests may call it again with a scratch folder."""
    if _configured['done'] and directory is None:
        return
    folder = directory or (DATA / 'logs')
    os.makedirs(folder, exist_ok=True)
    root = logging.getLogger('poppys')
    root.setLevel(os.environ.get('POPPYS_LOG_LEVEL', 'INFO').upper())
    for h in list(root.handlers):
        if getattr(h, '_poppys', False):
            root.removeHandler(h); h.close()
    handlers = [logging.handlers.RotatingFileHandler(folder / 'poppys.log', maxBytes=MAX_BYTES, backupCount=BACKUPS, encoding='utf-8')]
    if not os.environ.get('POPPYS_LOG_QUIET'):
        handlers.append(logging.StreamHandler())
    for h in handlers:
        h.setFormatter(JsonLines()); h._poppys = True
        root.addHandler(h)
    root.propagate = False
    _configured['done'] = True
    return folder
