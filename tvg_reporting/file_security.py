from __future__ import annotations

import hashlib
import io
import os
import zipfile
from dataclasses import dataclass

from django.core.exceptions import ValidationError

PDF_TYPE = "application/pdf"
PPTX_TYPE = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
GENERIC_TYPES = {"", "application/octet-stream", "application/zip"}
BLOCKED_PDF_MARKERS = (
    b"/JavaScript",
    b"/JS",
    b"/Launch",
    b"/EmbeddedFile",
    b"/RichMedia",
)
BLOCKED_PPTX_MARKERS = (
    "vbaproject.bin",
    "activex/",
    "embeddings/",
    "oleobject",
    "customui/",
)


@dataclass(frozen=True)
class InspectedPresentation:
    content: bytes
    sha256: str
    content_type: str
    original_filename: str


def inspect_presentation(upload, *, max_bytes=20 * 1024 * 1024):
    filename = os.path.basename(getattr(upload, "name", ""))
    extension = os.path.splitext(filename)[1].casefold()
    if extension not in {".pdf", ".pptx"}:
        raise ValidationError("Upload a PDF or PPTX presentation.")
    if getattr(upload, "size", 0) > max_bytes:
        raise ValidationError("Presentations must be 20 MB or smaller.")
    position = upload.tell() if hasattr(upload, "tell") else 0
    content = upload.read(max_bytes + 1)
    if hasattr(upload, "seek"):
        upload.seek(position)
    if len(content) > max_bytes:
        raise ValidationError("Presentations must be 20 MB or smaller.")
    supplied_type = str(getattr(upload, "content_type", "") or "").casefold()
    if extension == ".pdf":
        if supplied_type not in GENERIC_TYPES | {PDF_TYPE}:
            raise ValidationError("The file type does not match its PDF extension.")
        if not content.startswith(b"%PDF-") or b"%%EOF" not in content[-2048:]:
            raise ValidationError("The uploaded file is not a valid PDF.")
        if any(marker in content for marker in BLOCKED_PDF_MARKERS):
            raise ValidationError("This PDF contains unsupported active content.")
        content_type = PDF_TYPE
    else:
        if supplied_type not in GENERIC_TYPES | {PPTX_TYPE}:
            raise ValidationError("The file type does not match its PPTX extension.")
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                entries = archive.infolist()
                if len(entries) > 2000:
                    raise ValidationError("This PPTX contains too many components.")
                names = set()
                uncompressed = 0
                for entry in entries:
                    name = entry.filename.replace("\\", "/")
                    normalized = name.casefold()
                    if name.startswith("/") or ".." in name.split("/"):
                        raise ValidationError("This PPTX contains an unsafe path.")
                    if entry.flag_bits & 0x1:
                        raise ValidationError("Encrypted PPTX files are not supported.")
                    if any(marker in normalized for marker in BLOCKED_PPTX_MARKERS):
                        raise ValidationError(
                            "This PPTX contains unsupported active content."
                        )
                    uncompressed += entry.file_size
                    if uncompressed > 80 * 1024 * 1024:
                        raise ValidationError(
                            "This PPTX expands beyond the safe limit."
                        )
                    names.add(normalized)
                if (
                    "[content_types].xml" not in names
                    or "ppt/presentation.xml" not in names
                ):
                    raise ValidationError("The uploaded file is not a valid PPTX.")
                content_types = archive.read("[Content_Types].xml")
                if b"macroEnabled" in content_types or b"vbaProject" in content_types:
                    raise ValidationError(
                        "Macro-enabled presentations are not supported."
                    )
        except (zipfile.BadZipFile, KeyError, RuntimeError) as exc:
            raise ValidationError("The uploaded file is not a valid PPTX.") from exc
        content_type = PPTX_TYPE
    return InspectedPresentation(
        content=content,
        sha256=hashlib.sha256(content).hexdigest(),
        content_type=content_type,
        original_filename=filename,
    )
