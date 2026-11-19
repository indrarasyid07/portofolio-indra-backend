"""Database constraint, transaction, and relationship loading checks."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from app.db.base import Base
from app.models import (
    AdminSession,
    AdminUser,
    EducationRecord,
    Experience,
    LoginAttempt,
    MediaAsset,
    Profile,
    Project,
    ProjectImage,
    ProjectSkill,
    Skill,
    SkillCategory,
)


def project(slug: str, **kwargs: object) -> Project:
    return Project(
        slug=slug,
        title=f"Project {slug}",
        project_type="web",
        year=2026,
        role="Developer",
        summary="A test project",
        **kwargs,
    )


@pytest.mark.asyncio
async def test_schema_has_expected_entities_and_no_seed_data(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    expected = {
        "profiles",
        "projects",
        "project_images",
        "experiences",
        "education_records",
        "skill_categories",
        "skills",
        "project_skills",
        "media_assets",
        "admin_users",
        "admin_sessions",
        "login_attempts",
    }
    assert expected <= set(Base.metadata.tables)
    async with session_factory() as session:
        for model in (
            Profile,
            Project,
            Experience,
            EducationRecord,
            SkillCategory,
            Skill,
            AdminUser,
        ):
            assert await session.scalar(select(func.count()).select_from(model)) == 0


@pytest.mark.asyncio
async def test_content_create_update_delete_and_constraints(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        row = project("contoh-aplikasi")
        session.add(row)
        await session.commit()
        row.title = "Judul diperbarui"
        await session.commit()
        assert (
            await session.scalar(select(Project.title).where(Project.id == row.id))
            == "Judul diperbarui"
        )

        session.add(project("contoh-aplikasi"))
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()

        invalid = project("-tidak-valid-")
        session.add(invalid)
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()

        await session.delete(row)
        await session.commit()
        assert await session.scalar(select(func.count()).select_from(Project)) == 0


@pytest.mark.asyncio
async def test_experience_education_and_skill_crud_with_partial_dates(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        experience = Experience(
            role="Backend Developer",
            organization="Organisasi",
            employment_type="contract",
            start_year=2021,
            start_month=6,
            end_year=2023,
            end_month=2,
            summary="Membangun API",
            highlights=["Menyusun endpoint"],
            status="draft",
            sort_order=0,
        )
        course = EducationRecord(
            kind="course",
            title="Kursus basis data",
            institution="Penyelenggara",
            start_year=2024,
            start_month=None,
            end_year=None,
            end_month=None,
            status="draft",
            sort_order=0,
        )
        category = SkillCategory(slug="bahasa", label="Bahasa", is_published=False)
        skill = Skill(category=category, name="Python", status="draft", sort_order=0)
        session.add_all([experience, course, category, skill])
        await session.commit()

        experience.summary = "Membangun layanan API"
        course.credential_id = "CERT-001"
        skill.note = "Pemakaian umum"
        await session.commit()
        assert experience.start_month == 6
        assert course.end_year is None
        assert skill.note == "Pemakaian umum"

        await session.delete(experience)
        await session.delete(course)
        await session.delete(skill)
        await session.commit()
        await session.delete(category)
        await session.commit()
        assert await session.scalar(select(func.count()).select_from(Experience)) == 0
        assert await session.scalar(select(func.count()).select_from(EducationRecord)) == 0
        assert await session.scalar(select(func.count()).select_from(Skill)) == 0


@pytest.mark.asyncio
async def test_singleton_profile_and_publication_constraints(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        session.add(
            Profile(
                id=1,
                name="Nama",
                role="Developer",
                headline="Headline",
                intro="Intro",
                about="Tentang",
            )
        )
        await session.commit()
        session.add(Profile(id=2, name="Kedua", role="", headline="", intro="", about=""))
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()

        profile_row = await session.get(Profile, 1)
        assert profile_row is not None
        profile_row.headline = "Headline diperbarui"
        await session.commit()
        await session.delete(profile_row)
        await session.commit()

        first = project("unggulan-satu", status="published", featured=True)
        second = project("unggulan-dua", status="published", featured=True)
        session.add_all([first, second])
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()


@pytest.mark.asyncio
async def test_fk_sort_cover_and_delete_policies(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        image = MediaAsset(
            provider="local",
            bucket="test",
            object_key="test/image.webp",
            content_type="image/webp",
            byte_size=25,
            sha256="a" * 64,
            state="ready",
            original_filename="image.webp",
        )
        first = project("galeri-satu")
        second = project("galeri-dua")
        session.add_all([image, first, second])
        await session.flush()
        session.add_all(
            [
                ProjectImage(project=first, media_asset=image, sort_order=0, is_cover=True),
                ProjectImage(project=second, media_asset=image, sort_order=0, is_cover=True),
            ]
        )
        await session.commit()

        image.alt_text = "Tangkapan layar proyek"
        await session.commit()

        with pytest.raises(IntegrityError):
            session.add(ProjectImage(project=first, media_asset=image, sort_order=1, is_cover=True))
            await session.commit()
        await session.rollback()

        with pytest.raises(IntegrityError):
            session.add(
                ProjectImage(project=first, media_asset=image, sort_order=0, is_cover=False)
            )
            await session.commit()
        await session.rollback()

        with pytest.raises(IntegrityError):
            await session.delete(image)
            await session.commit()
        await session.rollback()

        await session.delete(first)
        await session.commit()
        remaining = await session.scalar(select(func.count()).select_from(ProjectImage))
        assert remaining == 1


@pytest.mark.asyncio
async def test_failed_multi_table_write_rolls_back(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        session.add(project("transaksi-gagal"))
        session.add(
            MediaAsset(
                provider="local",
                bucket="test",
                object_key="bad/image.webp",
                content_type="image/webp",
                byte_size=0,
                sha256="b" * 64,
                state="pending",
                original_filename="image.webp",
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()
        assert await session.scalar(select(func.count()).select_from(Project)) == 0
        assert await session.scalar(select(func.count()).select_from(MediaAsset)) == 0


@pytest.mark.asyncio
async def test_admin_models_persist_only_hashes_and_utc_datetimes(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime.now(UTC)
    async with session_factory() as session:
        user = AdminUser(
            username="admin",
            normalized_username="admin",
            password_hash="$argon2id$placeholder-hash-only",
        )
        session.add(user)
        await session.flush()
        session.add(
            AdminSession(
                admin_user=user,
                token_hash="c" * 64,
                csrf_hash="d" * 64,
                key_version=1,
                created_at=now,
                last_seen_at=now,
                expires_at=now.replace(year=now.year + 1),
            )
        )
        session.add(
            LoginAttempt(
                attempt_key_hash="e" * 64,
                source_key_hash="f" * 64,
                attempted_at=now,
                outcome="failure",
            )
        )
        await session.commit()
        assert await session.scalar(select(func.count()).select_from(AdminSession)) == 1
        assert await session.scalar(select(func.count()).select_from(LoginAttempt)) == 1
        user.is_active = False
        saved_session = await session.scalar(select(AdminSession))
        saved_attempt = await session.scalar(select(LoginAttempt))
        assert saved_session is not None
        assert saved_attempt is not None
        saved_session.revoked_at = now
        saved_attempt.outcome = "locked"
        await session.commit()
        await session.delete(saved_session)
        await session.delete(saved_attempt)
        await session.commit()
        assert await session.scalar(select(func.count()).select_from(AdminSession)) == 0
        assert await session.scalar(select(func.count()).select_from(LoginAttempt)) == 0


@pytest.mark.asyncio
async def test_selectin_loading_avoids_project_stack_n_plus_one(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        category = SkillCategory(slug="backend", label="Backend", is_published=True)
        skill = Skill(category=category, name="FastAPI", status="published", sort_order=0)
        projects = [project(f"daftar-{number}") for number in range(4)]
        session.add_all([category, *projects])
        await session.flush()
        for item in projects:
            session.add(
                ProjectSkill(project=item, skill=skill, sort_order=0, name_snapshot="FastAPI")
            )
        await session.commit()

    engine = session_factory.kw["bind"]
    assert engine is not None
    statement_count = 0

    def count_statement(*_: object) -> None:
        nonlocal statement_count
        statement_count += 1

    event.listen(engine.sync_engine, "before_cursor_execute", count_statement)
    async with session_factory() as session:
        result = await session.scalars(
            select(Project).options(
                selectinload(Project.stack_links).selectinload(ProjectSkill.skill)
            )
        )
        loaded_projects = result.all()
        assert len(loaded_projects) == 4
        assert all(item.stack_links[0].skill.name == "FastAPI" for item in loaded_projects)
    event.remove(engine.sync_engine, "before_cursor_execute", count_statement)
    assert statement_count == 3
