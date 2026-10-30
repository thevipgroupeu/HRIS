from __future__ import annotations

from datetime import timedelta

from django.utils import timezone

from recruitment.models import Stage
from tvg_interviews.models import InterviewType


def make_interview_type(*, opening, interviewers=(), code="synthetic-interview"):
    recruitment = opening.recruitment

    def stage(name, stage_type, sequence):
        row, _created = Stage.objects.get_or_create(
            recruitment_id=recruitment,
            stage=name,
            defaults={"stage_type": stage_type, "sequence": sequence},
        )
        return row

    interview_type = InterviewType.all_objects.create(
        recruitment=recruitment,
        code=code,
        name="Synthetic structured interview",
        duration_minutes=45,
        provider_key="simulated",
        instructions="Join the synthetic video call five minutes early.",
        interview_stage=stage("Synthetic Interview", "interview", 20),
        selected_stage=stage("Synthetic Selected", "hired", 40),
        rejected_stage=stage("Synthetic Rejected", "cancelled", 50),
        hold_stage=stage("Synthetic Hold", "test", 30),
    )
    interview_type.default_interviewers.set(interviewers)
    return interview_type


def future_slots(*hours):
    now = timezone.now().replace(second=0, microsecond=0)
    return [now + timedelta(hours=value) for value in hours]
