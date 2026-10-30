from django.core.management.base import BaseCommand

from tvg_erasmus.services import purge_expired_verification_evidence


class Command(BaseCommand):
    help = "Delete expired private verification bytes and retain audit metadata."

    def handle(self, *args, **options):
        purged = purge_expired_verification_evidence()
        self.stdout.write(self.style.SUCCESS(f"Purged {purged} evidence file(s)."))
