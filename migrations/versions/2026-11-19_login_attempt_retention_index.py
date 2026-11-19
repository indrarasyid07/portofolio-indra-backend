"""Index login attempt timestamps for bounded retention cleanup."""

from collections.abc import Sequence

from alembic import op

revision: str = "b7f4c7a1d2e9"
down_revision: str | None = "9c32c40d902e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_login_attempts_attempted_at",
        "login_attempts",
        ["attempted_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_login_attempts_attempted_at", table_name="login_attempts")
