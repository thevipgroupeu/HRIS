from __future__ import annotations

import uuid
from string import Formatter
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from base.horilla_company_manager import HorillaCompanyManager
from employee.models import Employee
from horilla.models import HorillaModel
from recruitment.models import InterviewSchedule, Recruitment, Stage
from tvg_careers.models import Application

COMPANY_TIME_ZONE = "Europe/Warsaw"


def validate_iana_timezone(value):
    try:
        ZoneInfo(value)
    except (TypeError, ZoneInfoNotFoundError) as exc:
        raise ValidationError(
            _("Use a valid IANA timezone, for example Europe/Paris.")
        ) from exc


class InterviewType(HorillaModel):
    class LocationMode(models.TextChoices):
        VIDEO = "video", _("Video call")
        PHONE = "phone", _("Phone")
        ONSITE = "onsite", _("On-site")

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    recruitment = models.ForeignKey(
        Recruitment,
        on_delete=models.PROTECT,
        related_name="tvg_interview_types",
    )
    code = models.SlugField(max_length=50)
    name = models.CharField(max_length=120)
    location_mode = models.CharField(
        max_length=16,
        choices=LocationMode.choices,
        default=LocationMode.VIDEO,
    )
    duration_minutes = models.PositiveSmallIntegerField(
        default=45,
        validators=[MinValueValidator(15), MaxValueValidator(240)],
    )
    provider_key = models.SlugField(
        max_length=50,
        default="zoho_bookings",
        help_text=_("Configured calendar provider key, such as zoho_bookings."),
    )
    instructions = models.TextField(blank=True)
    default_interviewers = models.ManyToManyField(
        Employee,
        blank=True,
        related_name="tvg_default_interview_types",
    )
    interview_stage = models.ForeignKey(
        Stage,
        on_delete=models.PROTECT,
        related_name="tvg_interview_type_entries",
    )
    selected_stage = models.ForeignKey(
        Stage,
        on_delete=models.PROTECT,
        related_name="tvg_selected_interview_types",
    )
    rejected_stage = models.ForeignKey(
        Stage,
        on_delete=models.PROTECT,
        related_name="tvg_rejected_interview_types",
    )
    hold_stage = models.ForeignKey(
        Stage,
        on_delete=models.PROTECT,
        related_name="tvg_hold_interview_types",
    )
    candidate_reschedule_limit = models.PositiveSmallIntegerField(
        default=1,
        validators=[MaxValueValidator(5)],
    )
    reschedule_notice_hours = models.PositiveSmallIntegerField(
        default=12,
        validators=[MinValueValidator(1), MaxValueValidator(168)],
    )

    objects = HorillaCompanyManager("recruitment__company_id")
    all_objects = models.Manager()

    class Meta:
        ordering = ("recruitment", "name")
        constraints = [
            models.UniqueConstraint(
                fields=("recruitment", "code"),
                name="tvg_interviews_unique_type_code",
            )
        ]
        permissions = (
            ("configure_interview_type", "Configure interview types"),
            ("invite_candidate", "Invite a verified candidate to interview"),
            ("manage_interview", "Manage candidate interviews"),
            ("record_feedback", "Record structured interview feedback"),
            ("record_decision", "Record a human recruitment decision"),
            ("override_reschedule_policy", "Override interview reschedule policy"),
        )

    def clean(self):
        super().clean()
        errors = {}
        stages = {
            "interview_stage": (self.interview_stage, {"interview"}),
            "selected_stage": (self.selected_stage, {"hired", "interview", "test"}),
            "rejected_stage": (self.rejected_stage, {"cancelled"}),
            "hold_stage": (
                self.hold_stage,
                {"applied", "initial", "test", "interview"},
            ),
        }
        for field, (stage, allowed_types) in stages.items():
            if stage and stage.recruitment_id_id != self.recruitment_id:
                errors[field] = _("The stage must belong to this recruitment.")
            elif stage and stage.stage_type not in allowed_types:
                errors[field] = _("The selected stage has an incompatible stage type.")
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.recruitment} - {self.name}"


class InterviewInvitation(HorillaModel):
    class Status(models.TextChoices):
        INVITED = "invited", _("Invited")
        BOOKED = "booked", _("Booked")
        COMPLETED = "completed", _("Completed")
        CANCELLED = "cancelled", _("Cancelled")
        EXPIRED = "expired", _("Expired")

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    application = models.ForeignKey(
        Application,
        on_delete=models.PROTECT,
        related_name="interview_invitations",
    )
    interview_type = models.ForeignKey(
        InterviewType,
        on_delete=models.PROTECT,
        related_name="invitations",
    )
    interviewers = models.ManyToManyField(
        Employee,
        related_name="tvg_interview_invitations",
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.INVITED,
        db_index=True,
    )
    company_timezone = models.CharField(
        max_length=64,
        default=COMPANY_TIME_ZONE,
        validators=[validate_iana_timezone],
    )
    candidate_timezone = models.CharField(
        max_length=64,
        default=COMPANY_TIME_ZONE,
        validators=[validate_iana_timezone],
    )
    instructions_snapshot = models.TextField(blank=True)
    provider_key_snapshot = models.SlugField(max_length=50)
    external_invitation_reference = models.CharField(max_length=200, blank=True)
    invited_by = models.ForeignKey(
        Employee,
        on_delete=models.PROTECT,
        related_name="tvg_interview_invitations_sent",
    )
    invited_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)

    objects = HorillaCompanyManager("application__opening__venture__legal_employer")
    all_objects = models.Manager()

    class Meta:
        ordering = ("-invited_at",)
        constraints = [
            models.UniqueConstraint(
                fields=("application", "interview_type"),
                condition=Q(status__in=("invited", "booked")),
                name="tvg_interviews_one_active_invitation",
            )
        ]

    def clean(self):
        super().clean()
        errors = {}
        if self.application_id and self.interview_type_id:
            if (
                self.application.opening.recruitment_id
                != self.interview_type.recruitment_id
            ):
                errors["interview_type"] = _(
                    "Interview type must match the application recruitment."
                )
        if self.expires_at and self.expires_at <= self.invited_at:
            errors["expires_at"] = _("Expiry must be after the invitation time.")
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f"{self.application} - {self.interview_type.name}"


class InterviewSlot(HorillaModel):
    class Status(models.TextChoices):
        AVAILABLE = "available", _("Available")
        BOOKED = "booked", _("Booked")
        CANCELLED = "cancelled", _("Cancelled")

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    invitation = models.ForeignKey(
        InterviewInvitation,
        on_delete=models.CASCADE,
        related_name="slots",
    )
    start_at = models.DateTimeField()
    end_at = models.DateTimeField()
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.AVAILABLE,
        db_index=True,
    )
    external_slot_reference = models.CharField(max_length=200, blank=True)

    objects = HorillaCompanyManager(
        "invitation__application__opening__venture__legal_employer"
    )
    all_objects = models.Manager()

    class Meta:
        ordering = ("start_at",)
        constraints = [
            models.UniqueConstraint(
                fields=("invitation", "start_at"),
                name="tvg_interviews_unique_invitation_slot",
            ),
            models.CheckConstraint(
                condition=Q(end_at__gt=models.F("start_at")),
                name="tvg_interviews_slot_end_after_start",
            ),
        ]

    def clean(self):
        super().clean()
        if self.start_at and self.end_at and self.end_at <= self.start_at:
            raise ValidationError({"end_at": _("Slot end must be after its start.")})
        if self.invitation_id and self.start_at and self.end_at:
            expected_seconds = self.invitation.interview_type.duration_minutes * 60
            if int((self.end_at - self.start_at).total_seconds()) != expected_seconds:
                raise ValidationError(
                    {"end_at": _("Slot duration must match the interview type.")}
                )

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.invitation} at {self.start_at.isoformat()}"


class InterviewBooking(HorillaModel):
    class Status(models.TextChoices):
        BOOKED = "booked", _("Booked")
        COMPLETED = "completed", _("Completed")
        CANCELLED = "cancelled", _("Cancelled")

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    invitation = models.OneToOneField(
        InterviewInvitation,
        on_delete=models.PROTECT,
        related_name="booking",
    )
    slot = models.OneToOneField(
        InterviewSlot,
        on_delete=models.PROTECT,
        related_name="booking",
    )
    native_schedule = models.OneToOneField(
        InterviewSchedule,
        on_delete=models.PROTECT,
        related_name="tvg_booking",
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.BOOKED,
        db_index=True,
    )
    candidate_timezone = models.CharField(
        max_length=64,
        validators=[validate_iana_timezone],
    )
    meeting_link = models.URLField(max_length=500, blank=True)
    provider_booking_reference = models.CharField(max_length=200)
    candidate_reschedule_count = models.PositiveSmallIntegerField(default=0)
    booked_at = models.DateTimeField(default=timezone.now)
    rescheduled_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)

    objects = HorillaCompanyManager(
        "invitation__application__opening__venture__legal_employer"
    )
    all_objects = models.Manager()

    class Meta:
        ordering = ("-slot__start_at",)

    def clean(self):
        super().clean()
        if (
            self.slot_id
            and self.invitation_id
            and self.slot.invitation_id != self.invitation_id
        ):
            raise ValidationError(
                {"slot": _("The slot must belong to the invitation.")}
            )

    def __str__(self):
        return f"{self.invitation} - {self.get_status_display()}"


class InterviewFeedback(HorillaModel):
    class Recommendation(models.TextChoices):
        NO_RECOMMENDATION = "none", _("No recommendation")
        SELECTED = "selected", _("Recommend selected")
        REJECTED = "rejected", _("Recommend rejected")
        HOLD = "hold", _("Recommend on hold")

    booking = models.ForeignKey(
        InterviewBooking,
        on_delete=models.PROTECT,
        related_name="feedback_entries",
    )
    interviewer = models.ForeignKey(
        Employee,
        on_delete=models.PROTECT,
        related_name="tvg_interview_feedback",
    )
    competency_ratings = models.JSONField(default=dict, blank=True)
    strengths = models.TextField(blank=True)
    concerns = models.TextField(blank=True)
    private_notes = models.TextField(blank=True)
    recommendation = models.CharField(
        max_length=16,
        choices=Recommendation.choices,
        default=Recommendation.NO_RECOMMENDATION,
    )
    submitted_at = models.DateTimeField(default=timezone.now)

    objects = HorillaCompanyManager(
        "booking__invitation__application__opening__venture__legal_employer"
    )
    all_objects = models.Manager()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("booking", "interviewer"),
                name="tvg_interviews_one_feedback_per_interviewer",
            )
        ]

    def clean(self):
        super().clean()
        if not isinstance(self.competency_ratings, dict):
            raise ValidationError(
                {"competency_ratings": _("Ratings must be an object.")}
            )
        if len(self.competency_ratings) > 20:
            raise ValidationError(
                {"competency_ratings": _("Use at most 20 competency ratings.")}
            )
        cleaned = {}
        for name, rating in self.competency_ratings.items():
            name = str(name).strip()[:80]
            if (
                not name
                or not isinstance(rating, int)
                or isinstance(rating, bool)
                or rating not in range(1, 6)
            ):
                raise ValidationError(
                    {
                        "competency_ratings": _(
                            "Each rating must be an integer from 1 to 5."
                        )
                    }
                )
            cleaned[name] = rating
        self.competency_ratings = cleaned
        if (
            self.booking_id
            and self.interviewer_id
            and not self.booking.invitation.interviewers.filter(
                pk=self.interviewer_id
            ).exists()
        ):
            raise ValidationError(
                {"interviewer": _("Feedback is limited to assigned interviewers.")}
            )

    def save(self, *args, **kwargs):
        if self.pk and type(self).all_objects.filter(pk=self.pk).exists():
            raise ValidationError(_("Submitted interview feedback is immutable."))
        self.full_clean()
        return super().save(*args, **kwargs)


class DecisionNotificationTemplate(HorillaModel):
    class Outcome(models.TextChoices):
        SELECTED = "selected", _("Selected")
        REJECTED = "rejected", _("Rejected")
        HOLD = "hold", _("On hold")

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    recruitment = models.ForeignKey(
        Recruitment,
        on_delete=models.PROTECT,
        related_name="tvg_decision_templates",
    )
    name = models.CharField(max_length=120)
    outcome = models.CharField(max_length=16, choices=Outcome.choices)
    subject = models.CharField(max_length=200)
    body = models.TextField(
        help_text=_(
            "Allowed placeholders: candidate_name, role_title and venture_name."
        ),
    )
    approved = models.BooleanField(default=False)
    approved_by = models.ForeignKey(
        Employee,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="tvg_approved_decision_templates",
    )
    approved_at = models.DateTimeField(null=True, blank=True)

    objects = HorillaCompanyManager("recruitment__company_id")
    all_objects = models.Manager()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("recruitment", "name"),
                name="tvg_interviews_unique_decision_template",
            )
        ]
        permissions = (("approve_decision_template", "Approve decision templates"),)

    def clean(self):
        super().clean()
        allowed = {"candidate_name", "role_title", "venture_name"}
        for field_name in ("subject", "body"):
            try:
                placeholders = {
                    name
                    for _literal, name, _spec, _conversion in Formatter().parse(
                        getattr(self, field_name)
                    )
                    if name
                }
            except ValueError as exc:
                raise ValidationError(
                    {field_name: _("Template placeholders are malformed.")}
                ) from exc
            if placeholders - allowed:
                raise ValidationError(
                    {
                        field_name: _(
                            "Use only candidate_name, role_title and venture_name placeholders."
                        )
                    }
                )
        if self.pk:
            original = type(self).all_objects.filter(pk=self.pk).first()
            protected_fields = (
                "recruitment_id",
                "name",
                "outcome",
                "subject",
                "body",
                "approved",
                "approved_by_id",
                "approved_at",
            )
            if (
                original
                and original.approved
                and any(
                    getattr(original, field) != getattr(self, field)
                    for field in protected_fields
                )
            ):
                raise ValidationError(
                    _(
                        "Approved decision templates are immutable; deactivate this template and create a new version."
                    )
                )

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.recruitment} - {self.name}"


class InterviewDecision(HorillaModel):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    booking = models.OneToOneField(
        InterviewBooking,
        on_delete=models.PROTECT,
        related_name="decision",
    )
    outcome = models.CharField(
        max_length=16,
        choices=DecisionNotificationTemplate.Outcome.choices,
    )
    decided_by = models.ForeignKey(
        Employee,
        on_delete=models.PROTECT,
        related_name="tvg_interview_decisions",
    )
    template = models.ForeignKey(
        DecisionNotificationTemplate,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="decisions",
    )
    notification_subject = models.CharField(max_length=200)
    notification_message = models.TextField()
    internal_reason = models.TextField(blank=True)
    resulting_stage = models.ForeignKey(
        Stage,
        on_delete=models.PROTECT,
        related_name="tvg_interview_decisions",
    )
    decided_at = models.DateTimeField(default=timezone.now)

    objects = HorillaCompanyManager(
        "booking__invitation__application__opening__venture__legal_employer"
    )
    all_objects = models.Manager()

    class Meta:
        ordering = ("-decided_at",)

    def save(self, *args, **kwargs):
        if self.pk and type(self).all_objects.filter(pk=self.pk).exists():
            raise ValidationError(_("Interview decisions are immutable."))
        return super().save(*args, **kwargs)


class InterviewEvent(HorillaModel):
    class ActorType(models.TextChoices):
        STAFF = "staff", _("Staff")
        CANDIDATE = "candidate", _("Candidate")
        SYSTEM = "system", _("System")

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    invitation = models.ForeignKey(
        InterviewInvitation,
        on_delete=models.PROTECT,
        related_name="events",
    )
    event_type = models.CharField(max_length=40)
    actor_type = models.CharField(max_length=16, choices=ActorType.choices)
    actor_employee = models.ForeignKey(
        Employee,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="tvg_interview_events",
    )
    actor_person_public_id = models.UUIDField(null=True, blank=True)
    payload = models.JSONField(default=dict, blank=True)
    occurred_at = models.DateTimeField(default=timezone.now)

    objects = HorillaCompanyManager(
        "invitation__application__opening__venture__legal_employer"
    )
    all_objects = models.Manager()

    class Meta:
        ordering = ("occurred_at",)

    def save(self, *args, **kwargs):
        if self.pk and type(self).all_objects.filter(pk=self.pk).exists():
            raise ValidationError(_("Interview events are immutable."))
        return super().save(*args, **kwargs)


class InterviewNotification(HorillaModel):
    class EventType(models.TextChoices):
        INVITATION = "invitation", _("Interview invitation")
        BOOKING_CONFIRMATION = "booking_confirmation", _("Booking confirmation")
        REMINDER = "reminder", _("Interview reminder")
        RESCHEDULE_CONFIRMATION = "reschedule_confirmation", _(
            "Reschedule confirmation"
        )
        CANCELLATION = "cancellation", _("Cancellation")
        DECISION = "decision", _("Decision notification")

    class Audience(models.TextChoices):
        CANDIDATE = "candidate", _("Candidate")
        INTERVIEWER = "interviewer", _("Interviewer")
        HR = "hr", _("HR")

    event_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    invitation = models.ForeignKey(
        InterviewInvitation,
        on_delete=models.PROTECT,
        related_name="notifications",
    )
    event_type = models.CharField(max_length=32, choices=EventType.choices)
    audience = models.CharField(max_length=16, choices=Audience.choices)
    recipient_employee = models.ForeignKey(
        Employee,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="tvg_interview_notifications",
    )
    idempotency_key = models.CharField(max_length=240, unique=True)
    subject = models.CharField(max_length=200)
    message = models.TextField()
    payload = models.JSONField(default=dict, blank=True)
    occurred_at = models.DateTimeField(default=timezone.now)
    published_at = models.DateTimeField(null=True, blank=True)
    delivery_attempts = models.PositiveIntegerField(default=0)
    last_error = models.CharField(max_length=500, blank=True)

    objects = HorillaCompanyManager(
        "invitation__application__opening__venture__legal_employer"
    )
    all_objects = models.Manager()

    class Meta:
        ordering = ("occurred_at",)

    def save(self, *args, **kwargs):
        if self.pk and type(self).all_objects.filter(pk=self.pk).exists():
            immutable = {
                "invitation_id",
                "event_type",
                "audience",
                "recipient_employee_id",
                "idempotency_key",
                "subject",
                "message",
                "payload",
                "occurred_at",
            }
            prior = type(self).all_objects.values(*immutable).get(pk=self.pk)
            if any(prior[field] != getattr(self, field) for field in immutable):
                raise ValidationError(_("Published notification content is immutable."))
        return super().save(*args, **kwargs)
