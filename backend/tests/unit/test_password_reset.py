"""Password-reset flow: request (non-enumerating), reset, single-use, and expiry.

Uses a fresh temp SQLite engine wired in via FastAPI dependency overrides, mirroring
test_api_smoke. The outbound email is monkeypatched so tests can capture the token that
would have been emailed.
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


def _capture_reset_email(monkeypatch) -> dict:
    """Patch the reset email sender; return a dict that fills with the latest token."""
    captured: dict = {}

    async def fake_send(to, token, reset_url):
        captured["to"] = to
        captured["token"] = token
        captured["reset_url"] = reset_url

    # The service imports the symbol into its own namespace, so patch it there.
    from app.services import password_reset

    monkeypatch.setattr(password_reset, "send_password_reset_email", fake_send)
    return captured


async def _register(client, email, password="password123"):
    r = await client.post("/auth/register", json={"email": email, "password": password})
    assert r.status_code == 201


@pytest.mark.asyncio
async def test_forgot_password_is_non_enumerating(client, monkeypatch):
    captured = _capture_reset_email(monkeypatch)
    await _register(client, "known@b.com")

    # Unknown email: same generic 200, and no email/token issued.
    r_unknown = await client.post(
        "/auth/forgot-password", json={"email": "nobody@b.com"}
    )
    assert r_unknown.status_code == 200
    unknown_msg = r_unknown.json()["message"]
    assert "nobody@b.com" not in unknown_msg
    assert "token" not in captured  # nothing was sent

    # Known email: identical message, and a token IS issued.
    r_known = await client.post(
        "/auth/forgot-password", json={"email": "known@b.com"}
    )
    assert r_known.status_code == 200
    assert r_known.json()["message"] == unknown_msg
    assert captured.get("to") == "known@b.com"
    assert captured.get("token")
    assert "Reset_Password?token=" in captured["reset_url"]


@pytest.mark.asyncio
async def test_reset_password_happy_path(client, monkeypatch):
    captured = _capture_reset_email(monkeypatch)
    await _register(client, "reset@b.com", "oldpassword1")

    await client.post("/auth/forgot-password", json={"email": "reset@b.com"})
    token = captured["token"]

    r = await client.post(
        "/auth/reset-password", json={"token": token, "new_password": "newpassword2"}
    )
    assert r.status_code == 200

    # Old password no longer works; new one does.
    bad = await client.post(
        "/auth/login", json={"email": "reset@b.com", "password": "oldpassword1"}
    )
    assert bad.status_code == 401
    good = await client.post(
        "/auth/login", json={"email": "reset@b.com", "password": "newpassword2"}
    )
    assert good.status_code == 200
    assert good.json()["access_token"]


@pytest.mark.asyncio
async def test_reset_password_rejects_invalid_token(client):
    r = await client.post(
        "/auth/reset-password",
        json={"token": "not-a-real-token", "new_password": "whatever123"},
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_reset_token_is_single_use(client, monkeypatch):
    captured = _capture_reset_email(monkeypatch)
    await _register(client, "single@b.com")

    await client.post("/auth/forgot-password", json={"email": "single@b.com"})
    token = captured["token"]

    first = await client.post(
        "/auth/reset-password", json={"token": token, "new_password": "firstpass12"}
    )
    assert first.status_code == 200

    second = await client.post(
        "/auth/reset-password", json={"token": token, "new_password": "secondpass3"}
    )
    assert second.status_code == 400


@pytest.mark.asyncio
async def test_reset_token_expires(client, monkeypatch):
    captured = _capture_reset_email(monkeypatch)
    await _register(client, "expired@b.com")

    # Force tokens to be created already-expired.
    from app.services import password_reset

    monkeypatch.setattr(
        password_reset.settings, "password_reset_expire_minutes", -1
    )

    await client.post("/auth/forgot-password", json={"email": "expired@b.com"})
    token = captured["token"]

    r = await client.post(
        "/auth/reset-password", json={"token": token, "new_password": "newpassword2"}
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_send_email_without_smtp_logs_instead_of_raising(caplog):
    """With SMTP unconfigured (default), send_email logs and never raises."""
    import logging

    from app.services.email import send_email

    with caplog.at_level(logging.WARNING):
        await send_email("someone@b.com", "Subject", "Body")

    assert any("SMTP not configured" in rec.message for rec in caplog.records)


class _FakeSMTP:
    """Records the SMTP conversation so tests can assert on it."""

    def __init__(self, calls: list):
        self.calls = calls

    def __call__(self, host, port, timeout=None):
        self.calls.append(("connect", host, port))
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def ehlo(self):
        self.calls.append(("ehlo",))

    def starttls(self):
        self.calls.append(("starttls",))

    def login(self, username, password):
        self.calls.append(("login", username, password))

    def send_message(self, msg):
        self.calls.append(("send", msg["To"], msg["From"]))


def _configure_smtp(monkeypatch, *, port):
    from app.services import email as email_mod

    monkeypatch.setattr(email_mod.settings, "smtp_host", "smtp.gmail.com")
    monkeypatch.setattr(email_mod.settings, "smtp_port", port)
    monkeypatch.setattr(email_mod.settings, "smtp_use_tls", True)
    monkeypatch.setattr(email_mod.settings, "smtp_username", "u@gmail.com")
    monkeypatch.setattr(email_mod.settings, "smtp_password", "app-pw")
    monkeypatch.setattr(email_mod.settings, "smtp_from", "")  # fall back to username
    return email_mod


@pytest.mark.asyncio
async def test_send_email_starttls_path(monkeypatch):
    calls: list = []
    email_mod = _configure_smtp(monkeypatch, port=587)
    monkeypatch.setattr(email_mod.smtplib, "SMTP", _FakeSMTP(calls))

    await email_mod.send_email("to@x.com", "Subject", "Body")

    kinds = [c[0] for c in calls]
    assert ("connect", "smtp.gmail.com", 587) in calls
    assert "starttls" in kinds
    assert ("login", "u@gmail.com", "app-pw") in calls
    # From falls back to the SMTP username when smtp_from is blank.
    assert ("send", "to@x.com", "u@gmail.com") in calls


@pytest.mark.asyncio
async def test_send_email_ssl_path_on_465(monkeypatch):
    calls: list = []
    email_mod = _configure_smtp(monkeypatch, port=465)
    monkeypatch.setattr(email_mod.smtplib, "SMTP_SSL", _FakeSMTP(calls))

    await email_mod.send_email("to@x.com", "Subject", "Body")

    kinds = [c[0] for c in calls]
    assert ("connect", "smtp.gmail.com", 465) in calls
    # Implicit TLS — no STARTTLS handshake on 465.
    assert "starttls" not in kinds
    assert ("login", "u@gmail.com", "app-pw") in calls
