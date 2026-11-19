"""Public-safe response schemas, intentionally excluding storage/auth internals."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, HttpUrl


class ReadModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ProfileRead(ReadModel):
    name: str
    role: str
    headline: str
    intro: str
    about: str
    location: str | None
    availability: str | None
    public_email: str | None
    github_url: HttpUrl | None
    linkedin_url: HttpUrl | None
    cv_url: HttpUrl | None
    status: str
    published_at: datetime | None


class ProjectRead(ReadModel):
    id: UUID
    slug: str
    title: str
    subtitle: str | None
    project_type: str
    year: int
    role: str
    summary: str
    challenge: str | None
    solution: str | None
    outcome: str | None
    demo_url: HttpUrl | None
    repository_url: HttpUrl | None
    featured: bool
    published_at: datetime | None


class ExperienceRead(ReadModel):
    id: UUID
    role: str
    organization: str
    employment_type: str
    location: str | None
    start_year: int
    start_month: int | None
    end_year: int | None
    end_month: int | None
    summary: str
    highlights: list[str]
    published_at: datetime | None


class EducationRead(ReadModel):
    id: UUID
    kind: str
    title: str
    institution: str
    start_year: int | None
    start_month: int | None
    end_year: int | None
    end_month: int | None
    description: str | None
    proof_url: HttpUrl | None
    credential_id: str | None
    published_at: datetime | None


class SkillRead(ReadModel):
    id: UUID
    category_id: UUID
    name: str
    note: str | None
