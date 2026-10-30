from __future__ import annotations

from uuid import uuid4

from tvg_core.models import Person, Venture


def make_venture(*, company, name="Synthetic Venture", slug=None, **overrides):
    suffix = uuid4().hex[:8]
    values = {
        "legal_employer": company,
        "name": f"{name} {suffix}" if slug is None else name,
        "slug": slug or f"synthetic-venture-{suffix}",
        "summary": "Synthetic recruiting brand used only by automated tests.",
    }
    values.update(overrides)
    return Venture.all_objects.create(**values)


def make_person(
    *,
    company,
    email=None,
    preferred_name="Synthetic Person",
    employee=None,
    **overrides,
):
    suffix = uuid4().hex[:8]
    values = {
        "legal_employer": company,
        "primary_email": email or f"person-{suffix}@example.test",
        "preferred_name": preferred_name,
        "employee": employee,
    }
    values.update(overrides)
    return Person.all_objects.create(**values)
