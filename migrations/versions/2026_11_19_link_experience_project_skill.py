"""Link projects and careers, record experience stack, add skill imagery."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c81a42d9e630"
down_revision: str | None = "b7f4c7a1d2e9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "project_experiences",
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("experience_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["experience_id"], ["experiences.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("project_id", "experience_id"),
    )
    op.create_table(
        "experience_skills",
        sa.Column("experience_id", sa.Uuid(), nullable=False),
        sa.Column("skill_id", sa.Uuid(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("name_snapshot", sa.String(length=100), nullable=False),
        sa.ForeignKeyConstraint(["experience_id"], ["experiences.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["skill_id"], ["skills.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("experience_id", "skill_id"),
        sa.UniqueConstraint("experience_id", "sort_order", name="uq_experience_skills_sort"),
        sa.CheckConstraint("sort_order >= 0", name="nonnegative_sort"),
    )
    op.add_column("skills", sa.Column("icon_asset_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_skills_icon_asset_id_media_assets",
        "skills",
        "media_assets",
        ["icon_asset_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_skills_icon_asset_id_media_assets", "skills", type_="foreignkey")
    op.drop_column("skills", "icon_asset_id")
    op.drop_table("experience_skills")
    op.drop_table("project_experiences")
