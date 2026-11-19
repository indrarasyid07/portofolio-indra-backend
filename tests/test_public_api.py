"""Database-backed HTTP tests for the anonymous portfolio API."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.v1.public import get_session
from app.core.config import get_settings
from app.db.engine import DatabaseUnavailableError
from app.main import app
from app.models import (
    EducationRecord,
    Experience,
    MediaAsset,
    Profile,
    Project,
    ProjectImage,
    ProjectSkill,
    Skill,
    SkillCategory,
)


@pytest_asyncio.fixture
async def public_client(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncClient]:
    async def override_session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://testserver",
    ) as client:
        yield client
    app.dependency_overrides.pop(get_session, None)


def make_project(
    slug: str,
    *,
    status: str = "published",
    sort_order: int = 0,
    year: int = 2026,
    featured: bool = False,
    project_type: str = "web",
) -> Project:
    return Project(
        slug=slug,
        title=f"Proyek {slug}",
        project_type=project_type,
        year=year,
        role="Programmer",
        summary="Ringkasan publik",
        challenge="Isi studi kasus panjang",
        status=status,
        sort_order=sort_order,
        featured=featured,
        published_at=datetime.now(UTC) if status == "published" else None,
    )


@pytest.mark.asyncio
async def test_public_lists_are_empty_without_published_content_and_require_no_cookie(
    public_client: AsyncClient,
) -> None:
    for path in (
        "/api/v1/public/projects",
        "/api/v1/public/experiences",
        "/api/v1/public/education",
        "/api/v1/public/skills",
    ):
        response = await public_client.get(path)
        assert response.status_code == 200
        assert response.json()["data"] == []
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["x-request-id"].startswith("req_")
    assert (await public_client.get("/api/v1/public/profile")).status_code == 404
    assert (await public_client.get("/api/v1/public/contact")).status_code == 404
    assert (await public_client.get("/api/v1/public/projects")).status_code == 200


@pytest.mark.asyncio
async def test_projects_only_expose_published_records_and_detail_is_not_found_for_drafts(
    public_client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        session.add_all([make_project("terbit"), make_project("rahasia", status="draft")])
        await session.commit()

    public_client.cookies.set("admin", "invalid")
    response = await public_client.get("/api/v1/public/projects")
    public_client.cookies.delete("admin")
    assert response.status_code == 200
    body = response.json()
    assert [project["slug"] for project in body["data"]] == ["terbit"]
    assert "challenge" not in body["data"][0]

    published = await public_client.get("/api/v1/public/projects/terbit")
    draft = await public_client.get("/api/v1/public/projects/rahasia")
    absent = await public_client.get("/api/v1/public/projects/tidak-ada")
    assert published.status_code == 200
    assert published.json()["data"]["challenge"] == "Isi studi kasus panjang"
    for hidden in (draft, absent):
        assert hidden.status_code == 404
        assert hidden.json()["error"]["code"] == "not_found"


@pytest.mark.asyncio
async def test_projects_filters_cursor_pagination_and_query_validation(
    public_client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        session.add_all(
            [
                make_project("satu", sort_order=0, year=2026, featured=True),
                make_project("dua", sort_order=0, year=2025, project_type="api"),
                make_project("tiga", sort_order=1, year=2024),
                make_project("draft", status="draft", sort_order=0, year=2027),
            ]
        )
        await session.commit()

    first_page = await public_client.get("/api/v1/public/projects?limit=2&type=web")
    assert first_page.status_code == 200
    assert [item["slug"] for item in first_page.json()["data"]] == ["satu", "tiga"]
    cursor = first_page.json()["meta"]["next_cursor"]
    assert cursor is None

    featured = await public_client.get("/api/v1/public/projects?featured=true")
    assert [item["slug"] for item in featured.json()["data"]] == ["satu"]

    paged = await public_client.get("/api/v1/public/projects?limit=1")
    assert [item["slug"] for item in paged.json()["data"]] == ["satu"]
    next_page = await public_client.get(
        "/api/v1/public/projects",
        params={"limit": 1, "cursor": paged.json()["meta"]["next_cursor"]},
    )
    assert [item["slug"] for item in next_page.json()["data"]] == ["dua"]

    invalid = await public_client.get("/api/v1/public/projects?limit=0")
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "validation_error"
    assert "input" not in invalid.text.lower()
    bad_cursor = await public_client.get("/api/v1/public/projects?cursor=not-a-cursor")
    assert bad_cursor.status_code == 400
    assert bad_cursor.json()["error"]["code"] == "invalid_request"


@pytest.mark.asyncio
async def test_experience_and_education_share_one_published_source(
    public_client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        session.add_all(
            [
                Experience(
                    role="Backend Developer",
                    organization="Tim A",
                    employment_type="full_time",
                    start_year=2023,
                    start_month=None,
                    end_year=None,
                    end_month=None,
                    summary="Membangun API",
                    highlights=["Membuat endpoint"],
                    status="published",
                    sort_order=0,
                ),
                Experience(
                    role="Draft Role",
                    organization="Tim B",
                    employment_type="contract",
                    start_year=2025,
                    start_month=None,
                    end_year=None,
                    end_month=None,
                    summary="Rahasia draft",
                    highlights=[],
                    status="draft",
                    sort_order=1,
                ),
                EducationRecord(
                    kind="course",
                    title="Python Lanjutan",
                    institution="Kursus X",
                    start_year=2024,
                    start_month=4,
                    end_year=2024,
                    end_month=6,
                    description="Materi pengembangan",
                    proof_url="javascript:alert(1)",
                    credential_id="PUBLIC-1",
                    status="published",
                    sort_order=0,
                ),
                EducationRecord(
                    kind="formal",
                    title="Draft pendidikan",
                    institution="Sekolah",
                    status="draft",
                    sort_order=1,
                ),
            ]
        )
        await session.commit()

    experiences = await public_client.get("/api/v1/public/experiences")
    education = await public_client.get("/api/v1/public/education?kind=course")
    invalid_kind = await public_client.get("/api/v1/public/education?kind=other")
    assert [row["role"] for row in experiences.json()["data"]] == ["Backend Developer"]
    assert experiences.json()["data"][0]["end_year"] is None
    assert [row["title"] for row in education.json()["data"]] == ["Python Lanjutan"]
    assert education.json()["data"][0]["proof_url"] is None
    assert invalid_kind.status_code == 422


@pytest.mark.asyncio
async def test_profile_and_contact_only_return_validated_public_fields(
    public_client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        session.add(
            Profile(
                id=1,
                name="Indra",
                role="Programmer",
                headline="Membangun perangkat lunak",
                intro="Halo",
                about="Tentang saya",
                location="Jakarta",
                availability="Terbuka untuk diskusi",
                public_email="indra@example.com",
                github_url="javascript:alert(1)",
                linkedin_url="http://not-secure.example.test/indra",
                cv_url="https://example.test/cv.pdf",
                status="published",
            )
        )
        await session.commit()

    profile = await public_client.get("/api/v1/public/profile")
    contact = await public_client.get("/api/v1/public/contact")
    assert profile.status_code == 200
    assert profile.json()["data"]["name"] == "Indra"
    assert profile.json()["data"]["social_links"] == {"github": None, "linkedin": None}
    assert "email" not in profile.json()["data"]
    assert "password_hash" not in profile.text
    assert contact.json()["data"]["email"] == "indra@example.com"


@pytest.mark.asyncio
async def test_ready_media_urls_are_derived_and_object_keys_are_never_exposed(
    public_client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("PUBLIC_SITE_ORIGIN", "https://portfolio.example.test")
    get_settings.cache_clear()
    try:
        async with session_factory() as session:
            category = SkillCategory(slug="backend", label="Backend", is_published=True)
            skill = Skill(category=category, name="Python", status="published", sort_order=0)
            project = make_project("media-study")
            cover = MediaAsset(
                provider="local",
                bucket="public",
                object_key="projects/media-study/cover.webp",
                content_type="image/webp",
                byte_size=10,
                sha256="a" * 64,
                state="ready",
                original_filename="cover.webp",
                alt_text="Tangkapan layar aplikasi",
                caption="Tampilan utama",
            )
            unsafe = MediaAsset(
                provider="local",
                bucket="public",
                object_key="../private.webp",
                content_type="image/webp",
                byte_size=10,
                sha256="b" * 64,
                state="ready",
                original_filename="private.webp",
                alt_text="Media yang ditolak",
            )
            session.add_all([category, skill, project, cover, unsafe])
            await session.flush()
            session.add_all(
                [
                    ProjectImage(project=project, media_asset=cover, sort_order=0, is_cover=True),
                    ProjectImage(project=project, media_asset=unsafe, sort_order=1, is_cover=False),
                    ProjectSkill(
                        project=project,
                        skill=skill,
                        sort_order=0,
                        name_snapshot="Python (snapshot)",
                    ),
                ]
            )
            await session.commit()

        response = await public_client.get("/api/v1/public/projects/media-study")
        assert response.status_code == 200
        body = response.json()["data"]
        assert (
            body["cover"]["url"] == f"https://portfolio.example.test/api/v1/public/media/{cover.id}"
        )
        assert body["cover"]["alt_text"] == "Tangkapan layar aplikasi"
        assert body["images"][1]["url"].startswith(
            "https://portfolio.example.test/api/v1/public/media/"
        )
        assert body["stack_labels"] == ["Python (snapshot)"]
        assert "private.webp" not in response.text
        assert "object_key" not in response.text
    finally:
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_private_media_route_hides_drafts_and_serves_only_published_assets(
    public_client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api.v1 import media as media_api

    def read_test_object(_: str, *, maximum: int = 0) -> bytes:
        assert maximum > 0
        return b"normalized-image"

    monkeypatch.setattr(media_api, "read_object", read_test_object)
    async with session_factory() as session:
        project = make_project("private-gallery", status="draft")
        asset = MediaAsset(
            provider="neon",
            bucket="private",
            object_key="portfolio-media/ready/opaque.webp",
            content_type="image/webp",
            byte_size=15,
            sha256="c" * 64,
            state="ready",
            original_filename="opaque.webp",
            alt_text="Tampilan dashboard",
        )
        session.add_all([project, asset])
        await session.flush()
        session.add(ProjectImage(project=project, media_asset=asset, sort_order=0, is_cover=True))
        await session.commit()
        asset_id = asset.id

    hidden = await public_client.get(f"/api/v1/public/media/{asset_id}")
    assert hidden.status_code == 404

    async with session_factory() as session:
        project = await session.scalar(select(Project).where(Project.slug == "private-gallery"))
        assert project is not None
        project.status = "published"
        await session.commit()
    visible = await public_client.get(f"/api/v1/public/media/{asset_id}")
    assert visible.status_code == 200
    assert visible.content == b"normalized-image"
    assert visible.headers["x-content-type-options"] == "nosniff"
    assert "max-age=300" in visible.headers["cache-control"]
    assert "object_key" not in visible.text


@pytest.mark.asyncio
async def test_skills_are_filtered_by_published_category_and_skill(
    public_client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        public_category = SkillCategory(slug="tools", label="Tools", is_published=True)
        hidden_category = SkillCategory(slug="draft", label="Draft", is_published=False)
        session.add_all(
            [
                public_category,
                hidden_category,
                Skill(category=public_category, name="Git", status="published", sort_order=0),
                Skill(category=public_category, name="Draft skill", status="draft", sort_order=1),
                Skill(
                    category=hidden_category, name="Private skill", status="published", sort_order=0
                ),
            ]
        )
        await session.commit()

    response = await public_client.get("/api/v1/public/skills")
    assert response.status_code == 200
    assert [item["label"] for item in response.json()["data"]] == ["Tools"]
    assert [item["name"] for item in response.json()["data"][0]["skills"]] == ["Git"]


@pytest.mark.asyncio
async def test_database_failure_is_a_safe_retryable_service_error(
    public_client: AsyncClient,
) -> None:
    async def unavailable_session() -> AsyncIterator[AsyncSession]:
        raise DatabaseUnavailableError("secret connection detail")
        yield

    app.dependency_overrides[get_session] = unavailable_session
    response = await public_client.get("/api/v1/public/projects")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "database_unavailable"
    assert "secret connection detail" not in response.text

    async def database_error_session() -> AsyncIterator[AsyncSession]:
        raise OperationalError("select secret_sql", {}, Exception("secret db detail"))
        yield

    app.dependency_overrides[get_session] = database_error_session
    response = await public_client.get("/api/v1/public/projects")
    assert response.status_code == 503
    assert "secret_sql" not in response.text
    assert "secret db detail" not in response.text

    async def unexpected_error_session() -> AsyncIterator[AsyncSession]:
        raise RuntimeError("private configuration")
        yield

    app.dependency_overrides[get_session] = unexpected_error_session
    response = await public_client.get("/api/v1/public/projects")
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"
    assert "private configuration" not in response.text
    app.dependency_overrides.pop(get_session, None)


@pytest.mark.asyncio
async def test_openapi_and_cors_match_read_only_public_api(
    public_client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        session.add(
            Profile(
                id=1,
                name="Indra",
                role="Programmer",
                headline="Headline",
                intro="Intro",
                about="Tentang",
                status="published",
            )
        )
        await session.commit()

    response = await public_client.get(
        "/api/v1/public/profile", headers={"Origin": "http://localhost:4321"}
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:4321"
    assert response.headers.get("access-control-allow-credentials") is None
    disallowed = await public_client.get(
        "/api/v1/public/profile", headers={"Origin": "https://unapproved.example.test"}
    )
    assert "access-control-allow-origin" not in disallowed.headers

    schema = await public_client.get("/api/openapi.json")
    assert schema.status_code == 200
    paths = schema.json()["paths"]
    assert "/api/v1/public/profile" in paths
    assert "/api/v1/admin/projects" in paths
    assert "get" in paths["/api/v1/admin/projects"]
    assert "post" in paths["/api/v1/admin/projects"]
    assert "400" in paths["/api/v1/public/projects"]["get"]["responses"]
    assert "422" in paths["/api/v1/public/projects"]["get"]["responses"]
