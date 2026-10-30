from __future__ import annotations

import hashlib
from datetime import date
from tempfile import TemporaryDirectory

from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.storage import FileSystemStorage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from horilla.testkit import make_company
from tvg_core.tests.factories import make_person
from tvg_erasmus.document_services import (
    ACCOMMODATION_QUESTION,
    approve_document,
    approve_document_requirement,
    approve_document_template,
    candidate_document_version,
    candidate_submit_document,
    create_learning_agreement_amendment,
    document_projection,
    initialize_document_lifecycle,
    record_accommodation_response,
    record_signature,
    request_signing_service,
    return_signed_document,
    review_document,
    update_accommodation_follow_up,
    upload_manual_signed_document,
)
from tvg_erasmus.models import (
    ErasmusAccommodationRequest,
    ErasmusCaseDocument,
    ErasmusDocumentEvent,
    ErasmusDocumentRequirement,
    ErasmusDocumentSignature,
    ErasmusDocumentTemplate,
    ErasmusDocumentVersion,
)
from tvg_erasmus.services import (
    approve_erasmus_case,
    confirm_acceptance_receipt,
    confirm_arrangement_dates,
    issue_acceptance_letter,
    send_acceptance_letter,
)
from tvg_erasmus.tests import test_acceptance_workflow as acceptance_helpers

ARRANGEMENT_TOKEN = acceptance_helpers.ARRANGEMENT_TOKEN


def synthetic_pdf(name="synthetic-learning-agreement.pdf"):
    return SimpleUploadedFile(
        name,
        b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\n%%EOF",
        content_type="application/pdf",
    )


@override_settings(
    CAREERHUB_ARRANGEMENT_API_ENABLED=True,
    CAREERHUB_ARRANGEMENT_API_TOKEN=ARRANGEMENT_TOKEN,
    TVG_ERASMUS_SIGNING_BACKENDS={
        "self_hosted": "tvg_erasmus.signing.TestSigningProvider"
    },
    TVG_INTERVIEW_PROVIDER_BACKENDS={
        "simulated": "tvg_interviews.providers.SimulatedCalendarProvider",
        "zoho_bookings": "tvg_interviews.providers.DisabledCalendarProvider",
        "self_hosted": "tvg_interviews.providers.DisabledCalendarProvider",
    },
)
class ErasmusDocumentLifecycleTests(TestCase):
    # Reuse Phase 9's synthetic workflow builders without inheriting its tests.
    _restore_storage = acceptance_helpers.AcceptanceWorkflowTests._restore_storage
    _staff = acceptance_helpers.AcceptanceWorkflowTests._staff
    _verify_application = acceptance_helpers.AcceptanceWorkflowTests._verify_application
    _select_application = acceptance_helpers.AcceptanceWorkflowTests._select_application
    _case_values = acceptance_helpers.AcceptanceWorkflowTests._case_values
    _create_case = acceptance_helpers.AcceptanceWorkflowTests._create_case
    _approved_case = acceptance_helpers.AcceptanceWorkflowTests._approved_case
    _template = acceptance_helpers.AcceptanceWorkflowTests._template

    def setUp(self):
        acceptance_helpers.AcceptanceWorkflowTests.setUp(self)
        grants = Permission.objects.filter(
            content_type__app_label="tvg_erasmus",
            codename__in={
                "manage_case_documents",
                "review_case_documents",
                "approve_case_documents",
                "sign_case_documents",
                "return_case_documents",
                "download_case_documents",
                "approve_document_template",
                "approve_document_requirement",
            },
        )
        self.manager_user.user_permissions.add(*grants)
        self.manager_user = type(self.manager_user).objects.get(pk=self.manager_user.pk)

        self.document_media = TemporaryDirectory()
        self.addCleanup(self.document_media.cleanup)
        self.document_storage = FileSystemStorage(location=self.document_media.name)
        self.storage_fields = (
            ErasmusDocumentTemplate._meta.get_field("source_file"),
            ErasmusDocumentVersion._meta.get_field("file"),
        )
        self.original_document_storages = [
            field.storage for field in self.storage_fields
        ]
        for field in self.storage_fields:
            field.storage = self.document_storage
        self.addCleanup(self._restore_document_storage)

    def _restore_document_storage(self):
        for field, storage in zip(
            self.storage_fields, self.original_document_storages, strict=True
        ):
            field.storage = storage

    def _received_case(self):
        case = self._approved_case()
        letter = issue_acceptance_letter(
            user=self.manager_user,
            case=case,
            template=self._template(),
            issue_date=date(2026, 10, 27),
        )
        send_acceptance_letter(user=self.manager_user, letter=letter)
        confirm_acceptance_receipt(
            case_id=case.public_id,
            letter_id=letter.public_id,
            person_public_id=self.person.public_id,
        )
        case.refresh_from_db()
        return case

    def _requirement(
        self,
        *,
        code="learning-agreement",
        document_type=ErasmusDocumentRequirement.DocumentType.LEARNING_AGREEMENT,
        phase=ErasmusDocumentRequirement.Phase.BEFORE_MOBILITY,
        signer_roles=None,
        template=None,
        version=1,
    ):
        requirement = ErasmusDocumentRequirement.all_objects.create(
            company=self.company,
            code=code,
            version_number=version,
            title=f"Synthetic {code}",
            document_type=document_type,
            lifecycle_phase=phase,
            university=self._case_values()["university"],
            programme=self._case_values()["programme"],
            instructions="Use synthetic documentation only.",
            required=True,
            signer_roles=signer_roles
            or [
                ErasmusDocumentRequirement.SignerRole.STUDENT,
                ErasmusDocumentRequirement.SignerRole.UNIVERSITY,
                ErasmusDocumentRequirement.SignerRole.TVG_AUTHORIZED_REPRESENTATIVE,
            ],
            approved_template=template,
        )
        return approve_document_requirement(
            user=self.manager_user, requirement=requirement
        )

    def test_requirement_plan_is_frozen_when_documentation_opens(self):
        case = self._received_case()
        first = self._requirement(version=1)
        document = initialize_document_lifecycle(user=self.manager_user, case=case)[0]
        second = self._requirement(version=2)
        self.assertNotEqual(first.pk, second.pk)

        repeated = initialize_document_lifecycle(user=self.manager_user, case=case)[0]
        case.refresh_from_db()
        self.assertEqual(repeated.pk, document.pk)
        self.assertEqual(case.documents.count(), 1)
        self.assertEqual(document.requirement_id, first.pk)
        self.assertEqual(case.document_requirement_snapshot, [str(first.public_id)])

    def _document_template(self, *, name="Synthetic company NDA", version=1):
        upload = synthetic_pdf("synthetic-company-template.pdf")
        content = upload.read()
        upload.seek(0)
        template = ErasmusDocumentTemplate.all_objects.create(
            company=self.company,
            name=name,
            version_number=version,
            document_type=ErasmusDocumentRequirement.DocumentType.NDA,
            source_file=upload,
            content_type="application/pdf",
            sha256=hashlib.sha256(content).hexdigest(),
            size_bytes=len(content),
        )
        return approve_document_template(user=self.manager_user, template=template)

    def _submitted_document(self):
        case = self._received_case()
        self._requirement()
        document = initialize_document_lifecycle(user=self.manager_user, case=case)[0]
        candidate_submit_document(
            document_id=document.public_id,
            person_public_id=self.person.public_id,
            upload=synthetic_pdf(),
        )
        document.refresh_from_db()
        return case, document

    def test_synthetic_candidate_completes_learning_agreement_lifecycle(self):
        case = self._received_case()
        self._requirement()
        document = initialize_document_lifecycle(user=self.manager_user, case=case)[0]

        client = Client()
        with self.settings(
            ROOT_URLCONF="tvg_careers.peopleops_urls",
            TVG_LEGAL_EMPLOYER_ID=self.company.pk,
        ):
            upload = client.post(
                reverse(
                    "tvg_careers_internal:erasmus-document-upload",
                    args=(case.public_id, document.public_id),
                ),
                {
                    "person_public_id": str(self.person.public_id),
                    "document": synthetic_pdf(),
                },
                HTTP_AUTHORIZATION=f"Bearer {ARRANGEMENT_TOKEN}",
            )
        self.assertEqual(upload.status_code, 200)
        document.refresh_from_db()
        self.assertEqual(document.status, ErasmusCaseDocument.Status.SUBMITTED)

        review_document(user=self.manager_user, document=document)
        approve_document(user=self.manager_user, document=document)
        signed = upload_manual_signed_document(
            user=self.manager_user,
            document=document,
            upload=synthetic_pdf("synthetic-signed-learning-agreement.pdf"),
            signed_roles=document.required_signer_roles,
        )
        document.refresh_from_db()
        self.assertEqual(document.status, ErasmusCaseDocument.Status.SIGNED)
        self.assertEqual(signed.version_number, 2)
        self.assertEqual(
            ErasmusDocumentSignature.all_objects.filter(
                document=document, version=signed
            ).count(),
            3,
        )

        with self.assertRaises(PermissionDenied):
            candidate_document_version(
                document_id=document.public_id,
                version_id=signed.public_id,
                person_public_id=self.person.public_id,
            )

        return_signed_document(user=self.manager_user, document=document)
        returned = candidate_document_version(
            document_id=document.public_id,
            version_id=signed.public_id,
            person_public_id=self.person.public_id,
        )
        self.assertEqual(returned.pk, signed.pk)
        case.refresh_from_db()
        self.assertIsNotNone(case.before_mobility_completed_at)
        projection = document_projection(self.application)
        self.assertTrue(projection["before_mobility_complete"])
        self.assertEqual(projection["accommodation_question"], ACCOMMODATION_QUESTION)

        response = record_accommodation_response(
            case_id=case.public_id,
            person_public_id=self.person.public_id,
            needs_assistance=True,
        )
        self.assertEqual(response.status, ErasmusAccommodationRequest.Status.PENDING)
        self.assertFalse(hasattr(response, "notes"))
        response = update_accommodation_follow_up(
            user=self.manager_user,
            case=case,
            status=ErasmusAccommodationRequest.Status.CONTACTED,
        )
        self.assertEqual(response.status, ErasmusAccommodationRequest.Status.CONTACTED)
        self.assertEqual(response.handled_by_id, self.manager.pk)
        amendment = create_learning_agreement_amendment(
            user=self.manager_user, document=document
        )
        self.assertEqual(
            amendment.lifecycle_phase,
            ErasmusDocumentRequirement.Phase.DURING_MOBILITY,
        )
        self.assertEqual(amendment.amendment_of_id, document.pk)

    def test_state_gates_require_review_approval_and_all_signatures(self):
        _case, document = self._submitted_document()
        with self.assertRaisesMessage(ValidationError, "Human review"):
            approve_document(user=self.manager_user, document=document)

        review_document(user=self.manager_user, document=document)
        approve_document(user=self.manager_user, document=document)
        with self.assertRaisesMessage(ValidationError, "All required signatures"):
            return_signed_document(user=self.manager_user, document=document)

        first_role = document.required_signer_roles[0]
        record_signature(
            user=self.manager_user, document=document, signer_role=first_role
        )
        document.refresh_from_db()
        self.assertEqual(document.status, ErasmusCaseDocument.Status.APPROVED)

    def test_company_documents_are_materialized_only_after_learning_agreement(self):
        case = self._received_case()
        self._requirement()
        nda_template = self._document_template()
        self._requirement(
            code="nda",
            document_type=ErasmusDocumentRequirement.DocumentType.NDA,
            phase=ErasmusDocumentRequirement.Phase.AFTER_INTERNSHIP,
            signer_roles=[ErasmusDocumentRequirement.SignerRole.STUDENT],
            template=nda_template,
        )
        learning = initialize_document_lifecycle(user=self.manager_user, case=case)[0]
        self.assertEqual(case.documents.count(), 1)
        candidate_submit_document(
            document_id=learning.public_id,
            person_public_id=self.person.public_id,
            upload=synthetic_pdf(),
        )
        review_document(user=self.manager_user, document=learning)
        approve_document(user=self.manager_user, document=learning)
        upload_manual_signed_document(
            user=self.manager_user,
            document=learning,
            upload=synthetic_pdf("synthetic-signed-la.pdf"),
            signed_roles=learning.required_signer_roles,
        )
        return_signed_document(user=self.manager_user, document=learning)
        nda = case.documents.get(code="nda")
        self.assertEqual(nda.status, ErasmusCaseDocument.Status.REQUESTED)
        self.assertEqual(nda.template_id, nda_template.pk)
        self.assertEqual(
            nda.lifecycle_phase,
            ErasmusDocumentRequirement.Phase.AFTER_INTERNSHIP,
        )

    def test_document_versions_and_events_are_immutable(self):
        _case, document = self._submitted_document()
        version = document.versions.get()
        version.original_filename = "mutated.pdf"
        with self.assertRaisesMessage(ValidationError, "immutable"):
            version.save()
        event = document.events.first()
        event.event_type = "mutated"
        with self.assertRaisesMessage(ValidationError, "immutable"):
            event.save()

        review_document(
            user=self.manager_user,
            document=document,
            revision_reason="Synthetic university form needs a corrected section.",
        )
        candidate_submit_document(
            document_id=document.public_id,
            person_public_id=self.person.public_id,
            upload=synthetic_pdf("synthetic-learning-agreement-v2.pdf"),
        )
        self.assertEqual(
            list(document.versions.values_list("version_number", flat=True)), [2, 1]
        )

    def test_candidate_and_staff_access_are_object_scoped(self):
        case, document = self._submitted_document()
        other_person = make_person(
            company=self.company, email="phase10.other@example.test"
        )
        client = Client()
        with self.settings(
            ROOT_URLCONF="tvg_careers.peopleops_urls",
            TVG_LEGAL_EMPLOYER_ID=self.company.pk,
        ):
            unauthenticated = client.post(
                reverse(
                    "tvg_careers_internal:erasmus-document-upload",
                    args=(case.public_id, document.public_id),
                ),
                {
                    "person_public_id": str(self.person.public_id),
                    "document": synthetic_pdf("unauthenticated.pdf"),
                },
            )
            response = client.post(
                reverse(
                    "tvg_careers_internal:erasmus-document-upload",
                    args=(case.public_id, document.public_id),
                ),
                {
                    "person_public_id": str(other_person.public_id),
                    "document": synthetic_pdf("other.pdf"),
                },
                HTTP_AUTHORIZATION=f"Bearer {ARRANGEMENT_TOKEN}",
            )
        self.assertEqual(unauthenticated.status_code, 404)
        self.assertEqual(response.status_code, 404)

        other_company = make_company("Synthetic Phase 10 Other Employer")
        outsider_user, _employee = self._staff(
            "synthetic-phase10-outsider",
            company=other_company,
            permissions={"tvg_erasmus": {"review_case_documents"}},
        )
        with self.assertRaises(PermissionDenied):
            review_document(user=outsider_user, document=document)

    def test_assigned_supervisor_can_review_with_explicit_permission(self):
        supervisor_user, supervisor = self._staff(
            "synthetic-phase10-supervisor",
            company=self.company,
            permissions={"tvg_erasmus": {"review_case_documents"}},
        )
        case = self._create_case(supervisor=supervisor)
        confirm_arrangement_dates(
            case_id=case.public_id,
            person_public_id=self.person.public_id,
            start_date=date(2027, 1, 15),
            end_date=date(2027, 4, 15),
        )
        approve_erasmus_case(user=self.manager_user, case=case)
        letter = issue_acceptance_letter(
            user=self.manager_user,
            case=case,
            template=self._template(),
            issue_date=date(2026, 10, 27),
        )
        send_acceptance_letter(user=self.manager_user, letter=letter)
        confirm_acceptance_receipt(
            case_id=case.public_id,
            letter_id=letter.public_id,
            person_public_id=self.person.public_id,
        )
        self._requirement()
        document = initialize_document_lifecycle(user=self.manager_user, case=case)[0]
        candidate_submit_document(
            document_id=document.public_id,
            person_public_id=self.person.public_id,
            upload=synthetic_pdf(),
        )
        reviewed = review_document(user=supervisor_user, document=document)
        self.assertEqual(reviewed.status, ErasmusCaseDocument.Status.REVIEWED)
        self.assertEqual(reviewed.reviewed_by_id, supervisor.pk)

    def test_templates_and_signing_integration_require_human_approval(self):
        case = self._received_case()
        with self.assertRaisesMessage(ValidationError, "approved template"):
            ErasmusDocumentRequirement.all_objects.create(
                company=self.company,
                code="unapproved-company-document",
                title="Synthetic unapproved company document",
                document_type=ErasmusDocumentRequirement.DocumentType.NDA,
                lifecycle_phase=ErasmusDocumentRequirement.Phase.BEFORE_MOBILITY,
                signer_roles=[ErasmusDocumentRequirement.SignerRole.STUDENT],
            )
        template = self._document_template()
        template.name = "Attempted mutation"
        with self.assertRaisesMessage(ValidationError, "immutable"):
            template.save()

        self._requirement()
        initialize_document_lifecycle(user=self.manager_user, case=case)
        document = case.documents.get()
        candidate_submit_document(
            document_id=document.public_id,
            person_public_id=self.person.public_id,
            upload=synthetic_pdf(),
        )
        review_document(user=self.manager_user, document=document)
        approve_document(user=self.manager_user, document=document)
        signing = request_signing_service(user=self.manager_user, document=document)
        repeat = request_signing_service(user=self.manager_user, document=document)
        self.assertEqual(signing.pk, repeat.pk)
        self.assertTrue(signing.provider_reference.startswith("synthetic:"))
        self.assertEqual(
            ErasmusDocumentEvent.all_objects.filter(
                document=document,
                event_type="erasmus.signing_service_requested",
            ).count(),
            1,
        )
