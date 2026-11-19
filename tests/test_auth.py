"""Authentication and session integration tests over an isolated SQLite database."""

from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.responses import Response

from app.api.v1.auth import _set_session_cookie, get_session
from app.core.config import get_settings
from app.main import app
from app.models import AdminSession, AdminUser, LoginAttempt
from app.security.dependencies import cookie_name
from app.security.passwords import hash_password
from app.security.tokens import sign_proxy_client_ip

TEST_USER = "IndraAdmin"
TEST_PASSWORD = "Sufficiently-long-test-passphrase"
TEST_IP = "203.0.113.27"


@pytest.fixture(autouse=True)
def auth_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    values = {
        "APP_ENV": "test",
        "SESSION_HASH_KEY": "test-session-hash-key-with-32-bytes-minimum-0001",
        "LOGIN_RATE_KEY": "test-login-rate-key-with-32-bytes-minimum-0002",
        "AUTH_PROXY_KEY": "test-auth-proxy-key-with-32-bytes-minimum-0003",
        "PUBLIC_SITE_ORIGIN": "http://testserver",
        "SESSION_IDLE_MINUTES": "30",
        "SESSION_ABSOLUTE_HOURS": "12",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest_asyncio.fixture
async def auth_client(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncClient]:
    async def override_session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://testserver",
    ) as client:
        yield client
    app.dependency_overrides.pop(get_session, None)


def login_headers(*, origin: str = "http://testserver") -> dict[str, str]:
    timestamp = str(int(datetime.now(UTC).timestamp()))
    return {
        "Origin": origin,
        "X-Portfolio-Client-IP": TEST_IP,
        "X-Portfolio-Client-Time": timestamp,
        "X-Portfolio-Client-Signature": sign_proxy_client_ip(TEST_IP, timestamp),
    }


async def create_admin(session_factory: async_sessionmaker[AsyncSession]) -> None:
    async with session_factory() as session:
        session.add(
            AdminUser(
                username=TEST_USER,
                normalized_username=TEST_USER.casefold(),
                password_hash=hash_password(TEST_PASSWORD),
                is_active=True,
            )
        )
        await session.commit()


@pytest.mark.asyncio
async def test_login_session_logout_revokes_token_and_csrf_is_not_stored_raw(
    auth_client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await create_admin(session_factory)
    login = await auth_client.post(
        "/api/v1/auth/login",
        json={"username": TEST_USER, "password": TEST_PASSWORD},
        headers=login_headers(),
    )
    assert login.status_code == 200
    body = login.json()
    token = auth_client.cookies.get("portfolio_session")
    assert token
    assert "token" not in body
    assert login.headers["cache-control"] == "no-store"
    cookie = login.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie and "path=/" in cookie
    assert "domain=" not in cookie

    async with session_factory() as session:
        stored = (await session.scalars(select(AdminSession))).one()
        assert stored.token_hash != token
        assert stored.csrf_hash != body["csrf_token"]

    current = await auth_client.get("/api/v1/auth/session")
    assert current.status_code == 200
    assert current.json()["csrf_token"] == body["csrf_token"]
    assert current.json()["admin"] == {"username": TEST_USER}

    no_csrf = await auth_client.post(
        "/api/v1/auth/logout", json={}, headers={"Origin": "http://testserver"}
    )
    assert no_csrf.status_code == 403
    assert no_csrf.json()["error"]["code"] == "csrf_failed"

    logout = await auth_client.post(
        "/api/v1/auth/logout",
        json={},
        headers={"Origin": "http://testserver", "X-CSRF-Token": body["csrf_token"]},
    )
    assert logout.status_code == 204
    assert "max-age=0" in logout.headers["set-cookie"].lower()
    async with session_factory() as session:
        stored = (await session.scalars(select(AdminSession))).one()
        assert stored.revoked_at is not None
    assert (await auth_client.get("/api/v1/auth/session")).status_code == 401


@pytest.mark.asyncio
async def test_invalid_login_is_generic_and_uses_hashed_attempt_keys(
    auth_client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await create_admin(session_factory)
    wrong = await auth_client.post(
        "/api/v1/auth/login",
        json={"username": TEST_USER, "password": "incorrect-password"},
        headers=login_headers(),
    )
    absent = await auth_client.post(
        "/api/v1/auth/login",
        json={"username": "other-admin", "password": "incorrect-password"},
        headers=login_headers(),
    )
    assert wrong.status_code == absent.status_code == 401
    assert wrong.json()["error"]["code"] == absent.json()["error"]["code"]
    assert wrong.json()["error"]["message"] == absent.json()["error"]["message"]
    assert TEST_USER.casefold() not in wrong.text
    assert TEST_PASSWORD not in wrong.text
    async with session_factory() as session:
        attempts = list((await session.scalars(select(LoginAttempt))).all())
    assert len(attempts) == 2
    assert all(attempt.attempt_key_hash != TEST_USER.casefold() for attempt in attempts)
    assert all(attempt.source_key_hash != TEST_IP for attempt in attempts)


@pytest.mark.asyncio
async def test_rate_limit_is_shared_in_database_and_returns_retry_after(
    auth_client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    for _ in range(5):
        response = await auth_client.post(
            "/api/v1/auth/login",
            json={"username": "unknown-admin", "password": "incorrect-password"},
            headers=login_headers(),
        )
        assert response.status_code == 401
    limited = await auth_client.post(
        "/api/v1/auth/login",
        json={"username": "unknown-admin", "password": "incorrect-password"},
        headers=login_headers(),
    )
    assert limited.status_code == 429
    assert limited.headers["retry-after"].isdigit()
    async with session_factory() as session:
        attempts = list((await session.scalars(select(LoginAttempt))).all())
    assert len(attempts) == 5


@pytest.mark.asyncio
async def test_source_rate_limit_counts_attempts_across_usernames(
    auth_client: AsyncClient,
) -> None:
    for index in range(20):
        response = await auth_client.post(
            "/api/v1/auth/login",
            json={"username": f"unknown-{index:02d}", "password": "incorrect-password"},
            headers=login_headers(),
        )
        assert response.status_code == 401
    limited = await auth_client.post(
        "/api/v1/auth/login",
        json={"username": "another-unknown", "password": "incorrect-password"},
        headers=login_headers(),
    )
    assert limited.status_code == 429


@pytest.mark.asyncio
async def test_origin_content_type_and_unauthorized_write_guards(
    auth_client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    unauthenticated = await auth_client.post("/api/v1/admin/projects", json={})
    assert unauthenticated.status_code == 401

    bad_content_type = await auth_client.post(
        "/api/v1/auth/login",
        content="username=admin&password=password",
        headers={**login_headers(), "Content-Type": "application/x-www-form-urlencoded"},
    )
    assert bad_content_type.status_code == 415

    await create_admin(session_factory)
    login = await auth_client.post(
        "/api/v1/auth/login",
        json={"username": TEST_USER, "password": TEST_PASSWORD},
        headers=login_headers(),
    )
    csrf = login.json()["csrf_token"]

    foreign_origin = await auth_client.post(
        "/api/v1/admin/projects",
        json={},
        headers={"Origin": "https://attacker.example", "X-CSRF-Token": csrf},
    )
    assert foreign_origin.status_code == 403

    missing_csrf = await auth_client.post(
        "/api/v1/admin/projects", json={}, headers={"Origin": "http://testserver"}
    )
    assert missing_csrf.status_code == 403

    authenticated_invalid_payload = await auth_client.post(
        "/api/v1/admin/projects",
        json={},
        headers={"Origin": "http://testserver", "X-CSRF-Token": csrf},
    )
    assert authenticated_invalid_payload.status_code == 422
    assert authenticated_invalid_payload.json()["error"]["code"] == "validation_error"


@pytest.mark.asyncio
async def test_expired_session_is_revoked_and_cookie_is_cleared(
    auth_client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await create_admin(session_factory)
    login = await auth_client.post(
        "/api/v1/auth/login",
        json={"username": TEST_USER, "password": TEST_PASSWORD},
        headers=login_headers(),
    )
    assert login.status_code == 200
    async with session_factory() as session:
        stored = (await session.scalars(select(AdminSession))).one()
        stored.last_seen_at = datetime.now(UTC) - timedelta(minutes=31)
        await session.commit()

    response = await auth_client.get("/api/v1/auth/session")
    assert response.status_code == 401
    assert "max-age=0" in response.headers["set-cookie"].lower()
    async with session_factory() as session:
        stored = (await session.scalars(select(AdminSession))).one()
        assert stored.revoked_at is not None


def test_preview_cookie_is_secure_and_uses_host_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "preview")
    get_settings.cache_clear()
    response = Response()
    _set_session_cookie(response, "opaque-test-token")
    assert cookie_name() == "__Host-portfolio_session"
    cookie = response.headers["set-cookie"].lower()
    assert "secure" in cookie and "httponly" in cookie and "samesite=strict" in cookie
    assert "path=/" in cookie and "domain=" not in cookie
