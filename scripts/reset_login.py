"""Remove every POPPYS AI sign-in account on THIS computer, so a new administrator can be created:

    python scripts\\reset_login.py

Use it when the administrator's username (for example a Gmail address) or password must change, or when the
password is lost. It lists the accounts, asks you to type DELETE, then removes all accounts and signs everyone
out. Nothing else changes: drafts, mappings, the data dictionary, the audit history and Oracle are not touched.
The reset itself is written to the audit history. Then open http://127.0.0.1:8008: the app asks for the new
administrator's username and password (the same first-run screen as a new install).
"""
from __future__ import annotations
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend import local_store as store  # noqa: E402


def main(argv=None, ask=input):
    store.init()
    with store.db() as c:
        accounts = [(r['username'], r['role']) for r in c.execute('SELECT username, role FROM users ORDER BY username')]
    if not accounts:
        print('There are no sign-in accounts. Open http://127.0.0.1:8008 and create the administrator.')
        return 0
    print('Sign-in accounts on this computer:')
    for name, role in accounts:
        print(f'  {name}  ({role})')
    if '--yes' not in (argv or []) and ask('Type DELETE to remove all of them: ').strip() != 'DELETE':
        print('Nothing was changed.')
        return 1
    with store.db() as c:
        c.execute('DELETE FROM sessions')
        c.execute('DELETE FROM users')
    store.audit('console', 'login_reset', 'workspace', removed=[name for name, _ in accounts])
    print(f'Removed {len(accounts)} account(s); everyone is signed out.\n'
          'Now open http://127.0.0.1:8008 and create the new administrator (new username or Gmail, and a new password of 10+ characters).')
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
