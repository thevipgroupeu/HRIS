from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from asset.models import Asset
from tvg_erasmus.models import ErasmusCase
from tvg_onboarding.models import (
    AssetAcknowledgmentTemplate,
    InternOnboarding,
    OnboardingAccessProfile,
    OnboardingAssetAllocation,
)
from tvg_onboarding.services import (
    allocate_asset,
    approve_intern_onboarding,
    complete_checklist_item,
    complete_intern_onboarding,
    complete_manual_provisioning,
    create_employee_for_onboarding,
    grant_onboarding_access,
    prepare_asset_acknowledgment,
    record_asset_acknowledgment,
    start_intern_onboarding,
    visible_onboardings,
)


def _detail(onboarding):
    return redirect("tvg_onboarding:detail", public_id=onboarding.public_id)


def _error_redirect(request, onboarding, action):
    try:
        return action()
    except ValidationError as exc:
        messages.error(
            request, "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc)
        )
        return _detail(onboarding)


@login_required
def dashboard(request):
    return render(
        request,
        "tvg_onboarding/dashboard.html",
        {"onboardings": visible_onboardings(request.user)},
    )


@login_required
def detail(request, public_id):
    onboarding = get_object_or_404(
        visible_onboardings(request.user), public_id=public_id
    )
    company = onboarding.venture.legal_employer
    return render(
        request,
        "tvg_onboarding/detail.html",
        {
            "onboarding": onboarding,
            "assets": Asset.available_assets()
            .filter(asset_category_id__company_id=company)
            .distinct(),
            "access_profiles": OnboardingAccessProfile.objects.filter(
                company=company,
                venture=onboarding.venture,
                department=onboarding.department,
                approved_at__isnull=False,
                is_active=True,
            ),
            "asset_templates": AssetAcknowledgmentTemplate.objects.filter(
                company=company, approved_at__isnull=False
            ),
        },
    )


@login_required
@require_POST
def start(request, case_id):
    case = get_object_or_404(ErasmusCase.all_objects, public_id=case_id)
    onboarding = start_intern_onboarding(user=request.user, case=case)
    messages.success(request, "Intern onboarding created for human approval.")
    return _detail(onboarding)


def _onboarding(request, public_id):
    return get_object_or_404(visible_onboardings(request.user), public_id=public_id)


@login_required
@require_POST
def approve(request, public_id):
    onboarding = _onboarding(request, public_id)
    return _error_redirect(
        request,
        onboarding,
        lambda: (
            _detail(approve_intern_onboarding(user=request.user, onboarding=onboarding))
        ),
    )


@login_required
@require_POST
def provision_manual(request, public_id):
    onboarding = _onboarding(request, public_id)

    def action():
        complete_manual_provisioning(
            user=request.user,
            onboarding=onboarding,
            work_email=request.POST.get("work_email", ""),
            external_reference=request.POST.get("external_reference", ""),
        )
        messages.success(request, "Manual account provisioning completion recorded.")
        return _detail(onboarding)

    return _error_redirect(request, onboarding, action)


@login_required
@require_POST
def convert_employee(request, public_id):
    onboarding = _onboarding(request, public_id)

    def action():
        create_employee_for_onboarding(
            user=request.user,
            onboarding=onboarding,
            phone=request.POST.get("phone", ""),
        )
        messages.success(request, "Candidate linked to the native employee record.")
        return _detail(onboarding)

    return _error_redirect(request, onboarding, action)


@login_required
@require_POST
def grant_access(request, public_id):
    onboarding = _onboarding(request, public_id)
    profile = get_object_or_404(
        OnboardingAccessProfile.objects, pk=request.POST.get("profile_id")
    )

    def action():
        grant_onboarding_access(
            user=request.user, onboarding=onboarding, profile=profile
        )
        messages.success(request, "Approved department and venture access assigned.")
        return _detail(onboarding)

    return _error_redirect(request, onboarding, action)


@login_required
@require_POST
def allocate(request, public_id):
    onboarding = _onboarding(request, public_id)
    asset = get_object_or_404(Asset._base_manager, pk=request.POST.get("asset_id"))

    def action():
        allocate_asset(user=request.user, onboarding=onboarding, asset=asset)
        messages.success(request, "Equipment allocated in Horilla Assets.")
        return _detail(onboarding)

    return _error_redirect(request, onboarding, action)


@login_required
@require_POST
def prepare_acknowledgment(request, public_id, allocation_id):
    onboarding = _onboarding(request, public_id)
    allocation = get_object_or_404(
        OnboardingAssetAllocation.all_objects,
        public_id=allocation_id,
        onboarding=onboarding,
    )
    template = get_object_or_404(
        AssetAcknowledgmentTemplate.objects, pk=request.POST.get("template_id")
    )

    def action():
        prepare_asset_acknowledgment(
            user=request.user, allocation=allocation, template=template
        )
        messages.success(
            request, "Asset acknowledgment generated from the approved template."
        )
        return _detail(onboarding)

    return _error_redirect(request, onboarding, action)


@login_required
@require_POST
def acknowledge_asset(request, public_id, allocation_id):
    onboarding = _onboarding(request, public_id)
    allocation = get_object_or_404(
        OnboardingAssetAllocation.all_objects,
        public_id=allocation_id,
        onboarding=onboarding,
    )

    def action():
        record_asset_acknowledgment(
            user=request.user,
            allocation=allocation,
            reference=request.POST.get("reference", ""),
        )
        messages.success(request, "Asset acknowledgment recorded.")
        return _detail(onboarding)

    return _error_redirect(request, onboarding, action)


@login_required
def download_acknowledgment(request, public_id, allocation_id):
    onboarding = _onboarding(request, public_id)
    if not (
        request.user.is_superuser
        or request.user.has_perm("tvg_onboarding.allocate_onboarding_asset")
    ):
        raise PermissionDenied
    allocation = get_object_or_404(
        OnboardingAssetAllocation.all_objects,
        public_id=allocation_id,
        onboarding=onboarding,
    )
    if not allocation.pdf_file:
        raise Http404
    return FileResponse(
        allocation.pdf_file.open("rb"),
        as_attachment=True,
        filename=f"asset-acknowledgment-v{allocation.document_version}.pdf",
    )


@login_required
@require_POST
def complete_task(request, public_id, code):
    onboarding = _onboarding(request, public_id)

    def action():
        complete_checklist_item(
            user=request.user,
            onboarding=onboarding,
            code=code,
            evidence_note=request.POST.get("evidence_note", ""),
        )
        messages.success(request, "Onboarding checklist item completed.")
        return _detail(onboarding)

    return _error_redirect(request, onboarding, action)


@login_required
@require_POST
def finish(request, public_id):
    onboarding = _onboarding(request, public_id)

    def action():
        complete_intern_onboarding(user=request.user, onboarding=onboarding)
        messages.success(request, "The candidate is now an onboarded intern.")
        return _detail(onboarding)

    return _error_redirect(request, onboarding, action)
