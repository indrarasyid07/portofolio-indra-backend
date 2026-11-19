"""Image byte validation and metadata stripping tests."""

import io

import pytest
from PIL import Image

from app.services.media import InvalidImage, normalize_image


def image_bytes(fmt: str = "PNG") -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (12, 8), (15, 90, 42)).save(output, format=fmt)
    return output.getvalue()


@pytest.mark.parametrize(
    ("fmt", "content_type", "extension"),
    [("PNG", "image/png", "png"), ("JPEG", "image/jpeg", "jpg"), ("WEBP", "image/webp", "webp")],
)
def test_valid_raster_images_are_reencoded(fmt: str, content_type: str, extension: str) -> None:
    result = normalize_image(image_bytes(fmt), content_type, len(image_bytes(fmt)))
    assert result.content_type == content_type
    assert result.extension == extension
    assert (result.width, result.height) == (12, 8)
    assert len(result.sha256) == 64
    with Image.open(io.BytesIO(result.body)) as decoded:
        assert decoded.size == (12, 8)


def test_rejects_mime_spoof_oversize_bad_bytes_and_animated_images() -> None:
    png = image_bytes()
    with pytest.raises(InvalidImage):
        normalize_image(png, "image/jpeg", len(png))
    with pytest.raises(InvalidImage):
        normalize_image(png, "image/png", len(png) + 1)
    with pytest.raises(InvalidImage):
        normalize_image(b"<svg></svg>", "image/png", 11)
    frames = [Image.new("RGB", (4, 4), color) for color in ("red", "blue")]
    output = io.BytesIO()
    frames[0].save(output, format="GIF", save_all=True, append_images=frames[1:])
    gif = output.getvalue()
    with pytest.raises(InvalidImage):
        normalize_image(gif, "image/png", len(gif))
