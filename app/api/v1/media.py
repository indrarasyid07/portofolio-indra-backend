"""Admin media operations and publication-aware private image delivery."""

from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload
from starlette.responses import Response as StarletteResponse

from app.core.config import get_settings
from app.db.session import get_session
from app.models import MediaAsset, Profile, Project, ProjectImage, Skill
from app.security.dependencies import AdminPrincipal, require_admin, require_admin_write
from app.services.media import (
    ALLOWED_CONTENT_TYPES,
    InvalidImage,
    MediaStorageUnavailable,
    create_upload_url,
    delete_object,
    normalize_image,
    object_key,
    put_object,
    read_object,
)

admin_router = APIRouter(prefix="/api/v1/admin", tags=["admin-media"])
public_router = APIRouter(prefix="/api/v1/public", tags=["public-media"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
AdminReadDep = Annotated[AdminPrincipal, Depends(require_admin)]
AdminWriteDep = Annotated[AdminPrincipal, Depends(require_admin_write)]
MAX_PROJECT_IMAGES = 12
MAX_BATCH_IMAGES = 6
SAFE_FILENAME = re.compile(r"[^A-Za-z0-9._-]+")


class UploadIntent(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    target: Literal["project", "profile", "skill"]
    project_id: UUID | None = None
    skill_id: UUID | None = None
    filename: str = Field(min_length=1, max_length=255)
    content_type: str
    byte_size: int = Field(gt=0, le=8 * 1024 * 1024)
    alt_text: str = Field(default="", max_length=180)
    caption: str | None = Field(default=None, max_length=240)


class UploadCompletion(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    project_id: UUID | None = None
    skill_id: UUID | None = None
    sort_order: int = Field(default=0, ge=0, le=1_000_000)
    is_cover: bool = False


class MediaMetadataUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    alt_text: str = Field(max_length=180)
    caption: str | None = Field(default=None, max_length=240)


class ImageOrderUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    image_ids: list[UUID] = Field(min_length=1, max_length=MAX_PROJECT_IMAGES)
    cover_id: UUID


def _http_error(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


def _serialize(
    asset: MediaAsset,
    *,
    preview_url: str,
    sort_order: int | None = None,
    is_cover: bool = False,
) -> dict[str, object]:
    return {
        "id": asset.id,
        "url": preview_url,
        "alt_text": asset.alt_text,
        "caption": asset.caption,
        "content_type": asset.content_type,
        "byte_size": asset.byte_size,
        "width": asset.width,
        "height": asset.height,
        "sort_order": sort_order,
        "is_cover": is_cover,
    }


@admin_router.post("/media/intents", status_code=201)
async def create_intent(
    payload: UploadIntent, principal: AdminWriteDep, session: SessionDep
) -> dict[str, object]:
    if payload.content_type not in ALLOWED_CONTENT_TYPES:
        raise _http_error(422, "invalid_media", "Format yang didukung: JPEG, PNG, dan WebP.")
    settings = get_settings()
    if not settings.aws_s3_private_bucket_confirmed:
        raise _http_error(
            503,
            "storage_unavailable",
            "Level akses private bucket belum dikonfirmasi oleh operator.",
        )
    now = datetime.now(UTC)
    recent_count = int(
        await session.scalar(
            select(func.count())
            .select_from(MediaAsset)
            .where(
                MediaAsset.uploaded_by == principal.user.id,
                MediaAsset.created_at >= now - timedelta(hours=1),
            )
        )
        or 0
    )
    daily_bytes = int(
        await session.scalar(
            select(func.coalesce(func.sum(MediaAsset.byte_size), 0)).where(
                MediaAsset.uploaded_by == principal.user.id,
                MediaAsset.created_at >= now - timedelta(days=1),
            )
        )
        or 0
    )
    if recent_count >= 30 or daily_bytes + payload.byte_size > 96 * 1024 * 1024:
        raise _http_error(
            429, "media_rate_limited", "Batas upload sementara tercapai. Coba lagi nanti."
        )
    if payload.target == "project":
        if payload.project_id is None:
            raise _http_error(422, "validation_error", "Proyek wajib dipilih.")
        project = await session.get(Project, payload.project_id)
        if project is None:
            raise _http_error(404, "not_found", "Proyek tidak ditemukan.")
        count = int(
            await session.scalar(
                select(func.count())
                .select_from(ProjectImage)
                .where(ProjectImage.project_id == payload.project_id)
            )
            or 0
        )
        if count >= MAX_PROJECT_IMAGES:
            raise _http_error(409, "media_limit", "Proyek sudah mencapai batas 12 gambar.")
        target_prefix = f"project/{payload.project_id}"
    elif payload.target == "skill":
        if payload.project_id is not None or payload.skill_id is None:
            raise _http_error(422, "validation_error", "Tech stack wajib dipilih.")
        if await session.get(Skill, payload.skill_id) is None:
            raise _http_error(404, "not_found", "Tech stack tidak ditemukan.")
        target_prefix = f"skill/{payload.skill_id}"
    else:
        if payload.project_id is not None or payload.skill_id is not None:
            raise _http_error(422, "validation_error", "Foto profil tidak memakai project_id.")
        target_prefix = "profile"

    asset_id = uuid4()
    staging_key = object_key(asset_id, f"incoming/{target_prefix}")
    sanitized_name = SAFE_FILENAME.sub("_", payload.filename).strip("._-")[:120] or "gambar"
    if not all((settings.aws_endpoint_url_s3, settings.aws_s3_bucket, settings.aws_region)):
        raise _http_error(503, "storage_unavailable", "Object Storage belum dikonfigurasi.")
    try:
        upload_policy = await asyncio.to_thread(
            create_upload_url, staging_key, payload.content_type
        )
    except MediaStorageUnavailable as exc:
        raise _http_error(
            503, "storage_unavailable", "Object Storage belum dapat digunakan."
        ) from exc
    asset = MediaAsset(
        id=asset_id,
        provider="neon",
        bucket=settings.aws_s3_bucket,
        object_key=staging_key,
        content_type=payload.content_type,
        byte_size=payload.byte_size,
        sha256="0" * 64,
        state="pending",
        original_filename=sanitized_name,
        alt_text=payload.alt_text,
        caption=payload.caption or None,
        uploaded_by=principal.user.id,
        delete_after=now + timedelta(hours=24),
    )
    session.add(asset)
    try:
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    return {
        "asset_id": asset.id,
        "method": "POST",
        "fields": upload_policy["fields"],
        "expires_in": 300,
        "upload_url": upload_policy["url"],
        "preview_url": f"/api/v1/admin/media/{asset.id}/preview",
    }


@admin_router.post("/media/{asset_id}/complete", status_code=201)
async def complete_upload(
    asset_id: UUID,
    payload: UploadCompletion,
    principal: AdminWriteDep,
    session: SessionDep,
) -> dict[str, object]:
    asset = await session.scalar(
        select(MediaAsset).where(MediaAsset.id == asset_id).with_for_update()
    )
    if asset is None or asset.uploaded_by != principal.user.id:
        raise _http_error(404, "not_found", "Unggahan tidak ditemukan.")
    if asset.state == "ready":
        return _serialize(asset, preview_url=f"/api/v1/admin/media/{asset.id}/preview")
    if asset.state != "pending" or not asset.object_key.startswith("portfolio-media/incoming/"):
        raise _http_error(409, "invalid_state", "Unggahan tidak dapat diselesaikan.")
    is_project = "/incoming/project/" in asset.object_key
    is_skill = "/incoming/skill/" in asset.object_key
    project: Project | None = None
    skill: Skill | None = None
    if is_project:
        if payload.project_id is None:
            raise _http_error(422, "validation_error", "Proyek wajib dipilih.")
        expected_prefix = f"portfolio-media/incoming/project/{payload.project_id}/"
        if not asset.object_key.startswith(expected_prefix):
            raise _http_error(404, "not_found", "Unggahan tidak ditemukan.")
        project = await session.get(Project, payload.project_id, with_for_update=True)
        if project is None:
            raise _http_error(404, "not_found", "Proyek tidak ditemukan.")
        count = int(
            await session.scalar(
                select(func.count())
                .select_from(ProjectImage)
                .where(ProjectImage.project_id == project.id)
            )
            or 0
        )
        if count >= MAX_PROJECT_IMAGES:
            raise _http_error(409, "media_limit", "Proyek sudah mencapai batas 12 gambar.")
    elif is_skill:
        if payload.project_id is not None or payload.skill_id is None:
            raise _http_error(422, "validation_error", "Tech stack wajib dipilih.")
        expected_prefix = f"portfolio-media/incoming/skill/{payload.skill_id}/"
        if not asset.object_key.startswith(expected_prefix):
            raise _http_error(404, "not_found", "Unggahan tidak ditemukan.")
        skill = await session.get(Skill, payload.skill_id, with_for_update=True)
        if skill is None:
            raise _http_error(404, "not_found", "Tech stack tidak ditemukan.")
    elif payload.project_id is not None or payload.skill_id is not None:
        raise _http_error(422, "validation_error", "Unggahan ini adalah foto profil.")

    staging_key = asset.object_key
    try:
        source = await asyncio.to_thread(read_object, staging_key)
        normalized = normalize_image(source, asset.content_type, asset.byte_size)
        final_key = object_key(asset.id, "ready", normalized.extension)
        await asyncio.to_thread(put_object, final_key, normalized.body, normalized.content_type)
    except InvalidImage as exc:
        asset.state = "rejected"
        await session.commit()
        try:
            await asyncio.to_thread(delete_object, staging_key)
        except MediaStorageUnavailable:
            pass
        raise _http_error(422, "invalid_media", str(exc)) from exc
    except MediaStorageUnavailable as exc:
        raise _http_error(
            503, "storage_unavailable", "Validasi gambar gagal sementara. Coba lagi."
        ) from exc

    try:
        asset.object_key = final_key
        asset.content_type = normalized.content_type
        asset.byte_size = len(normalized.body)
        asset.width = normalized.width
        asset.height = normalized.height
        asset.sha256 = normalized.sha256
        asset.state = "ready"
        asset.delete_after = None
        if project is not None:
            images = list(
                (
                    await session.scalars(
                        select(ProjectImage)
                        .where(ProjectImage.project_id == project.id)
                        .order_by(ProjectImage.sort_order)
                        .with_for_update()
                    )
                ).all()
            )
            if payload.is_cover:
                for link in images:
                    link.is_cover = False
            elif images and not any(link.is_cover for link in images):
                payload.is_cover = True
            session.add(
                ProjectImage(
                    project_id=project.id,
                    media_asset_id=asset.id,
                    sort_order=max(payload.sort_order, len(images)),
                    is_cover=payload.is_cover or not images,
                )
            )
        elif skill is not None:
            skill.icon_asset_id = asset.id
            skill.updated_at = datetime.now(UTC)
        else:
            profile = await session.scalar(select(Profile).where(Profile.id == 1).with_for_update())
            if profile is None:
                profile = Profile(id=1, status="draft")
                session.add(profile)
            profile.portrait_asset_id = asset.id
            profile.updated_at = datetime.now(UTC)
        await session.commit()
    except Exception:
        await session.rollback()
        try:
            await asyncio.to_thread(delete_object, final_key)
        except MediaStorageUnavailable:
            pass
        raise
    try:
        await asyncio.to_thread(delete_object, staging_key)
    except MediaStorageUnavailable:
        # Pending-source objects are private and eligible for the operator cleanup task.
        pass
    await session.refresh(asset)
    return _serialize(
        asset,
        preview_url=f"/api/v1/admin/media/{asset.id}/preview",
        sort_order=payload.sort_order if project is not None else None,
        is_cover=payload.is_cover
        or (
            project is not None
            and await session.scalar(
                select(ProjectImage.media_asset_id).where(
                    ProjectImage.project_id == project.id,
                    ProjectImage.media_asset_id == asset.id,
                    ProjectImage.is_cover.is_(True),
                )
            )
            is not None
        ),
    )


@admin_router.get("/projects/{project_id}/images")
async def list_project_images(
    project_id: UUID, _: AdminReadDep, session: SessionDep
) -> list[dict[str, object]]:
    project = await session.get(Project, project_id)
    if project is None:
        raise _http_error(404, "not_found", "Proyek tidak ditemukan.")
    links = list(
        (
            await session.scalars(
                select(ProjectImage)
                .where(ProjectImage.project_id == project_id)
                .options(joinedload(ProjectImage.media_asset))
                .order_by(ProjectImage.sort_order)
            )
        ).all()
    )
    return [
        _serialize(
            link.media_asset,
            preview_url=f"/api/v1/admin/media/{link.media_asset_id}/preview",
            sort_order=link.sort_order,
            is_cover=link.is_cover,
        )
        for link in links
        if link.media_asset.state == "ready"
    ]


@admin_router.put("/projects/{project_id}/images/order")
async def update_project_image_order(
    project_id: UUID,
    payload: ImageOrderUpdate,
    _: AdminWriteDep,
    session: SessionDep,
) -> list[dict[str, object]]:
    links = list(
        (
            await session.scalars(
                select(ProjectImage)
                .where(ProjectImage.project_id == project_id)
                .options(joinedload(ProjectImage.media_asset))
                .with_for_update()
            )
        ).all()
    )
    if not links or {link.media_asset_id for link in links} != set(payload.image_ids):
        raise _http_error(409, "conflict", "Daftar gambar berubah. Muat ulang sebelum menyimpan.")
    by_id = {link.media_asset_id: link for link in links}
    if payload.cover_id not in by_id:
        raise _http_error(422, "validation_error", "Gambar sampul tidak termasuk proyek ini.")
    if len(payload.image_ids) != len(links):
        raise _http_error(
            409, "conflict", "Daftar gambar harus memuat setiap asset tepat satu kali."
        )
    shift = max((link.sort_order for link in links), default=0) + len(links) + 1
    for index, asset_id in enumerate(payload.image_ids):
        link = by_id[asset_id]
        link.sort_order = shift + index
        link.is_cover = False
    await session.flush()
    for index, asset_id in enumerate(payload.image_ids):
        link = by_id[asset_id]
        link.sort_order = index
        link.is_cover = asset_id == payload.cover_id
    await session.commit()
    return [
        _serialize(
            by_id[asset_id].media_asset,
            preview_url=f"/api/v1/admin/media/{asset_id}/preview",
            sort_order=by_id[asset_id].sort_order,
            is_cover=by_id[asset_id].is_cover,
        )
        for asset_id in payload.image_ids
    ]


@admin_router.patch("/media/{asset_id}")
async def update_media_metadata(
    asset_id: UUID,
    payload: MediaMetadataUpdate,
    _: AdminWriteDep,
    session: SessionDep,
) -> dict[str, object]:
    asset = await session.scalar(
        select(MediaAsset).where(MediaAsset.id == asset_id).with_for_update()
    )
    if asset is None or asset.state != "ready":
        raise _http_error(404, "not_found", "Gambar tidak ditemukan.")
    asset.alt_text = payload.alt_text
    asset.caption = payload.caption or None
    await session.commit()
    await session.refresh(asset)
    return _serialize(asset, preview_url=f"/api/v1/admin/media/{asset.id}/preview")


@admin_router.get("/media/{asset_id}")
async def get_media_metadata(
    asset_id: UUID, _: AdminReadDep, session: SessionDep
) -> dict[str, object]:
    asset = await session.get(MediaAsset, asset_id)
    if asset is None or asset.state != "ready":
        raise _http_error(404, "not_found", "Gambar tidak ditemukan.")
    return _serialize(asset, preview_url=f"/api/v1/admin/media/{asset.id}/preview")


@admin_router.delete("/projects/{project_id}/images/{asset_id}", status_code=204)
async def delete_project_image(
    project_id: UUID,
    asset_id: UUID,
    _: AdminWriteDep,
    session: SessionDep,
) -> Response:
    links = list(
        (
            await session.scalars(
                select(ProjectImage)
                .where(ProjectImage.project_id == project_id)
                .order_by(ProjectImage.sort_order)
                .with_for_update()
            )
        ).all()
    )
    target = next((link for link in links if link.media_asset_id == asset_id), None)
    if target is None:
        raise _http_error(404, "not_found", "Gambar proyek tidak ditemukan.")
    remaining = [link for link in links if link.media_asset_id != asset_id]
    target_asset = await session.get(MediaAsset, asset_id, with_for_update=True)
    await session.delete(target)
    if target.is_cover and remaining:
        remaining[0].is_cover = True
    shift = max((link.sort_order for link in remaining), default=0) + len(remaining) + 1
    for index, link in enumerate(remaining):
        link.sort_order = shift + index
    await session.flush()
    for index, link in enumerate(remaining):
        link.sort_order = index
    profile_reference = await session.scalar(
        select(Profile.id).where(Profile.portrait_asset_id == asset_id).limit(1)
    )
    other_links = int(
        await session.scalar(
            select(func.count())
            .select_from(ProjectImage)
            .where(ProjectImage.media_asset_id == asset_id, ProjectImage.project_id != project_id)
        )
        or 0
    )
    if target_asset and profile_reference is None and other_links == 0:
        target_asset.state = "deleting"
    await session.commit()
    if target_asset and target_asset.state == "deleting":
        try:
            await asyncio.to_thread(delete_object, target_asset.object_key)
            await session.delete(target_asset)
            await session.commit()
        except MediaStorageUnavailable as exc:
            raise _http_error(
                503,
                "storage_cleanup_pending",
                "Gambar dilepas; penghapusan berkas akan dicoba ulang.",
            ) from exc
    return Response(status_code=204)


@admin_router.get("/media/{asset_id}/preview")
async def admin_preview_media(
    asset_id: UUID, _: AdminReadDep, session: SessionDep
) -> StarletteResponse:
    asset = await session.get(MediaAsset, asset_id)
    if asset is None or asset.state != "ready":
        raise _http_error(404, "not_found", "Gambar tidak ditemukan.")
    try:
        body = await asyncio.to_thread(read_object, asset.object_key, maximum=5 * 1024 * 1024)
    except MediaStorageUnavailable as exc:
        raise _http_error(
            503, "storage_unavailable", "Gambar sementara tidak dapat dibaca."
        ) from exc
    return StarletteResponse(
        body,
        media_type=asset.content_type,
        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
    )


@public_router.get("/media/{asset_id}")
async def public_media(asset_id: UUID, session: SessionDep) -> StarletteResponse:
    asset = await session.scalar(
        select(MediaAsset)
        .where(MediaAsset.id == asset_id, MediaAsset.state == "ready")
        .options(selectinload(MediaAsset.project_links).joinedload(ProjectImage.project))
    )
    if asset is None:
        raise _http_error(404, "not_found", "Gambar tidak ditemukan.")
    is_published_profile = await session.scalar(
        select(Profile.id).where(
            Profile.status == "published", Profile.portrait_asset_id == asset_id
        )
    )
    is_published_project = any(link.project.status == "published" for link in asset.project_links)
    if not is_published_profile and not is_published_project:
        raise _http_error(404, "not_found", "Gambar tidak ditemukan.")
    try:
        body = await asyncio.to_thread(read_object, asset.object_key, maximum=5 * 1024 * 1024)
    except MediaStorageUnavailable as exc:
        raise _http_error(503, "storage_unavailable", "Gambar sementara tidak tersedia.") from exc
    return StarletteResponse(
        body,
        media_type=asset.content_type,
        headers={
            "Cache-Control": "public, max-age=300, stale-while-revalidate=3600",
            "X-Content-Type-Options": "nosniff",
        },
    )
