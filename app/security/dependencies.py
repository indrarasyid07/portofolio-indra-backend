"""Dependencies used to protect admin reads and state-changing requests."""

import hmac
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.core.config import get_settings
from app.db.session import get_session
from app.models import AdminSession, AdminUser
from app.security.tokens import csrf_token_for_session, hash_csrf_token, hash_session_token

SESSION_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{43}$")
SessionDep = Annotated[AsyncSession, Depends(get_session)]


@dataclass(frozen=True)
class AdminPrincipal:
    user: AdminUser
    session: AdminSession
    raw_token: str
    csrf_token: str


def cookie_name() -> str:
    return (
        "portfolio_session"
        if get_settings().app_env.lower() in {"development", "test"}
        else "__Host-portfolio_session"
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(
        key=cookie_name(),
        path="/",
        secure=get_settings().app_env.lower() not in {"development", "test"},
        httponly=True,
        samesite="strict",
    )


def unauthorized_response(response: Response) -> HTTPException:
    clear_session_cookie(response)
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={"code": "authentication_required", "message": "Autentikasi diperlukan."},
        headers={"Set-Cookie": response.headers["set-cookie"]},
    )


def check_origin(request: Request) -> None:
    settings = get_settings()
    allowed = settings.public_site_origin
    if allowed is None:
        if settings.app_env.lower() not in {"development", "test"}:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "code": "service_unavailable",
                    "message": "Layanan autentikasi belum dikonfigurasi.",
                },
            )
        allowed = "http://localhost:4321"
    allowed_origins = {value.strip() for value in allowed.split(",") if value.strip()}
    origin = request.headers.get("origin")
    if origin is None or origin == "null" or origin not in allowed_origins:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "forbidden", "message": "Origin tidak diizinkan."},
        )


async def require_admin(
    request: Request,
    response: Response,
    session: SessionDep,
) -> AdminPrincipal:
    raw_token = request.cookies.get(cookie_name(), "")
    if not SESSION_TOKEN_PATTERN.fullmatch(raw_token):
        raise unauthorized_response(response)

    result = await session.execute(
        select(AdminSession)
        .where(AdminSession.token_hash == hash_session_token(raw_token))
        .options(joinedload(AdminSession.admin_user))
    )
    stored = result.scalar_one_or_none()
    now = datetime.now(UTC)
    if stored is None or stored.revoked_at is not None or not stored.admin_user.is_active:
        raise unauthorized_response(response)

    last_seen = stored.last_seen_at
    expires_at = stored.expires_at
    if last_seen.tzinfo is None:
        last_seen = last_seen.replace(tzinfo=UTC)
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    idle_expiry = last_seen + timedelta(minutes=get_settings().session_idle_minutes)
    effective_expiry = min(expires_at, idle_expiry)
    if now >= effective_expiry:
        stored.revoked_at = now
        await session.commit()
        raise unauthorized_response(response)

    if now - last_seen >= timedelta(minutes=5):
        stored.last_seen_at = now
        await session.commit()

    csrf_token = csrf_token_for_session(raw_token)
    expected_hash = hash_csrf_token(csrf_token)
    if not hmac.compare_digest(expected_hash, stored.csrf_hash):
        raise unauthorized_response(response)
    principal = AdminPrincipal(stored.admin_user, stored, raw_token, csrf_token)
    request.state.admin_principal = principal
    return principal


async def require_admin_write(
    request: Request,
    response: Response,
    principal: Annotated[AdminPrincipal, Depends(require_admin)],
) -> AdminPrincipal:
    check_origin(request)
    if (
        request.headers.get("content-type", "").split(";", maxsplit=1)[0].strip().lower()
        != "application/json"
    ):
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail={"code": "unsupported_media_type", "message": "Jenis konten tidak didukung."},
        )
    submitted = request.headers.get("x-csrf-token", "")
    if not submitted or not hmac.compare_digest(submitted, principal.csrf_token):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "csrf_failed", "message": "Permintaan tidak dapat divalidasi."},
        )
    return principal
