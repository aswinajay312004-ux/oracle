"""Interactively replace this computer's POPPYS login password.

This does not change the Oracle account password or any saved order. It backs up
the local SQLite database before updating the selected account.
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime
from getpass import getpass
from pathlib import Path
import secrets
import sqlite3
import sys

# Allow `python scripts/reset_local_admin.py` from the repository root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.config import DATA  # noqa: E402
from backend.main import password_hash  # noqa: E402


def reset_password(db_path: Path, username: str, new_password: str) -> Path:
    """Back up the existing workspace, then change one user's password and sessions."""
    if not 10 <= len(new_password) <= 256:
        raise ValueError('Password must contain 10 to 256 characters.')
    db_path = db_path.resolve()
    if not db_path.is_file():
        raise FileNotFoundError(f'Local account database was not found: {db_path}')
    with closing(sqlite3.connect(f'file:{db_path.as_posix()}?mode=rw', uri=True, timeout=20)) as conn:
        if not conn.execute('SELECT 1 FROM users WHERE username=?', (username,)).fetchone():
            raise ValueError('That username does not exist in this local workspace.')
        backup_dir = db_path.parent / 'backups'
        backup_dir.mkdir(exist_ok=True)
        backup_path = backup_dir / f'poppys-before-password-reset-{datetime.now():%Y%m%d-%H%M%S}-{secrets.token_hex(3)}.sqlite3'
        with closing(sqlite3.connect(backup_path)) as backup:
            conn.backup(backup)
        salt = secrets.token_hex(16)
        with conn:
            conn.execute('UPDATE users SET salt=?, password_hash=? WHERE username=?',
                         (salt, password_hash(new_password, salt), username))
            conn.execute('DELETE FROM sessions WHERE username=?', (username,))
    return backup_path


def main() -> int:
    db_path = DATA / 'poppys.sqlite3'
    if not db_path.is_file():
        print(f'No POPPYS account database at {db_path}. Check POPPYS_DATA_DIR and the folder used to start the app.')
        return 1
    with closing(sqlite3.connect(f'file:{db_path.resolve().as_posix()}?mode=ro', uri=True)) as conn:
        usernames = [row[0] for row in conn.execute('SELECT username FROM users ORDER BY username')]
    if not usernames:
        print('No account exists here. Start the app and use its first-time setup screen.')
        return 1
    print(f'Local workspace: {DATA}')
    print('Accounts: ' + ', '.join(usernames))
    username = input('Type the exact username to reset: ').strip()
    if username not in usernames:
        print('Username did not match; nothing changed.')
        return 1
    new_password = getpass('New password (10-256 characters): ')
    confirmation = getpass('Repeat new password: ')
    if new_password != confirmation:
        print('Passwords did not match; nothing changed.')
        return 1
    try:
        backup_path = reset_password(db_path, username, new_password)
    except (ValueError, sqlite3.Error, OSError) as exc:
        print(f'Password was not reset: {exc}')
        return 1
    print(f'Password updated for {username}. Previous sessions were signed out.')
    print(f'Backup: {backup_path}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
