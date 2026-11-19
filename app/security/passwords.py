"""Argon2id password hashing and rehash checks."""

import secrets
from functools import lru_cache

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError


@lru_cache
def password_hasher() -> PasswordHasher:
    return PasswordHasher(
        time_cost=2,
        memory_cost=19_456,
        parallelism=1,
        hash_len=32,
        salt_len=16,
    )


def hash_password(password: str) -> str:
    return password_hasher().hash(password)


@lru_cache
def dummy_password_hash() -> str:
    return password_hasher().hash(secrets.token_urlsafe(32))


def verify_password(encoded_hash: str, password: str) -> bool:
    try:
        return password_hasher().verify(encoded_hash, password)
    except (InvalidHashError, VerificationError, VerifyMismatchError):
        return False


def needs_rehash(encoded_hash: str) -> bool:
    try:
        return password_hasher().check_needs_rehash(encoded_hash)
    except (InvalidHashError, VerificationError):
        return True
