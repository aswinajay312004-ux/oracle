"""Sharing the app with other computers through a Cloudflare quick tunnel (share-online.ps1): off unless POPPYS_REMOTE=1,
and then only the tunnel's https address is accepted besides this laptop's own names."""
import pytest
from fastapi.testclient import TestClient
from backend import local_store, main
from test_acceptance import offline_ai

LINK = 'quiet-river-1234.trycloudflare.com'
SAME = {'Sec-Fetch-Site': 'same-origin'}


@pytest.fixture
def app(tmp_path, monkeypatch):
    for mod in (local_store, main):
        monkeypatch.setattr(mod, 'DATA', tmp_path)
    offline_ai(monkeypatch)
    with TestClient(main.app) as c:
        assert c.post('/api/setup', json={'username': 'owner', 'password': 'a-long-demo-password-1'}, headers=SAME).status_code == 200
        yield c, monkeypatch


def via_tunnel(c, method, path, **kw):
    headers = {'Host': LINK, 'Origin': f'https://{LINK}', 'Cf-Connecting-Ip': kw.pop('ip', '203.0.113.7'), **SAME, **kw.pop('headers', {})}
    return c.request(method, f'http://{LINK}{path}', headers=headers, **kw)


def test_the_tunnel_address_is_refused_while_sharing_is_off(app):
    c, _ = app
    assert via_tunnel(c, 'GET', '/healthz').status_code == 400
    assert c.get('http://evil.example/healthz').status_code == 400
    assert c.get('/healthz').status_code == 200


def test_sharing_on_lets_a_friend_sign_in_and_work_through_the_https_link(app):
    c, mp = app
    mp.setattr(main, 'REMOTE', True)
    c.cookies.clear()
    assert via_tunnel(c, 'GET', '/').status_code == 200
    r = via_tunnel(c, 'POST', '/api/login', json={'username': 'owner', 'password': 'a-long-demo-password-1'})
    assert r.status_code == 200, r.text
    cookie = r.headers['set-cookie'].lower()
    assert 'secure' in cookie and 'httponly' in cookie and 'samesite=strict' in cookie
    token = r.cookies.get('poppys_session')
    me = via_tunnel(c, 'GET', '/api/session', headers={'Cookie': f'poppys_session={token}'})
    assert me.status_code == 200 and me.json()['username'] == 'owner'
    # another site, or the http form of the link, is still refused for changes
    assert via_tunnel(c, 'POST', '/api/logout', headers={'Origin': 'https://evil.example', 'Sec-Fetch-Site': 'cross-site'}).status_code == 403
    assert via_tunnel(c, 'POST', '/api/logout', headers={'Origin': f'http://{LINK}', 'Sec-Fetch-Site': 'same-origin'}).status_code == 403
    assert c.get('http://evil.example/healthz').status_code == 400


def test_through_the_link_no_first_administrator_and_one_persons_wrong_passwords_do_not_lock_out_another(app):
    c, mp = app
    mp.setattr(main, 'REMOTE', True)
    assert via_tunnel(c, 'POST', '/api/setup', json={'username': 'intruder', 'password': 'another-long-password'}).status_code == 403
    for _ in range(12):
        via_tunnel(c, 'POST', '/api/login', json={'username': 'guesser', 'password': 'wrong-password-123'}, ip='198.51.100.9')
    assert via_tunnel(c, 'POST', '/api/login', json={'username': 'guesser', 'password': 'wrong-password-123'}, ip='198.51.100.9').status_code == 429
    r = via_tunnel(c, 'POST', '/api/login', json={'username': 'owner', 'password': 'a-long-demo-password-1'}, ip='203.0.113.50')
    assert r.status_code == 200, r.text
