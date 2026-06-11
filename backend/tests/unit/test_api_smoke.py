"""End-to-end API smoke test: register -> login -> list models.

Uses a fresh temp SQLite engine wired in via FastAPI dependency overrides (no module
reloading), and creates the schema directly against the shared declarative Base.
"""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@pytest_asyncio.fixture
async def client(tmp_path):
    import app.models  # noqa: F401 — register models on Base.metadata before create_all
    from app.db import Base, get_db
    from app.main import app as fastapi_app

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/test.db")
    TestSession = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async def _override_get_db():
        async with TestSession() as session:
            yield session

    fastapi_app.dependency_overrides[get_db] = _override_get_db
    transport = ASGITransport(app=fastapi_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    fastapi_app.dependency_overrides.clear()
    await engine.dispose()


@pytest.mark.asyncio
async def test_health(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_register_login_models_flow(client):
    r = await client.post(
        "/auth/register", json={"email": "a@b.com", "password": "password123"}
    )
    assert r.status_code == 201

    r = await client.post(
        "/auth/login", json={"email": "a@b.com", "password": "password123"}
    )
    assert r.status_code == 200
    token = r.json()["access_token"]

    r = await client.get("/models", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert any(m.get("recommended") for m in r.json())


@pytest.mark.asyncio
async def test_models_requires_auth(client):
    assert (await client.get("/models")).status_code in (401, 403)


@pytest.mark.asyncio
async def test_api_key_set_list_delete(client):
    await client.post(
        "/auth/register", json={"email": "k@b.com", "password": "password123"}
    )
    token = (
        await client.post(
            "/auth/login", json={"email": "k@b.com", "password": "password123"}
        )
    ).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Nothing configured initially.
    r = await client.get("/auth/api-keys", headers=headers)
    assert r.status_code == 200
    assert all(not s["configured"] for s in r.json())

    # Set a key; provider reports configured and the secret is never echoed back.
    r = await client.put(
        "/auth/api-keys",
        headers=headers,
        json={"provider": "google", "api_key": "secret-value-123"},
    )
    assert r.status_code == 200
    assert any(s["provider"] == "google" and s["configured"] for s in r.json())
    assert "secret-value-123" not in r.text

    # Delete clears it.
    r = await client.delete("/auth/api-keys/google", headers=headers)
    assert all(not s["configured"] for s in r.json() if s["provider"] == "google")


@pytest.mark.asyncio
async def test_upload_file_writes_to_disk_and_rejects_bad_extension(client, tmp_path, monkeypatch):
    from pathlib import Path

    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "generated_dir", tmp_path)

    await client.post(
        "/auth/register", json={"email": "u@b.com", "password": "password123"}
    )
    token = (
        await client.post(
            "/auth/login", json={"email": "u@b.com", "password": "password123"}
        )
    ).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    csv_bytes = b"id,name\n1,Ada\n2,Linus\n"
    r = await client.post(
        "/connectors/upload",
        headers=headers,
        files={"file": ("people.csv", csv_bytes, "text/csv")},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["connector_type"] == "csv"
    assert body["size_bytes"] == len(csv_bytes)
    stored = Path(body["file_path"])
    assert stored.exists()
    assert stored.read_bytes() == csv_bytes

    bad = await client.post(
        "/connectors/upload",
        headers=headers,
        files={"file": ("malware.exe", b"00", "application/octet-stream")},
    )
    assert bad.status_code == 400
    assert "Unsupported" in bad.json()["detail"]
