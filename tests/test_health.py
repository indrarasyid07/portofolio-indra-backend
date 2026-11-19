import asyncio
from collections.abc import AsyncIterator
from typing import Any

from httpx import ASGITransport, AsyncClient
from sqlalchemy.sql.elements import TextClause

from app.db.engine import DatabaseUnavailableError
from app.db.session import get_session
from app.main import app


async def _request(path: str) -> tuple[int, dict[str, Any]]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get(path)
    return response.status_code, response.json()


def test_health_is_database_independent() -> None:
    status_code, body = asyncio.run(_request("/health"))

    assert status_code == 200
    assert body == {"status": "ok"}


class ReadySessionStub:
    async def execute(self, statement: TextClause) -> None:
        assert str(statement) == "SELECT 1"


def test_readiness_checks_database_and_returns_no_connection_details() -> None:
    async def override_session() -> AsyncIterator[ReadySessionStub]:
        yield ReadySessionStub()

    app.dependency_overrides[get_session] = override_session
    try:
        status_code, body = asyncio.run(_request("/ready"))
    finally:
        app.dependency_overrides.pop(get_session, None)

    assert status_code == 200
    assert body == {"status": "ready"}


def test_readiness_returns_safe_unavailable_response_when_database_is_down() -> None:
    async def override_session() -> AsyncIterator[ReadySessionStub]:
        raise DatabaseUnavailableError("private test detail")
        yield ReadySessionStub()

    app.dependency_overrides[get_session] = override_session
    try:
        status_code, body = asyncio.run(_request("/ready"))
    finally:
        app.dependency_overrides.pop(get_session, None)

    assert status_code == 503
    assert body["error"]["message"] == "Layanan data sementara tidak tersedia."
    assert "private test detail" not in str(body)
