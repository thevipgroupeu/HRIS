"""Deterministic, no-network PDF rendering for approved acceptance letters."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from html import escape
from io import BytesIO
from pathlib import Path

import reportlab
from django.core.exceptions import ValidationError
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer


@dataclass(frozen=True)
class RenderedAcceptanceLetter:
    content: bytes
    sha256: str
    rendered_text: str


def _font_name():
    """Use ReportLab's bundled Unicode font; never depend on host fonts."""

    name = "TVG-Vera"
    if name not in pdfmetrics.getRegisteredFontNames():
        path = Path(reportlab.__file__).resolve().parent / "fonts" / "Vera.ttf"
        if not path.exists():
            raise ValidationError("The bundled PDF font is unavailable.")
        pdfmetrics.registerFont(TTFont(name, str(path)))
    return name


def render_acceptance_letter(*, template, context):
    """Render a previously approved plain-text template into a PDF byte stream."""

    try:
        rendered_text = template.body.format_map(context)
    except (KeyError, ValueError) as exc:
        raise ValidationError(
            "The approved acceptance template contains an invalid placeholder."
        ) from exc

    font_name = _font_name()
    styles = getSampleStyleSheet()
    normal = ParagraphStyle(
        "TVGAcceptanceNormal",
        parent=styles["BodyText"],
        fontName=font_name,
        fontSize=10.5,
        leading=15,
        spaceAfter=8,
    )
    heading = ParagraphStyle(
        "TVGAcceptanceHeading",
        parent=styles["Heading1"],
        fontName=font_name,
        fontSize=17,
        leading=22,
        spaceAfter=16,
    )
    metadata = ParagraphStyle(
        "TVGAcceptanceMetadata",
        parent=normal,
        alignment=TA_RIGHT,
        fontSize=9,
        leading=12,
    )

    output = BytesIO()
    document = SimpleDocTemplate(
        output,
        pagesize=A4,
        leftMargin=24 * mm,
        rightMargin=24 * mm,
        topMargin=22 * mm,
        bottomMargin=22 * mm,
        title=f"Acceptance letter {context['letter_number']}",
        author=context["legal_entity_name"],
        subject="Erasmus+ internship acceptance arrangements",
    )

    def paragraph(value, style=normal):
        return Paragraph(escape(str(value)).replace("\n", "<br/>"), style)

    story = [
        paragraph(context["legal_entity_name"], heading),
        paragraph(
            f"Letter number: {context['letter_number']}<br/>"
            f"Issue date: {context['issue_date']}",
            metadata,
        ),
        Spacer(1, 8 * mm),
    ]
    for block in rendered_text.split("\n\n"):
        if block.strip():
            story.append(paragraph(block.strip()))
    story.extend(
        [
            Spacer(1, 10 * mm),
            paragraph(context["authorized_signatory_name"]),
            paragraph(context["authorized_signatory_title"]),
            Spacer(1, 8 * mm),
            paragraph(context["legal_address"], metadata),
            paragraph(context["registration_details"], metadata),
        ]
    )
    document.build(story)
    content = output.getvalue()
    return RenderedAcceptanceLetter(
        content=content,
        sha256=hashlib.sha256(content).hexdigest(),
        rendered_text=rendered_text,
    )
