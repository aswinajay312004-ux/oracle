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


@pytest.mark.skipif(not POWERSHELL, reason='PowerShell is not installed')
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
    files = [str(DEPLOY / n) for n in PS_SCRIPTS]
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
