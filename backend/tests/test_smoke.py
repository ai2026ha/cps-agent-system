import os
os.environ["DATABASE_URL"] = "sqlite:///./test_cps.db"
os.environ["ADMIN_USERNAME"] = "admin"
os.environ["ADMIN_PASSWORD"] = "ChangeMe123!"
from fastapi.testclient import TestClient
from app.main import app


def test_login_and_dashboard():
    with TestClient(app) as c:
        r = c.post('/api/auth/login', json={'username':'admin','password':'ChangeMe123!'})
        assert r.status_code == 200
        token = r.json()['access_token']
        r2 = c.get('/api/dashboard', headers={'Authorization':f'Bearer {token}'})
        assert r2.status_code == 200
        assert 'agents' in r2.json()
