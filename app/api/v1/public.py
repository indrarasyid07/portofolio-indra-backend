"""Unauthenticated read-only portfolio endpoints."""

from __future__ import annotations

import base64
import binascii
import json
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import EmailStr, HttpUrl, TypeAdapter
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload, with_loader_criteria

from app.core.config import get_settings
from app.db.session import get_session
from app.models import (
    EducationRecord,
    Experience,
    ExperienceSkill,
    MediaAsset,
    Profile,
    Project,
    ProjectImage,
    ProjectSkill,
    Skill,
    SkillCategory,
)
from app.schemas.public import (
    ApiErrorEnvelope,
    ContactData,
    DataEnvelope,
    EducationData,
    ExperienceData,
    ExperienceReference,
    PageData,
    PageMeta,
    ProfileData,
    ProjectDetailData,
    ProjectListData,
    PublicImage,
    SkillCategoryData,
    SkillData,
    SocialLinks,
)

router = APIRouter(
    prefix="/api/v1/public",
    tags=["public"],
    responses={
        400: {"model": ApiErrorEnvelope, "description": "Invalid request or cursor."},
        404: {"model": ApiErrorEnvelope, "description": "Published resource not found."},
        422: {"model": ApiErrorEnvelope, "description": "Query validation failed."},
        503: {"model": ApiErrorEnvelope, "description": "Database unavailable."},
    },
)
SessionDep = Annotated[AsyncSession, Depends(get_session)]
EducationKind = Literal["formal", "course", "certification"]
ProjectType = Literal["web", "mobile", "desktop", "api", "other"]
URL_ADAPTER = TypeAdapter(HttpUrl)
EMAIL_ADAPTER = TypeAdapter(EmailStr)


class PublicApiError(HTTPException):
    """An HTTP error with a stable public API error code."""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(status_code=status_code, detail={"code": code, "message": message})


def _cursor_encode(values: list[str | int | None]) -> str:
    raw = json.dumps(values, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _cursor_decode(cursor: str | None) -> tuple[int, int | None, UUID] | None:
    if cursor is None:
        return None
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        value: Any = json.loads(base64.b64decode(padded, altchars=b"-_", validate=True))
        if (
            not isinstance(value, list)
            or len(value) != 3
            or type(value[0]) is not int
            or value[0] < 0
            or (value[1] is not None and type(value[1]) is not int)
            or not isinstance(value[2], str)
        ):
            raise ValueError
        return value[0], value[1], UUID(value[2])
    except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError, binascii.Error) as exc:
        raise PublicApiError(400, "invalid_request", "Cursor tidak valid.") from exc


def _cursor_filter(
    cursor: tuple[int, int | None, UUID] | None,
    sort_column: Any,
    year_column: Any,
    id_column: Any,
) -> Any | None:
    if cursor is None:
        return None
    sort_order, year, row_id = cursor
    if year is None:
        return or_(
            sort_column > sort_order,
            and_(sort_column == sort_order, year_column.is_(None), id_column > row_id),
        )
    return or_(
        sort_column > sort_order,
        and_(
            sort_column == sort_order,
            or_(
                year_column < year,
                year_column.is_(None),
                and_(year_column == year, id_column > row_id),
            ),
        ),
    )


def _next_cursor(rows: list[Any], limit: int) -> str | None:
    if len(rows) <= limit:
        return None
    last = rows[limit - 1]
    return _cursor_encode([last.sort_order, last.year, str(last.id)])


def _safe_external_url(value: str | None) -> HttpUrl | None:
    if not value:
        return None
    parts = urlsplit(value.strip())
    settings = get_settings()
    local_http = (
        settings.app_env.lower() in {"development", "test"}
        and parts.scheme == "http"
        and parts.hostname in {"localhost", "127.0.0.1", "::1"}
    )
    if (
        (parts.scheme != "https" and not local_http)
        or not parts.hostname
        or parts.username is not None
        or parts.password is not None
    ):
        return None
    try:
        return URL_ADAPTER.validate_python(value.strip())
    except ValueError:
        return None


def _safe_media_url(asset: MediaAsset | None) -> HttpUrl | None:
    if asset is None or asset.state != "ready":
        return None
    base = get_settings().public_site_origin
    if not base:
        return None
    base_parts = urlsplit(base.strip())
    local_http = (
        get_settings().app_env.lower() in {"development", "test"}
        and base_parts.scheme == "http"
        and base_parts.hostname in {"localhost", "127.0.0.1", "::1"}
    )
    if (
        (base_parts.scheme != "https" and not local_http)
        or not base_parts.hostname
        or base_parts.username is not None
        or base_parts.password is not None
        or base_parts.query
        or base_parts.fragment
    ):
        return None
    base_path = base_parts.path.rstrip("/")
    url = urlunsplit(
        (
            base_parts.scheme,
            base_parts.netloc,
            f"{base_path}/api/v1/public/media/{asset.id}",
            "",
            "",
        )
    )
    try:
        return URL_ADAPTER.validate_python(url)
    except ValueError:
        return None


def _public_image(asset: MediaAsset | None) -> PublicImage | None:
    if asset is None or asset.state != "ready":
        return None
    return PublicImage(
        url=_safe_media_url(asset),
        alt_text=asset.alt_text,
        caption=asset.caption,
    )


def _project_options() -> tuple[Any, ...]:
    return (
        selectinload(Project.images).joinedload(ProjectImage.media_asset),
        selectinload(Project.stack_links).joinedload(ProjectSkill.skill),
        selectinload(Project.experiences),
    )


def _project_stack(project: Project) -> list[str]:
    return [link.name_snapshot for link in project.stack_links if link.skill.status == "published"]


def _cover(project: Project) -> PublicImage | None:
    cover = next((image for image in project.images if image.is_cover), None)
    return _public_image(cover.media_asset) if cover else None


def _ordered_images(project: Project) -> list[PublicImage]:
    result: list[PublicImage] = []
    for link in sorted(project.images, key=lambda image: image.sort_order):
        public_image = _public_image(link.media_asset)
        if public_image is not None:
            result.append(public_image)
    return result


@router.get("/profile", response_model=DataEnvelope[ProfileData])
async def get_public_profile(session: SessionDep) -> DataEnvelope[ProfileData]:
    result = await session.execute(
        select(Profile)
        .where(Profile.id == 1, Profile.status == "published")
        .options(joinedload(Profile.portrait_asset))
    )
    profile = result.scalar_one_or_none()
    if profile is None:
        raise PublicApiError(404, "not_found", "Profil tidak ditemukan.")

    social_links = SocialLinks(
        github=_safe_external_url(profile.github_url),
        linkedin=_safe_external_url(profile.linkedin_url),
    )
    data = ProfileData(
        name=profile.name,
        role=profile.role,
        headline=profile.headline,
        intro=profile.intro,
        about=profile.about,
        location=profile.location,
        availability=profile.availability,
        portrait=_public_image(profile.portrait_asset),
        cv_url=_safe_external_url(profile.cv_url),
        social_links=social_links,
    )
    return DataEnvelope(data=data)


@router.get("/projects", response_model=PageData[list[ProjectListData]])
async def get_public_projects(
    session: SessionDep,
    featured: bool | None = None,
    project_type: Annotated[ProjectType | None, Query(alias="type")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
) -> PageData[list[ProjectListData]]:
    position = _cursor_decode(cursor)
    statement = (
        select(Project)
        .where(Project.status == "published")
        .options(*_project_options())
        .order_by(Project.sort_order.asc(), Project.year.desc(), Project.id.asc())
    )
    if featured is not None:
        statement = statement.where(Project.featured.is_(featured))
    if project_type is not None:
        statement = statement.where(Project.project_type == project_type)
    cursor_filter = _cursor_filter(position, Project.sort_order, Project.year, Project.id)
    if cursor_filter is not None:
        statement = statement.where(cursor_filter)

    projects = list((await session.scalars(statement.limit(limit + 1))).all())
    next_cursor = _next_cursor(projects, limit)
    data = [
        ProjectListData(
            id=project.id,
            slug=project.slug,
            title=project.title,
            subtitle=project.subtitle,
            project_type=project.project_type,
            year=project.year,
            role=project.role,
            summary=project.summary,
            featured=project.featured,
            cover=_cover(project),
            stack_labels=_project_stack(project),
        )
        for project in projects[:limit]
    ]
    return PageData(data=data, meta=PageMeta(next_cursor=next_cursor, limit=limit))


@router.get("/projects/{slug}", response_model=DataEnvelope[ProjectDetailData])
async def get_public_project(slug: str, session: SessionDep) -> DataEnvelope[ProjectDetailData]:
    result = await session.execute(
        select(Project)
        .where(Project.slug == slug, Project.status == "published")
        .options(*_project_options())
    )
    project = result.scalar_one_or_none()
    if project is None:
        raise PublicApiError(404, "not_found", "Proyek tidak ditemukan.")
    data = ProjectDetailData(
        id=project.id,
        slug=project.slug,
        title=project.title,
        subtitle=project.subtitle,
        project_type=project.project_type,
        year=project.year,
        role=project.role,
        summary=project.summary,
        challenge=project.challenge,
        solution=project.solution,
        outcome=project.outcome,
        demo_url=_safe_external_url(project.demo_url),
        repository_url=_safe_external_url(project.repository_url),
        featured=project.featured,
        cover=_cover(project),
        images=_ordered_images(project),
        stack_labels=_project_stack(project),
        related_experiences=[
            ExperienceReference(id=item.id, role=item.role, organization=item.organization)
            for item in project.experiences
            if item.status == "published"
        ],
        published_at=project.published_at,
    )
    return DataEnvelope(data=data)


@router.get("/experiences", response_model=PageData[list[ExperienceData]])
async def get_public_experiences(
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
) -> PageData[list[ExperienceData]]:
    position = _cursor_decode(cursor)
    statement = (
        select(Experience)
        .where(Experience.status == "published")
        .options(selectinload(Experience.stack_links).joinedload(ExperienceSkill.skill))
        .order_by(Experience.sort_order.asc(), Experience.start_year.desc(), Experience.id.asc())
    )
    cursor_filter = _cursor_filter(
        position, Experience.sort_order, Experience.start_year, Experience.id
    )
    if cursor_filter is not None:
        statement = statement.where(cursor_filter)
    rows = list((await session.scalars(statement.limit(limit + 1))).all())
    data = [
        ExperienceData.model_validate(
            {
                **{
                    field: getattr(row, field)
                    for field in (
                        "id",
                        "role",
                        "organization",
                        "employment_type",
                        "location",
                        "start_year",
                        "start_month",
                        "end_year",
                        "end_month",
                        "summary",
                        "highlights",
                        "published_at",
                    )
                },
                "stack_labels": [
                    link.name_snapshot
                    for link in sorted(row.stack_links, key=lambda link: link.sort_order)
                    if link.skill.status == "published"
                ],
            }
        )
        for row in rows[:limit]
    ]
    return PageData(
        data=data,
        meta=PageMeta(next_cursor=_next_cursor(rows, limit), limit=limit),
    )


@router.get("/education", response_model=PageData[list[EducationData]])
async def get_public_education(
    session: SessionDep,
    kind: EducationKind | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
) -> PageData[list[EducationData]]:
    position = _cursor_decode(cursor)
    statement = (
        select(EducationRecord)
        .where(EducationRecord.status == "published")
        .order_by(
            EducationRecord.sort_order.asc(),
            EducationRecord.start_year.desc().nulls_last(),
            EducationRecord.id.asc(),
        )
    )
    if kind is not None:
        statement = statement.where(EducationRecord.kind == kind)
    cursor_filter = _cursor_filter(
        position,
        EducationRecord.sort_order,
        EducationRecord.start_year,
        EducationRecord.id,
    )
    if cursor_filter is not None:
        statement = statement.where(cursor_filter)
    rows = list((await session.scalars(statement.limit(limit + 1))).all())
    data = [
        EducationData(
            id=row.id,
            kind=row.kind,
            title=row.title,
            institution=row.institution,
            start_year=row.start_year,
            start_month=row.start_month,
            end_year=row.end_year,
            end_month=row.end_month,
            description=row.description,
            proof_url=_safe_external_url(row.proof_url),
            credential_id=row.credential_id,
            published_at=row.published_at,
        )
        for row in rows[:limit]
    ]
    return PageData(data=data, meta=PageMeta(next_cursor=_next_cursor(rows, limit), limit=limit))


@router.get("/skills", response_model=PageData[list[SkillCategoryData]])
async def get_public_skills(session: SessionDep) -> PageData[list[SkillCategoryData]]:
    categories = list(
        (
            await session.scalars(
                select(SkillCategory)
                .where(SkillCategory.is_published.is_(True))
                .options(selectinload(SkillCategory.skills).joinedload(Skill.icon_asset))
                .options(with_loader_criteria(Skill, Skill.status == "published"))
                .order_by(SkillCategory.sort_order.asc(), SkillCategory.label.asc())
            )
        ).all()
    )
    data = [
        SkillCategoryData(
            id=category.id,
            slug=category.slug,
            label=category.label,
            skills=[
                SkillData(
                    id=skill.id,
                    name=skill.name,
                    note=skill.note,
                    image=_public_image(skill.icon_asset),
                )
                for skill in category.skills
            ],
        )
        for category in categories
    ]
    return PageData(data=data)


@router.get("/contact", response_model=DataEnvelope[ContactData])
async def get_public_contact(session: SessionDep) -> DataEnvelope[ContactData]:
    profile = await session.scalar(
        select(Profile).where(Profile.id == 1, Profile.status == "published")
    )
    if profile is None:
        raise PublicApiError(404, "not_found", "Kontak tidak tersedia.")
    email: EmailStr | None = None
    if profile.public_email:
        try:
            email = EMAIL_ADAPTER.validate_python(profile.public_email.strip())
        except ValueError:
            email = None
    data = ContactData(
        email=email,
        github=_safe_external_url(profile.github_url),
        linkedin=_safe_external_url(profile.linkedin_url),
    )
    return DataEnvelope(data=data)
