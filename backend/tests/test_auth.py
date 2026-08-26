"""用户认证接口测试 (注册/登录/鉴权)

通过覆盖 FastAPI 的 get_db 依赖 + 临时 SQLite 库隔离, 不污染开发库。
"""
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.main import app
from app.db import database as dbmod
from app.db import models as models_mod


@pytest.fixture()
def auth_client(tmp_path, monkeypatch):
    """指向临时库的测试客户端 (每测试一个独立临时库)"""
    db_path = tmp_path / "test_auth.db"
    # 建一个独立引擎与会话
    engine = dbmod.create_engine(
        f"sqlite:///{db_path.as_posix()}", connect_args={"check_same_thread": False}
    )
    session_local = dbmod.sessionmaker(bind=engine, autoflush=False, autocommit=False)

    # 建表
    models_mod.Base.metadata.create_all(bind=engine)

    # 覆盖 get_db 依赖: 让路由用到临时会话
    def _override_get_db():
        db = session_local()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[dbmod.get_db] = _override_get_db
    yield TestClient(app)
    app.dependency_overrides.clear()


def _register(client, username="tester", password="secret123"):
    return client.post("/api/auth/register", json={
        "username": username, "password": password,
    })


def test_register_success(auth_client):
    r = _register(auth_client)
    assert r.status_code == 200
    data = r.json()
    assert data["username"] == "tester"
    assert data["token_type"] == "bearer"
    assert data["access_token"]


def test_register_duplicate_username(auth_client):
    assert _register(auth_client).status_code == 200
    r = _register(auth_client)
    assert r.status_code == 409
    assert "已存在" in r.json()["detail"]


def test_register_weak_password(auth_client):
    r = _register(auth_client, password="123")
    assert r.status_code == 422


def test_login_success(auth_client):
    _register(auth_client)
    r = auth_client.post("/api/auth/login", json={
        "username": "tester", "password": "secret123",
    })
    assert r.status_code == 200
    assert r.json()["access_token"]


def test_login_wrong_password(auth_client):
    _register(auth_client)
    r = auth_client.post("/api/auth/login", json={
        "username": "tester", "password": "wrong-pass",
    })
    assert r.status_code == 401


def test_login_unknown_user(auth_client):
    r = auth_client.post("/api/auth/login", json={
        "username": "ghost", "password": "secret123",
    })
    assert r.status_code == 401


def test_history_requires_auth(auth_client):
    r = auth_client.get("/api/history")
    assert r.status_code == 401


def test_history_with_token(auth_client):
    tok = _register(auth_client).json()["access_token"]
    r = auth_client.get("/api/history", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200
    assert r.json()["success"] is True


def test_history_invalid_token(auth_client):
    r = auth_client.get("/api/history", headers={"Authorization": "Bearer bad.token"})
    assert r.status_code == 401


def test_auth_me(auth_client):
    tok = _register(auth_client).json()["access_token"]
    r = auth_client.get("/api/auth/me", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200
    assert r.json()["username"] == "tester"
