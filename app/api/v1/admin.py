"""Authenticated portfolio content management endpoints."""

from datetime import UTC, datetime
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

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
from app.schemas.admin import (
    AdminEducationRead,
    AdminExperienceRead,
    AdminOverview,
    AdminPage,
    AdminPageMeta,
    AdminProfileRead,
    AdminProjectRead,
    AdminSkillCategoryRead,
    AdminSkillRead,
    EducationCreate,
    EducationUpdate,
    ExperienceCreate,
    ExperienceUpdate,
    ProfilePut,
    ProjectCreate,
    ProjectUpdate,
    SkillCategoryCreate,
    SkillCategoryUpdate,
    SkillCreate,
    SkillUpdate,
    VersionedInput,
)
from app.security.dependencies import AdminPrincipal, require_admin, require_admin_write

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
AdminReadDep = Annotated[AdminPrincipal, Depends(require_admin)]
AdminWriteDep = Annotated[AdminPrincipal, Depends(require_admin_write)]
PublicStatus = Literal["draft", "published"]
LIST_LIMIT = 50


def _now() -> datetime:
    return datetime.now(UTC)


def _aware(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)


def _conflict(message: str = "Data berubah atau melanggar aturan unik.") -> HTTPException:
    return HTTPException(status_code=409, detail={"code": "conflict", "message": message})


def _not_found(label: str) -> HTTPException:
    return HTTPException(
        status_code=404,
        detail={"code": "not_found", "message": f"{label} tidak ditemukan."},
    )


async def _commit(session: AsyncSession) -> None:
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise _conflict() from exc


async def _flush(session: AsyncSession) -> None:
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise _conflict() from exc


def _check_version(current: datetime | None, expected: datetime) -> None:
    if _aware(current) != _aware(expected):
        raise _conflict("Konten berubah sejak dibuka. Muat ulang sebelum menyimpan.")


def _profile_out(row: Profile | None) -> AdminProfileRead:
    return AdminProfileRead(
        id=1,
        name=row.name if row else "",
        role=row.role if row else "",
        headline=row.headline if row else "",
        intro=row.intro if row else "",
        about=row.about if row else "",
        location=row.location if row else None,
        availability=row.availability if row else None,
        public_email=row.public_email if row else None,
        github_url=row.github_url if row else None,
        linkedin_url=row.linkedin_url if row else None,
        cv_url=row.cv_url if row else None,
        portrait_asset_id=row.portrait_asset_id if row else None,
        status=row.status if row else "draft",
        published_at=row.published_at if row else None,
        updated_at=row.updated_at if row else None,
    )


def _project_out(row: Project) -> AdminProjectRead:
    ordered = sorted(row.stack_links, key=lambda link: link.sort_order)
    return AdminProjectRead(
        id=row.id,
        slug=row.slug,
        title=row.title,
        subtitle=row.subtitle,
        project_type=row.project_type,
        year=row.year,
        role=row.role,
        summary=row.summary,
        challenge=row.challenge,
        solution=row.solution,
        outcome=row.outcome,
        demo_url=row.demo_url,
        repository_url=row.repository_url,
        featured=row.featured,
        status=row.status,
        sort_order=row.sort_order,
        published_at=row.published_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
        skill_ids=[link.skill_id for link in ordered],
        skill_labels=[link.name_snapshot for link in ordered],
        experience_ids=[item.id for item in row.experiences],
        experience_labels=[f"{item.role} · {item.organization}" for item in row.experiences],
    )


def _experience_out(row: Experience) -> AdminExperienceRead:
    ordered = sorted(row.stack_links, key=lambda link: link.sort_order)
    return AdminExperienceRead.model_validate(
        {
            **{
                field: getattr(row, field)
                for field in (
                    "id",
                    "status",
                    "published_at",
                    "created_at",
                    "updated_at",
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
                    "sort_order",
                )
            },
            "skill_ids": [link.skill_id for link in ordered],
            "skill_labels": [link.name_snapshot for link in ordered],
            "is_current": row.end_year is None,
        }
    )


def _education_out(row: EducationRecord) -> AdminEducationRead:
    return AdminEducationRead.model_validate(row)


def _skill_out(row: Skill) -> AdminSkillRead:
    return AdminSkillRead(
        id=row.id,
        status=row.status,
        created_at=row.created_at,
        updated_at=row.updated_at,
        category_id=row.category_id,
        category_label=row.category.label,
        name=row.name,
        note=row.note,
        sort_order=row.sort_order,
        icon_asset_id=row.icon_asset_id,
    )


async def _page(
    session: AsyncSession,
    model: Any,
    *,
    limit: int,
    offset: int,
    state: PublicStatus | None,
    search: str | None,
    search_columns: tuple[Any, ...],
    options: tuple[Any, ...] = (),
) -> tuple[list[Any], int]:
    filters = []
    if state is not None and hasattr(model, "status"):
        filters.append(model.status == state)
    if search:
        phrase = f"%{search.strip()}%"
        filters.append(or_(*(column.ilike(phrase) for column in search_columns)))
    count = int(await session.scalar(select(func.count()).select_from(model).where(*filters)) or 0)
    statement = (
        select(model).where(*filters).order_by(model.sort_order.asc(), model.created_at.desc())
    )
    for option in options:
        statement = statement.options(option)
    rows = list((await session.scalars(statement.offset(offset).limit(limit))).all())
    return rows, count


@router.get("/overview", response_model=AdminOverview)
async def overview(_: AdminReadDep, session: SessionDep) -> AdminOverview:
    profile = await session.scalar(select(Profile).where(Profile.id == 1))
    projects_total = int(await session.scalar(select(func.count()).select_from(Project)) or 0)
    projects_published = int(
        await session.scalar(
            select(func.count()).select_from(Project).where(Project.status == "published")
        )
        or 0
    )
    experiences_total = int(await session.scalar(select(func.count()).select_from(Experience)) or 0)
    education_total = int(
        await session.scalar(select(func.count()).select_from(EducationRecord)) or 0
    )
    skills_total = int(await session.scalar(select(func.count()).select_from(Skill)) or 0)
    return AdminOverview(
        profile_status=profile.status if profile else None,
        projects_total=projects_total,
        projects_published=projects_published,
        experiences_total=experiences_total,
        education_total=education_total,
        skills_total=skills_total,
    )


@router.get("/profile", response_model=AdminProfileRead)
async def get_profile(_: AdminReadDep, session: SessionDep) -> AdminProfileRead:
    row = await session.scalar(select(Profile).where(Profile.id == 1))
    return _profile_out(row)


@router.put("/profile", response_model=AdminProfileRead)
async def put_profile(
    payload: ProfilePut, _: AdminWriteDep, session: SessionDep
) -> AdminProfileRead:
    values = payload.model_dump(exclude={"expected_updated_at"})
    if "portrait_asset_id" not in payload.model_fields_set:
        values.pop("portrait_asset_id", None)
    _validate_urls(values)
    if payload.portrait_asset_id is not None:
        asset = await session.get(MediaAsset, payload.portrait_asset_id)
        if asset is None or asset.state != "ready":
            raise HTTPException(status_code=422, detail="Foto profil belum siap digunakan.")
    row = await session.scalar(select(Profile).where(Profile.id == 1).with_for_update())
    if row is None:
        if payload.expected_updated_at is not None:
            raise _conflict("Profil belum tersimpan. Muat ulang sebelum menyimpan.")
        row = Profile(id=1, status="draft")
        session.add(row)
    else:
        if payload.expected_updated_at is None:
            raise HTTPException(status_code=428, detail="Versi profil wajib dikirim.")
        _check_version(row.updated_at, payload.expected_updated_at)
    for key, value in values.items():
        setattr(row, key, str(value) if key == "public_email" and value is not None else value)
    await _commit(session)
    await session.refresh(row)
    return _profile_out(row)


async def _set_publication(
    session: AsyncSession,
    row: Any,
    *,
    expected_updated_at: datetime,
    publish: bool,
) -> None:
    _check_version(row.updated_at, expected_updated_at)
    if publish:
        if isinstance(row, Profile):
            if not row.name or not row.role or not row.headline or not row.intro:
                raise HTTPException(
                    status_code=422,
                    detail={
                        "code": "validation_error",
                        "message": (
                            "Lengkapi nama, peran, headline, dan pengantar "
                            "sebelum menerbitkan profil."
                        ),
                    },
                )
            if row.portrait_asset_id is not None:
                portrait = await session.get(MediaAsset, row.portrait_asset_id)
                if portrait is None or portrait.state != "ready" or not portrait.alt_text.strip():
                    raise HTTPException(
                        status_code=422,
                        detail="Tambahkan teks alternatif pada foto profil sebelum menerbitkan.",
                    )
        elif isinstance(row, Project):
            if not row.title or not row.slug or not row.summary or not row.role:
                raise HTTPException(
                    status_code=422, detail="Lengkapi konten wajib sebelum menerbitkan proyek."
                )
            images = list(
                (
                    await session.scalars(
                        select(ProjectImage)
                        .where(ProjectImage.project_id == row.id)
                        .options(selectinload(ProjectImage.media_asset))
                    )
                ).all()
            )
            if any(
                link.media_asset.state != "ready" or not link.media_asset.alt_text.strip()
                for link in images
            ):
                raise HTTPException(
                    status_code=422,
                    detail="Lengkapi teks alternatif semua gambar sebelum menerbitkan proyek.",
                )
        elif isinstance(row, Experience):
            if not row.role or not row.organization or not row.summary:
                raise HTTPException(
                    status_code=422, detail="Lengkapi konten wajib sebelum menerbitkan pengalaman."
                )
        elif isinstance(row, EducationRecord):
            if not row.title or not row.institution:
                raise HTTPException(
                    status_code=422, detail="Lengkapi konten wajib sebelum menerbitkan pendidikan."
                )
        elif isinstance(row, Skill):
            if not row.name:
                raise HTTPException(
                    status_code=422, detail="Lengkapi nama tech stack sebelum menerbitkan."
                )
        if isinstance(row, Project) and row.featured:
            await session.execute(
                update(Project)
                .where(
                    Project.status == "published", Project.featured.is_(True), Project.id != row.id
                )
                .values(featured=False, updated_at=_now())
            )
    row.status = "published" if publish else "draft"
    if hasattr(row, "published_at"):
        row.published_at = _now() if publish else None
    if hasattr(row, "updated_at"):
        row.updated_at = _now()
    await _commit(session)


async def _transition(
    session: AsyncSession,
    model: Any,
    key: UUID | int,
    expected: datetime,
    publish: bool,
    label: str,
    *,
    options: tuple[Any, ...] = (),
) -> Any:
    row = await session.scalar(
        select(model).where(model.id == key).options(*options).with_for_update()
    )
    if row is None:
        raise _not_found(label)
    target_status = "published" if publish else "draft"
    if row.status == target_status:
        return row
    await _set_publication(
        session,
        row,
        expected_updated_at=expected,
        publish=publish,
    )
    return row


class PublicationChange(VersionedInput):
    pass


@router.post("/profile/publish", response_model=AdminProfileRead)
async def publish_profile(
    payload: PublicationChange, _: AdminWriteDep, session: SessionDep
) -> AdminProfileRead:
    row = await session.scalar(select(Profile).where(Profile.id == 1).with_for_update())
    if row is None:
        raise _not_found("Profil")
    _check_version(row.updated_at, payload.expected_updated_at)
    if not row.name or not row.role or not row.headline or not row.intro:
        raise HTTPException(
            status_code=422,
            detail="Lengkapi nama, peran, headline, dan pengantar sebelum menerbitkan profil.",
        )
    row.status = "published"
    row.published_at = _now()
    row.updated_at = _now()
    await _commit(session)
    await session.refresh(row)
    return _profile_out(row)


@router.post("/profile/unpublish", response_model=AdminProfileRead)
async def unpublish_profile(
    payload: PublicationChange, _: AdminWriteDep, session: SessionDep
) -> AdminProfileRead:
    row = await session.scalar(select(Profile).where(Profile.id == 1).with_for_update())
    if row is None:
        raise _not_found("Profil")
    _check_version(row.updated_at, payload.expected_updated_at)
    row.status = "draft"
    row.published_at = None
    row.updated_at = _now()
    await _commit(session)
    await session.refresh(row)
    return _profile_out(row)


@router.get("/projects", response_model=AdminPage[AdminProjectRead])
async def list_projects(
    _: AdminReadDep,
    session: SessionDep,
    state: Annotated[PublicStatus | None, Query(alias="status")] = None,
    search: Annotated[str | None, Query(max_length=100)] = None,
    limit: Annotated[int, Query(ge=1, le=LIST_LIMIT)] = LIST_LIMIT,
    offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
) -> AdminPage[AdminProjectRead]:
    rows, total = await _page(
        session,
        Project,
        limit=limit,
        offset=offset,
        state=state,
        search=search,
        search_columns=(Project.title, Project.slug, Project.summary),
        options=(selectinload(Project.stack_links), selectinload(Project.experiences)),
    )
    return AdminPage(
        data=[_project_out(row) for row in rows],
        meta=AdminPageMeta(total=total, limit=limit, offset=offset),
    )


@router.get("/projects/{item_id}", response_model=AdminProjectRead)
async def get_project(item_id: UUID, _: AdminReadDep, session: SessionDep) -> AdminProjectRead:
    row = await session.scalar(
        select(Project)
        .where(Project.id == item_id)
        .options(selectinload(Project.stack_links), selectinload(Project.experiences))
    )
    if row is None:
        raise _not_found("Proyek")
    return _project_out(row)


async def _set_project_skills(session: AsyncSession, row: Project, skill_ids: list[UUID]) -> None:
    unique_ids = list(dict.fromkeys(skill_ids))
    if unique_ids:
        skills = list((await session.scalars(select(Skill).where(Skill.id.in_(unique_ids)))).all())
        if len(skills) != len(unique_ids):
            raise HTTPException(status_code=422, detail="Ada tech stack yang tidak ditemukan.")
        skill_map = {skill.id: skill for skill in skills}
    else:
        skill_map = {}
    row.stack_links.clear()
    await session.flush()
    for index, skill_id in enumerate(unique_ids):
        skill = skill_map[skill_id]
        row.stack_links.append(
            ProjectSkill(
                project_id=row.id,
                skill_id=skill.id,
                sort_order=index,
                name_snapshot=skill.name,
            )
        )


async def _set_project_experiences(
    session: AsyncSession, row: Project, experience_ids: list[UUID]
) -> None:
    unique_ids = list(dict.fromkeys(experience_ids))
    experiences = (
        list((await session.scalars(select(Experience).where(Experience.id.in_(unique_ids)))).all())
        if unique_ids
        else []
    )
    if len(experiences) != len(unique_ids):
        raise HTTPException(status_code=422, detail="Ada pengalaman/karier yang tidak ditemukan.")
    row.experiences = experiences


async def _set_experience_skills(
    session: AsyncSession, row: Experience, skill_ids: list[UUID]
) -> None:
    unique_ids = list(dict.fromkeys(skill_ids))
    skills = (
        list((await session.scalars(select(Skill).where(Skill.id.in_(unique_ids)))).all())
        if unique_ids
        else []
    )
    if len(skills) != len(unique_ids):
        raise HTTPException(status_code=422, detail="Ada tech stack yang tidak ditemukan.")
    skill_map = {skill.id: skill for skill in skills}
    row.stack_links.clear()
    await session.flush()
    row.stack_links.extend(
        ExperienceSkill(
            experience_id=row.id,
            skill_id=skill_id,
            sort_order=index,
            name_snapshot=skill_map[skill_id].name,
        )
        for index, skill_id in enumerate(unique_ids)
    )


@router.post("/projects", response_model=AdminProjectRead, status_code=201)
async def create_project(
    payload: ProjectCreate, _: AdminWriteDep, session: SessionDep
) -> AdminProjectRead:
    values = payload.model_dump(exclude={"skill_ids", "experience_ids"})
    _validate_urls(values)
    row = Project(id=uuid4(), status="draft", **values)
    row.stack_links = []
    session.add(row)
    await _flush(session)
    await _set_project_skills(session, row, payload.skill_ids)
    await _set_project_experiences(session, row, payload.experience_ids)
    await _commit(session)
    result = await session.scalar(
        select(Project)
        .where(Project.id == row.id)
        .options(selectinload(Project.stack_links), selectinload(Project.experiences))
    )
    return _project_out(result or row)


@router.put("/projects/{item_id}", response_model=AdminProjectRead)
async def update_project(
    item_id: UUID, payload: ProjectUpdate, _: AdminWriteDep, session: SessionDep
) -> AdminProjectRead:
    row = await session.scalar(
        select(Project)
        .where(Project.id == item_id)
        .with_for_update()
        .options(selectinload(Project.stack_links), selectinload(Project.experiences))
    )
    if row is None:
        raise _not_found("Proyek")
    _check_version(row.updated_at, payload.expected_updated_at)
    values = payload.model_dump(exclude={"expected_updated_at", "skill_ids", "experience_ids"})
    _validate_urls(values)
    if row.status == "published" and values["featured"]:
        await session.execute(
            update(Project)
            .where(
                Project.status == "published",
                Project.featured.is_(True),
                Project.id != row.id,
            )
            .values(featured=False, updated_at=_now())
        )
    for key, value in values.items():
        setattr(row, key, value)
    row.updated_at = _now()
    await _set_project_skills(session, row, payload.skill_ids)
    await _set_project_experiences(session, row, payload.experience_ids)
    await _commit(session)
    result = await session.scalar(
        select(Project)
        .where(Project.id == item_id)
        .options(selectinload(Project.stack_links), selectinload(Project.experiences))
    )
    return _project_out(result or row)


@router.delete("/projects/{item_id}", status_code=204)
async def delete_project(
    item_id: UUID, payload: VersionedInput, _: AdminWriteDep, session: SessionDep
) -> Response:
    row = await session.scalar(select(Project).where(Project.id == item_id).with_for_update())
    if row is None:
        raise _not_found("Proyek")
    _check_version(row.updated_at, payload.expected_updated_at)
    await session.delete(row)
    await _commit(session)
    return Response(status_code=204)


@router.post("/projects/{item_id}/publish", response_model=AdminProjectRead)
async def publish_project(
    item_id: UUID, payload: PublicationChange, _: AdminWriteDep, session: SessionDep
) -> AdminProjectRead:
    row = await _transition(
        session,
        Project,
        item_id,
        payload.expected_updated_at,
        True,
        "Proyek",
        options=(selectinload(Project.stack_links), selectinload(Project.experiences)),
    )
    return _project_out(row)


@router.post("/projects/{item_id}/unpublish", response_model=AdminProjectRead)
async def unpublish_project(
    item_id: UUID, payload: PublicationChange, _: AdminWriteDep, session: SessionDep
) -> AdminProjectRead:
    row = await _transition(
        session,
        Project,
        item_id,
        payload.expected_updated_at,
        False,
        "Proyek",
        options=(selectinload(Project.stack_links), selectinload(Project.experiences)),
    )
    return _project_out(row)


@router.get("/experiences", response_model=AdminPage[AdminExperienceRead])
async def list_experiences(
    _: AdminReadDep,
    session: SessionDep,
    state: Annotated[PublicStatus | None, Query(alias="status")] = None,
    search: Annotated[str | None, Query(max_length=100)] = None,
    limit: Annotated[int, Query(ge=1, le=LIST_LIMIT)] = LIST_LIMIT,
    offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
) -> AdminPage[AdminExperienceRead]:
    rows, total = await _page(
        session,
        Experience,
        limit=limit,
        offset=offset,
        state=state,
        search=search,
        search_columns=(Experience.role, Experience.organization, Experience.summary),
        options=(selectinload(Experience.stack_links),),
    )
    return AdminPage(
        data=[_experience_out(row) for row in rows],
        meta=AdminPageMeta(total=total, limit=limit, offset=offset),
    )


@router.get("/experiences/{item_id}", response_model=AdminExperienceRead)
async def get_experience(
    item_id: UUID, _: AdminReadDep, session: SessionDep
) -> AdminExperienceRead:
    row = await session.scalar(
        select(Experience)
        .where(Experience.id == item_id)
        .options(selectinload(Experience.stack_links))
    )
    if row is None:
        raise _not_found("Pengalaman")
    return _experience_out(row)


@router.post("/experiences", response_model=AdminExperienceRead, status_code=201)
async def create_experience(
    payload: ExperienceCreate, _: AdminWriteDep, session: SessionDep
) -> AdminExperienceRead:
    values = payload.model_dump(exclude={"skill_ids", "is_current"})
    row = Experience(id=uuid4(), status="draft", **values)
    row.stack_links = []
    session.add(row)
    await _flush(session)
    await _set_experience_skills(session, row, payload.skill_ids)
    await _commit(session)
    result = await session.scalar(
        select(Experience)
        .where(Experience.id == row.id)
        .options(selectinload(Experience.stack_links))
    )
    return _experience_out(result or row)


@router.put("/experiences/{item_id}", response_model=AdminExperienceRead)
async def update_experience(
    item_id: UUID, payload: ExperienceUpdate, _: AdminWriteDep, session: SessionDep
) -> AdminExperienceRead:
    row = await session.scalar(
        select(Experience)
        .where(Experience.id == item_id)
        .with_for_update()
        .options(selectinload(Experience.stack_links))
    )
    if row is None:
        raise _not_found("Pengalaman")
    _check_version(row.updated_at, payload.expected_updated_at)
    values = payload.model_dump(exclude={"expected_updated_at", "skill_ids", "is_current"})
    for key, value in values.items():
        setattr(row, key, value)
    row.updated_at = _now()
    await _set_experience_skills(session, row, payload.skill_ids)
    await _commit(session)
    result = await session.scalar(
        select(Experience)
        .where(Experience.id == item_id)
        .options(selectinload(Experience.stack_links))
    )
    return _experience_out(result or row)


@router.delete("/experiences/{item_id}", status_code=204)
async def delete_experience(
    item_id: UUID, payload: VersionedInput, _: AdminWriteDep, session: SessionDep
) -> Response:
    row = await session.scalar(select(Experience).where(Experience.id == item_id).with_for_update())
    if row is None:
        raise _not_found("Pengalaman")
    _check_version(row.updated_at, payload.expected_updated_at)
    await session.delete(row)
    await _commit(session)
    return Response(status_code=204)


@router.post("/experiences/{item_id}/publish", response_model=AdminExperienceRead)
async def publish_experience(
    item_id: UUID, payload: PublicationChange, _: AdminWriteDep, session: SessionDep
) -> AdminExperienceRead:
    row = await _transition(
        session,
        Experience,
        item_id,
        payload.expected_updated_at,
        True,
        "Pengalaman",
        options=(selectinload(Experience.stack_links),),
    )
    return _experience_out(row)


@router.post("/experiences/{item_id}/unpublish", response_model=AdminExperienceRead)
async def unpublish_experience(
    item_id: UUID, payload: PublicationChange, _: AdminWriteDep, session: SessionDep
) -> AdminExperienceRead:
    row = await _transition(
        session,
        Experience,
        item_id,
        payload.expected_updated_at,
        False,
        "Pengalaman",
        options=(selectinload(Experience.stack_links),),
    )
    return _experience_out(row)


@router.get("/education", response_model=AdminPage[AdminEducationRead])
async def list_education(
    _: AdminReadDep,
    session: SessionDep,
    state: Annotated[PublicStatus | None, Query(alias="status")] = None,
    kind: Literal["formal", "course", "certification"] | None = None,
    search: Annotated[str | None, Query(max_length=100)] = None,
    limit: Annotated[int, Query(ge=1, le=LIST_LIMIT)] = LIST_LIMIT,
    offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
) -> AdminPage[AdminEducationRead]:
    filters = []
    if state:
        filters.append(EducationRecord.status == state)
    if kind:
        filters.append(EducationRecord.kind == kind)
    if search:
        filters.append(
            or_(
                EducationRecord.title.ilike(f"%{search}%"),
                EducationRecord.institution.ilike(f"%{search}%"),
            )
        )
    total = int(
        await session.scalar(select(func.count()).select_from(EducationRecord).where(*filters)) or 0
    )
    rows = list(
        (
            await session.scalars(
                select(EducationRecord)
                .where(*filters)
                .order_by(EducationRecord.sort_order, EducationRecord.start_year.desc().nullslast())
                .offset(offset)
                .limit(limit)
            )
        ).all()
    )
    return AdminPage(
        data=[_education_out(row) for row in rows],
        meta=AdminPageMeta(total=total, limit=limit, offset=offset),
    )


@router.get("/education/{item_id}", response_model=AdminEducationRead)
async def get_education(item_id: UUID, _: AdminReadDep, session: SessionDep) -> AdminEducationRead:
    row = await session.get(EducationRecord, item_id)
    if row is None:
        raise _not_found("Pendidikan")
    return _education_out(row)


@router.post("/education", response_model=AdminEducationRead, status_code=201)
async def create_education(
    payload: EducationCreate, _: AdminWriteDep, session: SessionDep
) -> AdminEducationRead:
    values = payload.model_dump()
    _validate_urls(values)
    row = EducationRecord(id=uuid4(), status="draft", **values)
    session.add(row)
    await _commit(session)
    await session.refresh(row)
    return _education_out(row)


@router.put("/education/{item_id}", response_model=AdminEducationRead)
async def update_education(
    item_id: UUID, payload: EducationUpdate, _: AdminWriteDep, session: SessionDep
) -> AdminEducationRead:
    row = await session.scalar(
        select(EducationRecord).where(EducationRecord.id == item_id).with_for_update()
    )
    if row is None:
        raise _not_found("Pendidikan")
    _check_version(row.updated_at, payload.expected_updated_at)
    values = payload.model_dump(exclude={"expected_updated_at"})
    _validate_urls(values)
    for key, value in values.items():
        setattr(row, key, value)
    row.updated_at = _now()
    await _commit(session)
    await session.refresh(row)
    return _education_out(row)


@router.delete("/education/{item_id}", status_code=204)
async def delete_education(
    item_id: UUID, payload: VersionedInput, _: AdminWriteDep, session: SessionDep
) -> Response:
    row = await session.scalar(
        select(EducationRecord).where(EducationRecord.id == item_id).with_for_update()
    )
    if row is None:
        raise _not_found("Pendidikan")
    _check_version(row.updated_at, payload.expected_updated_at)
    await session.delete(row)
    await _commit(session)
    return Response(status_code=204)


@router.post("/education/{item_id}/publish", response_model=AdminEducationRead)
async def publish_education(
    item_id: UUID, payload: PublicationChange, _: AdminWriteDep, session: SessionDep
) -> AdminEducationRead:
    row = await _transition(
        session, EducationRecord, item_id, payload.expected_updated_at, True, "Pendidikan"
    )
    return _education_out(row)


@router.post("/education/{item_id}/unpublish", response_model=AdminEducationRead)
async def unpublish_education(
    item_id: UUID, payload: PublicationChange, _: AdminWriteDep, session: SessionDep
) -> AdminEducationRead:
    row = await _transition(
        session, EducationRecord, item_id, payload.expected_updated_at, False, "Pendidikan"
    )
    return _education_out(row)


@router.get("/skill-categories", response_model=list[AdminSkillCategoryRead])
async def list_skill_categories(
    _: AdminReadDep, session: SessionDep
) -> list[AdminSkillCategoryRead]:
    rows = list(
        (
            await session.scalars(
                select(SkillCategory)
                .options(selectinload(SkillCategory.skills))
                .order_by(SkillCategory.sort_order, SkillCategory.label)
            )
        ).all()
    )
    return [
        AdminSkillCategoryRead(
            id=row.id,
            slug=row.slug,
            label=row.label,
            sort_order=row.sort_order,
            is_published=row.is_published,
            updated_at=row.updated_at,
            skill_count=len(row.skills),
        )
        for row in rows
    ]


@router.post("/skill-categories", response_model=AdminSkillCategoryRead, status_code=201)
async def create_skill_category(
    payload: SkillCategoryCreate, _: AdminWriteDep, session: SessionDep
) -> AdminSkillCategoryRead:
    row = SkillCategory(id=uuid4(), **payload.model_dump())
    session.add(row)
    await _commit(session)
    await session.refresh(row)
    return AdminSkillCategoryRead(
        id=row.id,
        slug=row.slug,
        label=row.label,
        sort_order=row.sort_order,
        is_published=row.is_published,
        updated_at=row.updated_at,
        skill_count=0,
    )


@router.put("/skill-categories/{item_id}", response_model=AdminSkillCategoryRead)
async def update_skill_category(
    item_id: UUID, payload: SkillCategoryUpdate, _: AdminWriteDep, session: SessionDep
) -> AdminSkillCategoryRead:
    row = await session.scalar(
        select(SkillCategory).where(SkillCategory.id == item_id).with_for_update()
    )
    if row is None:
        raise _not_found("Kategori")
    _check_version(row.updated_at, payload.expected_updated_at)
    for key, value in payload.model_dump(exclude={"expected_updated_at"}).items():
        setattr(row, key, value)
    row.updated_at = _now()
    await _commit(session)
    await session.refresh(row)
    count = int(
        await session.scalar(
            select(func.count()).select_from(Skill).where(Skill.category_id == row.id)
        )
        or 0
    )
    return AdminSkillCategoryRead(
        id=row.id,
        slug=row.slug,
        label=row.label,
        sort_order=row.sort_order,
        is_published=row.is_published,
        updated_at=row.updated_at,
        skill_count=count,
    )


@router.delete("/skill-categories/{item_id}", status_code=204)
async def delete_skill_category(
    item_id: UUID, payload: VersionedInput, _: AdminWriteDep, session: SessionDep
) -> Response:
    row = await session.scalar(
        select(SkillCategory).where(SkillCategory.id == item_id).with_for_update()
    )
    if row is None:
        raise _not_found("Kategori")
    _check_version(row.updated_at, payload.expected_updated_at)
    if await session.scalar(select(Skill.id).where(Skill.category_id == item_id).limit(1)):
        raise _conflict(
            "Kategori masih memiliki tech stack. Hapus atau pindahkan isinya terlebih dahulu."
        )
    await session.delete(row)
    await _commit(session)
    return Response(status_code=204)


class CategoryPublication(PublicationChange):
    pass


@router.post("/skill-categories/{item_id}/publish", response_model=AdminSkillCategoryRead)
async def publish_skill_category(
    item_id: UUID, payload: CategoryPublication, _: AdminWriteDep, session: SessionDep
) -> AdminSkillCategoryRead:
    row = await session.scalar(
        select(SkillCategory).where(SkillCategory.id == item_id).with_for_update()
    )
    if row is None:
        raise _not_found("Kategori")
    _check_version(row.updated_at, payload.expected_updated_at)
    row.is_published = True
    row.updated_at = _now()
    await _commit(session)
    await session.refresh(row)
    count = int(
        await session.scalar(
            select(func.count()).select_from(Skill).where(Skill.category_id == row.id)
        )
        or 0
    )
    return AdminSkillCategoryRead(
        id=row.id,
        slug=row.slug,
        label=row.label,
        sort_order=row.sort_order,
        is_published=True,
        updated_at=row.updated_at,
        skill_count=count,
    )


@router.post("/skill-categories/{item_id}/unpublish", response_model=AdminSkillCategoryRead)
async def unpublish_skill_category(
    item_id: UUID, payload: CategoryPublication, _: AdminWriteDep, session: SessionDep
) -> AdminSkillCategoryRead:
    row = await session.scalar(
        select(SkillCategory).where(SkillCategory.id == item_id).with_for_update()
    )
    if row is None:
        raise _not_found("Kategori")
    _check_version(row.updated_at, payload.expected_updated_at)
    row.is_published = False
    row.updated_at = _now()
    await _commit(session)
    await session.refresh(row)
    count = int(
        await session.scalar(
            select(func.count()).select_from(Skill).where(Skill.category_id == row.id)
        )
        or 0
    )
    return AdminSkillCategoryRead(
        id=row.id,
        slug=row.slug,
        label=row.label,
        sort_order=row.sort_order,
        is_published=False,
        updated_at=row.updated_at,
        skill_count=count,
    )


@router.get("/skills", response_model=AdminPage[AdminSkillRead])
async def list_skills(
    _: AdminReadDep,
    session: SessionDep,
    state: Annotated[PublicStatus | None, Query(alias="status")] = None,
    category_id: UUID | None = None,
    search: Annotated[str | None, Query(max_length=100)] = None,
    limit: Annotated[int, Query(ge=1, le=LIST_LIMIT)] = LIST_LIMIT,
    offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
) -> AdminPage[AdminSkillRead]:
    filters = []
    if state:
        filters.append(Skill.status == state)
    if category_id:
        filters.append(Skill.category_id == category_id)
    if search:
        filters.append(Skill.name.ilike(f"%{search}%"))
    total = int(await session.scalar(select(func.count()).select_from(Skill).where(*filters)) or 0)
    rows = list(
        (
            await session.scalars(
                select(Skill)
                .where(*filters)
                .options(selectinload(Skill.category))
                .order_by(Skill.sort_order, Skill.name)
                .offset(offset)
                .limit(limit)
            )
        ).all()
    )
    return AdminPage(
        data=[_skill_out(row) for row in rows],
        meta=AdminPageMeta(total=total, limit=limit, offset=offset),
    )


@router.get("/skills/{item_id}", response_model=AdminSkillRead)
async def get_skill(item_id: UUID, _: AdminReadDep, session: SessionDep) -> AdminSkillRead:
    row = await session.scalar(
        select(Skill).where(Skill.id == item_id).options(selectinload(Skill.category))
    )
    if row is None:
        raise _not_found("Tech stack")
    return _skill_out(row)


@router.post("/skills", response_model=AdminSkillRead, status_code=201)
async def create_skill(
    payload: SkillCreate, _: AdminWriteDep, session: SessionDep
) -> AdminSkillRead:
    if await session.get(SkillCategory, payload.category_id) is None:
        raise HTTPException(status_code=422, detail="Kategori tidak ditemukan.")
    if payload.icon_asset_id is not None:
        asset = await session.get(MediaAsset, payload.icon_asset_id)
        if asset is None or asset.state != "ready":
            raise HTTPException(status_code=422, detail="Gambar tech stack belum siap digunakan.")
    row = Skill(id=uuid4(), status="draft", **payload.model_dump())
    session.add(row)
    await _commit(session)
    result = await session.scalar(
        select(Skill).where(Skill.id == row.id).options(selectinload(Skill.category))
    )
    return _skill_out(result or row)


@router.put("/skills/{item_id}", response_model=AdminSkillRead)
async def update_skill(
    item_id: UUID, payload: SkillUpdate, _: AdminWriteDep, session: SessionDep
) -> AdminSkillRead:
    row = await session.scalar(
        select(Skill)
        .where(Skill.id == item_id)
        .with_for_update()
        .options(selectinload(Skill.category))
    )
    if row is None:
        raise _not_found("Tech stack")
    _check_version(row.updated_at, payload.expected_updated_at)
    if await session.get(SkillCategory, payload.category_id) is None:
        raise HTTPException(status_code=422, detail="Kategori tidak ditemukan.")
    if payload.icon_asset_id is not None:
        asset = await session.get(MediaAsset, payload.icon_asset_id)
        if asset is None or asset.state != "ready":
            raise HTTPException(status_code=422, detail="Gambar tech stack belum siap digunakan.")
    for key, value in payload.model_dump(exclude={"expected_updated_at"}).items():
        setattr(row, key, value)
    row.updated_at = _now()
    await _commit(session)
    result = await session.scalar(
        select(Skill).where(Skill.id == item_id).options(selectinload(Skill.category))
    )
    return _skill_out(result or row)


@router.delete("/skills/{item_id}", status_code=204)
async def delete_skill(
    item_id: UUID, payload: VersionedInput, _: AdminWriteDep, session: SessionDep
) -> Response:
    row = await session.scalar(select(Skill).where(Skill.id == item_id).with_for_update())
    if row is None:
        raise _not_found("Tech stack")
    _check_version(row.updated_at, payload.expected_updated_at)
    references = await session.scalar(
        select(ProjectSkill.project_id).where(ProjectSkill.skill_id == item_id).limit(1)
    )
    if references:
        raise _conflict("Tech stack masih dipakai proyek. Lepaskan dari proyek terlebih dahulu.")
    if await session.scalar(
        select(ExperienceSkill.experience_id).where(ExperienceSkill.skill_id == item_id).limit(1)
    ):
        raise _conflict(
            "Tech stack masih dipakai pengalaman kerja. Lepaskan dari pengalaman terlebih dahulu."
        )
    await session.delete(row)
    await _commit(session)
    return Response(status_code=204)


@router.post("/skills/{item_id}/publish", response_model=AdminSkillRead)
async def publish_skill(
    item_id: UUID, payload: PublicationChange, _: AdminWriteDep, session: SessionDep
) -> AdminSkillRead:
    row = await _transition(
        session,
        Skill,
        item_id,
        payload.expected_updated_at,
        True,
        "Tech stack",
        options=(selectinload(Skill.category),),
    )
    return _skill_out(row)


@router.post("/skills/{item_id}/unpublish", response_model=AdminSkillRead)
async def unpublish_skill(
    item_id: UUID, payload: PublicationChange, _: AdminWriteDep, session: SessionDep
) -> AdminSkillRead:
    row = await _transition(
        session,
        Skill,
        item_id,
        payload.expected_updated_at,
        False,
        "Tech stack",
        options=(selectinload(Skill.category),),
    )
    return _skill_out(row)


def _validate_urls(values: dict[str, Any]) -> None:
    from urllib.parse import urlsplit

    from pydantic import HttpUrl, TypeAdapter

    from app.core.config import get_settings

    url_adapter = TypeAdapter(HttpUrl)

    for key in ("github_url", "linkedin_url", "cv_url", "demo_url", "repository_url", "proof_url"):
        if key not in values:
            continue
        value = values.get(key)
        if value is None or value == "":
            values[key] = None
            continue
        try:
            parsed = urlsplit(value)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"URL {key} tidak valid.") from exc
        local_http = (
            get_settings().app_env.lower() in {"development", "test"}
            and parsed.scheme == "http"
            and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        )
        if (parsed.scheme != "https" and not local_http) or not parsed.hostname:
            raise HTTPException(status_code=422, detail=f"URL {key} harus menggunakan HTTPS.")
        if parsed.username is not None or parsed.password is not None:
            raise HTTPException(status_code=422, detail=f"URL {key} tidak boleh berisi kredensial.")
        try:
            values[key] = str(url_adapter.validate_python(value))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"URL {key} tidak valid.") from exc
