"""Public portfolio content and media metadata."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

JSON_DOCUMENT = JSON().with_variant(JSONB, "postgresql")


class MediaAsset(Base, TimestampMixin):
    __tablename__ = "media_assets"
    __table_args__ = (
        UniqueConstraint(
            "provider", "bucket", "object_key", name="uq_media_assets_provider_bucket_object"
        ),
        CheckConstraint("provider IN ('neon', 's3_compatible', 'local')", name="provider"),
        CheckConstraint("state IN ('pending', 'ready', 'rejected', 'deleting')", name="state"),
        CheckConstraint("byte_size > 0", name="positive_byte_size"),
        CheckConstraint("width IS NULL OR width > 0", name="positive_width"),
        CheckConstraint("height IS NULL OR height > 0", name="positive_height"),
        CheckConstraint("length(sha256) = 64", name="sha256_length"),
        Index("ix_media_assets_state_delete_after", "state", "delete_after"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    bucket: Mapped[str] = mapped_column(String(100), nullable=False)
    object_key: Mapped[str] = mapped_column(String(500), nullable=False)
    content_type: Mapped[str] = mapped_column(String(80), nullable=False)
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    alt_text: Mapped[str] = mapped_column(String(180), nullable=False, default="")
    caption: Mapped[str | None] = mapped_column(String(240))
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("admin_users.id", ondelete="SET NULL")
    )
    delete_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    profile: Mapped[Profile | None] = relationship(
        back_populates="portrait_asset", foreign_keys="Profile.portrait_asset_id", lazy="raise"
    )
    project_links: Mapped[list[ProjectImage]] = relationship(
        back_populates="media_asset", passive_deletes=True, lazy="raise"
    )


class Profile(Base, TimestampMixin):
    __tablename__ = "profiles"
    __table_args__ = (
        CheckConstraint("id = 1", name="singleton"),
        CheckConstraint("status IN ('draft', 'published')", name="status"),
        CheckConstraint("length(name) <= 120", name="name_length"),
    )

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, default=1)
    name: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    role: Mapped[str] = mapped_column(String(160), nullable=False, default="")
    headline: Mapped[str] = mapped_column(String(240), nullable=False, default="")
    intro: Mapped[str] = mapped_column(String(1200), nullable=False, default="")
    about: Mapped[str] = mapped_column(Text, nullable=False, default="")
    location: Mapped[str | None] = mapped_column(String(120))
    availability: Mapped[str | None] = mapped_column(String(160))
    portrait_asset_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("media_assets.id", ondelete="SET NULL")
    )
    public_email: Mapped[str | None] = mapped_column(String(320))
    github_url: Mapped[str | None] = mapped_column(Text)
    linkedin_url: Mapped[str | None] = mapped_column(Text)
    cv_url: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="draft")
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    portrait_asset: Mapped[MediaAsset | None] = relationship(
        back_populates="profile", foreign_keys=[portrait_asset_id], lazy="raise"
    )


class Project(Base, TimestampMixin):
    __tablename__ = "projects"
    __table_args__ = (
        CheckConstraint("status IN ('draft', 'published')", name="status"),
        CheckConstraint(
            "project_type IN ('web', 'mobile', 'desktop', 'api', 'other')", name="type"
        ),
        CheckConstraint("year BETWEEN 1900 AND 2200", name="year_range"),
        CheckConstraint("sort_order >= 0", name="nonnegative_sort"),
        CheckConstraint(
            "length(slug) > 0 AND slug = lower(slug) AND slug NOT LIKE '% %' "
            "AND slug NOT LIKE '-%' AND slug NOT LIKE '%-' AND slug NOT LIKE '%--%'",
            name="slug_format",
        ),
        UniqueConstraint("slug", name="uq_projects_slug"),
        Index("ix_projects_status_sort_published", "status", "sort_order", "published_at"),
        Index(
            "uq_projects_single_published_featured",
            "featured",
            unique=True,
            postgresql_where=text("status = 'published' AND featured"),
            sqlite_where=text("status = 'published' AND featured = 1"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    slug: Mapped[str] = mapped_column(String(140), nullable=False)
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    subtitle: Mapped[str | None] = mapped_column(String(180))
    project_type: Mapped[str] = mapped_column(String(32), nullable=False)
    year: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    role: Mapped[str] = mapped_column(String(240), nullable=False)
    summary: Mapped[str] = mapped_column(String(600), nullable=False)
    challenge: Mapped[str | None] = mapped_column(Text)
    solution: Mapped[str | None] = mapped_column(Text)
    outcome: Mapped[str | None] = mapped_column(Text)
    demo_url: Mapped[str | None] = mapped_column(Text)
    repository_url: Mapped[str | None] = mapped_column(Text)
    featured: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="draft")
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    images: Mapped[list[ProjectImage]] = relationship(
        back_populates="project", cascade="all, delete-orphan", passive_deletes=True, lazy="raise"
    )
    stack_links: Mapped[list[ProjectSkill]] = relationship(
        back_populates="project", cascade="all, delete-orphan", passive_deletes=True, lazy="raise"
    )
    experiences: Mapped[list[Experience]] = relationship(
        secondary="project_experiences", back_populates="projects", lazy="raise"
    )


class ProjectImage(Base):
    __tablename__ = "project_images"
    __table_args__ = (
        CheckConstraint("sort_order >= 0", name="nonnegative_sort"),
        UniqueConstraint("project_id", "sort_order", name="uq_project_images_project_sort"),
        Index(
            "uq_project_images_single_cover",
            "project_id",
            unique=True,
            postgresql_where=text("is_cover"),
            sqlite_where=text("is_cover = 1"),
        ),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True
    )
    media_asset_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("media_assets.id", ondelete="RESTRICT"), primary_key=True
    )
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_cover: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )

    project: Mapped[Project] = relationship(back_populates="images", lazy="raise")
    media_asset: Mapped[MediaAsset] = relationship(back_populates="project_links", lazy="raise")


class Experience(Base, TimestampMixin):
    __tablename__ = "experiences"
    __table_args__ = (
        CheckConstraint(
            "employment_type IN ('full_time', 'part_time', 'contract', 'internship', "
            "'freelance', 'other')",
            name="employment_type",
        ),
        CheckConstraint("status IN ('draft', 'published')", name="status"),
        CheckConstraint("sort_order >= 0", name="nonnegative_sort"),
        CheckConstraint("start_month IS NULL OR start_month BETWEEN 1 AND 12", name="start_month"),
        CheckConstraint("end_month IS NULL OR end_month BETWEEN 1 AND 12", name="end_month"),
        CheckConstraint("(end_year IS NULL) = (end_month IS NULL)", name="end_precision_pair"),
        CheckConstraint(
            "end_year IS NULL OR (end_year, COALESCE(end_month, 12)) >= "
            "(start_year, COALESCE(start_month, 1))",
            name="date_order",
        ),
        Index("ix_experiences_status_sort_start", "status", "sort_order", "start_year"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    role: Mapped[str] = mapped_column(String(180), nullable=False)
    organization: Mapped[str] = mapped_column(String(180), nullable=False)
    employment_type: Mapped[str] = mapped_column(String(32), nullable=False)
    location: Mapped[str | None] = mapped_column(String(140))
    start_year: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    start_month: Mapped[int | None] = mapped_column(SmallInteger)
    end_year: Mapped[int | None] = mapped_column(SmallInteger)
    end_month: Mapped[int | None] = mapped_column(SmallInteger)
    summary: Mapped[str] = mapped_column(String(2000), nullable=False)
    highlights: Mapped[list[str]] = mapped_column(JSON_DOCUMENT, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="draft")
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stack_links: Mapped[list[ExperienceSkill]] = relationship(
        back_populates="experience",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="raise",
    )
    projects: Mapped[list[Project]] = relationship(
        secondary="project_experiences", back_populates="experiences", lazy="raise"
    )


class EducationRecord(Base, TimestampMixin):
    __tablename__ = "education_records"
    __table_args__ = (
        CheckConstraint("kind IN ('formal', 'course', 'certification')", name="kind"),
        CheckConstraint("status IN ('draft', 'published')", name="status"),
        CheckConstraint("sort_order >= 0", name="nonnegative_sort"),
        CheckConstraint("start_month IS NULL OR start_month BETWEEN 1 AND 12", name="start_month"),
        CheckConstraint("end_month IS NULL OR end_month BETWEEN 1 AND 12", name="end_month"),
        CheckConstraint(
            "start_year IS NULL OR end_year IS NULL OR "
            "(end_year, COALESCE(end_month, 12)) >= (start_year, COALESCE(start_month, 1))",
            name="date_order",
        ),
        Index("ix_education_kind_status_sort", "kind", "status", "sort_order", "start_year"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    title: Mapped[str] = mapped_column(String(180), nullable=False)
    institution: Mapped[str] = mapped_column(String(180), nullable=False)
    start_year: Mapped[int | None] = mapped_column(SmallInteger)
    start_month: Mapped[int | None] = mapped_column(SmallInteger)
    end_year: Mapped[int | None] = mapped_column(SmallInteger)
    end_month: Mapped[int | None] = mapped_column(SmallInteger)
    description: Mapped[str | None] = mapped_column(String(2000))
    proof_url: Mapped[str | None] = mapped_column(Text)
    credential_id: Mapped[str | None] = mapped_column(String(180))
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="draft")
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SkillCategory(Base, TimestampMixin):
    __tablename__ = "skill_categories"
    __table_args__ = (
        UniqueConstraint("slug", name="uq_skill_categories_slug"),
        CheckConstraint("sort_order >= 0", name="nonnegative_sort"),
        Index("ix_skill_categories_published_sort", "is_published", "sort_order"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    slug: Mapped[str] = mapped_column(String(80), nullable=False)
    label: Mapped[str] = mapped_column(String(100), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_published: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    skills: Mapped[list[Skill]] = relationship(
        back_populates="category", passive_deletes="all", lazy="raise"
    )


class Skill(Base, TimestampMixin):
    __tablename__ = "skills"
    __table_args__ = (
        CheckConstraint("status IN ('draft', 'published')", name="status"),
        CheckConstraint("sort_order >= 0", name="nonnegative_sort"),
        UniqueConstraint("category_id", "name", name="uq_skills_category_name"),
        Index("ix_skills_category_status_sort", "category_id", "status", "sort_order"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    category_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("skill_categories.id", ondelete="RESTRICT"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    note: Mapped[str | None] = mapped_column(String(240))
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="draft")
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    icon_asset_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("media_assets.id", ondelete="SET NULL")
    )

    category: Mapped[SkillCategory] = relationship(back_populates="skills", lazy="raise")
    project_links: Mapped[list[ProjectSkill]] = relationship(back_populates="skill", lazy="raise")
    experience_links: Mapped[list[ExperienceSkill]] = relationship(
        back_populates="skill", lazy="raise"
    )
    icon_asset: Mapped[MediaAsset | None] = relationship(lazy="raise")


class ProjectSkill(Base):
    __tablename__ = "project_skills"
    __table_args__ = (
        CheckConstraint("sort_order >= 0", name="nonnegative_sort"),
        UniqueConstraint("project_id", "sort_order", name="uq_project_skills_project_sort"),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True
    )
    skill_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("skills.id", ondelete="RESTRICT"), primary_key=True
    )
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    name_snapshot: Mapped[str] = mapped_column(String(100), nullable=False)

    project: Mapped[Project] = relationship(back_populates="stack_links", lazy="raise")
    skill: Mapped[Skill] = relationship(back_populates="project_links", lazy="raise")


class ProjectExperience(Base):
    __tablename__ = "project_experiences"
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True
    )
    experience_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("experiences.id", ondelete="CASCADE"), primary_key=True
    )


class ExperienceSkill(Base):
    __tablename__ = "experience_skills"
    __table_args__ = (
        CheckConstraint("sort_order >= 0", name="nonnegative_sort"),
        UniqueConstraint("experience_id", "sort_order", name="uq_experience_skills_sort"),
    )

    experience_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("experiences.id", ondelete="CASCADE"), primary_key=True
    )
    skill_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("skills.id", ondelete="RESTRICT"), primary_key=True
    )
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    name_snapshot: Mapped[str] = mapped_column(String(100), nullable=False)

    experience: Mapped[Experience] = relationship(back_populates="stack_links", lazy="raise")
    skill: Mapped[Skill] = relationship(back_populates="experience_links", lazy="raise")
