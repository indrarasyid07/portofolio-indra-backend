"""Admin API coverage for authenticated CRUD and publication workflow."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.db.session import get_session
from app.main import app
from app.models import AdminSession, AdminUser, Experience, Profile
from app.security.passwords import hash_password
from app.security.tokens import hash_session_token

USERNAME = "ContentAdmin"
PASSWORD = "Another-secure-test-passphrase"
ORIGIN = "http://testserver"


@pytest.fixture(autouse=True)
def auth_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    values = {
        "APP_ENV": "test",
        "SESSION_HASH_KEY": "admin-test-session-key-with-at-least-32-bytes-01",
        "LOGIN_RATE_KEY": "admin-test-rate-key-with-at-least-32-bytes-02",
        "AUTH_PROXY_KEY": "admin-test-proxy-key-with-at-least-32-bytes-03",
        "PUBLIC_SITE_ORIGIN": ORIGIN,
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest_asyncio.fixture
async def admin_client(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncClient]:
    async def override_session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    async with session_factory() as session:
        session.add(
            AdminUser(
                username=USERNAME,
                normalized_username=USERNAME.casefold(),
                password_hash=hash_password(PASSWORD),
                is_active=True,
            )
        )
        await session.commit()
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url=ORIGIN,
    ) as client:
        login = await client.post(
            "/api/v1/auth/login",
            json={"username": USERNAME, "password": PASSWORD},
            headers={"Origin": ORIGIN},
        )
        assert login.status_code == 200
        client.headers.update({"Origin": ORIGIN, "X-CSRF-Token": login.json()["csrf_token"]})
        yield client
    app.dependency_overrides.pop(get_session, None)


def project_payload(**updates: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "slug": "portfolio-api",
        "title": "Portfolio API",
        "project_type": "api",
        "year": 2026,
        "role": "Backend developer",
        "summary": "API untuk mengelola konten portofolio.",
        "challenge": "Data perlu aman.",
        "solution": "Pakai endpoint tervalidasi.",
        "outcome": "Konten terstruktur.",
        "demo_url": None,
        "repository_url": "https://example.com/indra/portfolio",
        "featured": False,
        "sort_order": 0,
        "skill_ids": [],
    }
    payload.update(updates)
    return payload


@pytest.mark.asyncio
async def test_project_draft_publish_unpublish_and_optimistic_conflict(
    admin_client: AsyncClient,
) -> None:
    created = await admin_client.post("/api/v1/admin/projects", json=project_payload())
    assert created.status_code == 201
    draft = created.json()
    assert draft["status"] == "draft"
    assert (await admin_client.get("/api/v1/public/projects/portfolio-api")).status_code == 404

    update_body = project_payload(title="Portfolio API v2", expected_updated_at=draft["updated_at"])
    updated = await admin_client.put(f"/api/v1/admin/projects/{draft['id']}", json=update_body)
    assert updated.status_code == 200
    assert updated.json()["title"] == "Portfolio API v2"

    stale = await admin_client.put(f"/api/v1/admin/projects/{draft['id']}", json=update_body)
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "conflict"

    published = await admin_client.post(
        f"/api/v1/admin/projects/{draft['id']}/publish",
        json={"expected_updated_at": updated.json()["updated_at"]},
    )
    assert published.status_code == 200
    assert published.json()["status"] == "published"
    public_detail = await admin_client.get("/api/v1/public/projects/portfolio-api")
    assert public_detail.status_code == 200
    assert public_detail.json()["data"]["title"] == "Portfolio API v2"

    unpublished = await admin_client.post(
        f"/api/v1/admin/projects/{draft['id']}/unpublish",
        json={"expected_updated_at": published.json()["updated_at"]},
    )
    assert unpublished.status_code == 200
    assert unpublished.json()["status"] == "draft"
    assert (await admin_client.get("/api/v1/public/projects/portfolio-api")).status_code == 404


@pytest.mark.asyncio
async def test_slug_conflict_and_unsafe_url_are_rejected(admin_client: AsyncClient) -> None:
    first = await admin_client.post("/api/v1/admin/projects", json=project_payload())
    conflict = await admin_client.post(
        "/api/v1/admin/projects", json=project_payload(title="Duplicate")
    )
    assert first.status_code == 201
    assert conflict.status_code == 409

    unsafe = await admin_client.post(
        "/api/v1/admin/projects",
        json=project_payload(slug="unsafe-link", demo_url="javascript:alert(1)"),
    )
    assert unsafe.status_code == 422


@pytest.mark.asyncio
async def test_media_intent_completion_and_project_link_use_storage_adapter(
    admin_client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import io

    from PIL import Image

    from app.api.v1 import media as media_api
    from app.core.config import get_settings

    for key, value in {
        "AWS_ENDPOINT_URL_S3": "https://objects.example.test",
        "AWS_ACCESS_KEY_ID": "test-access-key",
        "AWS_SECRET_ACCESS_KEY": "test-secret-key-only-for-test",
        "AWS_REGION": "us-east-2",
        "AWS_S3_BUCKET": "portfolio-test",
        "AWS_S3_PRIVATE_BUCKET_CONFIRMED": "true",
    }.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    source = io.BytesIO()
    Image.new("RGB", (32, 24), "#386d44").save(source, format="PNG")
    png = source.getvalue()
    monkeypatch.setattr(
        media_api,
        "create_upload_url",
        lambda key, content_type: {
            "url": "https://objects.example.test/upload",
            "fields": {"key": key, "Content-Type": content_type},
        },
    )
    monkeypatch.setattr(media_api, "read_object", lambda key, *, maximum=0: png)
    monkeypatch.setattr(media_api, "put_object", lambda key, body, content_type: None)
    monkeypatch.setattr(media_api, "delete_object", lambda key: None)

    created = await admin_client.post("/api/v1/admin/projects", json=project_payload())
    assert created.status_code == 201
    project_id = created.json()["id"]
    intent = await admin_client.post(
        "/api/v1/admin/media/intents",
        json={
            "target": "project",
            "project_id": project_id,
            "filename": "screenshot.png",
            "content_type": "image/png",
            "byte_size": len(png),
            "alt_text": "Halaman dashboard proyek",
            "caption": "Tampilan ringkasan",
        },
    )
    assert intent.status_code == 201
    assert intent.json()["method"] == "POST"
    assert "screenshot.png" not in intent.text
    other_project = await admin_client.post(
        "/api/v1/admin/projects",
        json=project_payload(slug="other-project", title="Proyek lain"),
    )
    wrong_target = await admin_client.post(
        f"/api/v1/admin/media/{intent.json()['asset_id']}/complete",
        json={"project_id": other_project.json()["id"], "sort_order": 0, "is_cover": True},
    )
    assert wrong_target.status_code == 404
    completed = await admin_client.post(
        f"/api/v1/admin/media/{intent.json()['asset_id']}/complete",
        json={"project_id": project_id, "sort_order": 0, "is_cover": True},
    )
    assert completed.status_code == 201
    assert completed.json()["alt_text"] == "Halaman dashboard proyek"
    assert completed.json()["width"] == 32
    assert completed.json()["is_cover"] is True
    assert "object_key" not in completed.text
    media_path = f"/api/v1/public/media/{intent.json()['asset_id']}"
    assert (await admin_client.get(media_path)).status_code == 404
    published = await admin_client.post(
        f"/api/v1/admin/projects/{project_id}/publish",
        json={"expected_updated_at": created.json()["updated_at"]},
    )
    assert published.status_code == 200
    assert (await admin_client.get(media_path)).status_code == 200
    unpublished = await admin_client.post(
        f"/api/v1/admin/projects/{project_id}/unpublish",
        json={"expected_updated_at": published.json()["updated_at"]},
    )
    assert unpublished.status_code == 200
    assert (await admin_client.get(media_path)).status_code == 404
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_media_intents_fail_closed_until_private_bucket_is_confirmed(
    admin_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.core.config import get_settings

    monkeypatch.setenv("AWS_S3_PRIVATE_BUCKET_CONFIRMED", "false")
    get_settings.cache_clear()
    created = await admin_client.post("/api/v1/admin/projects", json=project_payload())
    intent = await admin_client.post(
        "/api/v1/admin/media/intents",
        json={
            "target": "project",
            "project_id": created.json()["id"],
            "filename": "draft.png",
            "content_type": "image/png",
            "byte_size": 128,
            "alt_text": "Tampilan draft",
        },
    )
    assert intent.status_code == 503
    assert intent.json()["error"]["code"] == "storage_unavailable"
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_profile_public_email_is_explicit_and_experience_is_single_entity(
    admin_client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    profile = await admin_client.put(
        "/api/v1/admin/profile",
        json={
            "name": "Indra",
            "role": "Programmer",
            "headline": "Membangun perangkat lunak yang berguna",
            "intro": "Profil singkat.",
            "about": "Tentang saya.",
            "location": "Indonesia",
            "availability": None,
            "public_email": "indra.public@example.com",
            "github_url": "https://github.com/indra",
            "linkedin_url": None,
            "cv_url": None,
        },
    )
    assert profile.status_code == 200
    published_profile = await admin_client.post(
        "/api/v1/admin/profile/publish",
        json={"expected_updated_at": profile.json()["updated_at"]},
    )
    assert published_profile.status_code == 200
    assert "password_hash" not in published_profile.json()
    contact = await admin_client.get("/api/v1/public/contact")
    assert contact.status_code == 200
    assert contact.json()["data"]["email"] == "indra.public@example.com"

    experience = await admin_client.post(
        "/api/v1/admin/experiences",
        json={
            "role": "Backend developer",
            "organization": "Tim produk",
            "employment_type": "full_time",
            "location": "Indonesia",
            "start_year": 2024,
            "start_month": 1,
            "end_year": None,
            "end_month": None,
            "summary": "Mengembangkan API.",
            "highlights": ["Mengelola layanan API"],
            "sort_order": 0,
        },
    )
    assert experience.status_code == 201
    published_experience = await admin_client.post(
        f"/api/v1/admin/experiences/{experience.json()['id']}/publish",
        json={"expected_updated_at": experience.json()["updated_at"]},
    )
    assert published_experience.status_code == 200
    public_experiences = await admin_client.get("/api/v1/public/experiences")
    assert len(public_experiences.json()["data"]) == 1

    async with session_factory() as session:
        row = await session.scalar(select(Profile).where(Profile.id == 1))
        count = int(await session.scalar(select(func.count()).select_from(Experience)) or 0)
    assert row is not None and row.public_email == "indra.public@example.com"
    assert count == 1


@pytest.mark.asyncio
async def test_anonymous_and_expired_sessions_cannot_manage_content(
    admin_client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    raw_token = admin_client.cookies.get("portfolio_session")
    assert raw_token
    admin_client.cookies.clear()
    anonymous = await admin_client.post(
        "/api/v1/admin/projects", json=project_payload(), headers={"Origin": ORIGIN}
    )
    assert anonymous.status_code == 401
    admin_client.cookies.set("portfolio_session", "malformed")
    malformed = await admin_client.get("/api/v1/admin/projects")
    assert malformed.status_code == 401
    admin_client.cookies.set("portfolio_session", raw_token)
    async with session_factory() as session:
        admin_session = await session.scalar(
            select(AdminSession).where(AdminSession.token_hash == hash_session_token(raw_token))
        )
        assert admin_session is not None
        admin_session.last_seen_at = datetime.now(UTC) - timedelta(minutes=31)
        await session.commit()
    expired = await admin_client.post(
        "/api/v1/admin/projects", json=project_payload(), headers={"Origin": ORIGIN}
    )
    assert expired.status_code == 401
    assert "max-age=0" in expired.headers["set-cookie"].lower()


@pytest.mark.asyncio
async def test_skill_categories_and_project_stack_share_published_content(
    admin_client: AsyncClient,
) -> None:
    category_response = await admin_client.post(
        "/api/v1/admin/skill-categories",
        json={"slug": "backend", "label": "Backend", "sort_order": 0},
    )
    assert category_response.status_code == 201
    category = category_response.json()
    skill_response = await admin_client.post(
        "/api/v1/admin/skills",
        json={
            "category_id": category["id"],
            "name": "FastAPI",
            "note": "REST API",
            "sort_order": 0,
        },
    )
    assert skill_response.status_code == 201
    skill = skill_response.json()
    assert skill["status"] == "draft"

    published_category = await admin_client.post(
        f"/api/v1/admin/skill-categories/{category['id']}/publish",
        json={"expected_updated_at": category["updated_at"]},
    )
    assert published_category.status_code == 200
    published_skill = await admin_client.post(
        f"/api/v1/admin/skills/{skill['id']}/publish",
        json={"expected_updated_at": skill["updated_at"]},
    )
    assert published_skill.status_code == 200

    project = await admin_client.post(
        "/api/v1/admin/projects",
        json=project_payload(slug="project-with-stack", skill_ids=[skill["id"]]),
    )
    assert project.status_code == 201
    updated_project = await admin_client.put(
        f"/api/v1/admin/projects/{project.json()['id']}",
        json=project_payload(
            slug="project-with-stack",
            title="Project with stack",
            skill_ids=[skill["id"]],
            expected_updated_at=project.json()["updated_at"],
        ),
    )
    assert updated_project.status_code == 200
    assert updated_project.json()["skill_ids"] == [skill["id"]]
    published_project = await admin_client.post(
        f"/api/v1/admin/projects/{project.json()['id']}/publish",
        json={"expected_updated_at": updated_project.json()["updated_at"]},
    )
    assert published_project.status_code == 200
    public_project = await admin_client.get("/api/v1/public/projects/project-with-stack")
    assert public_project.status_code == 200
    assert public_project.json()["data"]["stack_labels"] == ["FastAPI"]
    public_skills = await admin_client.get("/api/v1/public/skills")
    assert public_skills.status_code == 200
    assert public_skills.json()["data"][0]["skills"][0]["name"] == "FastAPI"
