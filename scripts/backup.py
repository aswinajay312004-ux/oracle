"""POPPYS AI backups: safe online copy of the local SQLite store, verification, restore and restore drill.

Decisions: docs/RESEARCH_DECISIONS.md, "Logging and ops: SQLite durability, backups, audit, versioning" and
checklist item 45. Operator guide: docs/RUNBOOK.md. Standard library only, so the nightly scheduled task can run
it with the app's own Python without importing the app.

  python scripts/backup.py                                  make a backup now (the nightly task runs this)
  python scripts/backup.py --verify latest                  check the newest backup (or give a .zip path)
  python scripts/backup.py --restore FILE.zip --to DIR      restore into a new, empty folder
  python scripts/backup.py --restore FILE.zip --to DATA_DIR --force    replace a live database (app stopped)
  python scripts/backup.py --drill latest --start-app       monthly restore drill
  python scripts/backup.py --list                           show the backups and what retention keeps

How a backup is made:
  1. sqlite3 Connection.backup(pages=1024, sleep=0.05) into a temporary file next to the destination, so the
     app can keep running (never a plain file copy, which can catch a half-written database);
  2. the copy must pass PRAGMA integrity_check == 'ok' and an empty PRAGMA foreign_key_check;
  3. a zip with the copy, config/oracle_mapping.json, config/semantic_catalog.json, data/schema_discovery.json
     (when present) and manifest.json (SHA-256 and size of every member, row counts, app version / git sha);
     .env.poppys and anything that looks like a secret is never included;
  4. fsync, os.replace() into place, a <name>.zip.sha256 file beside it, then the finished zip is verified again;
  5. retention: the newest backup of each of the last 14 days, 8 ISO weeks and 12 months that have backups.
     Old backups are only pruned after a new backup has been written and verified.

Exit codes: 0 OK, 1 FAILED (backup or check failed), 2 usage or setup problem, 3 REFUSED (restore would overwrite
a database or the app is running), 4 the backup is OK but the copy to the second location failed.
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import hashlib
import http.client
import json
import os
import platform
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
import zlib
from pathlib import Path

FORMAT = 'poppys-backup/1'
DB_NAME = 'poppys.sqlite3'
DB_MEMBER = 'data/' + DB_NAME
CONFIG_FILES = ('oracle_mapping.json', 'semantic_catalog.json')
DATA_EXTRAS = ('schema_discovery.json',)
MANIFEST = 'manifest.json'
RESTORED_CONFIG_DIR = 'config-from-backup'      # config is evidence of what was in force; the release ships its own
JOURNAL_SUFFIXES = ('-journal', '-wal', '-shm')
NAME_RE = re.compile(r'^poppys-backup-(\d{8})-(\d{6})(?:-([A-Za-z0-9._-]+))?\.zip$')
LABEL_RE = re.compile(r'^[A-Za-z0-9._-]{1,60}$')
# Defence in depth: members come from an allow-list, and anything secret-looking is refused on write and on verify.
SECRET_RE = re.compile(r'(^|/)(\.env($|\.)|secrets?(\.bin|\.json|\.txt)?$|id_(rsa|dsa|ecdsa|ed25519))|\.(pem|key|p12|pfx|jks)$', re.I)
OK, FAILED, USAGE, REFUSED, COPY_FAILED = 0, 1, 2, 3, 4
APP_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_KEEP = (14, 8, 12)


class Problem(Exception):
    """A failure with a plain-English message and the exit code it maps to."""

    def __init__(self, message, code=FAILED):
        super().__init__(message)
        self.code = code


def say(text=''):
    print(text, flush=True)


# --- small helpers -----------------------------------------------------------------------------------------------
def sha256_file(path, bufsize=1 << 20):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(bufsize), b''):
            h.update(block)
    return h.hexdigest()


def human(n):
    n = float(n or 0)
    for unit in ('bytes', 'KB', 'MB', 'GB'):
        if n < 1024 or unit == 'GB':
            return f'{int(n)} bytes' if unit == 'bytes' else f'{n:.1f} {unit}'
        n /= 1024
    return f'{n:.1f} GB'


def utc_now():
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def iso_z(t):
    return t.astimezone(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def db_uri(path, mode):
    # mode=rw never creates a missing file (a plain connect() would silently create an empty database).
    return Path(path).resolve().as_uri() + f'?mode={mode}'


@contextlib.contextmanager
def connect(path, mode='rw', timeout=20.0):
    # contextlib-style close: `with sqlite3.connect()` only commits, it never closes (a Windows handle leak).
    conn = sqlite3.connect(db_uri(path, mode), uri=True, timeout=timeout)
    try:
        yield conn
    finally:
        conn.close()


def fsync_file(path):
    with open(path, 'r+b') as f:      # FlushFileBuffers needs a handle with write access on Windows
        os.fsync(f.fileno())


def fsync_dir(path):
    if os.name == 'nt':
        return                            # Windows cannot open a directory for fsync; NTFS journals the rename
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def safe_member(name):
    p = Path(name)
    return not (name.startswith(('/', '\\')) or p.is_absolute() or '..' in p.parts or ':' in name or '\\' in name)


def quote_ident(name):
    return '"' + name.replace('"', '""') + '"'


def port_open(port, host='127.0.0.1', timeout=0.5):
    if not port:
        return False
    with contextlib.closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.settimeout(timeout)
        return s.connect_ex((host, int(port))) == 0


def http_get(url, timeout=3.0):
    """(status, body) or (None, b'') when nothing answers. Never uses a proxy: the app is on loopback."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(urllib.request.Request(url, headers={'Accept': 'application/json, */*'}), timeout=timeout) as r:
            return r.status, r.read(65536)
    except urllib.error.HTTPError as e:
        return e.code, e.read(65536)
    except (urllib.error.URLError, http.client.HTTPException, OSError):
        return None, b''


def append_log(log_path, line):
    """One plain line per run in backups/backup.log, so a failed nightly run is visible without Task Scheduler.
    Skipped when the folder does not exist (a backup creates it; a check never creates folders just to log)."""
    if not log_path or not Path(log_path).parent.is_dir():
        return
    try:
        with open(log_path, 'a', encoding='utf-8') as f:
            f.write(f'{iso_z(utc_now())} {line}\n')
    except OSError as e:
        say(f'  (could not write the log {log_path}: {e.strerror or e})')


# --- app information -------------------------------------------------------------------------------------------------
def app_info(app_root):
    """Version and git sha when they can be found; never fails a backup."""
    app_root = Path(app_root)
    info = {'version': None, 'git_sha': None}
    for key, fname in (('version', 'VERSION'), ('git_sha', 'GIT_SHA')):
        try:
            text = (app_root / fname).read_text(encoding='utf-8').strip().splitlines()
            info[key] = text[0].strip()[:80] if text else None
        except OSError:
            pass
    if not info['git_sha'] and (app_root / '.git').exists() and shutil.which('git'):
        try:
            r = subprocess.run(['git', '-C', str(app_root), 'rev-parse', 'HEAD'], capture_output=True, text=True, timeout=10)
            if r.returncode == 0 and re.fullmatch(r'[0-9a-f]{40}', r.stdout.strip()):
                info['git_sha'] = r.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            pass
    if not info['version'] and app_root.parent.name == 'releases':
        info['version'] = app_root.name          # installed layout: releases\<version>
    return info


# --- database checks ---------------------------------------------------------------------------------------------------
def inspect_db(path):
    """integrity_check, foreign_key_check, row counts and header facts of a database copy."""
    try:
        with connect(path, 'rw') as c:
            integrity = [str(r[0]) for r in c.execute('PRAGMA integrity_check')]
            fk = c.execute('PRAGMA foreign_key_check').fetchall()
            names = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite\\_%' ESCAPE '\\' ORDER BY name")]
            tables = {}
            for name in names:
                try:
                    tables[name] = c.execute(f'SELECT count(*) FROM {quote_ident(name)}').fetchone()[0]
                except sqlite3.Error:
                    tables[name] = None             # e.g. a virtual table whose module this Python lacks
            facts = {k: c.execute(f'PRAGMA {k}').fetchone()[0] for k in ('page_size', 'page_count', 'user_version', 'journal_mode')}
    except sqlite3.DatabaseError as e:
        raise Problem(f'The database copy cannot be read ({e}).') from None
    return dict(integrity_check=integrity, foreign_key_violations=len(fk), foreign_key_examples=[list(r) for r in fk[:10]],
                tables=tables, **facts)


def require_healthy(report, what='The database copy'):
    if report['integrity_check'] != ['ok']:
        first = '; '.join(report['integrity_check'][:3])
        raise Problem(f'{what} failed PRAGMA integrity_check: {first}')
    if report['foreign_key_violations']:
        raise Problem(f'{what} failed PRAGMA foreign_key_check: {report["foreign_key_violations"]} row(s) point at '
                      f'missing parent rows (first: {report["foreign_key_examples"][:3]}).')


def db_locked(path):
    """True when another connection holds a lock (the exclusive lock is refused)."""
    try:
        with connect(path, 'rw', timeout=1.0) as c:
            c.isolation_level = None
            c.execute('BEGIN EXCLUSIVE')
            c.execute('ROLLBACK')
        return False
    except sqlite3.OperationalError as e:
        if 'locked' in str(e).lower() or 'busy' in str(e).lower():
            return True
        raise Problem(f'Could not open {path} to check whether it is in use ({e}).', REFUSED) from None


def app_running(db_path, port):
    reasons = []
    if port and port_open(port):
        reasons.append(f'a program is answering on http://127.0.0.1:{port} (the POPPYS AI service or start.ps1)')
    if Path(db_path).exists() and db_locked(db_path):
        reasons.append('another program is using the database (the exclusive lock was refused)')
    return reasons


# --- retention -------------------------------------------------------------------------------------------------------------
def backup_time(name):
    m = NAME_RE.match(name)
    if not m:
        return None
    try:
        return dt.datetime.strptime(m.group(1) + m.group(2), '%Y%m%d%H%M%S')
    except ValueError:
        return None


def list_backups(folder):
    folder = Path(folder)
    if not folder.is_dir():
        return []
    found = [(t, p) for p in folder.iterdir() if p.is_file() and (t := backup_time(p.name))]
    return [p for t, p in sorted(found, key=lambda x: (x[0], x[1].name), reverse=True)]


def plan_retention(names, daily=14, weekly=8, monthly=12):
    """Names to keep: the newest backup of each of the last `daily` days, `weekly` ISO weeks and `monthly` months
    that have backups (the buckets overlap, like restic --keep-daily/--keep-weekly/--keep-monthly). The date comes
    from the file name, which is the backup's own local time, so copying a file does not change its age."""
    items = sorted(((backup_time(n), n) for n in names if backup_time(n)), reverse=True)
    keep = set()
    for count, bucket in ((daily, lambda t: t.date()), (weekly, lambda t: t.isocalendar()[:2]), (monthly, lambda t: (t.year, t.month))):
        seen = set()
        for t, n in items:
            b = bucket(t)
            if b in seen:
                continue
            if len(seen) >= count:
                break
            seen.add(b)
            keep.add(n)
    if items:
        keep.add(items[0][1])                 # never delete the newest backup, whatever the settings
    return keep


def prune(folder, keep_counts, protect=()):
    files = list_backups(folder)
    keep = plan_retention([p.name for p in files], *keep_counts) | {Path(p).name for p in protect}
    deleted = []
    for p in files:
        if p.name in keep:
            continue
        for victim in (p, p.with_name(p.name + '.sha256')):
            with contextlib.suppress(FileNotFoundError):
                victim.unlink()
        deleted.append(p.name)
    return len(files) - len(deleted), deleted


# --- making a backup ----------------------------------------------------------------------------------------------------
def collect_members(data_dir, app_root, include_uploads):
    members = []
    for name in CONFIG_FILES:
        p = Path(app_root) / 'config' / name
        if p.is_file():
            members.append(('config/' + name, p))
        else:
            say(f'  Note: {p} not found; it is not in this backup.')
    for name in DATA_EXTRAS:
        p = Path(data_dir) / name
        if p.is_file():
            members.append(('data/' + name, p))
    if include_uploads:
        members += [('data/' + p.name, p) for p in sorted(Path(data_dir).glob('*.upload')) if p.is_file()]
    for arc, _ in members:
        if SECRET_RE.search(arc):
            raise Problem(f'Refusing to put {arc} in a backup: it looks like a secret.', USAGE)
    return members


def clean_stale_partials(dest, older_than_s=86400):
    now = time.time()
    for p in Path(dest).glob('.partial-*'):
        with contextlib.suppress(OSError):
            if now - p.stat().st_mtime > older_than_s:
                shutil.rmtree(p, ignore_errors=True)


def unique_stamp(dest, local):
    """A time stamp no other backup in `dest` uses (whatever its label), so names stay strictly ordered by time."""
    while list(Path(dest).glob(f'poppys-backup-{local:%Y%m%d-%H%M%S}*.zip*')):
        local += dt.timedelta(seconds=1)
    return local


def write_sidecar(zip_path, digest):
    side = zip_path.with_name(zip_path.name + '.sha256')
    tmp = side.with_name('.' + side.name + '.tmp')
    tmp.write_text(f'{digest}  {zip_path.name}\n', encoding='ascii')
    os.replace(tmp, side)
    return side


def make_backup(data_dir, dest, app_root=APP_ROOT, label=None, include_uploads=False, keep=DEFAULT_KEEP, prune_old=True, now=None):
    data_dir, dest, app_root = Path(data_dir).absolute(), Path(dest).absolute(), Path(app_root).absolute()
    src = data_dir / DB_NAME
    if not src.is_file():
        raise Problem(f'There is no database at {src}. Check --data-dir (the folder that holds {DB_NAME}).', USAGE)
    if label and not LABEL_RE.match(label):
        raise Problem('--label may only contain letters, digits, dot, dash and underscore (at most 60).', USAGE)
    dest.mkdir(parents=True, exist_ok=True)
    clean_stale_partials(dest)
    size = src.stat().st_size
    free = shutil.disk_usage(dest).free
    need = 2 * size + 50 * 1024 * 1024
    if free < need:
        raise Problem(f'The disk is too full for a backup: {human(free)} free in {dest}, at least {human(need)} needed. '
                      'Free some space (see docs/RUNBOOK.md, "The disk is full") and run the backup again.')
    local = unique_stamp(dest, (now or dt.datetime.now()).astimezone().replace(microsecond=0))
    stamp = local.strftime('%Y%m%d-%H%M%S')
    say(f'  Database:    {src} ({human(size)})')
    say(f'  Destination: {dest}')
    partial = dest / f'.partial-{stamp}-{os.getpid()}'
    partial.mkdir()
    try:
        copy = partial / DB_NAME
        say('  Copying the database with the SQLite online backup API (the app can keep running)...')
        try:
            with connect(src, 'rw') as source, connect(copy, 'rwc') as target:
                source.backup(target, pages=1024, sleep=0.05)
        except sqlite3.DatabaseError as e:
            raise Problem(f'SQLite could not copy the database ({e}). If it says "malformed", the live database is '
                          'damaged: follow docs/RUNBOOK.md, "A backup reports a damaged database".') from None
        try:
            report = inspect_db(copy)
            require_healthy(report)
        except Problem as e:
            kept = dest / 'last-failed-check.sqlite3'
            with contextlib.suppress(OSError):
                os.replace(copy, kept)
                say(f'  The copy that failed the check is kept for investigation: {kept}')
            raise Problem(f'{e} The live database may be damaged: follow docs/RUNBOOK.md, "A backup reports a damaged database". '
                          'The older backups were not touched.') from None
        say(f'  Checked the copy: integrity_check ok, foreign_key_check ok ({len(report["tables"])} tables, '
            f'{sum(v or 0 for v in report["tables"].values())} rows)')
        members = [(DB_MEMBER, copy)] + collect_members(data_dir, app_root, include_uploads)
        created = utc_now()
        manifest = {
            'format': FORMAT,
            'created_utc': iso_z(created),
            'created_local': local.isoformat(timespec='seconds'),
            'label': label,
            'host': platform.node(),
            'app': {**app_info(app_root), 'app_root': str(app_root)},
            'python': platform.python_version(),
            'sqlite_version': sqlite3.sqlite_version,
            'database': {'member': DB_MEMBER, 'source': str(src), **report},
            'members': {arc: {'sha256': sha256_file(p), 'size': p.stat().st_size} for arc, p in members},
        }
        name = f'poppys-backup-{stamp}' + (f'-{label}' if label else '') + '.zip'
        tmp_zip = partial / (name + '.part')
        with zipfile.ZipFile(tmp_zip, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as z:
            for arc, p in members:
                z.write(p, arc)
            z.writestr(MANIFEST, json.dumps(manifest, indent=2, sort_keys=True))
        fsync_file(tmp_zip)
        final = dest / name
        os.replace(tmp_zip, final)
        digest = sha256_file(final)
        write_sidecar(final, digest)
        fsync_dir(dest)
        say(f'  Wrote {final.name} ({human(final.stat().st_size)}), SHA-256 {digest[:16]}...')
    finally:
        shutil.rmtree(partial, ignore_errors=True)
    say('  Verifying the finished backup file...')
    try:
        verify(final, quiet=True)
    except Problem as e:
        bad = final.with_name(final.name + '.failed-verify')
        with contextlib.suppress(OSError):
            os.replace(final, bad)
        raise Problem(f'The new backup did not pass verification ({e}). It was renamed to {bad.name}; the older '
                      'backups were not touched. Check the disk (chkdsk) and run the backup again.') from None
    say('  Verified: every file matches its SHA-256 and the database copy passes integrity_check.')
    kept, deleted = prune(dest, keep, protect=[final]) if prune_old else (len(list_backups(dest)), [])
    if deleted:
        say(f'  Retention ({keep[0]} daily, {keep[1]} weekly, {keep[2]} monthly): kept {kept}, deleted {len(deleted)}: {", ".join(deleted)}')
    else:
        say(f'  Retention ({keep[0]} daily, {keep[1]} weekly, {keep[2]} monthly): kept {kept}, nothing to delete.')
    return final, digest, kept, deleted


def copy_to_second_location(final, digest, second, keep):
    second = Path(second)
    second.mkdir(parents=True, exist_ok=True)
    tmp = second / ('.partial-' + final.name)
    shutil.copyfile(final, tmp)
    fsync_file(tmp)
    if sha256_file(tmp) != digest:
        tmp.unlink()
        raise Problem(f'The copy in {second} does not match the original (SHA-256 differs).')
    target = second / final.name
    os.replace(tmp, target)
    write_sidecar(target, digest)
    return prune(second, keep, protect=[target])


# --- verify --------------------------------------------------------------------------------------------------------------------------
def resolve_zip(arg, dest):
    if str(arg).lower() == 'latest':
        found = list_backups(dest)
        if not found:
            raise Problem(f'There are no backups in {dest}.', USAGE)
        return found[0]
    p = Path(arg)
    if not p.is_file():
        raise Problem(f'Backup file not found: {p}', USAGE)
    return p


def verify(zip_path, quiet=False, scratch=None):
    """Check a backup end to end; returns the manifest. Raises Problem (exit 1) on any mismatch."""
    zip_path = Path(zip_path)
    out = (lambda s: None) if quiet else say
    if not zip_path.is_file():
        raise Problem(f'Backup file not found: {zip_path}', USAGE)
    side = zip_path.with_name(zip_path.name + '.sha256')
    if side.is_file():
        try:
            expected = side.read_text(encoding='ascii').split()[0].lower()
        except (OSError, UnicodeDecodeError, IndexError):
            raise Problem(f'{side.name} is unreadable.') from None
        if sha256_file(zip_path) != expected:
            raise Problem(f'The SHA-256 of {zip_path.name} does not match {side.name}: the file changed or is damaged.')
        out('  whole-file SHA-256 (.sha256 file) ....... ok')
    else:
        out(f'  whole-file SHA-256 ....................... no {side.name} beside it (checked file by file instead)')
    try:
        z = zipfile.ZipFile(zip_path)
    except (zipfile.BadZipFile, OSError) as e:
        raise Problem(f'{zip_path.name} is not a readable zip file ({e}). It is damaged or incomplete.') from None
    with z:
        names = z.namelist()
        bad = [n for n in names if not safe_member(n)]
        if bad:
            raise Problem(f'The zip contains unsafe paths: {bad[:3]}')
        secret = [n for n in names if SECRET_RE.search(n)]
        if secret:
            raise Problem(f'The zip contains files that look like secrets: {secret[:3]}. Do not use or share it.')
        if MANIFEST not in names:
            raise Problem(f'{zip_path.name} has no {MANIFEST}: it is not a POPPYS AI backup or it is incomplete.')
        try:
            manifest = json.loads(z.read(MANIFEST))
        except (ValueError, zipfile.BadZipFile, zlib.error) as e:
            raise Problem(f'{MANIFEST} is damaged ({e}).') from None
        if manifest.get('format') != FORMAT:
            raise Problem(f'Unknown backup format {manifest.get("format")!r} (expected {FORMAT}).')
        listed = manifest.get('members') or {}
        present = set(names) - {MANIFEST}
        if set(listed) != present or DB_MEMBER not in present:
            raise Problem(f'The files in the zip do not match its manifest (missing: {sorted(set(listed) - present)[:5]}, '
                          f'unexpected: {sorted(present - set(listed))[:5]}).')
        out(f'  manifest ................................. ok (made {manifest.get("created_utc")}, '
            f'app {manifest.get("app", {}).get("version") or manifest.get("app", {}).get("git_sha") or "unknown"})')
        for name in sorted(present):
            h, size = hashlib.sha256(), 0
            try:
                with z.open(name) as f:
                    for block in iter(lambda: f.read(1 << 20), b''):
                        h.update(block)
                        size += len(block)
            except (zipfile.BadZipFile, zlib.error, EOFError, OSError) as e:
                raise Problem(f'{name} is damaged inside the zip ({e}).') from None
            if h.hexdigest() != listed[name].get('sha256') or size != listed[name].get('size'):
                raise Problem(f'{name} does not match the SHA-256 recorded when the backup was made.')
        out(f'  file hashes .............................. ok ({len(present)} files)')
        work = Path(tempfile.mkdtemp(prefix='poppys-verify-', dir=scratch))
        try:
            db = work / DB_NAME
            with z.open(DB_MEMBER) as f, open(db, 'wb') as g:
                shutil.copyfileobj(f, g, 1 << 20)
            report = inspect_db(db)
        finally:
            shutil.rmtree(work, ignore_errors=True)
    require_healthy(report, 'The database in the backup')
    out('  database integrity_check / foreign keys .. ok')
    recorded = (manifest.get('database') or {}).get('tables') or {}
    if recorded and report['tables'] != recorded:
        diff = sorted(k for k in set(recorded) | set(report['tables']) if recorded.get(k) != report['tables'].get(k))
        raise Problem(f'Row counts differ from the manifest for: {diff[:5]}')
    out(f'  row counts match the manifest ............ ok ({len(report["tables"])} tables, '
        f'{sum(v or 0 for v in report["tables"].values())} rows)')
    return manifest


# --- restore -------------------------------------------------------------------------------------------------------------------------
def restore(zip_path, to_dir, force=False, port=8008, quiet=False, manifest=None):
    """Restore a verified backup into `to_dir` (a data folder). Never deletes: replaced files are moved aside.
    `manifest` is the result of an earlier verify() of the same file, to avoid checking it twice."""
    out = (lambda s: None) if quiet else say
    zip_path, to_dir = Path(zip_path), Path(to_dir)
    if manifest is None:
        out(f'  Checking {zip_path.name} before restoring...')
        manifest = verify(zip_path, quiet=True)
        out('  The backup is complete and readable.')
    to_dir.mkdir(parents=True, exist_ok=True)
    plan = []                                     # (member, target) for files that must be written
    with zipfile.ZipFile(zip_path) as z:
        for name in sorted(set(z.namelist()) - {MANIFEST}):
            folder, _, fname = name.partition('/')
            if folder == 'data':
                plan.append((name, to_dir / fname))
            elif folder == 'config':
                plan.append((name, to_dir / RESTORED_CONFIG_DIR / fname))
        db = to_dir / DB_NAME
        clashes = [t for m, t in plan if t.exists() and not t.name.endswith('.upload')]
        clashes += [db.with_name(db.name + s) for s in JOURNAL_SUFFIXES if db.with_name(db.name + s).exists()]
        if clashes and not force:
            raise Problem(f'{to_dir} already has {", ".join(c.name for c in clashes[:4])}. Restore into a new, empty folder '
                          '(for example C:\\PoppysRestore\\<date>). To replace a live database, stop POPPYS AI first and '
                          'add --force; the current files are then moved aside, never deleted.', REFUSED)
        if force and db.exists():
            reasons = app_running(db, port)
            if reasons:
                raise Problem('POPPYS AI still seems to be running: ' + '; '.join(reasons) + '. Stop it first '
                              '(Stop-Service PoppysAI, or close the start.ps1 window), then run the restore again.', REFUSED)
        partial = to_dir / f'.partial-restore-{os.getpid()}'
        shutil.rmtree(partial, ignore_errors=True)
        partial.mkdir()
        try:
            staged = []
            for member, target in plan:
                tmp = partial / member.replace('/', '__')
                with z.open(member) as f, open(tmp, 'wb') as g:
                    shutil.copyfileobj(f, g, 1 << 20)
                fsync_file(tmp)
                staged.append((tmp, target))
            require_healthy(inspect_db(partial / DB_MEMBER.replace('/', '__')), 'The restored database')
            stamp = dt.datetime.now().strftime('%Y%m%d-%H%M%S')
            moved = []
            # The database first: on Windows the rename fails while any program holds it open, and then nothing
            # has changed yet. Its journal files go with it, or SQLite would replay the old journal into the new file.
            for old in [db] + [db.with_name(db.name + s) for s in JOURNAL_SUFFIXES] + [t for _, t in staged if t != db]:
                if old.exists() and not old.name.endswith('.upload'):
                    aside = old.with_name(f'{old.name}.before-restore-{stamp}')
                    try:
                        os.replace(old, aside)
                    except PermissionError:
                        raise Problem(f'{old} is in use by another program. Stop POPPYS AI and try again.', REFUSED) from None
                    moved.append(aside)
            for tmp, target in staged:
                if target.name.endswith('.upload') and target.exists():
                    continue                       # uploads are content files: an existing one is kept
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(tmp, target)
            fsync_dir(to_dir)
        finally:
            shutil.rmtree(partial, ignore_errors=True)
    for aside in moved:
        out(f'  Moved the previous file aside (not deleted): {aside}')
    tables = (manifest.get('database') or {}).get('tables') or {}
    out(f'  Restored {DB_NAME} ({len(tables)} tables, {sum(v or 0 for v in tables.values())} rows) into {to_dir}')
    if any(m.startswith('config/') for m, _ in plan):
        out(f'  The config files of that time are in {to_dir / RESTORED_CONFIG_DIR} (for comparison; the app uses its own config folder).')
    return manifest, moved


# --- restore drill ----------------------------------------------------------------------------------------------------------------
def wait_ready(port, seconds, proc=None):
    base = f'http://127.0.0.1:{port}'
    deadline, readyz = time.monotonic() + seconds, True
    while time.monotonic() < deadline:
        if proc is not None and proc.poll() is not None:
            raise Problem(f'The restored app stopped during start-up (exit code {proc.returncode}).')
        if readyz:
            code, _ = http_get(base + '/readyz')
            if code == 200:
                return '/readyz'
            if code == 404:
                readyz = False                   # this app version has no /readyz yet: fall back to the home page
                continue
        else:
            code, _ = http_get(base + '/')
            if code == 200:
                return '/'
        time.sleep(1)
    raise Problem(f'The restored app did not answer {base}/readyz (or /) within {seconds} s.')


def run_restored_app(app_root, app_dir, data_dir, port, seconds, users):
    """Start a scratch copy of the app on the restored data and check that it answers. Oracle is unreachable from it:
    there is no .env.poppys in the copy and every POPPYS_* setting is removed from its environment."""
    for part in ('backend', 'frontend', 'config'):
        if (Path(app_root) / part).is_dir():
            shutil.copytree(Path(app_root) / part, app_dir / part, ignore=shutil.ignore_patterns('__pycache__'))
    if not (app_dir / 'backend').is_dir():
        raise Problem(f'No backend folder under {app_root}; pass --app-root.', USAGE)
    if port_open(port):
        raise Problem(f'Port {port} is busy; pass --drill-port with a free port.', USAGE)
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith('POPPYS_')}
    env['POPPYS_DATA_DIR'] = str(data_dir)
    log_path = app_dir.parent / 'drill-app.log'
    say(f'  Starting the restored app on http://127.0.0.1:{port} (scratch copy, Oracle switched off)...')
    with open(log_path, 'wb') as log:
        proc = subprocess.Popen([sys.executable, '-E', '-s', '-X', 'utf8', '-m', 'uvicorn', 'backend.main:app', '--host', '127.0.0.1',
                                 '--port', str(port), '--workers', '1', '--no-access-log'], cwd=app_dir, env=env, stdout=log, stderr=subprocess.STDOUT)
        try:
            how = wait_ready(port, seconds, proc)
            say(f'  The restored app answered {how} with 200.')
            code, body = http_get(f'http://127.0.0.1:{port}/api/session')
            if code == 200:
                try:
                    needs_setup = json.loads(body or b'{}').get('needs_setup')
                except ValueError:
                    raise Problem('/api/session on the restored app did not return JSON.') from None
                if users and needs_setup:
                    raise Problem(f'The restored app shows the first-run screen although the backup has {users} user(s).')
                say(f'  The restored app reads its users from the restored database (needs_setup={needs_setup}).')
            elif code != 404:
                raise Problem(f'/api/session answered {code} on the restored app.')
        except Problem:
            log.flush()
            with contextlib.suppress(OSError):
                tail = log_path.read_bytes()[-3000:].decode('utf-8', 'replace').splitlines()[-15:]
                say('  Last lines of the restored app log:\n    ' + '\n    '.join(tail))
            raise
        finally:
            proc.terminate()
            try:
                proc.wait(15)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(15)


def drill(zip_path, app_root=APP_ROOT, start_app=False, port=8018, keep_files=False, seconds=60):
    say(f'  Backup: {zip_path}')
    manifest = verify(zip_path)
    scratch = Path(tempfile.mkdtemp(prefix='poppys-restore-drill-'))
    try:
        app_dir = scratch / 'app'
        data_dir = app_dir / 'data'
        restore(zip_path, data_dir, force=False, port=0, quiet=True, manifest=manifest)
        say(f'  Restored into the scratch folder {scratch}')
        report = inspect_db(data_dir / DB_NAME)
        require_healthy(report, 'The restored database')
        recorded = (manifest.get('database') or {}).get('tables') or {}
        if recorded and report['tables'] != recorded:
            raise Problem('The restored database has different row counts from the manifest.')
        say(f'  Restored database: integrity_check ok, foreign keys ok, row counts match ({len(report["tables"])} tables)')
        if start_app:
            run_restored_app(app_root, app_dir, data_dir, port, seconds, recorded.get('users'))
        return manifest
    finally:
        if keep_files:
            say(f'  Scratch folder kept: {scratch}')
        else:
            shutil.rmtree(scratch, ignore_errors=True)


# --- command line ----------------------------------------------------------------------------------------------------------------
def parser():
    p = argparse.ArgumentParser(
        prog='backup.py', formatter_class=argparse.RawDescriptionHelpFormatter,
        description='POPPYS AI backups: make, verify, restore and drill. See docs/RUNBOOK.md.',
        epilog='Exit codes: 0 OK, 1 FAILED, 2 usage or setup problem, 3 REFUSED (would overwrite / app running), '
               '4 backup OK but the second copy failed.')
    mode = p.add_mutually_exclusive_group()
    mode.add_argument('--verify', metavar='ZIP', help="check a backup end to end ('latest' = newest in --dest)")
    mode.add_argument('--restore', metavar='ZIP', help="restore a backup into --to ('latest' = newest in --dest)")
    mode.add_argument('--drill', metavar='ZIP', help="restore drill into a scratch folder ('latest' = newest in --dest)")
    mode.add_argument('--list', action='store_true', help='list the backups in --dest and what retention keeps')
    p.add_argument('--to', metavar='DIR', help='with --restore: the data folder to restore into (new and empty unless --force)')
    p.add_argument('--force', action='store_true', help='with --restore: replace an existing database (only when the app is stopped)')
    p.add_argument('--data-dir', metavar='DIR', help='folder with poppys.sqlite3 (default: $POPPYS_DATA_DIR or <app-root>/data)')
    p.add_argument('--app-root', metavar='DIR', default=str(APP_ROOT), help='the POPPYS AI folder (default: the folder above scripts/)')
    p.add_argument('--dest', metavar='DIR', help='backup folder (default: <data-dir>/backups)')
    p.add_argument('--label', help='extra word in the file name, e.g. pre-upgrade-1.2.0')
    p.add_argument('--copy-to', metavar='DIR', help='also copy the new backup to this second location (USB disk, second drive)')
    p.add_argument('--include-uploads', action='store_true', help='also include the uploaded buyer files (data/*.upload)')
    p.add_argument('--keep-daily', type=int, default=DEFAULT_KEEP[0])
    p.add_argument('--keep-weekly', type=int, default=DEFAULT_KEEP[1])
    p.add_argument('--keep-monthly', type=int, default=DEFAULT_KEEP[2])
    p.add_argument('--no-prune', action='store_true', help='do not delete old backups')
    p.add_argument('--port', type=int, default=8008, help='with --restore --force: refuse while something answers on this port (0 = skip)')
    p.add_argument('--start-app', action='store_true', help='with --drill: also start the restored app on --drill-port and check it answers')
    p.add_argument('--drill-port', type=int, default=8018)
    p.add_argument('--keep-scratch', action='store_true', help='with --drill: keep the scratch folder for inspection')
    p.add_argument('--log', metavar='FILE', help='append one line per run here (default: <dest>/backup.log; "none" to disable)')
    return p


def main(argv=None):
    with contextlib.suppress(AttributeError, ValueError):
        sys.stdout.reconfigure(errors='replace')
    args = parser().parse_args(argv)
    app_root = Path(args.app_root)
    data_dir = Path(args.data_dir or os.environ.get('POPPYS_DATA_DIR') or app_root / 'data')
    dest = Path(args.dest) if args.dest else data_dir / 'backups'
    keep = (args.keep_daily, args.keep_weekly, args.keep_monthly)
    log = None if (args.log or '').lower() == 'none' else Path(args.log) if args.log else dest / 'backup.log'
    action = 'VERIFY' if args.verify else 'RESTORE' if args.restore else 'DRILL' if args.drill else 'LIST' if args.list else 'BACKUP'
    try:
        if min(keep) < 1:
            raise Problem('--keep-daily, --keep-weekly and --keep-monthly must be at least 1.', USAGE)
        if args.to and not args.restore:
            raise Problem('--to is only used with --restore.', USAGE)
        if args.list:
            files = list_backups(dest)
            keepset = plan_retention([p.name for p in files], *keep)
            say(f'Backups in {dest}: {len(files)}')
            for p in files:
                state = 'keep' if p.name in keepset else 'deleted at the next backup'
                say(f'  {p.name}  {human(p.stat().st_size):>10}  {state}')
            return OK
        if args.verify:
            z = resolve_zip(args.verify, dest)
            say(f'Checking {z}')
            verify(z)
            say('OK: the backup is complete and readable.')
            append_log(log, f'VERIFY OK {z.name}')
            return OK
        if args.restore:
            if not args.to:
                raise Problem('--restore needs --to <folder>: a new, empty folder (or the data folder with --force).', USAGE)
            z = resolve_zip(args.restore, dest)
            say(f'Restoring {z}')
            restore(z, args.to, force=args.force, port=args.port)
            say(f'OK: restored into {args.to}.')
            append_log(log, f'RESTORE OK {z.name} -> {args.to}')
            return OK
        if args.drill:
            z = resolve_zip(args.drill, dest)
            say('POPPYS AI restore drill')
            drill(z, app_root, start_app=args.start_app, port=args.drill_port, keep_files=args.keep_scratch)
            say(f'OK: restore drill passed for {z.name}.' + ('' if args.start_app else ' (Add --start-app to also start the restored app.)'))
            append_log(log, f'DRILL OK {z.name}' + (' app-started' if args.start_app else ''))
            return OK
        say('POPPYS AI backup')
        final, digest, kept, deleted = make_backup(data_dir, dest, app_root, label=args.label, include_uploads=args.include_uploads,
                                                   keep=keep, prune_old=not args.no_prune)
        code = OK
        if args.copy_to:
            try:
                k2, d2 = copy_to_second_location(final, digest, args.copy_to, keep)
                say(f'  Second copy: {Path(args.copy_to) / final.name} (kept {k2}, deleted {len(d2)})')
            except (OSError, Problem) as e:
                say(f'WARNING: the backup is fine, but copying it to {args.copy_to} failed: {e}. Is the drive connected?')
                code = COPY_FAILED
        append_log(log, f'BACKUP {"OK" if code == OK else "OK-BUT-SECOND-COPY-FAILED"} {final.name} {final.stat().st_size} '
                        f'sha256={digest} kept={kept} deleted={len(deleted)}')
        say(f'OK: backup saved to {final}')
        return code
    except Problem as e:
        say(f'{"REFUSED" if e.code == REFUSED else "FAILED"}: {e}')
        append_log(log, f'{action} {"REFUSED" if e.code == REFUSED else "FAILED"} {e}')
        return e.code
    except OSError as e:
        msg = f'{e.strerror or e} ({e.filename})' if getattr(e, 'filename', None) else str(e)
        hint = ' The disk is full: see docs/RUNBOOK.md, "The disk is full".' if getattr(e, 'errno', None) == 28 else ''
        say(f'FAILED: {msg}.{hint}')
        append_log(log, f'{action} FAILED {msg}')
        return FAILED


if __name__ == '__main__':
    sys.exit(main())
