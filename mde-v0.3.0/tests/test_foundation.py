from fastapi.testclient import TestClient
from mde_v030.main import app
from mde_v030.database import init_db

def test_health():
    init_db()
    with TestClient(app) as client:
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["version"] == "0.3.0"

def test_auth_rejects_bad_password():
    init_db()
    with TestClient(app) as client:
        response = client.get("/player_api.php", params={"username":"demo","password":"wrong"})
        assert response.status_code == 401
