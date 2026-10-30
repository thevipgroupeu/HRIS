from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

from django.utils import timezone

from base.models import Department, JobPosition
from recruitment.models import Candidate, Recruitment
from tvg_careers.models import Application, JobOpening
from tvg_careers.questionnaires import ensure_questionnaire_version
from tvg_core.tests.factories import make_person, make_venture


def make_job_opening(
    *,
    company,
    venture=None,
    title="Synthetic Software Intern",
    slug=None,
    is_published=False,
    **overrides,
):
    suffix = uuid4().hex[:8]
    department = Department.objects.create(department=f"Synthetic Dept {suffix}")
    department.company_id.add(company)
    position = JobPosition.objects.create(
        job_position=f"Synthetic Role {suffix}",
        department_id=department,
    )
    position.company_id.add(company)

    today = timezone.localdate()
    recruitment = Recruitment.default.create(
        title=title,
        description="Synthetic role description for automated tests.",
        is_event_based=False,
        is_published=is_published,
        closed=False,
        job_position_id=position,
        vacancy=1,
        company_id=company,
        start_date=today,
        end_date=today + timedelta(days=30),
    )
    recruitment.open_positions.add(position)
    venture = venture or make_venture(company=company)
    values = {
        "public_slug": slug or f"synthetic-opening-{suffix}",
        "recruitment": recruitment,
        "venture": venture,
        "opportunity_type": JobOpening.OpportunityType.INTERNSHIP,
        "responsibilities": "Complete synthetic project tasks.",
        "learning_outcomes": "Learn synthetic delivery practices.",
        "work_location": "Synthetic Krakow Office",
        "work_mode": JobOpening.WorkMode.HYBRID,
        "erasmus_plus_applicable": True,
        "expected_start_date_from": today + timedelta(days=45),
        "expected_start_date_to": today + timedelta(days=60),
        "minimum_duration_weeks": 12,
        "preferred_duration_weeks": 24,
    }
    values.update(overrides)
    opening = JobOpening.all_objects.create(**values)
    ensure_questionnaire_version(recruitment)
    return opening


def make_application(
    *,
    opening,
    person=None,
    email=None,
    name="Synthetic Candidate",
):
    person = person or make_person(
        company=opening.venture.legal_employer,
        email=email,
        preferred_name=name,
    )
    email = email or person.primary_email
    stage = opening.recruitment.stage_set.filter(stage_type="applied").first()
    candidate = Candidate.objects.create(
        name=name,
        email=email,
        recruitment_id=opening.recruitment,
        job_position_id=opening.job_position,
        stage_id=stage,
        source="software",
    )
    return Application.all_objects.create(
        person=person,
        candidate=candidate,
        opening=opening,
    )
