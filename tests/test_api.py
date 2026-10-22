import os
from copy import deepcopy
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from backend import local_store, main

@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.setattr(local_store,'DATA',tmp_path)
    monkeypatch.setattr(main,'DATA',tmp_path)
    with TestClient(main.app) as c:yield c

def test_authentication_required_and_setup_once(client):
    assert client.get('/api/drafts').status_code==401
    assert client.get('/api/session').json()['needs_setup']
    assert client.post('/api/setup',json={'username':'tester','password':'test-password-123'}).status_code==200
    assert client.get('/api/session').json()['authenticated']
    assert client.post('/api/setup',json={'username':'another','password':'test-password-123'}).status_code==409
    assert client.post('/api/logout').status_code==200
    assert client.post('/api/login',json={'username':'tester','password':'wrong-password'}).status_code==401
    assert client.post('/api/login',json={'username':'tester','password':'test-password-123'}).status_code==200

def test_cross_origin_and_unsupported_upload_rejected(client):
    assert client.post('/api/setup',headers={'Origin':'https://attacker.example'},json={'username':'tester','password':'test-password-123'}).status_code==403
    client.post('/api/setup',json={'username':'tester','password':'test-password-123'})
    assert client.post('/api/uploads',files={'file':('bad.xls',b'bad')}).status_code==415

@pytest.mark.skipif(os.getenv('POPPYS_TEST_ORACLE')!='1',reason='Opt-in local Oracle API preview test')
def test_real_merch_upload_corrections_preview_and_stale_accept(client):
    path=Path(r'C:\Users\ASUS2025\Downloads\Merch Dept Worked Manual Excel (2).xlsx')
    if not path.exists():pytest.skip('Client workbook not installed')
    client.post('/api/setup',json={'username':'tester','password':'test-password-123'})
    r=client.post('/api/uploads',files={'file':(path.name,path.read_bytes())})
    assert r.status_code==200,r.text
    d=r.json();id=d['id']
    first=client.post(f'/api/drafts/{id}/preview',json={'version':d['version']}).json()
    assert first['plan']['table']=='ORDERENTRY' and first['plan']['totals']['main_rows']>0
    assert client.post(f'/api/drafts/{id}/accept',json={'version':first['version']-1,'plan_hash':first['plan']['hash']}).status_code==409
    # Client data is never committed by this fixture; synthetic TEST orders cover approval.
