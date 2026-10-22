"""Opt-in DDL check for the current admin schema designer on local GARMENT_DEMO.

Set both POPPYS_TEST_ORACLE=1 and POPPYS_TEST_DDL=1. The test adds one TEST_ table.
"""
import os
import secrets

import pytest
from fastapi.testclient import TestClient

from backend import local_store, main


pytestmark = pytest.mark.skipif(
    os.getenv('POPPYS_TEST_ORACLE') != '1' or os.getenv('POPPYS_TEST_DDL') != '1',
    reason='Set POPPYS_TEST_ORACLE=1 and POPPYS_TEST_DDL=1 for local schema tests',
)


def test_create_table_with_explicit_confirmation(tmp_path, monkeypatch):
    monkeypatch.setattr(local_store, 'DATA', tmp_path)
    monkeypatch.setattr(main, 'DATA', tmp_path)
    name = 'TEST_PRINT_' + secrets.token_hex(3).upper()
    spec = dict(name=name, columns=[dict(name='PRINT_ID', type='NUMBER', required=True),
                                    dict(name='ORDER_ID', type='NUMBER'),
                                    dict(name='INK_TYPE', type='VARCHAR2', length=40)], primary_key=['PRINT_ID'])
    with TestClient(main.app) as client:
        assert client.post('/api/setup', json={'username': 'admin', 'password': 'test-password-123'}).status_code == 200
        preview = client.post('/api/db/tables/preview', json=spec)
        assert preview.status_code == 200, preview.text
        plan = preview.json()
        assert plan['errors'] == [] and plan['enabled']
        assert client.post('/api/db/tables', json=dict(spec=spec, hash=plan['hash'], confirm='WRONG')).status_code == 409
        created = client.post('/api/db/tables', json=dict(spec=spec, hash=plan['hash'], confirm=name))
        assert created.status_code == 200, created.text
        detail = client.get(f'/api/db/tables/{name}')
        assert detail.status_code == 200, detail.text
        assert [c['name'] for c in detail.json()['columns']] == ['PRINT_ID', 'ORDER_ID', 'INK_TYPE']
