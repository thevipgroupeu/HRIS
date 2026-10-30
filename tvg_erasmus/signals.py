from django.core.exceptions import ValidationError
from django.db.models.signals import pre_save
from django.dispatch import receiver

from recruitment.models import Candidate, InterviewSchedule, Stage
from tvg_erasmus.services import validate_candidate_interview_eligibility


def _enforce(candidate):
    try:
        validate_candidate_interview_eligibility(candidate)
    except ValidationError as exc:
        raise ValidationError({"candidate_id": exc.messages[0]}) from exc


@receiver(pre_save, sender=InterviewSchedule)
def prevent_ineligible_interview(sender, instance, raw=False, **kwargs):
    if not raw:
        _enforce(instance.candidate_id)


@receiver(pre_save, sender=Candidate)
def prevent_ineligible_interview_stage(sender, instance, raw=False, **kwargs):
    if raw or instance.canceled or not instance.stage_id_id:
        return
    stage_type = (
        Stage._base_manager.filter(pk=instance.stage_id_id)
        .values_list("stage_type", flat=True)
        .first()
    )
    if stage_type == "interview":
        _enforce(instance)
