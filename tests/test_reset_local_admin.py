import sqlite3

from backend.main import password_hash
from scripts.reset_local_admin import reset_password


def test_reset_backs_up_workspace_and_invalidates_old_sessions(tmp_path):
    db_path = tmp_path / 'poppys.sqlite3'
    old_salt = '00' * 16
    with sqlite3.connect(db_path) as conn:
        conn.executescript('''
            CREATE TABLE users(username TEXT PRIMARY KEY, salt TEXT, password_hash TEXT);
            CREATE TABLE sessions(token_hash TEXT, username TEXT, expires REAL);
            CREATE TABLE drafts(id TEXT PRIMARY KEY, payload TEXT);
        ''')
        conn.execute('INSERT INTO users VALUES (?,?,?)', ('admin', old_salt, password_hash('previous-password', old_salt)))
        conn.execute('INSERT INTO sessions VALUES (?,?,?)', ('token', 'admin', 9999999999))
        conn.execute('INSERT INTO drafts VALUES (?,?)', ('order-1', '{}'))

    backup_path = reset_password(db_path, 'admin', 'replacement-password')

    with sqlite3.connect(db_path) as conn:
        salt, stored_hash = conn.execute('SELECT salt,password_hash FROM users WHERE username=?', ('admin',)).fetchone()
        assert stored_hash == password_hash('replacement-password', salt)
        assert stored_hash != password_hash('previous-password', salt)
        assert conn.execute('SELECT count(*) FROM sessions').fetchone()[0] == 0
        assert conn.execute('SELECT payload FROM drafts WHERE id=?', ('order-1',)).fetchone()[0] == '{}'
    with sqlite3.connect(backup_path) as backup:
        assert backup.execute('SELECT count(*) FROM sessions').fetchone()[0] == 1
        assert backup.execute('SELECT payload FROM drafts WHERE id=?', ('order-1',)).fetchone()[0] == '{}'
