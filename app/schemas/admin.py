"""Admin-only request and response contracts for portfolio content."""

from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

Slug = Annotated[str, Field(min_length=1, max_length=140, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")]
PositiveOrder = Annotated[int, Field(ge=0, le=1_000_000)]
Year = Annotated[int, Field(ge=1900, le=2200)]
Month = Annotated[int | None, Field(default=None, ge=1, le=12)]
ShortText = Annotated[str, Field(min_length=1)]


class AdminInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class VersionedInput(AdminInput):
    expected_updated_at: datetime

    @field_validator("expected_updated_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Timestamp versi harus memiliki zona waktu.")
        return value


class ProfilePut(AdminInput):
    expected_updated_at: datetime | None = None
    name: Annotated[str, Field(max_length=120)] = ""
    role: Annotated[str, Field(max_length=160)] = ""
    headline: Annotated[str, Field(max_length=240)] = ""
    intro: Annotated[str, Field(max_length=1200)] = ""
    about: str = ""
    location: Annotated[str | None, Field(max_length=120)] = None
    availability: Annotated[str | None, Field(max_length=160)] = None
    public_email: EmailStr | None = None
    github_url: Annotated[str | None, Field(max_length=2048)] = None
    linkedin_url: Annotated[str | None, Field(max_length=2048)] = None
    cv_url: Annotated[str | None, Field(max_length=2048)] = None
    portrait_asset_id: UUID | None = None

    @field_validator("expected_updated_at")
    @classmethod
    def require_timezone_if_versioned(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("Timestamp versi harus memiliki zona waktu.")
        return value


class ProjectFields(AdminInput):
    slug: Slug
    title: Annotated[str, Field(min_length=1, max_length=120)]
    subtitle: Annotated[str | None, Field(max_length=180)] = None
    project_type: Literal["web", "mobile", "desktop", "api", "other"]
    year: Year
    role: Annotated[str, Field(min_length=1, max_length=240)]
    summary: Annotated[str, Field(min_length=1, max_length=600)]
    challenge: str | None = None
    solution: str | None = None
    outcome: str | None = None
    demo_url: Annotated[str | None, Field(max_length=2048)] = None
    repository_url: Annotated[str | None, Field(max_length=2048)] = None
    featured: bool = False
    sort_order: PositiveOrder = 0
    skill_ids: list[UUID] = Field(default_factory=list, max_length=50)
    experience_ids: list[UUID] = Field(default_factory=list, max_length=30)


class ProjectCreate(ProjectFields):
    pass


class ProjectUpdate(ProjectFields, VersionedInput):
    pass


class ExperienceFields(AdminInput):
    role: Annotated[str, Field(min_length=1, max_length=180)]
    organization: Annotated[str, Field(min_length=1, max_length=180)]
    employment_type: Literal[
        "full_time", "part_time", "contract", "internship", "freelance", "other"
    ]
    location: Annotated[str | None, Field(max_length=140)] = None
    start_year: Year
    start_month: Month
    end_year: Annotated[int | None, Field(default=None, ge=1900, le=2200)]
    end_month: Month
    is_current: bool = False
    summary: Annotated[str, Field(min_length=1, max_length=2000)]
    highlights: list[Annotated[str, Field(min_length=1, max_length=500)]] = Field(
        default_factory=list, max_length=30
    )
    sort_order: PositiveOrder = 0
    skill_ids: list[UUID] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def valid_period(self) -> "ExperienceFields":
        if self.is_current:
            self.end_year = None
            self.end_month = None
        if (self.end_year is None) != (self.end_month is None):
            raise ValueError(
                "Isi tahun dan bulan akhir bersama-sama, atau biarkan keduanya kosong."
            )
        if self.end_year is not None:
            if (self.end_year, self.end_month or 12) < (self.start_year, self.start_month or 1):
                raise ValueError("Tanggal akhir harus setelah tanggal mulai.")
        return self


class ExperienceCreate(ExperienceFields):
    pass


class ExperienceUpdate(ExperienceFields, VersionedInput):
    pass


class EducationFields(AdminInput):
    kind: Literal["formal", "course", "certification"]
    title: Annotated[str, Field(min_length=1, max_length=180)]
    institution: Annotated[str, Field(min_length=1, max_length=180)]
    start_year: Annotated[int | None, Field(default=None, ge=1900, le=2200)] = None
    start_month: Month = None
    end_year: Annotated[int | None, Field(default=None, ge=1900, le=2200)] = None
    end_month: Month = None
    description: Annotated[str | None, Field(max_length=2000)] = None
    proof_url: Annotated[str | None, Field(max_length=2048)] = None
    credential_id: Annotated[str | None, Field(max_length=180)] = None
    sort_order: PositiveOrder = 0

    @model_validator(mode="after")
    def valid_period(self) -> "EducationFields":
        if (self.start_year is None) != (self.start_month is None) and self.start_month is not None:
            raise ValueError("Bulan mulai memerlukan tahun mulai.")
        if (self.end_year is None) != (self.end_month is None) and self.end_month is not None:
            raise ValueError("Bulan akhir memerlukan tahun akhir.")
        if self.start_year is not None and self.end_year is not None:
            if (self.end_year, self.end_month or 12) < (self.start_year, self.start_month or 1):
                raise ValueError("Tanggal akhir harus setelah tanggal mulai.")
        return self


class EducationCreate(EducationFields):
    pass


class EducationUpdate(EducationFields, VersionedInput):
    pass


class SkillCategoryFields(AdminInput):
    slug: Annotated[str, Field(min_length=1, max_length=80, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")]
    label: Annotated[str, Field(min_length=1, max_length=100)]
    sort_order: PositiveOrder = 0


class SkillCategoryCreate(SkillCategoryFields):
    pass


class SkillCategoryUpdate(SkillCategoryFields, VersionedInput):
    pass


class SkillFields(AdminInput):
    category_id: UUID
    name: Annotated[str, Field(min_length=1, max_length=100)]
    note: Annotated[str | None, Field(max_length=240)] = None
    sort_order: PositiveOrder = 0
    icon_asset_id: UUID | None = None


class SkillCreate(SkillFields):
    pass


class SkillUpdate(SkillFields, VersionedInput):
    pass


class AdminProfileRead(BaseModel):
    id: int
    name: str
    role: str
    headline: str
    intro: str
    about: str
    location: str | None
    availability: str | None
    public_email: str | None
    github_url: str | None
    linkedin_url: str | None
    cv_url: str | None
    portrait_asset_id: UUID | None
    status: str
    published_at: datetime | None
    updated_at: datetime | None

    @field_validator("published_at", "updated_at")
    @classmethod
    def normalize_optional_timestamps(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            return value.replace(tzinfo=UTC)
        return value


class AdminContentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    status: str
    published_at: datetime | None
    created_at: datetime
    updated_at: datetime

    @field_validator("created_at", "updated_at", "published_at")
    @classmethod
    def normalize_timestamps(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            return value.replace(tzinfo=UTC)
        return value


class AdminProjectRead(AdminContentRead):
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
    demo_url: str | None
    repository_url: str | None
    featured: bool
    sort_order: int
    skill_ids: list[UUID]
    skill_labels: list[str]
    experience_ids: list[UUID]
    experience_labels: list[str]


class AdminExperienceRead(AdminContentRead):
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
    sort_order: int
    skill_ids: list[UUID]
    skill_labels: list[str]
    is_current: bool


class AdminEducationRead(AdminContentRead):
    kind: str
    title: str
    institution: str
    start_year: int | None
    start_month: int | None
    end_year: int | None
    end_month: int | None
    description: str | None
    proof_url: str | None
    credential_id: str | None
    sort_order: int


class AdminSkillCategoryRead(BaseModel):
    id: UUID
    slug: str
    label: str
    sort_order: int
    is_published: bool
    updated_at: datetime
    skill_count: int

    @field_validator("updated_at")
    @classmethod
    def normalize_updated_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            return value.replace(tzinfo=UTC)
        return value


class AdminSkillRead(BaseModel):
    id: UUID
    status: str
    category_id: UUID
    category_label: str
    name: str
    note: str | None
    icon_asset_id: UUID | None
    sort_order: int
    created_at: datetime
    updated_at: datetime

    @field_validator("created_at", "updated_at")
    @classmethod
    def normalize_timestamps(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            return value.replace(tzinfo=UTC)
        return value


class AdminPageMeta(BaseModel):
    total: int
    limit: int
    offset: int


class AdminPage[T](BaseModel):
    data: list[T]
    meta: AdminPageMeta


class AdminOverview(BaseModel):
    profile_status: str | None
    projects_total: int
    projects_published: int
    experiences_total: int
    education_total: int
    skills_total: int
