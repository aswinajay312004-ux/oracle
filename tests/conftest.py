"""Shared test setup. Password hashing runs at a low scrypt cost here (the stored hash names its own cost, so
verification is the same code); tests/test_auth.py checks that the product's own setting is the OWASP one."""
import pytest
from backend import auth


@pytest.fixture(autouse=True)
def quick_password_hashing(monkeypatch):
    monkeypatch.setattr(auth, 'COST', dict(n=2 ** 10, r=8, p=1))
