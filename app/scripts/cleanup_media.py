"""Report or remove expired and unreferenced portfolio media objects."""

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import get_settings
from app.db.engine import get_engine
from app.models import MediaAsset, Profile, ProjectImage
from app.services.media import MediaStorageUnavailable, delete_object

APP_PREFIX = "portfolio-media/"


async def cleanup(*, apply: bool) -> tuple[int, int, int]:
    settings = get_settings()
    if not settings.aws_s3_bucket:
        raise SystemExit("AWS_S3_BUCKET belum dikonfigurasi.")
    factory = async_sessionmaker(get_engine(), expire_on_commit=False)
    now = datetime.now(UTC)
    cutoff = now - timedelta(hours=24)
    selected: list[MediaAsset] = []
    async with factory() as session:
        assets = list((await session.scalars(select(MediaAsset))).all())
        for asset in assets:
            if (
                asset.provider != "neon"
                or asset.bucket != settings.aws_s3_bucket
                or not asset.object_key.startswith(APP_PREFIX)
            ):
                continue
            profile_ref = await session.scalar(
                select(Profile.id).where(Profile.portrait_asset_id == asset.id).limit(1)
            )
            project_ref = await session.scalar(
                select(ProjectImage.media_asset_id)
                .where(ProjectImage.media_asset_id == asset.id)
                .limit(1)
            )
            unreferenced = not (profile_ref or project_ref)
            expired_pending = (
                asset.state in {"pending", "rejected"}
                and (asset.delete_after is not None and asset.delete_after <= now)
                and unreferenced
            )
            stale_deleting = asset.state == "deleting" and unreferenced
            orphaned_ready = asset.state == "ready" and asset.created_at < cutoff and unreferenced
            if expired_pending or stale_deleting or orphaned_ready:
                selected.append(asset)

        total_bytes = sum(asset.byte_size for asset in selected)
        print(
            f"candidates={len(selected)} bytes={total_bytes} MiB={total_bytes / (1024 * 1024):.2f} "
            f"mode={'apply' if apply else 'dry-run'}"
        )
        if not apply:
            return len(selected), total_bytes, 0

        deleted = 0
        for asset in selected:
            asset.state = "deleting"
        await session.commit()
        for asset in selected:
            try:
                await asyncio.to_thread(delete_object, asset.object_key)
            except MediaStorageUnavailable:
                print(f"pending_retry asset_id={asset.id}")
                continue
            await session.delete(asset)
            await session.commit()
            deleted += 1
        return len(selected), total_bytes, deleted


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply", action="store_true", help="Delete listed stale objects; default is report only."
    )
    args = parser.parse_args()
    candidates, bytes_to_free, deleted = asyncio.run(cleanup(apply=args.apply))
    if args.apply:
        print(f"deleted={deleted} remaining={candidates - deleted} candidate_bytes={bytes_to_free}")


if __name__ == "__main__":
    main()
