"""Test-wide settings. Uploads are parsed in-process for speed; tests named test_isolated_* in
tests/test_formats.py exercise the production path (a separate worker process with time and memory limits).

POPPYS_COOKIE_SECURE=false: the session cookie is Secure in production (Edge and Chrome treat http://localhost
and http://127.0.0.1 as secure contexts), but TestClient talks to http://testserver, which is not one, so its
cookie jar would never send a Secure cookie back. tests/test_security.py checks the production flags.

POPPYS_TEST_ORACLE=1 turns on tests that save TEST orders to Oracle and commit them. pytest_configure() refuses to
run them unless the Oracle address the app would use (POPPYS_ORACLE_DSN, else ORACLE_DSN in .env.poppys, else the
default) is this computer: 127.0.0.1, localhost or ::1, as in CI and on the laptop's own Docker container."""
import ipaddress
import os
import re
from pathlib import Path

import pytest

os.environ.setdefault('POPPYS_INLINE_PARSE', '1')
os.environ.setdefault('POPPYS_COOKIE_SECURE', 'false')
os.environ.setdefault('POPPYS_EXTRA_HOSTS', 'testserver')    # TestClient's Host; the app answers only to loopback names otherwise


DEFAULT_DSN = '127.0.0.1:1521/FREEPDB1'      # backend/oracle_store.py DEFAULT_DSN


def oracle_dsn():
    """The DSN the app would connect to, resolved like backend/config.py setting('ORACLE_DSN')."""
    try:
        from backend.config import setting
        return setting('ORACLE_DSN', DEFAULT_DSN)
    except Exception:                          # the guard must work even when the app does not import
        from dotenv import dotenv_values
        values = dotenv_values(Path(__file__).resolve().parents[1] / '.env.poppys')
        return os.environ.get('POPPYS_ORACLE_DSN', values.get('ORACLE_DSN') or DEFAULT_DSN)


def dsn_hosts(dsn):
    """Every host an Oracle DSN names: EZConnect ([tcp://][//]host[:port][/service], several hosts with commas, IPv6
    in brackets) or a connect descriptor ((DESCRIPTION=...(HOST=x)...)). A TNS alias gives the alias itself, which is
    not a loopback name, so it is refused."""
    dsn = (dsn or '').strip()
    if '(' in dsn:
        return re.findall(r'\(\s*HOST\s*=\s*([^)\s]+)\s*\)', dsn, re.I)
    address = re.sub(r'^[A-Za-z][A-Za-z0-9+.-]*://', '', dsn).lstrip('/')
    address = re.split(r'[/?]', address, maxsplit=1)[0]
    hosts = []
    for part in address.split(','):
        part = part.strip()
        if part.startswith('['):
            hosts.append(part[1:part.find(']')] if ']' in part else part[1:])
        elif part:
            hosts.append(part.split(':')[0])
    return hosts


def is_loopback(host):
    if host.lower() == 'localhost':
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def remote_oracle_reason(dsn):
    """Why tests that write to Oracle must not run against `dsn`, or None when every host is this computer."""
    hosts = dsn_hosts(dsn)
    if hosts and all(is_loopback(h) for h in hosts):
        return None
    where = ', '.join(hosts) or 'no host at all'
    return (f'POPPYS_TEST_ORACLE=1 runs tests that save TEST orders to Oracle and commit them, but the Oracle address '
            f'names {where}, not this computer. These tests run only against a local throwaway Oracle (127.0.0.1, '
            'localhost or ::1), never a shared or production database. Point POPPYS_ORACLE_DSN at the local test '
            'container, or unset POPPYS_TEST_ORACLE.')


def pytest_configure(config):
    if os.environ.get('POPPYS_TEST_ORACLE') == '1':
        reason = remote_oracle_reason(oracle_dsn())
        if reason:
            pytest.exit(reason, returncode=4)


def pytest_make_parametrize_id(config, val, argname):
    """Long file contents make test IDs of thousands of characters; pytest puts the ID in the
    PYTEST_CURRENT_TEST environment variable, which Windows caps at 32,767 characters."""
    if isinstance(val, (bytes, str)) and len(val) > 60:
        return f'{argname}-{len(val)}'
    return None
