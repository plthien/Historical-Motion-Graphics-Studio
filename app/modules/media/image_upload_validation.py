from __future__ import annotations

import hashlib
from io import BytesIO
from typing import Any

from app.api.errors import AppError

MAX_IMAGE_UPLOAD_BYTES = 10 * 1024 * 1024
SUPPORTED_IMAGE_UPLOAD_MIME_TYPES = {
    "image/jpeg",
    "image/png",
    "image/webp",
}
_UPLOAD_MIME_EXTENSIONS = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
}
_PIL_FORMAT_MIME_TYPES = {
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "WEBP": "image/webp",
}


def _normalize_content_type(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.split(";", 1)[0].strip().lower()
    return normalized or None


def _sniff_upload_image_mime(signature: bytes) -> str | None:
    if signature.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if signature.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if len(signature) >= 12 and signature[:4] == b"RIFF" and signature[8:12] == b"WEBP":
        return "image/webp"
    return None


def validate_uploaded_image(
    content: bytes,
    *,
    declared_content_type: str | None,
    error_prefix: str,
    image_label: str,
) -> dict[str, Any]:
    """Validate and normalize a user-uploaded still image.

    The domain owner supplies its error prefix and human-readable image label so
    Style Bible and Scene Assets share one byte-validation policy without
    sharing domain publication semantics.
    """
    if not content:
        raise AppError(
            f"{error_prefix}_EMPTY",
            f"Uploaded {image_label} must not be empty.",
            400,
            retryable=False,
        )
    if len(content) > MAX_IMAGE_UPLOAD_BYTES:
        raise AppError(
            f"{error_prefix}_TOO_LARGE",
            f"Uploaded {image_label} exceeds the maximum allowed size.",
            413,
            retryable=False,
            details={"max_bytes": MAX_IMAGE_UPLOAD_BYTES},
        )
    declared = _normalize_content_type(declared_content_type)
    if declared is not None and declared not in SUPPORTED_IMAGE_UPLOAD_MIME_TYPES:
        raise AppError(
            f"{error_prefix}_UNSUPPORTED_TYPE",
            f"Uploaded {image_label} must be JPEG, PNG, or WebP.",
            415,
            retryable=False,
        )
    signature_mime = _sniff_upload_image_mime(content[:16])
    if signature_mime not in SUPPORTED_IMAGE_UPLOAD_MIME_TYPES:
        raise AppError(
            f"{error_prefix}_UNSUPPORTED_TYPE",
            f"Uploaded {image_label} must be JPEG, PNG, or WebP.",
            415,
            retryable=False,
        )

    try:
        from PIL import Image, ImageOps, UnidentifiedImageError
    except ImportError as exc:  # pragma: no cover - supported runtime provides Pillow.
        raise AppError(
            f"{error_prefix}_VALIDATOR_UNAVAILABLE",
            "Image upload validation is not available in this runtime.",
            500,
            retryable=False,
        ) from exc

    try:
        with Image.open(BytesIO(content)) as image:
            image.load()
            image_format = str(image.format or "").upper()
            mime_type = _PIL_FORMAT_MIME_TYPES.get(image_format)
            if mime_type != signature_mime:
                raise AppError(
                    f"{error_prefix}_UNSUPPORTED_TYPE",
                    f"Uploaded {image_label} type does not match its content.",
                    415,
                    retryable=False,
                )
            if getattr(image, "is_animated", False) or int(getattr(image, "n_frames", 1) or 1) > 1:
                raise AppError(
                    f"{error_prefix}_ANIMATED_UNSUPPORTED",
                    f"Animated images are not supported for {image_label} uploads.",
                    415,
                    retryable=False,
                )
            width, height = image.size
            if width < 1 or height < 1:
                raise AppError(
                    f"{error_prefix}_DIMENSIONS_INVALID",
                    f"Uploaded {image_label} dimensions are invalid.",
                    400,
                    retryable=False,
                )
            normalized_image = ImageOps.exif_transpose(image)
            if image_format == "JPEG" and normalized_image.mode not in {"RGB", "L"}:
                normalized_image = normalized_image.convert("RGB")
            output = BytesIO()
            save_options: dict[str, Any] = {"format": image_format}
            if image_format in {"JPEG", "WEBP"}:
                save_options["quality"] = 95
            normalized_image.save(output, **save_options)
    except AppError:
        raise
    except UnidentifiedImageError as exc:
        raise AppError(
            f"{error_prefix}_DECODE_FAILED",
            f"Uploaded {image_label} file could not be decoded as an image.",
            415,
            retryable=False,
        ) from exc
    except Exception as exc:
        raise AppError(
            f"{error_prefix}_DECODE_FAILED",
            f"Uploaded {image_label} file could not be decoded as an image.",
            415,
            retryable=False,
        ) from exc

    normalized = output.getvalue()
    if not normalized or len(normalized) > MAX_IMAGE_UPLOAD_BYTES:
        raise AppError(
            f"{error_prefix}_TOO_LARGE",
            f"Uploaded {image_label} exceeds the maximum allowed size after validation.",
            413,
            retryable=False,
            details={"max_bytes": MAX_IMAGE_UPLOAD_BYTES},
        )
    if _sniff_upload_image_mime(normalized[:16]) != mime_type:
        raise AppError(
            f"{error_prefix}_DECODE_FAILED",
            f"Uploaded {image_label} could not be normalized safely.",
            415,
            retryable=False,
        )
    return {
        "content": normalized,
        "mime_type": mime_type,
        "sha256": hashlib.sha256(normalized).hexdigest(),
        "width": int(normalized_image.width),
        "height": int(normalized_image.height),
        "extension": _UPLOAD_MIME_EXTENSIONS[mime_type],
    }
