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
