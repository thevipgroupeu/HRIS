from django.core.management.base import BaseCommand

from tvg_erasmus.services import queue_due_verification_reminders


class Command(BaseCommand):
    help = "Queue idempotent verification reminder events without sending email."

    def handle(self, *args, **options):
        queued = queue_due_verification_reminders()
        self.stdout.write(
            self.style.SUCCESS(f"Queued {len(queued)} reminder event(s).")
        )
