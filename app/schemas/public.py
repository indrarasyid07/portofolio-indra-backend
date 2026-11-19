"""Response DTOs for anonymous public API routes."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, HttpUrl


class PublicModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")


class PageMeta(PublicModel):
    next_cursor: str | None
    limit: int


class PageData[T](PublicModel):
    data: T
    meta: PageMeta | None = None


class DataEnvelope[T](PublicModel):
    data: T


class ApiErrorField(PublicModel):
    path: str
    code: str
    message: str


class ApiError(PublicModel):
    code: str
    message: str
    request_id: str
    fields: list[ApiErrorField] | None = None


class ApiErrorEnvelope(PublicModel):
    error: ApiError


class PublicImage(PublicModel):
    url: HttpUrl | None
    alt_text: str
    caption: str | None


class SocialLinks(PublicModel):
    github: HttpUrl | None
    linkedin: HttpUrl | None


class ProfileData(PublicModel):
    name: str
    role: str
    headline: str
    intro: str
    about: str
    location: str | None
    availability: str | None
    portrait: PublicImage | None
    cv_url: HttpUrl | None
    social_links: SocialLinks


class ProjectListData(PublicModel):
    id: UUID
    slug: str
    title: str
    subtitle: str | None
    project_type: str
    year: int
    role: str
    summary: str
    featured: bool
    cover: PublicImage | None
    stack_labels: list[str]


class ExperienceReference(PublicModel):
    id: UUID
    role: str
    organization: str


class ProjectDetailData(ProjectListData):
    challenge: str | None
    solution: str | None
    outcome: str | None
    demo_url: HttpUrl | None
    repository_url: HttpUrl | None
    images: list[PublicImage]
    published_at: datetime | None
    related_experiences: list[ExperienceReference]


class ExperienceData(PublicModel):
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
    stack_labels: list[str]


class EducationData(PublicModel):
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


class SkillData(PublicModel):
    id: UUID
    name: str
    note: str | None
    image: PublicImage | None


class SkillCategoryData(PublicModel):
    id: UUID
    slug: str
    label: str
    skills: list[SkillData]


class ContactData(PublicModel):
    email: EmailStr | None
    github: HttpUrl | None
    linkedin: HttpUrl | None
