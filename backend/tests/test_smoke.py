import os
from pathlib import Path

TEST_DB = Path(__file__).resolve().parent.parent / "test_cps.db"
if TEST_DB.exists():
    TEST_DB.unlink()

os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB}"
os.environ["ADMIN_USERNAME"] = "admin"
os.environ["ADMIN_PASSWORD"] = "ChangeMe123!"

from fastapi.testclient import TestClient
from app.main import app


def login(c, username, password):
    r = c.post('/api/auth/login', json={'username': username, 'password': password})
    assert r.status_code == 200, r.text
    return r.json()['access_token']


def auth(token):
    return {'Authorization': f'Bearer {token}'}


def test_login_and_dashboard():
    with TestClient(app) as c:
        token = login(c, 'admin', 'ChangeMe123!')
        r = c.get('/api/dashboard', headers=auth(token))
        assert r.status_code == 200
        assert 'agents' in r.json()


def test_agent_id_invite_and_parent_are_automatic():
    with TestClient(app) as c:
        admin_token = login(c, 'admin', 'ChangeMe123!')

        parent = c.post('/api/agents', headers=auth(admin_token), json={
            'username': 'parent_agent',
            'password': 'ParentPass123!',
            'agent_name': '一级代理',
            'commission_rate': 0.1,
        })
        assert parent.status_code == 200, parent.text
        parent_data = parent.json()
        assert parent_data['agent_id'].startswith('AG')
        assert len(parent_data['agent_id']) == 10
        assert len(parent_data['invite_code']) == 8
        assert parent_data['parent_agent_id'] is None

        parent_token = login(c, 'parent_agent', 'ParentPass123!')
        child = c.post('/api/agents', headers=auth(parent_token), json={
            'username': 'child_agent',
            'password': 'ChildPass123!',
            'agent_name': '二级代理',
            'commission_rate': 0.05,
            # 即使旧前端误传这些字段，后端也不会据此决定代理ID和归属。
            'agent_id': 'MANUAL-ID',
            'parent_agent_id': 'OTHER-ID',
        })
        assert child.status_code == 200, child.text
        child_data = child.json()
        assert child_data['agent_id'].startswith('AG')
        assert child_data['agent_id'] != 'MANUAL-ID'
        assert child_data['agent_id'] != parent_data['agent_id']
        assert child_data['parent_agent_id'] == parent_data['agent_id']
        assert child_data['invite_code'] != parent_data['invite_code']

        rows = c.get('/api/agents', headers=auth(parent_token))
        assert rows.status_code == 200, rows.text
        assert len(rows.json()) == 1
        assert rows.json()[0]['agent_id'] == child_data['agent_id']
        assert rows.json()[0]['parent_agent_id'] == parent_data['agent_id']
