"""Opaque session tokens, CSRF tokens, and privacy-preserving identifiers."""

import base64
import hashlib
import hmac
import secrets

from fastapi import HTTPException, status
from pydantic import SecretStr

from app.core.config import get_settings


def _key(secret: SecretStr | None, setting: str) -> bytes:
    if secret is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Layanan autentikasi belum dikonfigurasi.",
        )
    value = secret.get_secret_value().encode("utf-8")
    if len(value) < 32:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Layanan autentikasi belum dikonfigurasi.",
        )
    return value


def _encoded_digest(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def new_session_token() -> str:
    return secrets.token_urlsafe(32)


def hash_session_token(token: str) -> str:
    settings = get_settings()
    key = _key(settings.session_hash_key, "SESSION_HASH_KEY")
    return hmac.new(key, b"session:v1:" + token.encode("ascii"), hashlib.sha256).hexdigest()


def csrf_token_for_session(token: str) -> str:
    settings = get_settings()
    key = _key(settings.session_hash_key, "SESSION_HASH_KEY")
    digest = hmac.new(key, b"csrf:v1:" + token.encode("ascii"), hashlib.sha256).digest()
    return _encoded_digest(digest)


def hash_csrf_token(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def pseudonym(value: str, *, context: str) -> str:
    settings = get_settings()
    key = _key(settings.login_rate_key, "LOGIN_RATE_KEY")
    return hmac.new(
        key, context.encode("ascii") + b":" + value.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def sign_proxy_client_ip(address: str, timestamp: str) -> str:
    settings = get_settings()
    key = _key(settings.auth_proxy_key, "AUTH_PROXY_KEY")
    message = f"client-ip:v1:{timestamp}:{address}".encode("ascii")
    return hmac.new(key, message, hashlib.sha256).hexdigest()


def verify_proxy_client_ip(address: str, timestamp: str, signature: str) -> bool:
    expected = sign_proxy_client_ip(address, timestamp)
    return hmac.compare_digest(expected, signature)
