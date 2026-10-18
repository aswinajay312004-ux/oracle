"""Sign-in security (docs/RESEARCH_DECISIONS.md, "Security: passwords and throttling", "sessions and cookies").

Passwords are hashed with scrypt from the standard library at the OWASP Password Storage Cheat Sheet setting
(N=2^17, r=8, p=1: 128 MiB and about half a second per check, so guessing offline is slow). The decision named
Argon2id; scrypt is the cheat sheet's listed alternative, memory-hard in the same way, and needs no compiled
package in the offline Windows install. A stored hash names its own settings ("scrypt$17$8$1$salt$hash"), so a
stronger setting later re-hashes each password at its next sign-in. Accounts from earlier versions (PBKDF2-SHA256,
300,000 rounds, salt column) still sign in, once, and are re-hashed then.

New passwords: at least 12 characters, not a known common password, not the username or this product's own words.

Wrong passwords are counted in SQLite (so the count survives a restart) per account name, whether or not the
account exists, so a refusal never tells that an account exists. After 5 wrong passwords in 15 minutes, each
further try for that name waits 2^(n-5) seconds, at most 15 minutes; 30 wrong passwords from one address in
15 minutes pause that address. A correct password clears the account's count.
"""
from __future__ import annotations
import hashlib
import hmac
import re
import secrets
import time
from functools import lru_cache

from . import local_store as store
from .config import ROOT

COST = dict(n=2 ** 17, r=8, p=1)
MAXMEM = 256 * 1024 * 1024
LEGACY_ROUNDS = 300_000
MIN_LENGTH = 12
WINDOW_S = 900; FREE_TRIES = 5; MAX_WAIT_S = 900; ADDRESS_LIMIT = 30
IDLE_S = 1800; ABSOLUTE_S = 28800; TOUCH_S = 60       # sessions: idle and absolute limits, last_seen refreshed at most each minute
OWN_WORDS = {'password', 'passwd', 'qwerty', 'admin', 'administrator', 'welcome', 'letmein', 'iloveyou', 'changeme', 'secret',
             'poppys', 'poppysai', 'poppysknitwear', 'knitwear', 'garment', 'garments', 'oracle', 'axpert', 'order', 'orders'}


def hash_password(password):
    n, r, p = COST['n'], COST['r'], COST['p']
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=n, r=r, p=p, maxmem=MAXMEM, dklen=32)
    return f'scrypt${n.bit_length() - 1}${r}${p}${salt.hex()}${digest.hex()}'


def verify(password, stored, legacy_salt=''):
    """(correct, should_rehash) for a stored hash of this version or of an earlier one."""
    stored = stored or ''
    if stored.startswith('scrypt$'):
        try:
            _, logn, r, p, salt, digest = stored.split('$')
            n, r, p = 2 ** int(logn), int(r), int(p)
            got = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=n, r=r, p=p, maxmem=MAXMEM, dklen=len(digest) // 2)
        except (ValueError, MemoryError):
            return False, False
        ok = hmac.compare_digest(got.hex(), digest)
        return ok, ok and (n < COST['n'] or r < COST['r'] or p < COST['p'])
    try:
        got = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(legacy_salt or ''), LEGACY_ROUNDS).hex()
    except ValueError:
        return False, False
    ok = bool(stored) and hmac.compare_digest(got, stored)
    return ok, ok


def verify_unknown(password):
    """The same work as checking a real account, so an unknown name answers as slowly as a wrong password."""
    verify(password, _dummy(COST['n'], COST['r'], COST['p']))
    return False


@lru_cache(maxsize=4)
def _dummy(n, r, p):
    return hash_password(secrets.token_urlsafe(16))


@lru_cache(maxsize=1)
def _common():
    path = ROOT / 'config' / 'common_passwords.txt'
    try:
        return frozenset(x.strip() for x in path.read_text(encoding='utf-8').splitlines() if x.strip() and not x.startswith('#'))
    except OSError:
        return frozenset()


def problem(password, username=''):
    """Why a new password is not acceptable, or None."""
    if len(password) < MIN_LENGTH:
        return f'Use at least {MIN_LENGTH} characters for the password.'
    low = password.lower()
    letters = re.sub(r'[^a-z]', '', low)
    if low in _common() or letters in OWN_WORDS or len(set(low)) < 5:
        return 'That password is too easy to guess (a common password or a word from this product). Choose another one.'
    if username and len(username) >= 3 and username.lower() in low:
        return 'The password must not contain the username.'
    return None


# --- throttling ---------------------------------------------------------------------------------
def wait_seconds(username, address, now=None):
    """Seconds this sign-in must wait before it is checked at all (0 when it may go ahead)."""
    now = now or time.time(); since = now - WINDOW_S
    with store.db() as c:
        account = c.execute('SELECT COUNT(*), MAX(at) FROM auth_failures WHERE username=? AND at>?', (username.lower(), since)).fetchone()
        from_address = c.execute('SELECT COUNT(*), MIN(at) FROM auth_failures WHERE address=? AND at>?', (address, since)).fetchone()
    waits = []
    if account[0] >= FREE_TRIES:
        waits.append(account[1] + min(2 ** (account[0] - FREE_TRIES), MAX_WAIT_S) - now)
    if from_address[0] >= ADDRESS_LIMIT:
        waits.append(from_address[1] + WINDOW_S - now)
    return max(0, int(-(-max(waits) // 1))) if waits else 0


def failed(username, address, now=None):
    now = now or time.time()
    with store.db() as c:
        c.execute('DELETE FROM auth_failures WHERE at<?', (now - 86400,))
        c.execute('INSERT INTO auth_failures(username,address,at) VALUES (?,?,?)', (username.lower(), address, now))


def succeeded(username):
    with store.db() as c:
        c.execute('DELETE FROM auth_failures WHERE username=?', (username.lower(),))
