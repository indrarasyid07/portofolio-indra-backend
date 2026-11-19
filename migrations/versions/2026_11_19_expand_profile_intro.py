"""Allow a longer profile experience summary."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d39f6b20a841"
down_revision: str | None = "c81a42d9e630"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "profiles",
        "intro",
        existing_type=sa.String(length=600),
        type_=sa.String(length=1200),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "profiles",
        "intro",
        existing_type=sa.String(length=1200),
        type_=sa.String(length=600),
        existing_nullable=False,
    )
