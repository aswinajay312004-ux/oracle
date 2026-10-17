"""scripts/backup.py (online SQLite backup, verify, restore, retention, drill) and the Windows service files it ships
with (deploy/windows, setup.ps1, start.ps1, docs/RUNBOOK.md). Everything runs in temporary folders; the real data/
folder is never used. PowerShell functions are tested by taking them out of the scripts with the PowerShell parser
(the scripts themselves need an administrator and Windows)."""
import datetime as dt
import hashlib
import importlib.util
import json
import os
import re
import secrets
import shutil
import socket
import sqlite3
import struct
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
import zipfile
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location('poppys_backup', ROOT / 'scripts' / 'backup.py')
bk = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bk)

DEPLOY = ROOT / 'deploy' / 'windows'
PS_SCRIPTS = ['install-service.ps1', 'upgrade.ps1', 'rollback.ps1', 'backup-task.ps1', 'uninstall-service.ps1']
ROOT_PS = [ROOT / 'setup.ps1', ROOT / 'start.ps1', ROOT / 'scripts' / 'check.ps1']
# On Windows the laptop's own Windows PowerShell 5.1 (powershell.exe), so the parse checks 5.1; elsewhere PowerShell 7.
POWERSHELL = (shutil.which('powershell') or shutil.which('pwsh')) if os.name == 'nt' else (shutil.which('pwsh') or shutil.which('powershell'))
GIT = shutil.which('git')
needs_ps = pytest.mark.skipif(not POWERSHELL, reason='PowerShell is not installed')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run(*args):
    return bk.main([str(a) for a in args])


@pytest.fixture
def app(tmp_path):
    """A small app folder: config, a data folder with a real SQLite store (with a foreign key), and planted secrets."""
    root = tmp_path / 'app'
    (root / 'config').mkdir(parents=True)
    (root / 'config' / 'oracle_mapping.json').write_text('{"tables": {"ORDMAS": {"status": "verified"}}}', encoding='utf-8')
    (root / 'config' / 'semantic_catalog.json').write_text('{"models": ["ORDMAS"]}', encoding='utf-8')
    data = root / 'data'
    data.mkdir()
    (data / 'schema_discovery.json').write_text('{"tables": 3}', encoding='utf-8')
    (data / 'a1b2.upload').write_bytes(b'buyer workbook bytes')
    secret = 'Pw-' + secrets.token_hex(12)
    env_text = 'ORACLE_USER=POPPYS_APP\n' + 'ORACLE_PASSWORD' + '=' + secret + '\n'
    (root / '.env.poppys').write_text(env_text, encoding='utf-8')
    (data / '.env.poppys').write_text(env_text, encoding='utf-8')          # a stray copy must not be picked up either
    (data / 'secrets.bin').write_bytes(secret.encode())
    db = data / 'poppys.sqlite3'
    with closing(sqlite3.connect(db)) as c:
        c.execute('PRAGMA foreign_keys=ON')
        c.executescript('''
            CREATE TABLE users(username TEXT PRIMARY KEY, role TEXT NOT NULL);
            CREATE TABLE drafts(id TEXT PRIMARY KEY, owner TEXT NOT NULL REFERENCES users(username), payload TEXT);
            CREATE TABLE audit(id INTEGER PRIMARY KEY, at TEXT, actor TEXT, action TEXT, detail TEXT);''')
        c.executemany('INSERT INTO users VALUES (?,?)', [('admin', 'admin'), ('op', 'operator')])
        c.executemany('INSERT INTO drafts VALUES (?,?,?)', [(f'd{i}', 'op', json.dumps({'n': i, 'colour': 'NAVY ' * i})) for i in range(60)])
        c.executemany('INSERT INTO audit(at,actor,action,detail) VALUES (?,?,?,?)',
                      [(f'2026-10-{1 + i % 28:02d}', 'op', 'upload', 'x' * (i * 7)) for i in range(120)])
        c.commit()
    return SimpleNamespace(root=root, data=data, db=db, secret=secret, dest=data / 'backups')


def backup(app, *extra):
    return run('--data-dir', app.data, '--app-root', app.root, '--dest', app.dest, *extra)


def only_zip(folder):
    zips = sorted(Path(folder).glob('poppys-backup-*.zip'))
    assert len(zips) == 1, zips
    return zips[0]


def rows(db):
    with closing(sqlite3.connect(db)) as c:
        tables = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        return {t: c.execute(f'SELECT * FROM "{t}" ORDER BY rowid').fetchall() for t in tables}, \
            sorted(r[0] for r in c.execute("SELECT sql FROM sqlite_master WHERE sql IS NOT NULL"))


# --- backup --------------------------------------------------------------------------------------------------------------
def test_backup_writes_a_verified_zip_with_manifest_and_checksum(app, capsys):
    assert backup(app) == bk.OK
    out = capsys.readouterr().out
    z = only_zip(app.dest)
    assert bk.NAME_RE.match(z.name)
    assert 'integrity_check ok, foreign_key_check ok' in out and f'OK: backup saved to {z}' in out
    side = z.with_name(z.name + '.sha256').read_text(encoding='ascii').split()
    assert side == [sha(z), z.name]
    with zipfile.ZipFile(z) as f:
        assert set(f.namelist()) == {'data/poppys.sqlite3', 'config/oracle_mapping.json', 'config/semantic_catalog.json',
                                     'data/schema_discovery.json', 'manifest.json'}
        manifest = json.loads(f.read('manifest.json'))
        for name, meta in manifest['members'].items():
            body = f.read(name)
            assert hashlib.sha256(body).hexdigest() == meta['sha256'] and len(body) == meta['size']
    assert manifest['format'] == 'poppys-backup/1'
    assert manifest['database']['tables'] == {'users': 2, 'drafts': 60, 'audit': 120}
    assert manifest['database']['integrity_check'] == ['ok'] and manifest['database']['foreign_key_violations'] == 0
    assert set(manifest['app']) >= {'version', 'git_sha'} and manifest['sqlite_version'] == sqlite3.sqlite_version
    assert 'BACKUP OK ' + z.name in (app.dest / 'backup.log').read_text(encoding='utf-8')
    assert not list(app.dest.glob('.partial-*')), 'temporary files are cleaned up'


def test_backup_never_contains_env_files_or_secrets(app):
    assert backup(app, '--include-uploads') == bk.OK
    z = only_zip(app.dest)
    with zipfile.ZipFile(z) as f:
        names = f.namelist()
        assert 'data/a1b2.upload' in names
        assert not [n for n in names if '.env' in n or 'secret' in n]
        assert all(app.secret.encode() not in f.read(n) for n in names)
    assert app.secret.encode() not in z.read_bytes()


def test_backup_is_consistent_while_the_app_holds_a_read_transaction(app):
    with closing(sqlite3.connect(app.db)) as reader:
        reader.execute('BEGIN')
        reader.execute('SELECT count(*) FROM drafts').fetchone()          # a SHARED lock, as during a page load
        assert backup(app) == bk.OK
    assert run('--verify', only_zip(app.dest)) == bk.OK


def test_backup_refuses_a_damaged_database_and_does_not_prune(app, capsys):
    app.dest.mkdir()
    old = [app.dest / f'poppys-backup-2024{m:02d}01-013000.zip' for m in range(1, 13)]
    for p in old:
        p.write_bytes(b'')
    with closing(sqlite3.connect(app.db)) as c:
        page_size = c.execute('PRAGMA page_size').fetchone()[0]
    with open(app.db, 'r+b') as f:                         # overwrite the b-tree header of page 2
        f.seek(page_size)
        f.write(b'\xff' * 64)
    assert backup(app) == bk.FAILED
    out = capsys.readouterr().out
    assert 'FAILED:' in out and ('integrity_check' in out or 'damaged' in out or 'malformed' in out)
    assert sorted(app.dest.glob('poppys-backup-*.zip')) == sorted(old), 'no backup is written from a damaged database'
    assert all(p.exists() for p in old), 'old backups are never pruned after a failed backup'
    assert 'BACKUP FAILED' in (app.dest / 'backup.log').read_text(encoding='utf-8')


def test_backup_refuses_foreign_key_violations(app, capsys):
    with closing(sqlite3.connect(app.db)) as c:            # foreign_keys is OFF by default on a raw connection
        c.execute("INSERT INTO drafts VALUES ('orphan', 'ghost', '{}')")
        c.commit()
    assert backup(app) == bk.FAILED
    assert 'foreign_key_check' in capsys.readouterr().out
    assert not list(app.dest.glob('*.zip'))


def test_backup_stops_when_the_disk_is_too_full(app, monkeypatch, capsys):
    monkeypatch.setattr(bk.shutil, 'disk_usage', lambda p: SimpleNamespace(total=10**9, used=10**9, free=1024))
    assert backup(app) == bk.FAILED
    assert 'disk is too full' in capsys.readouterr().out


def test_usage_problems_exit_2(app, tmp_path, capsys):
    assert run('--data-dir', tmp_path / 'nothing', '--dest', tmp_path / 'b') == bk.USAGE
    assert not (tmp_path / 'nothing' / 'poppys.sqlite3').exists(), 'a missing database is never created'
    assert backup(app, '--label', 'bad label!') == bk.USAGE
    assert run('--restore', 'x.zip', '--dest', app.dest) == bk.USAGE                 # no --to
    assert run('--verify', tmp_path / 'missing.zip') == bk.USAGE
    assert 'FAILED' in capsys.readouterr().out


def test_second_copy_and_its_failure_code(app, tmp_path):
    usb = tmp_path / 'usb' / 'PoppysBackups'
    assert backup(app, '--copy-to', usb) == bk.OK
    z = only_zip(app.dest)
    assert sha(usb / z.name) == sha(z) and (usb / (z.name + '.sha256')).exists()
    blocker = tmp_path / 'not-a-folder'
    blocker.write_text('x')
    assert backup(app, '--copy-to', blocker) == bk.COPY_FAILED
    assert only_zip(app.dest).name != z.name, 'the main backup is still written (and replaces the same-day one)'


# --- verify ----------------------------------------------------------------------------------------------------------------
def rebuild(z, change):
    """Rewrite the zip with valid CRCs; change(name, data) returns new bytes or None to drop the member."""
    with zipfile.ZipFile(z) as f:
        members = [(i.filename, f.read(i.filename)) for i in f.infolist()]
    with zipfile.ZipFile(z, 'w', zipfile.ZIP_DEFLATED) as f:
        for name, data in members:
            data = change(name, data)
            if data is not None:
                f.writestr(name, data)


def flip_inside_db_member(z):
    with zipfile.ZipFile(z) as f:
        info = f.getinfo('data/poppys.sqlite3')
    raw = bytearray(z.read_bytes())
    name_len, extra_len = struct.unpack('<HH', raw[info.header_offset + 26:info.header_offset + 30])
    start = info.header_offset + 30 + name_len + extra_len
    for i in range(start + info.compress_size // 2, start + info.compress_size // 2 + 8):
        raw[i] ^= 0x5A
    z.write_bytes(bytes(raw))


def tampered_db(z):
    def change(name, data):
        if name != 'data/poppys.sqlite3':
            return data
        tmp = z.with_name('tamper.sqlite3')
        tmp.write_bytes(data)
        with closing(sqlite3.connect(tmp)) as c:
            c.execute("UPDATE users SET role='admin' WHERE username='op'")
            c.commit()
        out = tmp.read_bytes()
        tmp.unlink()
        return out
    rebuild(z, change)


DAMAGE = {
    'bytes flipped inside the database member': flip_inside_db_member,
    'file truncated': lambda z: z.write_bytes(z.read_bytes()[: z.stat().st_size // 2]),
    'database replaced with valid CRCs': tampered_db,
    'manifest missing': lambda z: rebuild(z, lambda n, d: None if n == 'manifest.json' else d),
    'unexpected member added': lambda z: _append(z, 'data/extra.txt', b'x'),
    'secret-looking member added': lambda z: _append(z, '.env.poppys', b'ORACLE_USER=X'),
}


def _append(z, name, data):
    with zipfile.ZipFile(z, 'a') as f:
        f.writestr(name, data)


@pytest.mark.parametrize('damage', list(DAMAGE))
def test_verify_detects_a_damaged_backup(app, capsys, damage):
    assert backup(app) == bk.OK
    z = only_zip(app.dest)
    z.with_name(z.name + '.sha256').unlink()          # so the check inside the zip is what catches it
    DAMAGE[damage](z)
    capsys.readouterr()
    assert run('--verify', z) == bk.FAILED
    assert 'FAILED:' in capsys.readouterr().out
    assert run('--restore', z, '--to', app.root / 'restored') == bk.FAILED, 'a damaged backup is never restored'
    assert not (app.root / 'restored' / 'poppys.sqlite3').exists()


def test_verify_detects_a_whole_file_checksum_mismatch(app, capsys):
    assert backup(app) == bk.OK
    z = only_zip(app.dest)
    z.with_name(z.name + '.sha256').write_text('0' * 64 + '  ' + z.name + '\n', encoding='ascii')
    assert run('--verify', z) == bk.FAILED
    assert '.sha256' in capsys.readouterr().out


def test_verify_latest_and_list(app, capsys):
    assert backup(app) == bk.OK
    assert run('--dest', app.dest, '--verify', 'latest') == bk.OK
    assert run('--dest', app.dest, '--list') == bk.OK
    out = capsys.readouterr().out
    assert 'OK: the backup is complete and readable.' in out and f'{only_zip(app.dest).name}' in out and 'keep' in out


# --- retention -------------------------------------------------------------------------------------------------------------
def test_retention_hand_checked_example():
    names = [f'poppys-backup-{(dt.date(2026, 8, 20) + dt.timedelta(days=i)):%Y%m%d}-013000.zip' for i in range(59)]
    names.append('poppys-backup-20261017-180000.zip')      # a second backup on the newest day
    assert names[-2] == 'poppys-backup-20261017-013000.zip'
    keep = bk.plan_retention(names, daily=3, weekly=2, monthly=2)
    # daily: 17 (the 18:00 one), 16, 15 October; weekly: week of 12-18 Oct (17th), week of 5-11 Oct (Sunday 11th);
    # monthly: October (17th), September (30th).
    assert keep == {'poppys-backup-20261017-180000.zip', 'poppys-backup-20261016-013000.zip', 'poppys-backup-20261015-013000.zip',
                    'poppys-backup-20261011-013000.zip', 'poppys-backup-20260930-013000.zip'}


def test_prune_keeps_14_daily_8_weekly_12_monthly_and_nothing_else(tmp_path):
    folder = tmp_path / 'backups'
    folder.mkdir()
    start = dt.datetime(2025, 9, 1, 1, 30)
    times = [start + dt.timedelta(days=i) for i in range(412)]
    times += [t.replace(hour=14, minute=5) for t in times[::9]]           # some days have a pre-upgrade backup too
    for t in times:
        name = f'poppys-backup-{t:%Y%m%d-%H%M%S}' + ('-pre-upgrade-1.0' if t.hour == 14 else '') + '.zip'
        (folder / name).write_bytes(b'zip')
        (folder / (name + '.sha256')).write_text('x')
    untouched = ['notes.txt', 'poppys-backup-latest.zip', 'backup.log', 'last-failed-check.sqlite3']
    for n in untouched:
        (folder / n).write_text('keep me')
    kept, deleted = bk.prune(folder, (14, 8, 12))

    def newest(key, count):
        buckets = sorted({key(t) for t in times}, reverse=True)[:count]
        return {max(t for t in times if key(t) == b) for b in buckets}
    expected = newest(lambda t: t.date(), 14) | newest(lambda t: t.isocalendar()[:2], 8) | newest(lambda t: (t.year, t.month), 12)
    remaining = {bk.backup_time(p.name) for p in folder.glob('poppys-backup-*.zip') if bk.backup_time(p.name)}
    assert remaining == expected
    assert kept == len(expected) and len(deleted) == len(times) - len(expected)
    assert all((folder / n).exists() for n in untouched), 'files that are not backups are never touched'
    for p in folder.glob('poppys-backup-*.zip.sha256'):
        assert (folder / p.name[:-len('.sha256')]).exists(), 'a checksum file goes with its backup'
    assert max(times) in remaining and len({t.date() for t in remaining if t >= max(times) - dt.timedelta(days=13)}) == 14


def test_a_real_backup_prunes_older_backups_after_it_succeeds(app):
    app.dest.mkdir()
    for i in range(40):
        (app.dest / f'poppys-backup-{(dt.date(2026, 1, 1) + dt.timedelta(days=i)):%Y%m%d}-013000.zip').write_bytes(b'')
    assert backup(app) == bk.OK
    names = {p.name for p in app.dest.glob('poppys-backup-*.zip')}
    assert len(names) == len(bk.plan_retention(list(names))) and len(names) < 41
    newest_today = max(names, key=bk.backup_time)
    assert bk.backup_time(newest_today).date() == dt.date.today()


# --- restore ---------------------------------------------------------------------------------------------------------------
def test_restore_round_trip_equals_source_rows(app, tmp_path, capsys):
    assert backup(app) == bk.OK
    z = only_zip(app.dest)
    target = tmp_path / 'restore-drill' / 'data'
    assert run('--restore', z, '--to', target) == bk.OK
    assert rows(target / 'poppys.sqlite3') == rows(app.db)
    assert (target / 'schema_discovery.json').read_bytes() == (app.data / 'schema_discovery.json').read_bytes()
    for name in ('oracle_mapping.json', 'semantic_catalog.json'):
        assert (target / 'config-from-backup' / name).read_bytes() == (app.root / 'config' / name).read_bytes()
    assert not [p for p in target.rglob('*') if '.env' in p.name or 'secret' in p.name]
    assert 'OK: restored into' in capsys.readouterr().out


def test_restore_refuses_to_overwrite_a_database_without_force(app, capsys):
    assert backup(app) == bk.OK
    before = sha(app.db)
    assert run('--restore', only_zip(app.dest), '--to', app.data) == bk.REFUSED
    assert 'REFUSED' in capsys.readouterr().out and sha(app.db) == before


def test_restore_force_is_refused_while_the_database_is_in_use(app, capsys):
    assert backup(app) == bk.OK
    before = sha(app.db)
    with closing(sqlite3.connect(app.db, isolation_level=None)) as other:
        other.execute('BEGIN IMMEDIATE')                  # the app in the middle of a save
        assert run('--restore', only_zip(app.dest), '--to', app.data, '--force', '--port', 0) == bk.REFUSED
        other.execute('ROLLBACK')
    assert 'still seems to be running' in capsys.readouterr().out and sha(app.db) == before


def test_restore_force_is_refused_while_the_app_port_answers(app, capsys):
    assert backup(app) == bk.OK
    with closing(socket.socket()) as s:
        s.bind(('127.0.0.1', 0))
        s.listen()
        port = s.getsockname()[1]
        assert run('--restore', only_zip(app.dest), '--to', app.data, '--force', '--port', port) == bk.REFUSED
    assert f'127.0.0.1:{port}' in capsys.readouterr().out


def test_restore_force_moves_the_live_files_aside_and_never_deletes(app):
    assert backup(app) == bk.OK
    with closing(sqlite3.connect(app.db)) as c:
        c.execute("INSERT INTO users VALUES ('later', 'viewer')")
        c.commit()
    (app.data / 'poppys.sqlite3-journal').write_bytes(b'')     # a leftover journal must not meet the restored file
    assert run('--restore', only_zip(app.dest), '--to', app.data, '--force', '--port', 0) == bk.OK
    with closing(sqlite3.connect(app.db)) as c:
        assert c.execute('SELECT count(*) FROM users').fetchone()[0] == 2
    aside = list(app.data.glob('poppys.sqlite3.before-restore-*'))
    assert len(aside) == 1
    with closing(sqlite3.connect(aside[0])) as c:
        assert c.execute('SELECT count(*) FROM users').fetchone()[0] == 3
    assert not (app.data / 'poppys.sqlite3-journal').exists() and list(app.data.glob('poppys.sqlite3-journal.before-restore-*'))
    assert (app.data / 'a1b2.upload').read_bytes() == b'buyer workbook bytes'


# --- restore drill -----------------------------------------------------------------------------------------------------------
def test_drill_without_the_app(app, capsys):
    assert backup(app) == bk.OK
    assert run('--dest', app.dest, '--app-root', app.root, '--drill', 'latest') == bk.OK
    assert 'OK: restore drill passed' in capsys.readouterr().out
    assert 'DRILL OK' in (app.dest / 'backup.log').read_text(encoding='utf-8')


FAKE_APP = '''
import json, os, sqlite3
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
DATA = Path(os.environ.get('POPPYS_DATA_DIR') or ROOT / 'data')
LEAKS = [k for k in os.environ if k.upper().startswith('POPPYS_') and k.upper() != 'POPPYS_DATA_DIR'] + \\
        [str(p) for p in ROOT.rglob('.env*')]
async def app(scope, receive, send):
    if scope['type'] == 'lifespan':
        while True:
            m = await receive()
            if m['type'] == 'lifespan.startup':
                await send({'type': 'lifespan.startup.complete'})
            elif m['type'] == 'lifespan.shutdown':
                await send({'type': 'lifespan.shutdown.complete'}); return
    if scope['path'] == '/':
        status, body = (500, ('leaked: %s' % LEAKS).encode()) if LEAKS else (200, b'<html>POPPYS</html>')
    elif scope['path'] == '/api/session':
        c = sqlite3.connect(DATA / 'poppys.sqlite3')
        try:
            needs = c.execute('SELECT 1 FROM users LIMIT 1').fetchone() is None
        finally:
            c.close()
        status, body = 200, json.dumps({'needs_setup': needs}).encode()
    else:
        status, body = 404, b'not found'
    await send({'type': 'http.response.start', 'status': status, 'headers': [(b'content-type', b'text/plain')]})
    await send({'type': 'http.response.body', 'body': body})
'''


def free_port():
    with closing(socket.socket()) as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


@pytest.mark.skipif(importlib.util.find_spec('uvicorn') is None, reason='uvicorn is not installed')
def test_drill_starts_the_restored_app_without_oracle_settings(app, tmp_path, monkeypatch, capsys):
    fake = tmp_path / 'fake-app'
    (fake / 'backend').mkdir(parents=True)
    (fake / 'backend' / '__init__.py').write_text('')
    (fake / 'backend' / 'main.py').write_text(FAKE_APP, encoding='utf-8')
    (fake / '.env.poppys').write_text('ORACLE_USER=X\n', encoding='utf-8')     # must not be copied into the drill
    monkeypatch.setenv('POPPYS_ORACLE_DSN', '127.0.0.1:1521/FREEPDB1')          # must not reach the drill app
    assert backup(app) == bk.OK
    capsys.readouterr()
    assert run('--dest', app.dest, '--app-root', fake, '--drill', 'latest', '--start-app', '--drill-port', free_port()) == bk.OK
    out = capsys.readouterr().out
    assert 'answered / with 200' in out and 'needs_setup=False' in out


@pytest.mark.skipif(importlib.util.find_spec('uvicorn') is None, reason='uvicorn is not installed')
def test_drill_fails_when_the_restored_app_cannot_start(app, tmp_path, capsys):
    fake = tmp_path / 'broken-app'
    (fake / 'backend').mkdir(parents=True)
    (fake / 'backend' / '__init__.py').write_text('')
    (fake / 'backend' / 'main.py').write_text('raise RuntimeError("cannot start")\n', encoding='utf-8')
    assert backup(app) == bk.OK
    assert run('--dest', app.dest, '--app-root', fake, '--drill', 'latest', '--start-app', '--drill-port', free_port()) == bk.FAILED
    assert 'stopped during start-up' in capsys.readouterr().out


# --- Windows service files (deploy/windows) and the runbook --------------------------------------------------------------------
def test_service_xml_is_well_formed_and_has_the_researched_settings():
    svc = ET.parse(DEPLOY / 'PoppysAI.xml').getroot()
    assert svc.tag == 'service' and svc.findtext('id') == 'PoppysAI'
    assert svc.findtext('executable').startswith('%BASE%\\current\\')
    assert svc.findtext('workingdirectory') == '%BASE%\\current' and svc.findtext('logpath').startswith('%BASE%\\')
    args = svc.findtext('arguments').split()
    assert args[:2] == ['-E', '-s'] and '-I' not in args
    flat = ' '.join(args)
    for flag in ('-m uvicorn backend.main:app', '--host 127.0.0.1', '--port 8008', '--workers 1', '--no-server-header', '--no-access-log',
                 '--no-proxy-headers', '--limit-concurrency 64', '--timeout-graceful-shutdown 20'):
        assert flag in flat, flag
    acct = svc.find('serviceaccount')
    assert acct.findtext('domain') == 'NT AUTHORITY' and acct.findtext('user') == 'LocalService' and acct.find('password') is None
    assert svc.find('delayedAutoStart') is not None and svc.findtext('startmode') == 'Automatic'
    assert [(f.get('action'), f.get('delay')) for f in svc.findall('onfailure')] == [('restart', '10 sec'), ('restart', '30 sec'), ('restart', '60 sec')]
    assert svc.findtext('resetfailure') == '1 hour' and svc.findtext('stoptimeout') == '30sec'
    log = svc.find('log')
    assert log.get('mode') == 'roll-by-size' and log.findtext('sizeThreshold') == '10240' and log.findtext('keepFiles') == '8'
    assert int(re.search(r'--timeout-graceful-shutdown (\d+)', flat).group(1)) < 30, 'uvicorn must finish before WinSW kills it'


@pytest.mark.parametrize('name', PS_SCRIPTS)
def test_powershell_scripts_are_ascii_and_windows_powershell_51_safe(name):
    text = (DEPLOY / name).read_text(encoding='utf-8')
    assert text.isascii(), 'Windows PowerShell 5.1 reads a script without a BOM as ANSI'
    assert "$ErrorActionPreference = 'Stop'" in text
    assert '#Requires -RunAsAdministrator' in text, 'every script changes services, folders or scheduled tasks'
    code = '\n'.join(line for line in text.splitlines() if not line.lstrip().startswith('#'))
    for token in ('??', '?.', '&&', '||', '-Parallel', 'ForEach-Object -Parallel'):
        assert token not in code, f'{token} needs PowerShell 7'


@pytest.mark.parametrize('path', ROOT_PS, ids=lambda p: p.name)
def test_laptop_powershell_scripts_are_ascii_and_windows_powershell_51_safe(path):
    text = path.read_text(encoding='utf-8')
    assert text.isascii(), 'Windows PowerShell 5.1 reads a script without a BOM as ANSI'
    code = '\n'.join(line for line in text.splitlines() if not line.lstrip().startswith('#'))
    for token in ('??', '?.', '&&', '||', '-Parallel'):
        assert token not in code, f'{token} needs PowerShell 7'


@needs_ps
def test_powershell_scripts_parse(tmp_path):
    checker = tmp_path / 'parse.ps1'
    checker.write_text('''$bad = 0
foreach ($f in $args) {
  $tokens = $null; $errors = $null
  [void][System.Management.Automation.Language.Parser]::ParseFile($f, [ref]$tokens, [ref]$errors)
  foreach ($e in $errors) { Write-Output ("{0}:{1}: {2}" -f $f, $e.Extent.StartLineNumber, $e.Message); $bad++ }
}
exit $bad
''', encoding='ascii')
    files = [str(DEPLOY / n) for n in PS_SCRIPTS] + [str(p) for p in ROOT_PS]
    r = subprocess.run([POWERSHELL, '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', str(checker), *files],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr


def test_scripts_pin_the_winsw_build_and_the_runbook_covers_operations():
    install = (DEPLOY / 'install-service.ps1').read_text(encoding='utf-8')
    assert 'https://github.com/winsw/winsw/releases' in install and '2.12.0' in install
    assert re.search(r"'[0-9a-f]{64}'", install), 'the expected SHA-256 of WinSW-x64.exe 2.12.0 is pinned'
    upgrade = (DEPLOY / 'upgrade.ps1').read_text(encoding='utf-8')
    assert 'backup.py' in upgrade and 'rollback' in upgrade.lower() and '/readyz' in upgrade
    task = (DEPLOY / 'backup-task.ps1').read_text(encoding='utf-8')
    assert "'01:30'" in task and 'backup.py' in task
    runbook = (ROOT / 'docs' / 'RUNBOOK.md').read_text(encoding='utf-8')
    for words in ('Install', 'Start and stop', 'Logs', 'Daily backup', 'Monthly restore drill', 'Upgrade', 'Rollback', 'Oracle or Docker is down',
                  'The disk is full', 'A save failed', 'PP_IMPORT_LOG', 'Audit history', 'Rotate the Oracle password', 'RPO', 'RTO',
                  'Not yet tested on real Windows', 'A backup reports a damaged database'):
        assert words in runbook, words
    for script in re.findall(r'[\w-]+\.ps1', runbook):
        assert (DEPLOY / script).exists() or (ROOT / script).exists() or (ROOT / 'scripts' / script).exists(), script
    assert not re.search(r'(ORACLE_PASSWORD|POPPYS_ORACLE_PASSWORD)\s*[=:]\s*["\']?[A-Za-z0-9]', runbook)


# --- review findings: database versions, restore safety, audit key and anchor --------------------------------------------------
def app_data(data, entries=3):
    """A real POPPYS AI data folder made by the app's own code (backend/local_store.py, current schema): the database
    with a keyed audit history, data/audit.key and data/audit.anchor. Run in a separate Python, so this test module's
    imports never point the app at a temporary folder."""
    code = ('import sys\nfrom backend import local_store as s\ns.init()\n'
            'for i in range(int(sys.argv[1])):\n    s.audit("admin", "test_event", "t%d" % i, n=i)\n')
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith('POPPYS_')}
    env['POPPYS_DATA_DIR'] = str(data)
    data.mkdir(parents=True, exist_ok=True)
    r = subprocess.run([sys.executable, '-c', code, str(entries)], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    return data


def fake_release(tmp_path, schema, name='release-old'):
    release = tmp_path / name
    (release / 'backend').mkdir(parents=True)
    (release / 'backend' / 'local_store.py').write_text(f'SCHEMA_VERSION = {schema}\n', encoding='utf-8')
    return release


def set_user_version(db, n):
    with closing(sqlite3.connect(db)) as c:
        c.execute(f'PRAGMA user_version={int(n)}')
        c.commit()


def test_app_schema_version_is_read_from_the_source_without_importing_it(tmp_path):
    from backend import local_store
    assert bk.app_schema_version(ROOT) == local_store.SCHEMA_VERSION

    def app_with(text, name):
        root = tmp_path / name
        (root / 'backend').mkdir(parents=True)
        if text is not None:
            (root / 'backend' / 'local_store.py').write_text(text, encoding='utf-8')
        return root
    assert bk.app_schema_version(app_with('SCHEMA_VERSION = 7\n', 'plain')) == 7
    assert bk.app_schema_version(app_with('MIGRATIONS = ((1, a), (2, b), (5, c))\nSCHEMA_VERSION = MIGRATIONS[-1][0]\n', 'list')) == 5
    assert bk.app_schema_version(app_with('def init():\n    pass\n', 'before-migrations')) == 0
    assert bk.app_schema_version(app_with(None, 'none')) is None
    with pytest.raises(bk.Problem):
        bk.app_schema_version(app_with('SCHEMA_VERSION = compute()\n', 'unknown'))


def test_db_info_reads_the_database_only_and_says_when_a_release_cannot_open_it(app, tmp_path, capsys):
    release = fake_release(tmp_path, 3)
    set_user_version(app.db, 3)
    assert run('--db-info', '--data-dir', app.data, '--app-root', release, '--log', 'none') == bk.OK
    set_user_version(app.db, 4)                                   # what a newer release's migration does
    before = sha(app.db), app.db.stat().st_mtime_ns
    capsys.readouterr()
    assert run('--db-info', '--data-dir', app.data, '--app-root', release, '--log', 'none') == bk.REFUSED
    out = capsys.readouterr().out
    assert 'Schema version: 4' in out and 'knows schema 3' in out and '2 users, 60 drafts, 120 audit' in out
    assert (sha(app.db), app.db.stat().st_mtime_ns) == before, 'read-only: the live database is not touched'
    assert run('--db-info', '--data-dir', tmp_path / 'nothing', '--log', 'none') == bk.USAGE


def test_restore_refuses_a_backup_from_a_newer_schema_unless_allowed(app, tmp_path, capsys):
    release = fake_release(tmp_path, 3)
    set_user_version(app.db, 4)
    assert backup(app) == bk.OK
    z = only_zip(app.dest)
    live = tmp_path / 'live'
    live.mkdir()
    shutil.copy(app.db, live / 'poppys.sqlite3')
    set_user_version(live / 'poppys.sqlite3', 3)
    before = sha(live / 'poppys.sqlite3')
    capsys.readouterr()
    assert run('--restore', z, '--to', live, '--force', '--port', 0, '--app-root', release, '--log', 'none') == bk.REFUSED
    out = capsys.readouterr().out
    assert 'newer POPPYS AI (database schema 4)' in out and 'knows schema 3' in out and '--allow-newer-schema' in out
    assert sha(live / 'poppys.sqlite3') == before and not list(live.glob('*.before-restore-*')), 'nothing was changed'
    assert run('--restore', z, '--to', live, '--force', '--port', 0, '--app-root', release, '--log', 'none', '--allow-newer-schema') == bk.OK
    assert 'WARNING' in capsys.readouterr().out


@pytest.mark.parametrize('fail_on', ['placing the restored database', 'moving the journal aside'])
def test_a_restore_that_fails_half_way_puts_every_file_back(app, monkeypatch, capsys, fail_on):
    assert backup(app) == bk.OK
    z = only_zip(app.dest)
    with closing(sqlite3.connect(app.db)) as c:
        c.execute("INSERT INTO users VALUES ('later', 'viewer')")
        c.commit()
    (app.data / 'poppys.sqlite3-journal').write_bytes(b'')       # moved aside right after the database
    before = {p.name: sha(p) for p in app.data.iterdir() if p.is_file()}
    real = os.replace

    def flaky(src, dst):
        src, dst = Path(src), Path(dst)
        if fail_on.startswith('placing') and '.partial-restore-' in str(src) and dst.name == 'poppys.sqlite3':
            raise PermissionError(13, 'The process cannot access the file because it is being used by another process', str(dst))
        if fail_on.startswith('moving') and src.name == 'poppys.sqlite3-journal' and '.before-restore-' in dst.name:
            raise PermissionError(13, 'The process cannot access the file because it is being used by another process', str(src))
        return real(src, dst)
    monkeypatch.setattr(bk.os, 'replace', flaky)
    code = run('--restore', z, '--to', app.data, '--force', '--port', 0, '--log', 'none')
    monkeypatch.undo()
    out = capsys.readouterr().out
    assert code == (bk.FAILED if fail_on.startswith('placing') else bk.REFUSED), out
    assert 'Nothing was changed' in out and 'put back' in out
    assert {p.name: sha(p) for p in app.data.iterdir() if p.is_file()} == before, 'every file is back, unchanged'
    assert not [p for p in app.data.iterdir() if '.before-restore-' in p.name or p.name.startswith('.partial-restore')]
    assert not (app.data / 'config-from-backup').exists(), 'a folder the restore created is removed again'
    with closing(sqlite3.connect(app.db)) as c:
        assert c.execute('SELECT count(*) FROM users').fetchone()[0] == 3


def test_rollback_never_starts_the_service_without_its_database():
    text = (DEPLOY / 'rollback.ps1').read_text(encoding='utf-8')
    i = text.index("$ToolsBackupPy, '--restore'")
    failure = text[i:text.index("Write-Step \"4. Switching to $To\"")]
    assert failure.index('Test-Path -LiteralPath $LiveDb') < failure.index('Start-AppService'), 'checked before the restart'
    assert "'start=', 'demand'" in failure, 'not even at the next Windows start'
    assert 'The database was not changed' not in text


def test_rollback_checks_the_database_version_and_upgrade_hands_over_the_pre_upgrade_backup():
    rb = (DEPLOY / 'rollback.ps1').read_text(encoding='utf-8')
    assert "'--db-info', '--data-dir', $DataDir, '--app-root', $target" in rb
    assert "'--restore', $RestoreDatabase, '--to', $DataDir, '--force',\n      '--app-root', $target" in rb, 'the restore checks the version too'
    main = rb[rb.index('$ToolsBackupPy = Resolve-RealPath'):]
    assert main.index('Test-DatabaseFit') < main.index('Stop-Service -Name $ServiceName'), 'a refusal costs no downtime'
    assert '$RestoreDatabase = $PreUpgradeBackup' in main and 'Get-NewerDatabaseText' in main
    assert main.index("Write-Step \"2. Checking") < main.index('Set-Junction $CurrentLink $target')
    up = (DEPLOY / 'upgrade.ps1').read_text(encoding='utf-8')
    auto = up[up.index('rolling back to $oldVersion automatically'):]
    assert 'PreUpgradeBackup = $backupZip.FullName' in auto
    runbook = (ROOT / 'docs' / 'RUNBOOK.md').read_text(encoding='utf-8')
    section = runbook[runbook.index('## 7. Rollback'):runbook.index('## 8.')]
    assert 'The database is not changed. That is right in nearly every case' not in section
    assert 'newer database' in section and '-RestoreDatabase' in section


def test_pre_upgrade_backups_are_never_pruned_by_the_upgrade(app):
    pre = app.dest / 'pre-upgrade'
    first = dt.datetime(2026, 10, 17, 10, 0)
    for hours in (0, 3):                                          # a failed attempt in the morning, a second one later
        bk.make_backup(app.data, pre, app.root, label='pre-upgrade-0.9.0', prune_old=False, now=first + dt.timedelta(hours=hours))
    assert len(list(pre.glob('poppys-backup-20261017-*-pre-upgrade-0.9.0.zip'))) == 2
    up = (DEPLOY / 'upgrade.ps1').read_text(encoding='utf-8')
    call = re.search(r'\$code = Invoke-NativeTimeout \$python @\((.*?)\) \(\$BackupMinutes \* 60\)', up, re.S).group(1)
    assert "'--dest', $preDir" in call and "'--no-prune'" in call


def test_backup_finishes_while_the_app_writes_continuously(app):
    """A step-wise copy (backup(pages=1024)) starts again after every write by another connection: with the app writing
    every few milliseconds it never finished. The one-step copy does, in about a second."""
    with closing(sqlite3.connect(app.db)) as c:
        c.execute('CREATE TABLE filler(id INTEGER PRIMARY KEY, v BLOB)')
        c.executemany('INSERT INTO filler(v) VALUES (?)', [(os.urandom(2000),) for _ in range(3000)])
        c.commit()
    writer = subprocess.Popen([sys.executable, '-c', 'import os, sqlite3, sys, time\nc = sqlite3.connect(sys.argv[1], timeout=20)\n'
                               'while True:\n    with c:\n        c.execute("UPDATE filler SET v=? WHERE id=1", (os.urandom(2000),))\n'
                               '    time.sleep(0.005)\n', str(app.db)])
    try:
        time.sleep(0.3)
        r = subprocess.run([sys.executable, str(ROOT / 'scripts' / 'backup.py'), '--data-dir', str(app.data), '--app-root', str(app.root),
                            '--dest', str(app.dest), '--log', 'none', '--no-prune'], capture_output=True, text=True, timeout=60)
    finally:
        writer.kill()
        writer.wait()
    assert r.returncode == 0, r.stdout + r.stderr
    up = (DEPLOY / 'upgrade.ps1').read_text(encoding='utf-8')
    assert "'taskkill.exe' @('/PID', [string]$proc.Id, '/T', '/F')" in up, 'the upgrade stops a backup that runs too long'


def test_backup_carries_the_audit_key_and_anchor_and_a_restore_keeps_the_history_provable(tmp_path, capsys):
    data = app_data(tmp_path / 'data')
    dest = tmp_path / 'backups'
    assert run('--data-dir', data, '--dest', dest, '--log', 'none') == bk.OK
    z = only_zip(dest)
    with zipfile.ZipFile(z) as f:
        manifest = json.loads(f.read('manifest.json'))
        for name in bk.AUDIT_MEMBERS:
            body = f.read(name)
            assert body == (data / name.split('/')[1]).read_bytes()
            assert manifest['members'][name] == {'sha256': hashlib.sha256(body).hexdigest(), 'size': len(body)}
        assert not [n for n in f.namelist() if '.env' in n or 'secret' in n]
    capsys.readouterr()
    assert run('--verify', z, '--log', 'none') == bk.OK
    assert 'audit key and anchor ..................... ok' in capsys.readouterr().out
    fresh = tmp_path / 'restored'
    assert run('--restore', z, '--to', fresh, '--log', 'none') == bk.OK
    assert 'Audit history of the restored data: ok (3 entries' in capsys.readouterr().out
    assert bk.check_audit_chain(ROOT, fresh)['ok'] is True


def test_restore_force_brings_back_the_anchor_of_the_restored_database(tmp_path):
    data = app_data(tmp_path / 'data', entries=2)
    dest = tmp_path / 'backups'
    assert run('--data-dir', data, '--dest', dest, '--log', 'none') == bk.OK
    app_data(data, entries=3)                                     # later work: the live anchor now names entry 5
    assert bk.check_audit_chain(ROOT, data)['last_id'] == 5
    assert run('--restore', only_zip(dest), '--to', data, '--force', '--port', 0, '--log', 'none') == bk.OK
    result = bk.check_audit_chain(ROOT, data)
    assert result['ok'] is True and result['last_id'] == 2, f'the anchor came back with its database: {result}'
    assert list(data.glob('audit.anchor.before-restore-*')) and list(data.glob('audit.key.before-restore-*')), 'moved aside, not deleted'


def without_member(z, member):
    """The zip as an older backup.py wrote it: without `member` and its manifest entry."""
    def change(name, body):
        if name == member:
            return None
        if name == 'manifest.json':
            manifest = json.loads(body)
            manifest['members'].pop(member)
            return json.dumps(manifest).encode()
        return body
    rebuild(z, change)
    z.with_name(z.name + '.sha256').unlink(missing_ok=True)


def test_a_backup_without_the_audit_key_is_flagged(tmp_path, capsys):
    data = app_data(tmp_path / 'data')
    dest = tmp_path / 'backups'
    assert run('--data-dir', data, '--dest', dest, '--log', 'none') == bk.OK
    z = only_zip(dest)
    without_member(z, 'data/audit.key')
    capsys.readouterr()
    assert run('--verify', z, '--log', 'none') == bk.OK, 'the data itself is complete'
    assert 'WARNING: this backup has no audit key (audit.key)' in capsys.readouterr().out
    assert run('--restore', z, '--to', tmp_path / 'fresh', '--log', 'none') == bk.AUDIT_UNPROVEN
    out = capsys.readouterr().out
    assert 'RESTORED, but the audit history cannot be proven untouched' in out and 'audit key file' in out
    assert run('--drill', z, '--log', 'none') == bk.FAILED, 'a drill must prove the audit history'
    assert bk.looks_secret('data/server.key') and bk.looks_secret('config/audit.key') and not bk.looks_secret('data/audit.key')


def test_an_older_backup_without_an_anchor_gets_one_from_the_restored_database(tmp_path, capsys):
    data = app_data(tmp_path / 'data')
    dest = tmp_path / 'backups'
    assert run('--data-dir', data, '--dest', dest, '--log', 'none') == bk.OK
    z = only_zip(dest)
    without_member(z, 'data/audit.anchor')
    fresh = tmp_path / 'fresh'
    capsys.readouterr()
    assert run('--restore', z, '--to', fresh, '--log', 'none') == bk.OK
    assert 'a new one was written from the restored database' in capsys.readouterr().out
    assert json.loads((fresh / 'audit.anchor').read_text(encoding='utf-8'))['id'] == 3


def test_drill_proves_the_audit_history_and_fails_on_a_wrong_key(tmp_path, capsys):
    data = app_data(tmp_path / 'data')
    dest = tmp_path / 'backups'
    assert run('--data-dir', data, '--dest', dest, '--log', 'none') == bk.OK
    z = only_zip(dest)
    capsys.readouterr()
    assert run('--drill', z, '--log', 'none') == bk.OK
    assert 'Audit history of the restored data: ok' in capsys.readouterr().out
    other = secrets.token_hex(32).encode()

    def change(name, body):
        if name == 'data/audit.key':
            return other
        if name == 'manifest.json':
            manifest = json.loads(body)
            manifest['members']['data/audit.key'] = {'sha256': hashlib.sha256(other).hexdigest(), 'size': len(other)}
            return json.dumps(manifest).encode()
        return body
    rebuild(z, change)
    z.with_name(z.name + '.sha256').unlink()
    assert run('--drill', z, '--log', 'none') == bk.FAILED
    assert 'rewritten' in capsys.readouterr().out


# --- PowerShell functions, taken out of the scripts with the PowerShell parser -------------------------------------------------
def ps_run(tmp_path, script, functions, body, env=None, timeout=120):
    """Define `functions` from `script` (parsed, the script itself is not run) and run `body`; returns its output.
    Inputs travel through environment variables, never through command-line quoting."""
    runner = tmp_path / f'ps-run-{len(list(tmp_path.glob("ps-run-*.ps1")))}.ps1'
    names = ', '.join(f"'{n}'" for n in functions)
    runner.write_text("$ErrorActionPreference = 'Stop'\n$tokens = $null; $errors = $null\n"
                      '$ast = [System.Management.Automation.Language.Parser]::ParseFile($env:PS_SCRIPT, [ref]$tokens, [ref]$errors)\n'
                      f'foreach ($name in @({names})) {{\n'
                      '  $fn = $ast.Find({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $name }, $true)\n'
                      '  if (-not $fn) { throw "function $name is missing" }\n'
                      '  . ([scriptblock]::Create($fn.Extent.Text))\n}\n' + body + '\n', encoding='ascii')
    full = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
    full.update(env or {})
    full['PS_SCRIPT'] = str(script)
    r = subprocess.run([POWERSHELL, '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', str(runner)],
                       capture_output=True, text=True, timeout=timeout, env=full)
    assert r.returncode == 0, r.stdout + r.stderr
    return r.stdout


def argv_split(line):
    """Split a command line the way python.exe builds sys.argv on Windows (the Microsoft C runtime rules)."""
    args, cur, i, quoted, have = [], [], 0, False, False
    while i < len(line):
        c = line[i]
        if c == '\\':
            j = i
            while j < len(line) and line[j] == '\\':
                j += 1
            n = j - i
            if j < len(line) and line[j] == '"':
                cur.append('\\' * (n // 2))
                if n % 2:
                    cur.append('"')
                    j += 1
            else:
                cur.append('\\' * n)
            i, have = j, True
        elif c == '"':
            if quoted and i + 1 < len(line) and line[i + 1] == '"':
                cur.append('"')
                i += 2
            else:
                quoted, have, i = not quoted, True, i + 1
        elif c in ' \t' and not quoted:
            if have:
                args.append(''.join(cur))
                cur, have = [], False
            i += 1
        else:
            cur.append(c)
            have, i = True, i + 1
    if have:
        args.append(''.join(cur))
    return args


ARG_VALUES = ['--data-dir', 'C:\\ProgramData\\PoppysAI\\data', '--copy-to', 'E:\\', 'D:\\Poppys Backups\\', 'x"y', 'C:\\a b\\c"d\\', '',
              'D:\\A&B', '100%', '--include-uploads']


@needs_ps
@pytest.mark.parametrize('script,function', [('backup-task.ps1', 'Join-CommandLine'), ('upgrade.ps1', None)])
def test_command_lines_survive_windows_argument_splitting(tmp_path, script, function):
    # Before the fix, -SecondCopy E:\ was written as --copy-to "E:\" and Python read E:" plus everything after it.
    assert argv_split('--copy-to "E:\\" --include-uploads') == ['--copy-to', 'E:" --include-uploads']
    join = (f'{function} $values' if function else "(@($values | ForEach-Object { ConvertTo-CommandLineArgument $_ }) -join ' ')")
    out = ps_run(tmp_path, DEPLOY / script, ['ConvertTo-CommandLineArgument'] + ([function] if function else []),
                 f'$values = $env:PS_VALUES | ConvertFrom-Json\nWrite-Output ({join})', env={'PS_VALUES': json.dumps(ARG_VALUES)})
    line = out.rstrip('\r\n')
    assert argv_split(line) == ARG_VALUES, line
    assert ' E:\\ ' in line, 'a drive root needs no quotes at all'


@needs_ps
def test_backup_task_refresh_reads_the_old_second_copy_folder(tmp_path):
    cases = ['-E -s -X utf8 "C:\\p\\backup.py" --dest "C:\\d" --copy-to "E:\\" --include-uploads',   # written before the fix
             '--dest C:\\d --copy-to E:\\ --include-uploads', '--copy-to "D:\\Poppys Backups\\\\"', '--dest C:\\d']
    out = ps_run(tmp_path, DEPLOY / 'backup-task.ps1', ['Get-CopyToOption'],
                 "$cases = $env:PS_CASES | ConvertFrom-Json\nforeach ($a in $cases) { Write-Output ('[' + (Get-CopyToOption $a) + ']') }",
                 env={'PS_CASES': json.dumps(cases)})
    assert out.split() == ['[E:\\]', '[E:\\]', '[D:\\Poppys', 'Backups\\]', '[]']


@needs_ps
def test_nightly_task_picks_the_active_releases_python_when_it_runs(tmp_path):
    wrapper = tmp_path / 'nightly-backup.cmd'
    ps_run(tmp_path, DEPLOY / 'backup-task.ps1', ['Write-BackupWrapper'], 'Write-BackupWrapper $env:PS_OUT $env:PS_APP',
           env={'PS_OUT': str(wrapper), 'PS_APP': 'C:\\ProgramData\\PoppysAI\\current'})
    raw = wrapper.read_bytes()
    assert raw.isascii() and raw.endswith(b'\r\n') and b'\n' not in raw.replace(b'\r\n', b''), 'cmd.exe wants CRLF lines'
    text = raw.decode('ascii')
    assert 'set "APP=C:\\ProgramData\\PoppysAI\\current"' in text
    assert text.index('%APP%\\python\\python.exe') < text.index('%APP%\\.venv\\Scripts\\python.exe'), 'the same choice as the service'
    assert '"%PY%" -E -s -X utf8 "%APP%\\scripts\\backup.py" %*' in text and text.rstrip().endswith('exit /b %ERRORLEVEL%')
    task = (DEPLOY / 'backup-task.ps1').read_text(encoding='utf-8')
    assert '$program = Join-Path $InstallRoot $WrapperName' in task and 'New-ScheduledTaskAction -Execute $program -Argument $argString' in task
    up = (DEPLOY / 'upgrade.ps1').read_text(encoding='utf-8')
    assert "Invoke-Helper 'backup-task.ps1' @{ InstallRoot = $InstallRoot; Refresh = $true }" in up


@needs_ps
def test_rollback_restores_the_restart_settings_of_poppysai_xml(tmp_path):
    out = ps_run(tmp_path, DEPLOY / 'rollback.ps1', ['ConvertTo-Millisecond', 'Get-RecoverySetting', 'Write-Note'],
                 '$s = Get-RecoverySetting $env:PS_XML\nWrite-Output ("{0}|{1}" -f $s.Reset, $s.Actions)', env={'PS_XML': str(DEPLOY / 'PoppysAI.xml')})
    assert out.strip().splitlines()[-1] == '3600|restart/10000/restart/30000/restart/60000'
    rb = (DEPLOY / 'rollback.ps1').read_text(encoding='utf-8')
    assert "@('failure', $ServiceName, 'reset=', '0', 'actions=', '\"\"')" in rb
    main = rb[rb.index('$ToolsBackupPy = Resolve-RealPath'):]
    assert main.index('Suspend-ServiceRecovery') < main.index('Stop-Service -Name $ServiceName -Force') < main.index('Set-Junction')
    start = re.search(r'function Start-AppService \{(.*?)\n\}', rb, re.S).group(1)
    assert start.index('Resume-ServiceRecovery') < start.index('Start-Service')
    assert re.search(r'function Stop-Switch\(.*?\{\s*Resume-ServiceRecovery', rb, re.S), 'every failure turns it on again'


def force_remove(func, path, exc):
    os.chmod(path, 0o700)            # git marks pack files read-only on Windows
    func(path)


@needs_ps
@pytest.mark.skipif(not GIT, reason='git is not installed')
def test_release_version_of_a_git_checkout_names_the_commit(tmp_path):
    repo = tmp_path / 'checkout'
    (repo / 'backend').mkdir(parents=True)
    (repo / 'VERSION').write_text('0.9.0\n', encoding='ascii')
    (repo / 'backend' / 'main.py').write_text('x = 1\n', encoding='ascii')
    genv = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}

    def git(*a):
        return subprocess.run([GIT, '-c', 'user.name=t', '-c', 'user.email=t@example.com', *a], cwd=repo, env=genv, check=True,
                              capture_output=True, text=True).stdout.strip()

    def version():
        return ps_run(tmp_path, DEPLOY / 'install-service.ps1', ['Get-ContentHash', 'Get-GitOutput', 'Get-ReleaseVersion', 'Write-Note'],
                      'Write-Output (Get-ReleaseVersion $env:PS_DIR)', env={'PS_DIR': str(repo)}).strip().splitlines()[-1]
    git('init', '-q')
    git('add', '-A')
    git('commit', '-q', '-m', 'one')
    first = git('rev-parse', '--short=7', 'HEAD')
    assert version() == f'0.9.0-{first}'
    (repo / 'backend' / 'main.py').write_text('x = 2\n', encoding='ascii')
    assert re.fullmatch(rf'0\.9\.0-{first}-dirty-[0-9a-f]{{8}}', version()), 'uncommitted changes are a release of their own'
    git('commit', '-q', '-am', 'two')
    assert version() == f"0.9.0-{git('rev-parse', '--short=7', 'HEAD')}", 'a git pull is a new release, with the same VERSION'
    shutil.rmtree(repo / '.git', onexc=force_remove)
    assert version() == '0.9.0', 'a release zip (no .git) keeps its VERSION'


@needs_ps
@pytest.mark.skipif(not GIT, reason='git is not installed')
def test_release_copy_of_docs_takes_only_the_files_git_tracks(tmp_path):
    repo = tmp_path / 'checkout'
    (repo / 'docs' / 'sub').mkdir(parents=True)
    (repo / 'docs' / 'workbook_analysis').mkdir()
    (repo / '.gitignore').write_text('docs/workbook_analysis/\ndocs/live_sample_records.txt\n', encoding='ascii')
    (repo / 'docs' / 'RUNBOOK.md').write_text('# runbook\n', encoding='ascii')
    (repo / 'docs' / 'sub' / 'live_schema_columns.json').write_text('{}\n', encoding='ascii')
    (repo / 'docs' / 'workbook_analysis' / 'client order.txt').write_text('buyer prices\n', encoding='ascii')
    (repo / 'docs' / 'live_sample_records.txt').write_text('real rows\n', encoding='ascii')
    genv = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
    for args in (('init', '-q'), ('add', '-A'), ('-c', 'user.name=t', '-c', 'user.email=t@example.com', 'commit', '-q', '-m', 'docs')):
        subprocess.run([GIT, *args], cwd=repo, env=genv, check=True, capture_output=True)
    release = tmp_path / 'release'
    (release / 'docs').mkdir(parents=True)
    (release / 'docs' / 'stale.md').write_text('from an earlier copy\n', encoding='ascii')
    ps_run(tmp_path, DEPLOY / 'install-service.ps1', ['Copy-Doc', 'Get-GitOutput', 'Write-Note', 'Invoke-Native'],
           'Copy-Doc $env:PS_FROM $env:PS_TO', env={'PS_FROM': str(repo), 'PS_TO': str(release)})
    assert sorted(p.relative_to(release).as_posix() for p in release.rglob('*') if p.is_file()) == ['docs/RUNBOOK.md', 'docs/sub/live_schema_columns.json']
    install = (DEPLOY / 'install-service.ps1').read_text(encoding='utf-8')
    parts = re.search(r"function Copy-Release.*?foreach \(\$part in @\(([^)]*)\)\)", install, re.S).group(1)
    assert "'docs'" not in parts and 'Copy-Doc $From $To' in install


# --- setup.ps1: every step's exit code is checked -------------------------------------------------------------------------------------
FAKE_PYTHON = '''#!/bin/sh
printf '%s\n' "$*" >> "$SETUP_LOG"
if [ -n "$FAIL_ON" ]; then
  case "$*" in *"$FAIL_ON"*) echo "stand-in failure for: $*" >&2; exit 1 ;; esac
fi
if [ "$1" = "-c" ]; then echo 3.13; fi
exit 0
'''
SETUP_CASES = {   # case: (FAIL_ON, message, last program that ran)
    'no-py': (None, 'The Python launcher (py) was not found', None),
    'pip': ('pip install --upgrade', 'Updating pip failed', 'pip install --upgrade'),
    'docker-missing': ('', 'Docker was not found', 'pip install -r'),
    'bootstrap': ('bootstrap_local.py', 'Creating the POPPYS_APP account failed', 'bootstrap_local.py'),
    'grant': ('grant_bom_tables.py', 'Granting the Oracle permissions failed', 'grant_bom_tables.py'),
    'check': ('check_oracle.py', 'The Oracle check found a problem', 'check_oracle.py'),
    'ok': ('', 'Setup finished. Next: .\\.venv\\Scripts\\python.exe scripts\\discover_schema.py', 'check_oracle.py'),
}


@needs_ps
@pytest.mark.skipif(os.name == 'nt', reason='the stand-in programs are shell scripts')
@pytest.mark.parametrize('case', list(SETUP_CASES))
def test_setup_stops_at_the_first_failing_step(tmp_path, case):
    fail_on, message, last = SETUP_CASES[case]
    work = tmp_path / 'repo'
    work.mkdir()
    shutil.copy(ROOT / 'setup.ps1', work / 'setup.ps1')
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    log = tmp_path / 'calls.log'
    log.write_text('')
    if fail_on is not None:                                       # an existing .venv; 'no-py' has none and no py launcher
        venv = work / '.venv' / 'Scripts'
        venv.mkdir(parents=True)
        (venv / 'python.exe').write_text(FAKE_PYTHON)
        (venv / 'python.exe').chmod(0o755)
    if case not in ('no-py', 'docker-missing'):
        (bin_dir / 'docker').write_text('#!/bin/sh\necho true\n')
        (bin_dir / 'docker').chmod(0o755)
    env = {**os.environ, 'PATH': str(bin_dir), 'SETUP_LOG': str(log), 'FAIL_ON': fail_on or ''}
    r = subprocess.run([POWERSHELL, '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', str(work / 'setup.ps1')],
                       capture_output=True, text=True, timeout=120, env=env, cwd=work)
    out = r.stdout + r.stderr
    assert message in out, out
    calls = log.read_text().splitlines()
    if case == 'ok':
        assert r.returncode == 0 and 'FAILED' not in out
    else:
        assert r.returncode == 1 and 'FAILED:' in out and 'Setup finished' not in out, out
    if last:
        assert last in calls[-1], calls
    else:
        assert calls == []


def test_setup_checks_each_program_and_names_the_venv_python():
    text = (ROOT / 'setup.ps1').read_text(encoding='utf-8')
    lines = [x.strip() for x in text.splitlines()]
    for i, line in enumerate(lines):
        if line.startswith('& '):
            assert 'LASTEXITCODE' in lines[i + 1], f'no exit-code check after: {line}'
    assert text.index("Get-Command py") < text.index('& py -3.13'), 'a missing launcher is found before $LASTEXITCODE is read'
    assert 'Next: python scripts' not in text


# --- the test-side guard for real-Oracle tests (tests/conftest.py) --------------------------------------------------------------
def load_conftest():
    spec = importlib.util.spec_from_file_location('poppys_conftest', ROOT / 'tests' / 'conftest.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('dsn,local', [
    ('127.0.0.1:1521/FREEPDB1', True), ('localhost/FREEPDB1', True), ('//LOCALHOST:1521/X', True), ('[::1]:1521/FREEPDB1', True),
    ('tcp://127.0.0.1:1521/FREEPDB1?connect_timeout=5', True), ('127.0.0.1:1521,localhost:1522/X', True), ('127.0.0.2:1521/X', True),
    ('(DESCRIPTION=(ADDRESS=(PROTOCOL=TCP)(HOST=127.0.0.1)(PORT=1521))(CONNECT_DATA=(SERVICE_NAME=FREEPDB1)))', True),
    ('db.example.com:1521/PROD', False), ('10.0.0.5/ORCL', False), ('ORCLPDB', False), ('127.0.0.1:1521,erp-db:1521/X', False),
    ('(DESCRIPTION=(ADDRESS=(PROTOCOL=TCP)(HOST=erp.example.com)(PORT=1521)))', False), ('[2001:db8::1]:1521/X', False)])
def test_oracle_write_tests_only_run_against_this_computer(dsn, local):
    assert (load_conftest().remote_oracle_reason(dsn) is None) == local


def test_pytest_refuses_to_start_oracle_tests_against_another_computer():
    env = {k: v for k, v in os.environ.items()}
    env.update(POPPYS_TEST_ORACLE='1', POPPYS_ORACLE_DSN='erp-db.example.com:1521/PROD')
    cmd = [sys.executable, '-m', 'pytest', '-q', '--collect-only', '-p', 'no:cacheprovider', 'tests/test_backup.py']
    r = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True, text=True, timeout=180)
    assert r.returncode == 4 and 'erp-db.example.com, not this computer' in r.stdout + r.stderr, r.stdout + r.stderr
    env['POPPYS_ORACLE_DSN'] = '127.0.0.1:1521/FREEPDB1'                # the CI Oracle job and the laptop's container
    r = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True, text=True, timeout=180)
    assert r.returncode == 0, r.stdout + r.stderr


# --- the remaining Windows-script findings ------------------------------------------------------------------------------------------
def test_reinstall_asks_which_database_when_the_checkout_one_is_newer():
    text = (DEPLOY / 'install-service.ps1').read_text(encoding='utf-8')
    assert "[ValidateSet('Source', 'Installed')][string]$UseDatabase" in text
    assert text.index('$sourceTime -gt $liveTime') < text.index("Write-Step 'Stopping the running service'"), 'asked before anything stops'
    question = text[text.index('$sourceTime -gt $liveTime'):text.index("Write-Step 'Stopping the running service'")]
    assert "'--db-info'" in question and '-UseDatabase Source' in question and '-UseDatabase Installed' in question
    assert "if ($replacing) { $restoreArgs += '--force' }" in text, 'the service database is moved aside by the restore, not deleted'


def test_install_never_copies_over_the_active_release():
    text = (DEPLOY / 'install-service.ps1').read_text(encoding='utf-8')
    guard = text[text.index('$repairOnly = $InPlace'):text.index('# Which database')]
    assert '$ActiveVersion -ieq $Version' in guard and '(Get-ContentHash $Source) -ne (Get-ContentHash $Release)' in guard
    assert 'Initialize-Release -From $Source -Release $Release -Ver $Version -InPlace $repairOnly' in text
    staged = text[text.index('if ($StageOnly) {'):]
    assert staged.index('exit 3') < staged.index('Initialize-Release'), 'upgrade.ps1 staging never touches the active release either'


@pytest.mark.parametrize('name', ['install-service.ps1', 'rollback.ps1'])
def test_settings_copy_is_protected_before_it_reaches_the_release_folder(name):
    text = (DEPLOY / name).read_text(encoding='utf-8')
    fn = re.search(r'function Sync-OracleSetting.*?\n\}\n', text, re.S).group(0)
    assert "Join-Path $ConfigDir ('.env.poppys.staging-'" in fn
    assert fn.index('Protect-SettingsFile $staged') < fn.index('[System.IO.File]::Move($staged, $copy)')
    assert 'Copy-Item -LiteralPath $master -Destination $copy' not in fn
    assert 'Remove-Item -LiteralPath $staged -Force' in fn, 'fail closed: nothing unprotected is left behind'


def test_upgrade_failure_prints_the_full_path_of_rollback():
    up = (DEPLOY / 'upgrade.ps1').read_text(encoding='utf-8')
    assert "$rollbackScript = Join-Path $newRelease 'deploy\\windows\\rollback.ps1'" in up
    assert "\"& '$rollbackScript' -To $oldVersion -RestoreDatabase '$($backupZip.FullName)'\"" in up


def test_password_rotation_never_shows_the_new_password():
    runbook = (ROOT / 'docs' / 'RUNBOOK.md').read_text(encoding='utf-8')
    section = runbook[runbook.index('## 10. Rotate the Oracle password'):runbook.index('## 11.')]
    assert 'whenever sqlerror exit failure' in section
    line = next(x for x in section.splitlines() if 'docker exec -i oracle26ai sqlplus' in x)
    assert line.rstrip().endswith('| Out-Null'), 'SQL*Plus echoes the failing statement, password included'
    assert '$LASTEXITCODE' in section and 'if ($ok)' in section


def test_every_launcher_ignores_proxy_headers():
    """Every client is 127.0.0.1, which uvicorn trusts for X-Forwarded-For by default: with proxy headers on, any local
    program could choose the address the sign-in throttle counts."""
    assert '--no-proxy-headers' in (ROOT / 'start.ps1').read_text(encoding='utf-8')
    assert '--no-proxy-headers' in ET.parse(DEPLOY / 'PoppysAI.xml').getroot().findtext('arguments').split()
    assert '--no-proxy-headers' in bk.uvicorn_command(8018)
    assert 'subprocess.Popen(uvicorn_command(port)' in (ROOT / 'scripts' / 'backup.py').read_text(encoding='utf-8')
    assert 'proxy_headers=False' in (ROOT / 'tests' / 'demo_server.py').read_text(encoding='utf-8')
    runbook = (ROOT / 'docs' / 'RUNBOOK.md').read_text(encoding='utf-8')
    for line in [x for x in runbook.splitlines() if 'uvicorn backend.main:app' in x]:
        assert '--no-proxy-headers' in line, line


def test_readonly_account_settings_go_into_the_service_master_copy(tmp_path, monkeypatch, capsys):
    sys.path.insert(0, str(ROOT / 'scripts'))
    try:
        spec = importlib.util.spec_from_file_location('poppys_create_readonly_user', ROOT / 'scripts' / 'create_readonly_user.py')
        cru = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cru)
    finally:
        sys.path.remove(str(ROOT / 'scripts'))
    from dotenv import dotenv_values
    main_line = 'ORACLE_USER=POPPYS_APP\n' + 'ORACLE_PASSWORD' + '=' + secrets.token_hex(8) + '\n'
    install = tmp_path / 'PoppysAI'
    (install / 'config').mkdir(parents=True)
    master, checkout = install / 'config' / '.env.poppys', tmp_path / 'checkout.env.poppys'
    master.write_text(main_line, encoding='utf-8')
    checkout.write_text(main_line, encoding='utf-8')
    monkeypatch.setenv('POPPYS_INSTALL_ROOT', str(install))
    monkeypatch.setattr(cru, 'CONFIG', checkout)
    state = {'ro_exists': '0', 'created': []}

    def fake_sqlplus(sql):
        if sql.startswith('create user'):
            assert 'ORACLE_RO_PASSWORD' in master.read_text(encoding='utf-8'), 'saved before the account is created'
            state['created'].append(sql)
            return ''
        if "username='POPPYS_RO'" in sql:
            return state['ro_exists']
        if "username='POPPYS_APP'" in sql or "table_name='PP_IMPORT_LOG'" in sql:
            return '1'
        return 'TABLES=5\nREADONLY=yes' if 'dbms_output' in sql else ''
    monkeypatch.setattr(cru, 'sqlplus', fake_sqlplus)
    cru.main()
    out = capsys.readouterr().out
    m, c = dotenv_values(master), dotenv_values(checkout)
    assert m['ORACLE_RO_USER'] == 'POPPYS_RO' and m['ORACLE_RO_PASSWORD'] and m['ORACLE_RO_PASSWORD'] == c['ORACLE_RO_PASSWORD']
    assert str(master) in out and 'install-service.ps1' in out and m['ORACLE_RO_PASSWORD'] not in out
    assert len(state['created']) == 1
    # The account exists and only this folder's copy has its lines (made before the service install): the master gets them.
    master.write_text(main_line, encoding='utf-8')
    state['ro_exists'] = '1'
    cru.main()
    assert dotenv_values(master)['ORACLE_RO_PASSWORD'] == c['ORACLE_RO_PASSWORD'] and len(state['created']) == 1
    assert str(master) in capsys.readouterr().out


# --- rollback.ps1 end to end, with only its Windows-specific helpers replaced (service, junction, sc.exe, icacls) ----------------
ROLLBACK_STUBS = {
    'Get-JunctionTarget': 'function Get-JunctionTarget([string]$Path) { $f = $Path + ".junction"; if (Test-Path -LiteralPath $f) { return [string](Get-Content -LiteralPath $f -TotalCount 1) }; return "" }',
    'Set-Junction': 'function Set-Junction([string]$Path, [string]$Target) { Add-Content $env:SIM_LOG "junction $(Split-Path -Leaf $Target)"; Set-Content -LiteralPath ($Path + ".junction") -Value $Target }',
    'Wait-PortFree': 'function Wait-PortFree([int]$Seconds) { return $true }',
    'Wait-PoppysReady': 'function Wait-PoppysReady([int]$Seconds) { Add-Content $env:SIM_LOG "ready"; return "/readyz" }',
    'Invoke-Native': ('function Invoke-Native([string]$FilePath, [string[]]$ArgumentList) {\n'
                      "  if ($FilePath -match '(sc|icacls)\\.exe$') { Add-Content $env:SIM_LOG ($FilePath + ' ' + ($ArgumentList -join ' ')); return 0 }\n"
                      "  $saved = $ErrorActionPreference; $ErrorActionPreference = 'Continue'\n"
                      "  try { & $FilePath @ArgumentList 2>&1 | ForEach-Object { Write-Host ('   ' + $_) }; return $LASTEXITCODE } finally { $ErrorActionPreference = $saved }\n}"),
}
ROLLBACK_PRELUDE = '''function Get-Service { param([string]$Name, $ErrorAction) return [pscustomobject]@{ Name = $Name; Status = [string](Get-Content $env:SIM_STATE -TotalCount 1) } }
function Stop-Service { param([string]$Name, [switch]$Force) Add-Content $env:SIM_LOG 'stop'; Set-Content $env:SIM_STATE 'Stopped' }
function Start-Service { param([string]$Name) Add-Content $env:SIM_LOG 'start'; Set-Content $env:SIM_STATE 'Running' }
'''


@pytest.fixture
def rollback_sim(tmp_path):
    """An install folder with releases 0.8 (database schema 3) and 0.9 (schema 4, active) and rollback.ps1 in a source
    folder next to the real scripts/backup.py. Returns run(*args) -> (exit code, output, log of service actions)."""
    if os.name == 'nt' or not POWERSHELL:
        pytest.skip('simulated on Linux/macOS with PowerShell 7 (stand-in releases link the test Python)')
    inst, src = tmp_path / 'PoppysAI', tmp_path / 'source'
    for d in ('releases', 'data/backups/pre-upgrade', 'logs', 'config'):
        (inst / d).mkdir(parents=True)
    (inst / 'PoppysAI.exe').write_text('WinSW stand-in')
    shutil.copy(DEPLOY / 'PoppysAI.xml', inst / 'PoppysAI.xml')
    for version, schema in (('0.8', 3), ('0.9', 4)):
        release = inst / 'releases' / version
        (release / 'backend').mkdir(parents=True)
        (release / 'backend' / 'main.py').write_text('')
        (release / 'backend' / 'local_store.py').write_text(f'SCHEMA_VERSION = {schema}\n')
        (release / '.venv' / 'Scripts').mkdir(parents=True)
        os.symlink(sys.executable, release / '.venv' / 'Scripts' / 'python.exe')
    (inst / 'current.junction').write_text(str(inst / 'releases' / '0.9'))
    (inst / 'previous.txt').write_text('0.8\n')
    (src / 'deploy' / 'windows').mkdir(parents=True)
    (src / 'scripts').mkdir()
    shutil.copy(ROOT / 'scripts' / 'backup.py', src / 'scripts' / 'backup.py')
    text = (DEPLOY / 'rollback.ps1').read_text(encoding='utf-8').replace('#Requires -RunAsAdministrator', '').replace('$Port = 8008', f'$Port = {free_port()}', 1)
    for name, stub in ROLLBACK_STUBS.items():
        m = re.search(r'^function ' + re.escape(name) + r'\b.*?^\}\n', text, re.S | re.M)
        text = text[:m.start()] + stub + '\n' + text[m.end():]
    i = text.index("$ErrorActionPreference = 'Stop'")
    (src / 'deploy' / 'windows' / 'rollback.ps1').write_text(text[:i] + ROLLBACK_PRELUDE + text[i:], encoding='utf-8')
    log, state = tmp_path / 'sim.log', tmp_path / 'state.txt'
    db = inst / 'data' / 'poppys.sqlite3'

    def run_rollback(*args):
        log.write_text('')
        state.write_text('Running')
        env = {**os.environ, 'SIM_LOG': str(log), 'SIM_STATE': str(state)}
        r = subprocess.run([POWERSHELL, '-NoProfile', '-NonInteractive', '-File', str(src / 'deploy' / 'windows' / 'rollback.ps1'), '-InstallRoot', str(inst),
                            *map(str, args)], capture_output=True, text=True, env=env, timeout=300)
        return r.returncode, r.stdout + r.stderr, log.read_text().splitlines()
    app_data(inst / 'data', entries=2)
    set_user_version(db, 3)                                        # as 0.8 left it
    r = subprocess.run([sys.executable, str(src / 'scripts' / 'backup.py'), '--data-dir', str(inst / 'data'), '--app-root', str(inst / 'releases' / '0.8'),
                        '--dest', str(inst / 'data' / 'backups' / 'pre-upgrade'), '--label', 'pre-upgrade-0.8', '--no-prune', '--log', 'none'],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout
    set_user_version(db, 4)                                        # 0.9 upgraded the database
    return SimpleNamespace(run=run_rollback, inst=inst, src=src, db=db, zip=only_zip(inst / 'data' / 'backups' / 'pre-upgrade'))


def test_rollback_to_a_release_that_cannot_open_the_database_changes_nothing(rollback_sim):
    code, out, log = rollback_sim.run()
    assert code == 1 and log == [], 'refused before the service was stopped'
    assert 'release 0.8 cannot open it' in out and f"-To 0.8 -RestoreDatabase '{rollback_sim.zip}'" in out, out
    with closing(sqlite3.connect(rollback_sim.db)) as c:
        assert c.execute('PRAGMA user_version').fetchone()[0] == 4


def test_automatic_rollback_restores_the_pre_upgrade_backup_when_the_database_is_too_new(rollback_sim):
    code, out, log = rollback_sim.run('-To', '0.8', '-PreUpgradeBackup', rollback_sim.zip, '-Reason', 'automatic')
    assert code == 0, out
    assert log[0].endswith('failure PoppysAI reset= 0 actions= ""') and log[1] == 'stop', 'restarts paused before the stop'
    assert log[2:] == ['junction 0.8', 'sc.exe failure PoppysAI reset= 3600 actions= restart/10000/restart/30000/restart/60000', 'start', 'ready']
    with closing(sqlite3.connect(rollback_sim.db)) as c:
        assert c.execute('PRAGMA user_version').fetchone()[0] == 3, 'the pre-upgrade database is back'
    assert list(rollback_sim.db.parent.glob('poppys.sqlite3.before-restore-*')), 'the newer one is kept aside'


def test_rollback_does_not_start_the_service_when_a_failed_restore_left_no_database(rollback_sim):
    backup_py = rollback_sim.src / 'scripts' / 'backup.py'          # a restore that dies after moving the database aside
    backup_py.write_text(backup_py.read_text(encoding='utf-8').replace(
        "if __name__ == '__main__':\n    sys.exit(main())",
        "if __name__ == '__main__':\n    if '--restore' in sys.argv:\n        to = Path(sys.argv[sys.argv.index('--to') + 1])\n"
        "        (to / DB_NAME).rename(to / (DB_NAME + '.before-restore-x'))\n        sys.exit(1)\n    sys.exit(main())"), encoding='utf-8')
    code, out, log = rollback_sim.run('-To', '0.9', '-RestoreDatabase', rollback_sim.zip)
    assert code == 1 and 'start' not in log, log
    assert 'sc.exe config PoppysAI start= demand' in log and 'was NOT started' in out
