"""Private S3-compatible storage and safe image normalization."""

# boto3 does not ship inline type annotations; its untyped client boundary is isolated here.
# mypy: disable-error-code="import-untyped"

from __future__ import annotations

import hashlib
import io
import warnings
from dataclasses import dataclass
from typing import Any, cast
from uuid import UUID

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from PIL import Image, ImageOps, UnidentifiedImageError

from app.core.config import get_settings

MAX_BYTES = 8 * 1024 * 1024
MAX_PIXELS = 40_000_000
MAX_DIMENSION = 8_000
MAX_CANONICAL_BYTES = 5 * 1024 * 1024
ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp"}
Image.MAX_IMAGE_PIXELS = MAX_PIXELS


class MediaStorageUnavailable(RuntimeError):
    """Storage credentials or service are unavailable."""


class InvalidImage(ValueError):
    """Uploaded bytes are not an allowed, bounded raster image."""


@dataclass(frozen=True)
class NormalizedImage:
    body: bytes
    content_type: str
    extension: str
    width: int
    height: int
    sha256: str


def _client() -> Any:
    settings = get_settings()
    access_key = settings.aws_access_key_id
    secret_key = settings.aws_secret_access_key
    if not all(
        (
            settings.aws_endpoint_url_s3,
            access_key,
            secret_key,
            settings.aws_region,
            settings.aws_s3_bucket,
        )
    ):
        raise MediaStorageUnavailable("Penyimpanan media belum dikonfigurasi.")
    assert access_key is not None and secret_key is not None
    return boto3.client(
        "s3",
        endpoint_url=settings.aws_endpoint_url_s3,
        region_name=settings.aws_region,
        aws_access_key_id=access_key.get_secret_value(),
        aws_secret_access_key=secret_key.get_secret_value(),
        config=Config(
            signature_version="s3v4", s3={"addressing_style": "path"}, retries={"max_attempts": 2}
        ),
    )


def _bucket() -> str:
    bucket = get_settings().aws_s3_bucket
    if not bucket:
        raise MediaStorageUnavailable("Bucket media belum dikonfigurasi.")
    return bucket


def create_upload_url(key: str, content_type: str) -> dict[str, Any]:
    try:
        return cast(
            dict[str, Any],
            _client().generate_presigned_post(
                Bucket=_bucket(),
                Key=key,
                Fields={"Content-Type": content_type},
                Conditions=[
                    {"Content-Type": content_type},
                    ["content-length-range", 1, MAX_BYTES + 16_384],
                ],
                ExpiresIn=300,
            ),
        )
    except (BotoCoreError, ClientError) as exc:
        raise MediaStorageUnavailable("URL unggah tidak dapat dibuat.") from exc


def read_object(key: str, *, maximum: int = MAX_BYTES) -> bytes:
    try:
        response = _client().get_object(Bucket=_bucket(), Key=key)
        if int(response.get("ContentLength", maximum + 1)) > maximum:
            raise InvalidImage("Ukuran berkas melebihi batas.")
        body = response["Body"]
        data = cast(bytes, body.read(maximum + 1))
        body.close()
        if len(data) > maximum:
            raise InvalidImage("Ukuran berkas melebihi batas.")
        return data
    except InvalidImage:
        raise
    except (BotoCoreError, ClientError, KeyError) as exc:
        raise MediaStorageUnavailable("Objek media tidak dapat dibaca.") from exc


def put_object(key: str, body: bytes, content_type: str) -> None:
    try:
        _client().put_object(
            Bucket=_bucket(),
            Key=key,
            Body=body,
            ContentType=content_type,
            CacheControl="public, max-age=31536000, immutable",
        )
    except (BotoCoreError, ClientError) as exc:
        raise MediaStorageUnavailable("Objek media tidak dapat disimpan.") from exc


def delete_object(key: str) -> None:
    try:
        _client().delete_object(Bucket=_bucket(), Key=key)
    except (BotoCoreError, ClientError) as exc:
        raise MediaStorageUnavailable("Objek media belum dapat dihapus.") from exc


def normalize_image(data: bytes, declared_type: str, expected_size: int) -> NormalizedImage:
    if not data or len(data) != expected_size or len(data) > MAX_BYTES:
        raise InvalidImage("Ukuran berkas tidak sesuai batas unggah.")
    if declared_type not in ALLOWED_CONTENT_TYPES:
        raise InvalidImage("Format yang didukung: JPEG, PNG, dan WebP.")

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            source = Image.open(io.BytesIO(data))
            if source.format not in {"JPEG", "PNG", "WEBP"}:
                raise InvalidImage("Format gambar tidak didukung.")
            actual_type = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}[
                source.format
            ]
            if actual_type != declared_type or getattr(source, "n_frames", 1) != 1:
                raise InvalidImage("Format berkas tidak cocok atau mengandung animasi.")
            source.verify()
            source = Image.open(io.BytesIO(data))
            source.load()
            width, height = source.size
            if (
                width < 1
                or height < 1
                or width > MAX_DIMENSION
                or height > MAX_DIMENSION
                or width * height > MAX_PIXELS
            ):
                raise InvalidImage("Dimensi gambar melebihi batas.")
            image = ImageOps.exif_transpose(source)
            if image.mode not in {"RGB", "RGBA"}:
                image = image.convert("RGBA" if "A" in image.getbands() else "RGB")
            output = io.BytesIO()
            if actual_type == "image/png":
                image.save(output, format="PNG", optimize=True)
                extension = "png"
            elif actual_type == "image/webp":
                image.save(output, format="WEBP", quality=86, method=4)
                extension = "webp"
            else:
                if image.mode == "RGBA":
                    background = Image.new("RGB", image.size, "white")
                    background.paste(image, mask=image.getchannel("A"))
                    image = background
                image.save(output, format="JPEG", quality=86, optimize=True, progressive=True)
                extension = "jpg"
    except InvalidImage:
        raise
    except (
        UnidentifiedImageError,
        OSError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
        ValueError,
    ) as exc:
        raise InvalidImage("Berkas tidak dapat dibaca sebagai gambar yang valid.") from exc

    result = output.getvalue()
    if not result or len(result) > MAX_CANONICAL_BYTES:
        raise InvalidImage("Ukuran hasil gambar melebihi batas.")
    return NormalizedImage(
        body=result,
        content_type=actual_type,
        extension=extension,
        width=image.width,
        height=image.height,
        sha256=hashlib.sha256(result).hexdigest(),
    )


def object_key(asset_id: UUID, stage: str, extension: str = "upload") -> str:
    return f"portfolio-media/{stage}/{asset_id.hex}.{extension}"
