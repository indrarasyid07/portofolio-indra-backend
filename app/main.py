"""FastAPI application with public content routes and stable error responses."""

import logging
import os
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from typing import Annotated
from uuid import uuid4

from fastapi import Depends, FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.api.v1.admin import router as admin_router
from app.api.v1.auth import router as auth_router
from app.api.v1.media import admin_router as admin_media_router
from app.api.v1.media import public_router as public_media_router
from app.api.v1.public import PublicApiError
from app.api.v1.public import router as public_router
from app.core.config import get_settings
from app.db.engine import DatabaseUnavailableError, get_engine
from app.db.session import get_session

logger = logging.getLogger("portfolio.api")


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Dispose the lazy database pool cleanly when the API process stops."""
    yield
    if get_settings().database_url is not None:
        await get_engine().dispose()


app = FastAPI(
    title="Portfolio API",
    version="1.0.0",
    docs_url="/api/docs",
    redoc_url=None,
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
)
app.include_router(public_router)
app.include_router(public_media_router)
app.include_router(auth_router)
app.include_router(admin_router)
app.include_router(admin_media_router)


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "req_unknown")


def _error_response(
    request: Request,
    status_code: int,
    code: str,
    message: str,
    fields: list[dict[str, str]] | None = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    error: dict[str, object] = {
        "code": code,
        "message": message,
        "request_id": _request_id(request),
    }
    if fields is not None:
        error["fields"] = fields
    response = JSONResponse(
        status_code=status_code,
        content={"error": error},
        headers=headers,
    )
    response.headers["X-Request-ID"] = _request_id(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    if request.url.path.startswith(("/api/v1/auth/", "/api/v1/admin")) or (
        request.url.path.startswith("/api/v1/public/")
        and not request.url.path.startswith("/api/v1/public/media/")
    ):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.middleware("http")
async def request_context(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    """Add request correlation and disable caches for public reads."""
    request.state.request_id = f"req_{uuid4().hex}"
    if (
        request.url.path in {"/api/v1/auth/login", "/api/v1/auth/logout"}
        and request.method == "POST"
    ):
        content_type = (
            request.headers.get("content-type", "").split(";", maxsplit=1)[0].strip().lower()
        )
        content_length = request.headers.get("content-length")
        if content_type != "application/json":
            return _error_response(
                request,
                415,
                "unsupported_media_type",
                "Jenis konten tidak didukung.",
            )
        if content_length and content_length.isdigit() and int(content_length) > 8192:
            return _error_response(request, 413, "request_too_large", "Permintaan terlalu besar.")
    if request.url.path.startswith("/api/v1/admin/") and request.method not in {"GET", "HEAD"}:
        content_length = request.headers.get("content-length")
        if content_length and content_length.isdigit() and int(content_length) > 65_536:
            return _error_response(request, 413, "request_too_large", "Permintaan terlalu besar.")
    response = await call_next(request)
    response.headers["X-Request-ID"] = request.state.request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    if request.url.path.startswith(("/api/v1/auth/", "/api/v1/admin")) or (
        request.url.path.startswith("/api/v1/public/")
        and not request.url.path.startswith("/api/v1/public/media/")
    ):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    fields = [
        {
            "path": ".".join(str(part) for part in issue.get("loc", ())[1:]),
            "code": str(issue.get("type", "invalid")),
            "message": "Nilai tidak sesuai format yang diharapkan.",
        }
        for issue in exc.errors()
    ]
    return _error_response(
        request,
        422,
        "validation_error",
        "Periksa kembali parameter yang dikirim.",
        fields,
    )


@app.exception_handler(PublicApiError)
async def public_api_error_handler(request: Request, exc: PublicApiError) -> JSONResponse:
    detail: dict[str, object] = exc.detail if isinstance(exc.detail, dict) else {}
    return _error_response(
        request,
        exc.status_code,
        str(detail.get("code", "invalid_request")),
        str(detail.get("message", "Permintaan tidak dapat diproses.")),
        headers=exc.headers,
    )


@app.exception_handler(StarletteHTTPException)
async def http_error_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    code, message = {
        404: ("not_found", "Resource tidak ditemukan."),
        405: ("method_not_allowed", "Metode tidak didukung."),
        401: ("authentication_required", "Autentikasi diperlukan."),
        403: ("forbidden", "Permintaan tidak diizinkan."),
        415: ("unsupported_media_type", "Jenis konten tidak didukung."),
        429: ("rate_limited", "Terlalu banyak permintaan. Coba lagi setelah jeda."),
        409: ("conflict", "Data berubah atau melanggar aturan unik."),
        428: ("precondition_required", "Versi data wajib disertakan untuk mengubah konten."),
        501: ("not_implemented", "Fitur admin belum tersedia."),
    }.get(exc.status_code, ("invalid_request", "Permintaan tidak dapat diproses."))
    if isinstance(exc.detail, dict):
        code = str(exc.detail.get("code", code))
        message = str(exc.detail.get("message", message))
    elif isinstance(exc.detail, str):
        message = exc.detail
    return _error_response(request, exc.status_code, code, message, headers=exc.headers)


@app.exception_handler(DatabaseUnavailableError)
async def database_not_configured_handler(
    request: Request, _: DatabaseUnavailableError
) -> JSONResponse:
    logger.warning("database_unavailable request_id=%s", _request_id(request))
    return _error_response(
        request,
        503,
        "database_unavailable",
        "Layanan data sementara tidak tersedia.",
    )


@app.exception_handler(SQLAlchemyError)
async def database_error_handler(request: Request, exc: SQLAlchemyError) -> JSONResponse:
    logger.error(
        "database_unavailable request_id=%s exception_type=%s",
        _request_id(request),
        type(exc).__name__,
    )
    return _error_response(
        request,
        503,
        "database_unavailable",
        "Layanan data sementara tidak tersedia.",
    )


@app.exception_handler(Exception)
async def unexpected_error_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.error(
        "internal_error request_id=%s exception_type=%s",
        _request_id(request),
        type(exc).__name__,
    )
    return _error_response(
        request,
        500,
        "internal_error",
        "Terjadi kesalahan internal.",
    )


trusted_hosts = [
    host.strip()
    for host in os.getenv("TRUSTED_HOSTS", "localhost,127.0.0.1,testserver").split(",")
    if host.strip()
]
allowed_origins = [
    origin.strip()
    for origin in os.getenv("ALLOWED_ORIGINS", "http://localhost:4321").split(",")
    if origin.strip()
]
if "*" in trusted_hosts or "*" in allowed_origins:
    raise RuntimeError(
        "Wildcard host/origin tidak diizinkan; isi allowlist domain secara eksplisit."
    )

app.add_middleware(TrustedHostMiddleware, allowed_hosts=trusted_hosts)
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=[],
)


@app.get("/health", tags=["health"])
async def health() -> dict[str, str]:
    """Report process liveness without querying or revealing dependency state."""
    return {"status": "ok"}


ReadySession = Annotated[AsyncSession, Depends(get_session)]


@app.get("/ready", tags=["health"])
async def readiness(session: ReadySession) -> dict[str, str]:
    """Check that the API can reach its configured database without exposing details."""
    await session.execute(text("SELECT 1"))
    return {"status": "ready"}
