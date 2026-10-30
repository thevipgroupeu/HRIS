from __future__ import annotations

import uuid

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models.functions import Lower
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _

from base.horilla_company_manager import HorillaCompanyManager
from base.models import Company
from horilla.models import HorillaModel


class Venture(HorillaModel):
    """Recruiting brand operated by one legal Horilla Company."""

    FLUXOPS = "fluxops"
    FINDMYSTAY = "findmystay"
    THEVIPGROUP = "thevipgroup"

    legal_employer = models.ForeignKey(
        Company,
        on_delete=models.PROTECT,
        related_name="tvg_ventures",
        verbose_name=_("Legal Employer"),
    )
    name = models.CharField(max_length=80, verbose_name=_("Recruiting Brand"))
    slug = models.SlugField(max_length=80, verbose_name=_("Brand Slug"))
    summary = models.TextField(blank=True, verbose_name=_("Public Summary"))
    display_order = models.PositiveSmallIntegerField(default=0)

    objects = HorillaCompanyManager("legal_employer")
    all_objects = models.Manager()

    class Meta:
        ordering = ("display_order", "name")
        constraints = [
            models.UniqueConstraint(
                fields=("legal_employer", "slug"),
                name="tvg_core_unique_venture_slug_per_employer",
            ),
            models.UniqueConstraint(
                fields=("legal_employer", "name"),
                name="tvg_core_unique_venture_name_per_employer",
            ),
        ]
        verbose_name = _("Recruiting Venture")
        verbose_name_plural = _("Recruiting Ventures")

    def clean(self):
        super().clean()
        self.name = (self.name or "").strip()
        self.slug = slugify(self.slug or self.name)
        if not self.name:
            raise ValidationError({"name": _("Recruiting brand is required.")})
        if not self.slug:
            raise ValidationError({"slug": _("A valid brand slug is required.")})

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.name} ({self.legal_employer})"


class Person(HorillaModel):
    """Stable identity spanning independent Candidate applications and Employee."""

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    legal_employer = models.ForeignKey(
        Company,
        on_delete=models.PROTECT,
        related_name="tvg_people",
        verbose_name=_("Legal Employer"),
    )
    primary_email = models.EmailField(verbose_name=_("Primary Email"))
    preferred_name = models.CharField(
        max_length=100,
        blank=True,
        verbose_name=_("Preferred Name"),
    )
    employee = models.OneToOneField(
        "employee.Employee",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="tvg_person_identity",
        verbose_name=_("Employee Record"),
    )

    objects = HorillaCompanyManager("legal_employer")
    all_objects = models.Manager()

    class Meta:
        ordering = ("primary_email",)
        constraints = [
            models.UniqueConstraint(
                Lower("primary_email"),
                "legal_employer",
                name="tvg_core_unique_person_email_per_employer",
            )
        ]
        permissions = (("link_person_employee", "Link person identity to employee"),)
        verbose_name = _("Person Identity")
        verbose_name_plural = _("Person Identities")

    def clean(self):
        super().clean()
        self.primary_email = (self.primary_email or "").strip().casefold()
        self.preferred_name = (self.preferred_name or "").strip()
        if self.employee_id and self.legal_employer_id:
            employee_company = self.employee.get_company()
            if not employee_company or employee_company.pk != self.legal_employer_id:
                raise ValidationError(
                    {
                        "employee": _(
                            "Employee record must belong to the same legal employer."
                        )
                    }
                )

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return self.preferred_name or self.primary_email
