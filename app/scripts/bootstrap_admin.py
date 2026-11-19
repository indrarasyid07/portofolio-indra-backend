"""Create the single admin account or rotate its password from an interactive prompt."""

import argparse
import asyncio
import getpass
import re
import sys
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.engine import DatabaseUnavailableError, get_engine
from app.models import AdminSession, AdminUser
from app.security.passwords import hash_password

USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9._-]{3,80}$")


def _read_username() -> str:
    username = input("Nama pengguna admin: ")
    if not USERNAME_PATTERN.fullmatch(username):
        raise ValueError(
            "Nama pengguna harus 3–80 karakter: huruf, angka, titik, garis bawah, atau hubung."
        )
    return username


def _read_password() -> str:
    password = getpass.getpass("Kata sandi baru (8–128 karakter): ")
    confirmation = getpass.getpass("Ulangi kata sandi: ")
    if not 8 <= len(password) <= 128 or len(password.encode("utf-8")) > 1024:
        raise ValueError("Kata sandi harus 8–128 karakter.")
    if password != confirmation:
        raise ValueError("Kedua kata sandi tidak sama.")
    return password


async def _save_admin(username: str, password: str, *, rotate: bool) -> None:
    engine = get_engine()
    async with AsyncSession(engine, expire_on_commit=False) as session:
        async with session.begin():
            admins = list((await session.scalars(select(AdminUser))).all())
            normalized = username.casefold()
            existing = next(
                (admin for admin in admins if admin.normalized_username == normalized), None
            )
            if rotate:
                if len(admins) != 1 or existing is None:
                    raise ValueError(
                        "Rotasi hanya berlaku untuk satu akun admin yang sudah terdaftar."
                    )
                existing.password_hash = hash_password(password)
                existing.password_changed_at = datetime.now(UTC)
                await session.execute(
                    update(AdminSession)
                    .where(
                        AdminSession.admin_user_id == existing.id, AdminSession.revoked_at.is_(None)
                    )
                    .values(revoked_at=datetime.now(UTC))
                )
            else:
                if admins:
                    raise ValueError(
                        "Akun admin sudah ada. Gunakan --rotate-password untuk rotasi."
                    )
                session.add(
                    AdminUser(
                        id=uuid4(),
                        username=username,
                        normalized_username=normalized,
                        password_hash=hash_password(password),
                        is_active=True,
                        password_changed_at=datetime.now(UTC),
                    )
                )


def bootstrap_admin() -> None:
    parser = argparse.ArgumentParser(description="Bootstrap akun admin secara interaktif.")
    parser.add_argument(
        "--rotate-password",
        action="store_true",
        help="Ganti kata sandi akun admin tunggal dan cabut semua sesi aktif.",
    )
    args = parser.parse_args()
    if get_settings().database_url is None:
        raise SystemExit("DATABASE_URL belum dikonfigurasi.")
    try:
        username = _read_username()
        password = _read_password()
        operation = _save_admin(username, password, rotate=args.rotate_password)
        if sys.platform == "win32":
            asyncio.run(operation, loop_factory=asyncio.SelectorEventLoop)
        else:
            asyncio.run(operation)
    except (ValueError, SQLAlchemyError, DatabaseUnavailableError) as exc:
        message = str(exc) if isinstance(exc, ValueError) else "database tidak tersedia."
        raise SystemExit(f"Bootstrap admin gagal: {message}") from None
    print("Akun admin berhasil diperbarui.")


def main() -> None:
    bootstrap_admin()


if __name__ == "__main__":
    main()
