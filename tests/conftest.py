"""Shared test setup. Password hashing runs at a low scrypt cost here (the stored hash names its own cost, so
verification is the same code); tests/test_auth.py checks that the product's own setting is the OWASP one.
Long or unprintable test parameters get a short, stable test ID."""
import hashlib
import pytest
from backend import auth

ID_LIMIT = 60


def pytest_make_parametrize_id(config, val, argname):
    """A parameter such as a 200,000-character JSON file or random bytes would become the test ID. pytest passes the ID
    to every test through an environment variable, and Windows refuses variables longer than 32,767 characters, so
    such tests failed there before they ran. Such values are named by their length and a hash instead."""
    if isinstance(val, (str, bytes)) and (len(val) > ID_LIMIT or not (val.decode('latin-1') if isinstance(val, bytes) else val).isprintable()):
        raw = val if isinstance(val, bytes) else val.encode('utf-8', 'surrogatepass')
        return f'{argname}-{len(val)}-{hashlib.sha256(raw).hexdigest()[:10]}'
    return None


@pytest.fixture(autouse=True)
def quick_password_hashing(monkeypatch):
    monkeypatch.setattr(auth, 'COST', dict(n=2 ** 10, r=8, p=1))
