from uuid import uuid4

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.utils import timezone


class CandidateProfileMigrationCompatibilityTests(TransactionTestCase):
    """Prove existing Phase 4 accounts survive the Phase 5/6 schema upgrades."""

    migrate_from = [("tvg_careerhub_accounts", "0001_initial")]
    migrate_to = [
        (
            "tvg_careerhub_accounts",
            "0004_alter_candidatesecurityevent_event_type",
        )
    ]

    def setUp(self):
        super().setUp()
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        old_apps = executor.loader.project_state(self.migrate_from).apps
        Account = old_apps.get_model("tvg_careerhub_accounts", "CandidateAccount")
        self.account_id = uuid4()
        Account.objects.create(
            id=self.account_id,
            email="migration.candidate@example.test",
            first_name="Migration",
            last_name="Candidate",
            password="synthetic-unusable-password-value",
            privacy_notice_version="2026-10",
            privacy_notice_acknowledged_at=timezone.now(),
            talent_pool_consent_updated_at=timezone.now(),
        )

        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)
        self.apps = executor.loader.project_state(self.migrate_to).apps

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def test_account_is_preserved_and_can_own_profile_and_multiple_applications(self):
        Account = self.apps.get_model("tvg_careerhub_accounts", "CandidateAccount")
        Profile = self.apps.get_model("tvg_careerhub_accounts", "CandidateProfile")
        Application = self.apps.get_model(
            "tvg_careerhub_accounts", "CandidateApplication"
        )

        account = Account.objects.get(pk=self.account_id)
        self.assertEqual(account.email, "migration.candidate@example.test")
        Profile.objects.create(account=account, university="Synthetic University")
        Application.objects.create(
            account=account,
            opening_public_id=uuid4(),
            opening_slug="synthetic-fluxops-role",
            opening_title="Synthetic FluxOps role",
            venture_slug="fluxops",
            venture_name="FluxOps",
        )
        Application.objects.create(
            account=account,
            opening_public_id=uuid4(),
            opening_slug="synthetic-findmystay-role",
            opening_title="Synthetic FindMyStay role",
            venture_slug="findmystay",
            venture_name="FindMyStay",
        )

        self.assertEqual(Application.objects.filter(account=account).count(), 2)
        self.assertEqual(
            set(Application.objects.values_list("source", flat=True)),
            {"company_website"},
        )
