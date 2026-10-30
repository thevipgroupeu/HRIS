from __future__ import annotations

import hashlib
from html import escape
from io import BytesIO

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer


def render_asset_acknowledgment(*, template, context):
    rendered_text = template.body.format_map(context)
    output = BytesIO()
    styles = getSampleStyleSheet()
    document = SimpleDocTemplate(
        output,
        pagesize=A4,
        leftMargin=24 * mm,
        rightMargin=24 * mm,
        topMargin=22 * mm,
        bottomMargin=22 * mm,
        title=f"Asset acknowledgment {context['asset_serial_number']}",
        author=context["company_name"],
        subject="Asset allocation acknowledgment",
    )
    story = [
        Paragraph(escape(template.name), styles["Heading1"]),
        Spacer(1, 8 * mm),
    ]
    for block in rendered_text.split("\n\n"):
        if block.strip():
            story.append(
                Paragraph(
                    escape(block.strip()).replace("\n", "<br/>"), styles["BodyText"]
                )
            )
    document.build(story)
    content = output.getvalue()
    return content, rendered_text, hashlib.sha256(content).hexdigest()
