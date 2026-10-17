"""Test-wide settings. Uploads are parsed in-process for speed; tests named test_isolated_* in
tests/test_formats.py exercise the production path (a separate worker process with time and memory limits).

POPPYS_COOKIE_SECURE=false: the session cookie is Secure in production (Edge and Chrome treat http://localhost
and http://127.0.0.1 as secure contexts), but TestClient talks to http://testserver, which is not one, so its
cookie jar would never send a Secure cookie back. tests/test_security.py checks the production flags."""
import os

os.environ.setdefault('POPPYS_INLINE_PARSE', '1')
os.environ.setdefault('POPPYS_COOKIE_SECURE', 'false')
