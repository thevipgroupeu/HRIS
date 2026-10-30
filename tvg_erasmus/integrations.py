"""Optional external-requirement boundary (including future NA10 use).

No NA10 fields, rules or network protocol are assumed here.  A reviewed adapter
can be configured later without changing document-state semantics.
"""

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.utils.module_loading import import_string


class DisabledExternalRequirementProvider:
    def publish(self, **kwargs):
        raise ValidationError("This external requirement integration is disabled.")


def get_external_requirement_provider(system_key):
    backends = getattr(settings, "TVG_ERASMUS_EXTERNAL_REQUIREMENT_BACKENDS", {})
    path = backends.get(system_key)
    if not path:
        raise ImproperlyConfigured(
            "No approved adapter is configured for this external requirement system."
        )
    return import_string(path)()
