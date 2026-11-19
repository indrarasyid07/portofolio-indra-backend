"""Login, session inspection, and logout routes."""

import hashlib
import ipaddress
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import InstrumentedAttribute
from starlette.concurrency import run_in_threadpool

from app.core.config import get_settings
from app.db.session import get_session
from app.models import AdminSession, AdminUser, LoginAttempt
from app.schemas.auth import (
    AdminSummary,
    EmptyRequest,
    LoginRequest,
    LoginResponse,
    SessionResponse,
)
from app.security.dependencies import (
    AdminPrincipal,
    check_origin,
    clear_session_cookie,
    cookie_name,
    require_admin,
    require_admin_write,
)
from app.security.passwords import (
    dummy_password_hash,
    hash_password,
    needs_rehash,
    verify_password,
)
from app.security.tokens import (
    csrf_token_for_session,
    hash_csrf_token,
    hash_session_token,
    new_session_token,
    pseudonym,
    verify_proxy_client_ip,
)

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
AdminDep = Annotated[AdminPrincipal, Depends(require_admin)]
WriteAdminDep = Annotated[AdminPrincipal, Depends(require_admin_write)]
ACCOUNT_LIMIT = 5
SOURCE_LIMIT = 20
WINDOW = timedelta(minutes=15)
RETENTION = timedelta(days=7)


def _safe_source_ip(request: Request) -> str:
    address = request.headers.get("x-portfolio-client-ip", "")
    timestamp = request.headers.get("x-portfolio-client-time", "")
    signature = request.headers.get("x-portfolio-client-signature", "")
    try:
        parsed = ipaddress.ip_address(address)
        issued_at = int(timestamp)
        if abs(int(datetime.now(UTC).timestamp()) - issued_at) <= 60 and verify_proxy_client_ip(
            address, timestamp, signature
        ):
            return str(parsed)
    except (ValueError, OverflowError):
        pass
    return request.client.host if request.client is not None else "unknown"


def _advisory_lock_ids(keys: Sequence[str]) -> list[int]:
    values = {
        int.from_bytes(hashlib.sha256(bytes.fromhex(key)).digest()[:8], "big", signed=True)
        for key in keys
    }
    return sorted(values)


async def _lock_rate_keys(session: AsyncSession, keys: Sequence[str]) -> None:
    if session.bind is None or session.bind.dialect.name != "postgresql":
        return
    for lock_id in _advisory_lock_ids(keys):
        await session.execute(select(func.pg_advisory_xact_lock(lock_id)))


async def _failure_count(
    session: AsyncSession,
    *,
    column: InstrumentedAttribute[str] | InstrumentedAttribute[str | None],
    key: str,
    since: datetime,
) -> tuple[int, datetime | None]:
    statement = select(func.count(LoginAttempt.id), func.min(LoginAttempt.attempted_at)).where(
        column == key,
        LoginAttempt.outcome == "failure",
        LoginAttempt.attempted_at >= since,
    )
    count, first_failure = (await session.execute(statement)).one()
    return int(count), first_failure


def _retry_after(first_failure: datetime | None, now: datetime) -> int:
    if first_failure is None:
        return 60
    if first_failure.tzinfo is None:
        first_failure = first_failure.replace(tzinfo=UTC)
    return max(1, min(900, int((first_failure + WINDOW - now).total_seconds())))


def _json_content_type(request: Request) -> bool:
    return (
        request.headers.get("content-type", "").split(";", maxsplit=1)[0].strip().lower()
        == "application/json"
    )


def _set_session_cookie(response: Response, token: str) -> None:
    secure = get_settings().app_env.lower() not in {"development", "test"}
    response.set_cookie(
        key=cookie_name(),
        value=token,
        max_age=get_settings().session_absolute_hours * 60 * 60,
        httponly=True,
        secure=secure,
        samesite="strict",
        path="/",
    )


@router.post("/login", response_model=LoginResponse)
async def login(
    payload: LoginRequest, request: Request, response: Response, session: SessionDep
) -> LoginResponse:
    if not _json_content_type(request):
        raise HTTPException(
            status_code=415,
            detail={"code": "unsupported_media_type", "message": "Jenis konten tidak didukung."},
        )
    check_origin(request)
    settings = get_settings()
    normalized = payload.username.casefold()
    source_ip = _safe_source_ip(request)
    attempt_key = pseudonym(normalized, context="account")
    source_key = pseudonym(source_ip, context="source")
    now = datetime.now(UTC)
    since = now - WINDOW
    valid = False
    user: AdminUser | None = None
    session_token = ""
    csrf_token = ""

    async with session.begin():
        await _lock_rate_keys(session, (attempt_key, source_key))
        account_count, account_first = await _failure_count(
            session, column=LoginAttempt.attempt_key_hash, key=attempt_key, since=since
        )
        source_count, source_first = await _failure_count(
            session, column=LoginAttempt.source_key_hash, key=source_key, since=since
        )
        if account_count >= ACCOUNT_LIMIT or source_count >= SOURCE_LIMIT:
            first = min(
                [item for item in (account_first, source_first) if item is not None],
                default=None,
            )
            retry_after = _retry_after(first, now)
            raise HTTPException(
                status_code=429,
                detail="Terlalu banyak percobaan. Coba lagi setelah jeda.",
                headers={"Retry-After": str(retry_after)},
            )

        user = await session.scalar(
            select(AdminUser).where(AdminUser.normalized_username == normalized)
        )
        password = payload.password.get_secret_value()
        encoded_hash = user.password_hash if user is not None and user.is_active else None
        if encoded_hash is None:
            encoded_hash = await run_in_threadpool(dummy_password_hash)
        password_valid = await run_in_threadpool(verify_password, encoded_hash, password)
        valid = bool(user is not None and user.is_active and password_valid)

        cutoff = now - RETENTION
        await session.execute(delete(LoginAttempt).where(LoginAttempt.attempted_at < cutoff))
        if not valid or user is None:
            session.add(
                LoginAttempt(
                    id=uuid4(),
                    attempt_key_hash=attempt_key,
                    source_key_hash=source_key,
                    attempted_at=now,
                    outcome="failure",
                )
            )
        else:
            if needs_rehash(user.password_hash):
                user.password_hash = await run_in_threadpool(hash_password, password)
                user.password_changed_at = now
            await session.execute(
                delete(LoginAttempt).where(LoginAttempt.attempt_key_hash == attempt_key)
            )
            session.add(
                LoginAttempt(
                    id=uuid4(),
                    attempt_key_hash=attempt_key,
                    source_key_hash=source_key,
                    attempted_at=now,
                    outcome="success",
                )
            )
            session_token = new_session_token()
            csrf_token = csrf_token_for_session(session_token)
            session.add(
                AdminSession(
                    id=uuid4(),
                    admin_user_id=user.id,
                    token_hash=hash_session_token(session_token),
                    key_version=1,
                    csrf_hash=hash_csrf_token(csrf_token),
                    created_at=now,
                    last_seen_at=now,
                    expires_at=now + timedelta(hours=settings.session_absolute_hours),
                    revoked_at=None,
                    user_agent_hash=pseudonym(
                        request.headers.get("user-agent", "")[:512], context="user-agent"
                    ),
                )
            )

    if not valid or user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Nama pengguna atau kata sandi tidak valid.",
        )
    _set_session_cookie(response, session_token)
    return LoginResponse(admin=AdminSummary(username=user.username), csrf_token=csrf_token)


@router.get("/session", response_model=SessionResponse)
async def get_auth_session(principal: AdminDep) -> SessionResponse:
    now = datetime.now(UTC)
    last_seen = principal.session.last_seen_at
    if last_seen.tzinfo is None:
        last_seen = last_seen.replace(tzinfo=UTC)
    absolute_expiry = principal.session.expires_at
    if absolute_expiry.tzinfo is None:
        absolute_expiry = absolute_expiry.replace(tzinfo=UTC)
    effective_expiry = min(
        absolute_expiry,
        last_seen + timedelta(minutes=get_settings().session_idle_minutes),
    )
    if effective_expiry <= now:
        raise HTTPException(status_code=401, detail="Sesi tidak valid.")
    return SessionResponse(
        admin=AdminSummary(username=principal.user.username),
        csrf_token=principal.csrf_token,
        expires_at=effective_expiry,
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    payload: EmptyRequest, request: Request, principal: WriteAdminDep, session: SessionDep
) -> Response:
    if not _json_content_type(request):
        raise HTTPException(
            status_code=415,
            detail={"code": "unsupported_media_type", "message": "Jenis konten tidak didukung."},
        )
    principal.session.revoked_at = datetime.now(UTC)
    await session.commit()
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    clear_session_cookie(response)
    return response
