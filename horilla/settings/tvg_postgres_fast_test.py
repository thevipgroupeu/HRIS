"""Fast PostgreSQL/Redis behavior tests with migrations checked separately."""

from .tvg_postgres_test import *  # noqa: F403


class _DisableMigrations:
    def __contains__(self, item):
        return True

    def __getitem__(self, item):
        return None


MIGRATION_MODULES = _DisableMigrations()
