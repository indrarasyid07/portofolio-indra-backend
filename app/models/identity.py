"""Admin identities and hashed authentication metadata."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin


class AdminUser(Base, TimestampMixin):
    __tablename__ = "admin_users"
    __table_args__ = (
        UniqueConstraint("normalized_username", name="uq_admin_users_normalized_username"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    username: Mapped[str] = mapped_column(String(80), nullable=False)
    normalized_username: Mapped[str] = mapped_column(String(80), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    password_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sessions: Mapped[list[AdminSession]] = relationship(
        back_populates="admin_user",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="raise",
    )


class AdminSession(Base):
    __tablename__ = "admin_sessions"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_admin_sessions_token_hash"),
        CheckConstraint("expires_at > created_at", name="expiry_after_create"),
        CheckConstraint("key_version > 0", name="positive_key_version"),
        Index("ix_admin_sessions_user_expiry", "admin_user_id", "expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    admin_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("admin_users.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    key_version: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1)
    csrf_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    user_agent_hash: Mapped[str | None] = mapped_column(String(64))

    admin_user: Mapped[AdminUser] = relationship(back_populates="sessions", lazy="raise")


class LoginAttempt(Base):
    __tablename__ = "login_attempts"
    __table_args__ = (
        CheckConstraint("outcome IN ('success', 'failure', 'locked')", name="outcome"),
        Index("ix_login_attempts_key_time", "attempt_key_hash", "attempted_at"),
        Index("ix_login_attempts_source_time", "source_key_hash", "attempted_at"),
        Index("ix_login_attempts_attempted_at", "attempted_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    attempt_key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_key_hash: Mapped[str | None] = mapped_column(String(64))
    attempted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
