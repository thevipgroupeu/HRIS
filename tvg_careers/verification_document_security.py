from __future__ import annotations

import hashlib
import io
import os
from dataclasses import dataclass

from django.core.exceptions import ValidationError
from PIL import Image, UnidentifiedImageError

from tvg_careers.document_security import inspect_candidate_document

ALLOWED_VERIFICATION_FORMATS = ("pdf", "jpg", "jpeg", "png")
IMAGE_CONTENT_TYPES = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
}


@dataclass(frozen=True)
class InspectedVerificationDocument:
    content: bytes
    sha256: str
    content_type: str
    extension: str


def inspect_verification_document(upload, *, allowed_formats, max_size_mb):
    """Validate bounded PDF/JPEG/PNG evidence without retaining it in logs."""

    formats = tuple(str(value).lower() for value in allowed_formats)
    if not formats or any(
        value not in ALLOWED_VERIFICATION_FORMATS for value in formats
    ):
        raise ValidationError("Verification upload formats are not configured safely.")
    max_bytes = min(int(max_size_mb), 5) * 1024 * 1024
    name = os.path.basename(getattr(upload, "name", ""))
    extension = os.path.splitext(name)[1].lower().lstrip(".")
    if extension not in formats:
        raise ValidationError("Upload a permitted PDF, JPG, JPEG or PNG document.")
    if getattr(upload, "size", 0) > max_bytes:
        raise ValidationError(
            f"Verification documents must be {max_size_mb} MB or smaller."
        )

    if extension == "pdf":
        inspected = inspect_candidate_document(
            upload,
            max_bytes=max_bytes,
            allowed_extensions=(".pdf",),
        )
        return InspectedVerificationDocument(
            content=inspected.content,
            sha256=inspected.sha256,
            content_type=inspected.content_type,
            extension=extension,
        )

    position = upload.tell() if hasattr(upload, "tell") else 0
    content = upload.read(max_bytes + 1)
    if hasattr(upload, "seek"):
        upload.seek(position)
    if len(content) > max_bytes:
        raise ValidationError(
            f"Verification documents must be {max_size_mb} MB or smaller."
        )
    supplied_type = str(getattr(upload, "content_type", "") or "").casefold()
    expected_type = IMAGE_CONTENT_TYPES[extension]
    if supplied_type and supplied_type not in {
        expected_type,
        "application/octet-stream",
    }:
        raise ValidationError("The file type does not match its image extension.")
    try:
        with Image.open(io.BytesIO(content)) as image:
            if image.width * image.height > 25_000_000:
                raise ValidationError("The image dimensions are too large.")
            image.verify()
            actual_format = str(image.format or "").casefold()
    except Image.DecompressionBombError as exc:
        raise ValidationError("The image dimensions are too large.") from exc
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ValidationError(
            "The uploaded file is not a valid image document."
        ) from exc
    expected_formats = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png"}
    if actual_format != expected_formats[extension]:
        raise ValidationError("The image content does not match its extension.")
    return InspectedVerificationDocument(
        content=content,
        sha256=hashlib.sha256(content).hexdigest(),
        content_type=expected_type,
        extension=extension,
    )
