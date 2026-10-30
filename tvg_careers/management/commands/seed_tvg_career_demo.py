from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from base.models import Company, Department, JobPosition
from horilla.settings.security import is_production_mode
from recruitment.models import Recruitment, Skill
from tvg_careers.models import JobOpening
from tvg_careers.questionnaires import ensure_questionnaire_version
from tvg_core.models import Venture

DEMO_TAG = "[SYNTHETIC TVG CAREER DEMO]"
VENTURES = (
    {
        "slug": Venture.FLUXOPS,
        "name": "FluxOps",
        "summary": "Factory-floor software and responsible manufacturing AI.",
        "title": "[SYNTHETIC] FluxOps Software Intern",
        "department": "Synthetic Product Engineering",
        "description": (
            f"{DEMO_TAG} Help shape a pilot-ready operations platform for "
            "manufacturing teams."
        ),
        "responsibilities": (
            "Map a supervised factory-floor workflow.\n"
            "Implement and test a small Django feature.\n"
            "Document decisions and review them with the product team."
        ),
        "learning_outcomes": (
            "Translate operational problems into software requirements.\n"
            "Practise tested Django delivery and responsible AI boundaries."
        ),
        "required_skills": ("Python", "Clear communication"),
        "preferred_skills": ("Django", "Manufacturing systems"),
        "work_mode": JobOpening.WorkMode.HYBRID,
        "erasmus_plus": True,
    },
    {
        "slug": Venture.FINDMYSTAY,
        "name": "FindMyStay",
        "summary": "European student accommodation marketplace.",
        "title": "[SYNTHETIC] Marketplace Product Intern",
        "department": "Synthetic Marketplace Product",
        "description": (
            f"{DEMO_TAG} Improve a multilingual accommodation journey for "
            "international students."
        ),
        "responsibilities": (
            "Review one synthetic renter journey.\n"
            "Turn findings into a scoped product improvement.\n"
            "Validate the result with accessibility and privacy checks."
        ),
        "learning_outcomes": (
            "Practise marketplace product discovery.\n"
            "Learn to balance trust, usability and operational constraints."
        ),
        "required_skills": ("Product thinking", "Clear communication"),
        "preferred_skills": ("User research", "SQL"),
        "work_mode": JobOpening.WorkMode.REMOTE,
        "erasmus_plus": True,
    },
    {
        "slug": Venture.THEVIPGROUP,
        "name": "THEVIPGROUP",
        "summary": "Company operations, people, IT and administration.",
        "title": "[SYNTHETIC] PeopleOps Automation Intern",
        "department": "Synthetic People Operations",
        "description": (
            f"{DEMO_TAG} Improve a documented internal process without "
            "automating human employment decisions."
        ),
        "responsibilities": (
            "Map a synthetic PeopleOps workflow.\n"
            "Prototype a safe, reviewable automation.\n"
            "Measure the result and document operational controls."
        ),
        "learning_outcomes": (
            "Learn process design and workflow automation.\n"
            "Apply least privilege, auditability and human approval controls."
        ),
        "required_skills": ("Process design", "Clear communication"),
        "preferred_skills": ("Workflow automation", "Data analysis"),
        "work_mode": JobOpening.WorkMode.ONSITE,
        "erasmus_plus": False,
    },
)


class Command(BaseCommand):
    help = (
        "Create three draft synthetic CareerHub roles for an existing legal employer."
    )

    def add_arguments(self, parser):
        parser.add_argument("--company-id", type=int, required=True)
        parser.add_argument(
            "--confirm-synthetic",
            action="store_true",
            help="Acknowledge that only synthetic, non-production demo rows are created.",
        )
        parser.add_argument(
            "--publish-for-local-review",
            action="store_true",
            help=(
                "Publish only these conspicuously synthetic roles in a non-production "
                "environment for CareerHub browser review."
            ),
        )

    def handle(self, *args, **options):
        if not options["confirm_synthetic"]:
            raise CommandError("Pass --confirm-synthetic to create demonstration data.")
        if is_production_mode(
            settings.DEBUG,
            getattr(settings, "HORILLA_ENV", ""),
        ):
            raise CommandError(
                "Synthetic CareerHub demo data is disabled in production mode."
            )

        try:
            company = Company.objects.get(pk=options["company_id"])
        except Company.DoesNotExist as exc:
            raise CommandError("The selected legal employer does not exist.") from exc

        today = timezone.localdate()
        created_openings = 0
        with transaction.atomic():
            for order, demo in enumerate(VENTURES, start=1):
                venture, _created = Venture.all_objects.update_or_create(
                    legal_employer=company,
                    slug=demo["slug"],
                    defaults={
                        "name": demo["name"],
                        "summary": demo["summary"],
                        "display_order": order,
                        "is_active": True,
                    },
                )
                department = Department.objects.filter(
                    department=demo["department"],
                    company_id=company,
                ).first()
                if department is None:
                    department = Department.objects.create(
                        department=demo["department"]
                    )
                    department.company_id.add(company)
                position = JobPosition.objects.filter(
                    job_position=f"Synthetic {demo['name']} Intern",
                    department_id=department,
                    company_id=company,
                ).first()
                if position is None:
                    position = JobPosition.objects.create(
                        job_position=f"Synthetic {demo['name']} Intern",
                        department_id=department,
                    )
                    position.company_id.add(company)

                recruitment = Recruitment.default.filter(
                    company_id=company,
                    job_position_id=position,
                    title__startswith="[SYNTHETIC",
                ).first()
                if recruitment is None:
                    recruitment = Recruitment.default.create(
                        title=demo["title"],
                        description=demo["description"],
                        is_event_based=False,
                        is_published=False,
                        closed=False,
                        job_position_id=position,
                        vacancy=1,
                        company_id=company,
                        start_date=today,
                        end_date=today + timedelta(days=60),
                    )
                    recruitment.open_positions.add(position)
                else:
                    recruitment.title = demo["title"]
                    recruitment.description = demo["description"]
                    recruitment.end_date = today + timedelta(days=60)
                    recruitment.vacancy = 1
                    recruitment.save()

                required_skills = []
                for title in demo["required_skills"]:
                    skill = Skill.objects.filter(title__iexact=title).first()
                    required_skills.append(skill or Skill.objects.create(title=title))
                recruitment.skills.set(required_skills)

                opening, opening_created = JobOpening.all_objects.get_or_create(
                    recruitment=recruitment,
                    defaults={
                        "venture": venture,
                        "public_slug": (
                            f"synthetic-{demo['slug']}-internship-{company.pk}"
                        ),
                        "opportunity_type": JobOpening.OpportunityType.INTERNSHIP,
                        "responsibilities": demo["responsibilities"],
                        "learning_outcomes": demo["learning_outcomes"],
                        "work_location": "Kraków, Poland · synthetic role",
                        "work_mode": demo["work_mode"],
                        "erasmus_plus_applicable": demo["erasmus_plus"],
                        "expected_start_date_from": today + timedelta(days=45),
                        "expected_start_date_to": today + timedelta(days=60),
                        "minimum_duration_weeks": 12,
                        "preferred_duration_weeks": 24,
                    },
                )
                if not opening_created:
                    opening.venture = venture
                    opening.opportunity_type = JobOpening.OpportunityType.INTERNSHIP
                    opening.responsibilities = demo["responsibilities"]
                    opening.learning_outcomes = demo["learning_outcomes"]
                    opening.work_location = "Kraków, Poland · synthetic role"
                    opening.work_mode = demo["work_mode"]
                    opening.erasmus_plus_applicable = demo["erasmus_plus"]
                    opening.expected_start_date_from = today + timedelta(days=45)
                    opening.expected_start_date_to = today + timedelta(days=60)
                    opening.minimum_duration_weeks = 12
                    opening.preferred_duration_weeks = 24
                    opening.save()

                preferred_skills = []
                for title in demo["preferred_skills"]:
                    skill = Skill.objects.filter(title__iexact=title).first()
                    preferred_skills.append(skill or Skill.objects.create(title=title))
                opening.preferred_skills.set(preferred_skills)
                ensure_questionnaire_version(recruitment)

                if options["publish_for_local_review"]:
                    recruitment.start_date = today - timedelta(days=1)
                    recruitment.end_date = today + timedelta(days=60)
                    recruitment.closed = False
                    recruitment.is_active = True
                    recruitment.is_published = True
                    recruitment.full_clean()
                    opening.recruitment = recruitment
                    opening.full_clean()
                    recruitment.save()
                created_openings += int(opening_created)

        state = (
            "published for local review"
            if options["publish_for_local_review"]
            else "left in their existing publication state"
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"Ensured three recruiting ventures and three synthetic roles; "
                f"{created_openings} opening(s) created and roles {state}."
            )
        )
