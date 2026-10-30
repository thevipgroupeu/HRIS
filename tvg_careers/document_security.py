"""Small, dependency-free checks for candidate PDF and DOCX uploads.

These checks are deliberately shared by the isolated CareerHub process and
the private PeopleOps receiver.  They are defence in depth, not a replacement
for malware scanning at the storage boundary.
"""

from __future__ import annotations

import hashlib
import io
import os
import zipfile
from dataclasses import dataclass

from django.core.exceptions import ValidationError

PDF_CONTENT_TYPE = "application/pdf"
DOCX_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)
GENERIC_CONTENT_TYPE = "application/octet-stream"
BLOCKED_PDF_MARKERS = (
    b"/JavaScript",
    b"/JS",
    b"/Launch",
    b"/EmbeddedFile",
    b"/RichMedia",
)
BLOCKED_DOCX_PATH_MARKERS = (
    "vbaproject.bin",
    "activex/",
    "embeddings/",
    "oleobject",
    "customui/",
)


@dataclass(frozen=True)
class InspectedCandidateDocument:
    content: bytes
    sha256: str
    content_type: str
    extension: str


def _inspect_pdf(content):
    if not content.startswith(b"%PDF-") or b"%%EOF" not in content[-2048:]:
        raise ValidationError("The uploaded file is not a valid PDF document.")
    if any(marker in content for marker in BLOCKED_PDF_MARKERS):
        raise ValidationError("This PDF contains unsupported active content.")


def _inspect_docx(content):
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            entries = archive.infolist()
            if len(entries) > 1000:
                raise ValidationError("This DOCX contains too many components.")
            total_uncompressed = 0
            normalized_names = set()
            for entry in entries:
                name = entry.filename.replace("\\", "/")
                normalized = name.casefold()
                if name.startswith("/") or ".." in name.split("/"):
                    raise ValidationError("This DOCX contains an unsafe path.")
                if entry.flag_bits & 0x1:
                    raise ValidationError("Encrypted DOCX files are not supported.")
                if any(marker in normalized for marker in BLOCKED_DOCX_PATH_MARKERS):
                    raise ValidationError(
                        "This DOCX contains unsupported active content."
                    )
                total_uncompressed += entry.file_size
                if total_uncompressed > 25 * 1024 * 1024:
                    raise ValidationError("This DOCX expands beyond the safe limit.")
                normalized_names.add(normalized)
            if (
                "[content_types].xml" not in normalized_names
                or "word/document.xml" not in normalized_names
            ):
                raise ValidationError("The uploaded file is not a valid DOCX document.")
            content_types = archive.read("[Content_Types].xml")
            if b"macroEnabled" in content_types or b"vbaProject" in content_types:
                raise ValidationError("Macro-enabled documents are not supported.")
    except (zipfile.BadZipFile, KeyError, RuntimeError) as exc:
        raise ValidationError(
            "The uploaded file is not a valid DOCX document."
        ) from exc


def inspect_candidate_document(
    upload,
    *,
    max_bytes=5 * 1024 * 1024,
    allowed_extensions=(".pdf", ".docx"),
):
    """Validate a CV/cover-letter file without retaining its contents in logs."""

    name = os.path.basename(getattr(upload, "name", ""))
    extension = os.path.splitext(name)[1].lower()
    if extension not in allowed_extensions:
        raise ValidationError("Upload a PDF or DOCX file.")
    size = getattr(upload, "size", None)
    if size is not None and size > max_bytes:
        raise ValidationError("Candidate documents must be 5 MB or smaller.")

    position = upload.tell() if hasattr(upload, "tell") else 0
    content = upload.read(max_bytes + 1)
    if hasattr(upload, "seek"):
        upload.seek(position)
    if len(content) > max_bytes:
        raise ValidationError("Candidate documents must be 5 MB or smaller.")

    supplied_type = str(getattr(upload, "content_type", "") or "").casefold()
    if extension == ".pdf":
        if supplied_type and supplied_type not in {
            PDF_CONTENT_TYPE,
            GENERIC_CONTENT_TYPE,
        }:
            raise ValidationError("The file type does not match its PDF extension.")
        _inspect_pdf(content)
        content_type = PDF_CONTENT_TYPE
    else:
        if supplied_type and supplied_type not in {
            DOCX_CONTENT_TYPE,
            GENERIC_CONTENT_TYPE,
            "application/zip",
        }:
            raise ValidationError("The file type does not match its DOCX extension.")
        _inspect_docx(content)
        content_type = DOCX_CONTENT_TYPE
    return InspectedCandidateDocument(
        content=content,
        sha256=hashlib.sha256(content).hexdigest(),
        content_type=content_type,
        extension=extension,
    )


def inspect_candidate_pdf(upload, *, max_bytes=5 * 1024 * 1024):
    """Validate and return ``(content, sha256)`` without retaining PII in logs."""

    result = inspect_candidate_document(
        upload,
        max_bytes=max_bytes,
        allowed_extensions=(".pdf",),
    )
    return result.content, result.sha256
