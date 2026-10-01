import os
import tempfile
from pathlib import Path

# Configure the test environment BEFORE any app import (settings are cached).
os.environ["APP_ENV"] = "test"
os.environ["APP_SECRET_KEY"] = "test-secret-key"
os.environ["BBB_URL"] = ""
os.environ["BBB_SHARED_SECRET"] = "test-bbb-secret"
os.environ["PUBLIC_APP_URL"] = "http://localhost:5173"
os.environ["API_BASE_URL"] = "http://localhost:8000"
os.environ["BBB_WEBHOOK_PATH_TOKEN"] = "test-hook-token"
os.environ["DATABASE_URL"] = f"sqlite:///{Path(tempfile.gettempdir()) / 'bbb_interview_test.db'}"
# Never let a real .env SMTP config make tests open network connections.
os.environ["SMTP_HOST"] = ""
os.environ["SMTP_USER"] = ""
os.environ["SMTP_PASSWORD"] = ""

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.db import Base, SessionLocal, engine, init_db  # noqa: E402
from app.models import User, UserRole  # noqa: E402
from app.security import hash_password  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _database():
    Base.metadata.drop_all(bind=engine)
    init_db()
    with SessionLocal() as db:
        db.add(
            User(
                email="admin@example.com",
                full_name="Ada Admin",
                password_hash=hash_password("admin123"),
                role=UserRole.admin,
            )
        )
        db.commit()
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture()
def client():
    from app.main import app as fastapi_app

    with TestClient(fastapi_app) as test_client:
        yield test_client


@pytest.fixture()
def auth_headers(client):
    resp = client.post(
        "/api/v1/auth/login", json={"email": "admin@example.com", "password": "admin123"}
    )
    assert resp.status_code == 200, resp.text
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}
